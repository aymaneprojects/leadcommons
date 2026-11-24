"""Email verification: syntax, DNS and mail provider, role/disposable/free-mail flags, optional
SMTP probe.

The SMTP probe runs only when asked (smtp_probe=True, i.e. settings.smtp_probe) and never in
demo mode (probing_allowed). It connects to the domain's MX on port 25 and sends only
EHLO/HELO, MAIL FROM:<>, RCPT TO (the candidates in order until one is accepted, then a
random, human-looking address on the same domain to detect catch-all servers) and QUIT. It
never sends DATA, so no message is ever delivered.

Politeness: one session per domain and attempt, at most one session per MX host every
`provider.min_interval` seconds across all threads, SMTP_TIMEOUT seconds per command, the next
MX host when one is unreachable. 4xx answers (greylisting, "try again later") are retried after
GREYLIST_DELAYS; 5.7.x policy blocks and unreachable hosts are not retried.

What the answers mean depends on the provider (providers.py): Google Workspace and Microsoft
365 acceptances are weak evidence, gateways' answers say little. score.py turns them into a
probability; this module only reports them.

checks values:
    syntax      ok | fail
    mx          ok | a (no MX, A-record fallback) | fail | error (DNS unreachable) | skipped
    provider    google | microsoft | zoho | proton | ovh | ionos | gandi | gateway | self | ...
    smtp        ok | fail (5xx) | temp (4xx after retries) | blocked (5.7.x policy) | error
                | skipped
    catch_all   yes | no | unknown | skipped
    role        yes | no | skipped
    disposable  yes | no | skipped
    free_mail   yes | no | skipped
"""

from __future__ import annotations

import secrets
import smtplib
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import dns.exception
import dns.resolver
from email_validator import EmailNotValidError, validate_email

from pepite.enrich.patterns import is_role_mailbox
from pepite.enrich.providers import (
    DISPOSABLE_DOMAINS,
    Provider,
    classify_mx,
    is_disposable,
    is_free_mail,
)
from pepite.enrich.score import DEFAULT_PRIOR, assess, combine, evidence_ratio
from pepite.models import EmailCandidate

__all__ = [
    "DISPOSABLE_DOMAINS",
    "check_mx",
    "probing_allowed",
    "reset_rate_limits",
    "verify_addresses",
]

DNS_LIFETIME = 5.0
SMTP_PORT = 25
SMTP_TIMEOUT = 6.0  # seconds per connect / command
GREYLIST_DELAYS: tuple[float, ...] = (20.0, 60.0)  # waits before the 2nd and 3rd attempt
MAX_MX_HOSTS = 2  # MX hosts tried when the best one is unreachable
_POLICY_HINTS = ("5.7.", "block", "blacklist", "spamhaus", "reputation", "banned")
_SYLLABLES = (
    "ba be bi bo da de di do fa fe ka ke ki ko la le li lo ma me mi mo na ne ni no pa pe ra re "
    "ri ro sa se si so ta te ti to va ve vi vo"
).split()

_sleep = time.sleep  # replaced in tests and in the offline evaluation
_pace_lock = threading.Lock()
_next_slot: dict[str, float] = {}


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
    temporary: bool = False  # the last attempt ended on a 4xx session error
    attempts: int = 0


@dataclass
class _Session:
    replies: dict[str, tuple[int, str]] = field(default_factory=dict)
    random: tuple[int, str] | None = None
    error: str | None = None
    unreachable: bool = False
    temporary: bool = False
    blocked: bool = False


def probing_allowed(requested: bool) -> bool:
    """SMTP probing only when asked for, and never in the public demo."""
    if not requested:
        return False
    from pepite.config import Settings

    return Settings().mode != "demo"


def reset_rate_limits() -> None:
    """Forget per-MX pacing (tests and evaluation runs)."""
    with _pace_lock:
        _next_slot.clear()


def _pace(host: str, interval: float) -> None:
    """Wait until `host` may receive another session (thread-safe)."""
    with _pace_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, 0.0))
        _next_slot[host] = slot + interval
    if slot > now:
        _sleep(slot - now)


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


def _random_local() -> str:
    """A plausible but unused name ("kodali.mesovira"): some servers treat hex strings as
    harvesting and answer differently, which would fake a catch-all result."""

    def word(n: int) -> str:
        return "".join(secrets.choice(_SYLLABLES) for _ in range(n))

    return f"{word(3)}.{word(4)}"


def _policy(message: str) -> bool:
    return any(hint in message.lower() for hint in _POLICY_HINTS)


def _close(smtp: smtplib.SMTP) -> None:
    try:
        smtp.quit()
    except (OSError, smtplib.SMTPException):
        smtp.close()


def _session(host: str, targets: Sequence[str], random_address: str | None) -> _Session:
    """One SMTP session: RCPT each target until one is accepted, then `random_address`."""
    out = _Session()
    try:
        smtp = smtplib.SMTP(host, SMTP_PORT, timeout=SMTP_TIMEOUT)
    except smtplib.SMTPConnectError as exc:  # greeting was not 220, e.g. "421 too busy"
        out.error = f"connection refused ({exc.smtp_code})"
        out.temporary = 400 <= exc.smtp_code < 500
        return out
    except (OSError, smtplib.SMTPException) as exc:
        out.error = str(exc) or type(exc).__name__
        out.unreachable = True
        return out
    try:
        code, _ = smtp.ehlo()
        if not 200 <= code < 300:
            code, _ = smtp.helo()
        if not 200 <= code < 300:
            out.error, out.temporary = f"HELO refused ({code})", 400 <= code < 500
            return out
        code, msg = smtp.mail("")  # MAIL FROM:<>
        if not 200 <= code < 300:
            text = msg.decode(errors="replace")
            out.error, out.temporary = f"MAIL FROM refused ({code})", 400 <= code < 500
            out.blocked = code >= 500 and _policy(text)
            return out
        accepted = not targets  # only the random check is left
        for address in targets:
            code, message = smtp.rcpt(address)
            text = message.decode(errors="replace")
            out.replies[address] = (code, text)
            if 200 <= code < 300:
                accepted = True
                break
            if code >= 500 and _policy(text):
                out.blocked = True
                break
        greylisted = any(400 <= code < 500 for code, _ in out.replies.values())
        if random_address and (accepted or (greylisted and not out.blocked)):
            # After a 4xx, ask the random address too: its greylisting timer starts now and
            # the retry can settle both at once.
            code, message = smtp.rcpt(random_address)
            out.random = (code, message.decode(errors="replace"))
    except (OSError, smtplib.SMTPException) as exc:
        out.error = str(exc) or type(exc).__name__
    finally:
        _close(smtp)
    return out


def _probe(hosts: Sequence[str], domain: str, targets: Sequence[str], prov: Provider) -> _Probe:
    """Ask the domain's mail server about `targets` (in order), retrying 4xx answers."""
    result = _Probe()
    random_address = f"{_random_local()}@{domain}"
    accepted: str | None = None
    for attempt in range(len(GREYLIST_DELAYS) + 1):
        if attempt:
            _sleep(GREYLIST_DELAYS[attempt - 1])
        ask = [] if accepted else [t for t in targets if _pending(result.replies.get(t))]
        session = _Session(unreachable=True)
        for host in hosts[:MAX_MX_HOSTS]:
            _pace(host, prov.min_interval)
            session = _session(host, ask, random_address if result.catch_all is None else None)
            if not session.unreachable:
                break
        result.attempts += 1
        result.replies.update(session.replies)
        if session.random is not None and not 400 <= session.random[0] < 500:
            result.catch_all = 200 <= session.random[0] < 300
        accepted = accepted or next(
            (t for t, (code, _) in session.replies.items() if 200 <= code < 300), None
        )
        if not accepted and any(c >= 500 and not _policy(m) for c, m in session.replies.values()):
            result.catch_all = False  # the server rejects unknown mailboxes
        result.temporary = session.temporary
        if (session.error and not session.temporary) or session.blocked:
            result.error = session.error
            break
        waiting = session.temporary or (
            accepted is None and any(_pending(r) for r in result.replies.values())
        )
        if not waiting and not (accepted and result.catch_all is None):
            break
    return result


def _pending(reply: tuple[int, str] | None) -> bool:
    """Not asked yet, or answered 4xx (greylisted, try again later)."""
    return reply is None or 400 <= reply[0] < 500


def _outcome(probe: _Probe, address: str) -> tuple[str, str]:
    """(smtp check, catch_all check) for one address of a probe."""
    catch_all = {True: "yes", False: "no", None: "unknown"}[probe.catch_all]
    reply = probe.replies.get(address)
    if reply is None:
        if probe.error:
            return "error", "unknown"
        if probe.temporary:
            return "temp", "unknown"
        return "skipped", catch_all if probe.catch_all is not None else "skipped"
    code, message = reply
    if 200 <= code < 300:
        return "ok", catch_all
    if code >= 500:
        return ("blocked", "unknown") if _policy(message) else ("fail", "no")
    return "temp", catch_all


def verify_addresses(addresses: Sequence[str], *, smtp_probe: bool = False) -> list[EmailCandidate]:
    """Verify several addresses, sharing DNS lookups and SMTP sessions per domain.

    With `smtp_probe` (and outside demo mode), addresses are tried in order and probing stops
    at the first mailbox the server accepts (later ones are reported with smtp "skipped").
    Each candidate's status and confidence say whether the address is deliverable, judged on
    its own (score.DEFAULT_PRIOR); the email finder recombines them with pattern evidence.
    """
    probe_on = probing_allowed(smtp_probe)
    parsed = [_parse(a) for a in addresses]
    mx: dict[str, tuple[str, list[str]]] = {}
    for p in parsed:
        if p and p.domain not in mx:
            mx[p.domain] = check_mx(p.domain)
    providers = {
        domain: classify_mx(hosts, domain) if status in {"ok", "a"} else None
        for domain, (status, hosts) in mx.items()
    }
    probes: dict[str, _Probe] = {}
    if probe_on:
        for domain, (status, hosts) in mx.items():
            prov = providers[domain]
            if status in {"ok", "a"} and prov is not None:
                targets = list(dict.fromkeys(p.address for p in parsed if p and p.domain == domain))
                probes[domain] = _probe(hosts, domain, targets, prov)

    out: list[EmailCandidate] = []
    for raw, p in zip(addresses, parsed, strict=True):
        if p is None:
            keys = ("mx", "provider", "smtp", "catch_all", "role", "disposable", "free_mail")
            checks = {"syntax": "fail", **dict.fromkeys(keys, "skipped")}
            out.append(EmailCandidate(raw.strip(), assess(0.0, checks)[0], 0, checks=checks))
            continue
        mx_status = mx[p.domain][0]
        prov = providers[p.domain]
        if p.domain in probes:
            smtp_check, catch_all = _outcome(probes[p.domain], p.address)
        else:
            smtp_check, catch_all = "skipped", "skipped"
        checks = {
            "syntax": "ok",
            "mx": mx_status,
            "provider": prov.slug if prov else "skipped",
            "smtp": smtp_check,
            "catch_all": catch_all,
            "role": "yes" if is_role_mailbox(p.local) else "no",
            "disposable": "yes" if is_disposable(p.domain) else "no",
            "free_mail": "yes" if is_free_mail(p.domain) else "no",
        }
        prior = DEFAULT_PRIOR
        (probability,) = combine([prior], [evidence_ratio(checks)], rest=1 - prior)
        status, conf = assess(probability, checks)
        out.append(EmailCandidate(p.address, status, conf, checks=checks))
    return out
