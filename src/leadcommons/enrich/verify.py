"""Email verification: syntax, DNS (MX, else A), role/disposable flags, optional SMTP probe.

The SMTP probe (off by default) connects to the best MX on port 25 and sends only
EHLO/HELO, MAIL FROM:<>, RCPT TO (target, then a random address on the same domain to detect
catch-all servers) and QUIT. It never sends DATA, so no message is ever delivered.

checks values:
    syntax      ok | fail
    mx          ok | a (no MX, A-record fallback) | fail | error (DNS unreachable) | skipped
    smtp        ok | fail (5xx) | temp (4xx) | blocked (5.7.x policy) | error | skipped
    catch_all   yes | no | unknown | skipped
    role        yes | no | skipped
    disposable  yes | no | skipped
"""

from __future__ import annotations

import secrets
import smtplib
from collections.abc import Sequence
from dataclasses import dataclass, field

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from leadcommons.enrich.patterns import is_role_mailbox
from leadcommons.enrich.score import confidence
from leadcommons.models import EmailCandidate, EmailStatus

DNS_LIFETIME = 5.0
SMTP_PORT = 25
SMTP_TIMEOUT = 10.0

DISPOSABLE_DOMAINS = frozenset(
    """
mailinator.com guerrillamail.com guerrillamail.net sharklasers.com 10minutemail.com
tempmail.com temp-mail.org yopmail.com yopmail.fr trashmail.com getnada.com dispostable.com
maildrop.cc throwawaymail.com fakeinbox.com mintemail.com mohmal.com emailondeck.com
tempr.email discard.email mailnesia.com mytemp.email burnermail.io moakt.com tempail.com
spamgourmet.com jetable.org
""".split()
)
_POLICY_HINTS = ("5.7.", "block", "blacklist", "spamhaus", "reputation", "banned")


@dataclass
class _Parsed:
    address: str
    local: str
    domain: str


@dataclass
class _Probe:
    replies: dict[str, tuple[int, str]] = field(default_factory=dict)
    catch_all: bool | None = None
    error: str | None = None


def _parse(address: str) -> _Parsed | None:
    try:
        result = validate_email(address.strip(), check_deliverability=False)
    except EmailNotValidError:
        return None
    local, domain = result.local_part.lower(), result.ascii_domain.lower()
    return _Parsed(f"{local}@{domain}", local, domain)


def check_mx(domain: str) -> tuple[str, list[str]]:
    """("ok", MX hosts by preference) | ("a", [domain]) | ("fail", []) | ("error", [])."""
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=DNS_LIFETIME)
        records = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answers)
        hosts = [host for _, host in records if host]
        return ("ok", hosts) if hosts else ("fail", [])  # RFC 7505 null MX: no mail
    except dns.resolver.NXDOMAIN:
        return "fail", []
    except dns.resolver.NoAnswer:
        pass
    except dns.exception.DNSException:
        return "error", []
    try:
        dns.resolver.resolve(domain, "A", lifetime=DNS_LIFETIME)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return "fail", []
    except dns.exception.DNSException:
        return "error", []
    return "a", [domain]


def _close(smtp: smtplib.SMTP) -> None:
    try:
        smtp.quit()
    except (OSError, smtplib.SMTPException):
        smtp.close()


def _probe(host: str, domain: str, addresses: Sequence[str]) -> _Probe:
    """One SMTP session: RCPT each address until one is accepted, then a random one."""
    result = _Probe()
    try:
        smtp = smtplib.SMTP(host, SMTP_PORT, timeout=SMTP_TIMEOUT)
    except (OSError, smtplib.SMTPException) as exc:
        result.error = str(exc) or type(exc).__name__
        return result
    try:
        code, _ = smtp.ehlo()
        if not 200 <= code < 300:
            code, _ = smtp.helo()
        if not 200 <= code < 300:
            result.error = f"HELO refused ({code})"
            return result
        code, _ = smtp.mail("")  # MAIL FROM:<>
        if not 200 <= code < 300:
            result.error = f"MAIL FROM refused ({code})"
            return result
        for address in addresses:
            code, message = smtp.rcpt(address)
            result.replies[address] = (code, message.decode(errors="replace"))
            if 200 <= code < 300:
                random_code, _ = smtp.rcpt(f"{secrets.token_hex(12)}@{domain}")
                if random_code < 300:
                    result.catch_all = True
                elif random_code >= 500:
                    result.catch_all = False
                break
    except (OSError, smtplib.SMTPException) as exc:
        result.error = str(exc) or type(exc).__name__
    finally:
        _close(smtp)
    return result


def _probe_outcome(probe: _Probe, address: str) -> tuple[EmailStatus, str, str]:
    """(status, smtp check, catch_all check) for one address of a probe session."""
    reply = probe.replies.get(address)
    if reply is None:
        if probe.error:
            return EmailStatus.UNKNOWN, "error", "unknown"
        if probe.catch_all:
            return EmailStatus.CATCH_ALL, "skipped", "yes"
        return EmailStatus.UNKNOWN, "skipped", "skipped"
    code, message = reply
    if 200 <= code < 300:
        if probe.catch_all is True:
            return EmailStatus.CATCH_ALL, "ok", "yes"
        if probe.catch_all is False:
            return EmailStatus.VALID, "ok", "no"
        return EmailStatus.UNKNOWN, "ok", "unknown"
    if code >= 500:
        if any(hint in message.lower() for hint in _POLICY_HINTS):
            return EmailStatus.UNKNOWN, "blocked", "unknown"
        return EmailStatus.INVALID, "fail", "no"
    return EmailStatus.UNKNOWN, "temp", "unknown"


def _is_disposable(domain: str) -> bool:
    parts = domain.split(".")
    return any(".".join(parts[i:]) in DISPOSABLE_DOMAINS for i in range(len(parts) - 1))


def verify_addresses(addresses: Sequence[str], *, smtp_probe: bool = False) -> list[EmailCandidate]:
    """Verify several addresses, sharing DNS lookups and one SMTP session per domain.

    With `smtp_probe`, addresses are tried in order and probing stops at the first mailbox the
    server accepts (later ones are reported with smtp "skipped").
    """
    parsed = [_parse(a) for a in addresses]
    mx: dict[str, tuple[str, list[str]]] = {}
    for p in parsed:
        if p and p.domain not in mx:
            mx[p.domain] = check_mx(p.domain)
    probes: dict[str, _Probe] = {}
    if smtp_probe:
        for domain, (status, hosts) in mx.items():
            if status in {"ok", "a"}:
                targets = [p.address for p in parsed if p and p.domain == domain]
                probes[domain] = _probe(hosts[0], domain, targets)

    out: list[EmailCandidate] = []
    for raw, p in zip(addresses, parsed, strict=True):
        if p is None:
            checks = dict.fromkeys(("mx", "smtp", "catch_all", "role", "disposable"), "skipped")
            out.append(
                EmailCandidate(
                    raw.strip(), EmailStatus.INVALID, 0, checks={"syntax": "fail", **checks}
                )
            )
            continue
        mx_status = mx[p.domain][0]
        if mx_status == "fail":
            status, smtp_check, catch_all = EmailStatus.INVALID, "skipped", "skipped"
        elif p.domain in probes:
            status, smtp_check, catch_all = _probe_outcome(probes[p.domain], p.address)
        else:
            status, smtp_check, catch_all = EmailStatus.UNKNOWN, "skipped", "skipped"
        checks = {
            "syntax": "ok",
            "mx": mx_status,
            "smtp": smtp_check,
            "catch_all": catch_all,
            "role": "yes" if is_role_mailbox(p.local) else "no",
            "disposable": "yes" if _is_disposable(p.domain) else "no",
        }
        out.append(EmailCandidate(p.address, status, confidence(0, status, checks), checks=checks))
    return out
