"""Find one person's work address from a company's public evidence, with a calibrated status.

    learn the company's format from its public addresses   patterns.learn_format
    rank candidate addresses with probabilities            patterns.rank_guesses
    verify the best ones (DNS; SMTP only when allowed)     verify.verify_addresses
    combine, calibrate, pick, explain                      score.combine / calibrate / assess

The returned candidate's `checks["basis"]` says in plain words why we believe it, including
when a provider's answer was not taken as proof (Google Workspace, Microsoft 365).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pepite.enrich.patterns import (
    FULL_NAME_PATTERNS,
    DomainFormat,
    Guess,
    is_role_mailbox,
    learn_format,
    rank_guesses,
)
from pepite.enrich.providers import is_disposable, is_free_mail, provider
from pepite.enrich.score import assess, calibrate, combine, evidence_ratio
from pepite.enrich.verify import probing_allowed, verify_addresses
from pepite.models import EmailCandidate, EmailStatus

MAX_PROBES = 3  # candidates asked over SMTP, best first


@dataclass
class Finding:
    candidate: EmailCandidate | None
    raw_probability: float | None = None  # posterior before calibration
    guesses: list[Guess] = field(default_factory=list)
    format: DomainFormat | None = None


def _verification_words(checks: dict[str, str], probing: bool) -> str:
    label = provider(checks.get("provider")).label
    trust = provider(checks.get("provider")).rcpt
    smtp, catch_all = checks.get("smtp"), checks.get("catch_all")
    if checks.get("mx") == "a":
        prefix = "no MX record (mail goes to the web host); "
    elif checks.get("mx") == "error":
        prefix = "DNS lookup failed; "
    else:
        prefix = ""
    if smtp == "ok":
        if catch_all == "yes":
            words = "the server accepts any address (catch-all), so it cannot confirm this one"
        elif trust == "limited":
            words = (
                f"{label} accepted the mailbox, which alone does not prove it is this "
                "person's: the status rests on the format evidence too"
            )
        elif trust == "hidden":
            words = f"{label} accepted it, but such gateways accept most addresses"
        elif catch_all == "no":
            words = "the mail server confirmed the mailbox and rejects unknown ones"
        else:
            words = "the mail server accepted the mailbox (catch-all test inconclusive)"
    elif smtp == "temp":
        words = "the mail server asked to try again later (greylisting); retry in a few minutes"
    elif smtp == "blocked":
        words = "the mail server refused our check (policy block); format evidence only"
    elif smtp == "error":
        words = "could not reach the mail server (port 25 is often blocked); format evidence only"
    elif smtp == "skipped" and probing:
        words = "not asked over SMTP (a better candidate was checked first)"
    else:
        words = "mailbox not checked over SMTP (off in settings); format evidence only"
    return prefix + words


def find_email_detail(
    full_name: str,
    domain: str,
    known_emails: Sequence[str],
    names: Sequence[str] = (),
    *,
    smtp_probe: bool = False,
) -> Finding:
    """Best address for `full_name` at `domain`, given the addresses and names on its site."""
    domain = domain.strip().lower()
    if not domain or is_free_mail(domain) or is_disposable(domain):
        return Finding(None)  # not a company domain: guessing would hit strangers
    fmt = learn_format(known_emails, domain, [*names, full_name])
    guesses = rank_guesses(full_name, domain, known_emails, names, fmt=fmt)
    if not guesses:
        return Finding(None, format=fmt)
    probing = probing_allowed(smtp_probe)
    batch = guesses[:MAX_PROBES] if probing else guesses[:1]
    checked = verify_addresses([g.address for g in batch], smtp_probe=probing)
    domain_checks = checked[0].checks
    if domain_checks.get("syntax") == "fail" or domain_checks.get("mx") == "fail":
        return Finding(None, guesses=guesses, format=fmt)  # the domain receives no mail
    ratios = [evidence_ratio(c.checks, g.pattern) for g, c in zip(batch, checked, strict=True)]
    ratios += [1.0] * (len(guesses) - len(batch))
    posterior = combine([g.probability for g in guesses], ratios)

    def checks_for(i: int) -> dict[str, str]:
        if i < len(checked):
            return dict(checked[i].checks)
        local = guesses[i].address.rpartition("@")[0]
        out = dict(domain_checks, smtp="skipped")
        out["role"] = "yes" if is_role_mailbox(local) else "no"
        if out.get("catch_all") not in {"yes", "no"}:
            out["catch_all"] = "skipped"
        return out

    known = {e.strip().lower() for e in known_emails}
    order = sorted(range(len(guesses)), key=lambda i: (-posterior[i], i))
    for i in order:
        checks = checks_for(i)
        guess = guesses[i]
        published = guess.address in known
        status, conf = assess(
            calibrate(posterior[i]),
            checks,
            calibrate(guess.probability),
            published=published and guess.pattern in FULL_NAME_PATTERNS,
        )
        if status is EmailStatus.INVALID:
            continue
        basis = "published on the company's own pages" if published else fmt.describe(guess.pattern)
        checks["basis"] = f"{basis}; {_verification_words(checks, probing)}"
        cand = EmailCandidate(guess.address, status, conf, guess.pattern, checks)
        return Finding(cand, posterior[i], guesses, fmt)
    return Finding(None, guesses=guesses, format=fmt)


def find_email_from(
    full_name: str,
    domain: str,
    known_emails: Sequence[str],
    names: Sequence[str] = (),
    *,
    smtp_probe: bool = False,
) -> EmailCandidate | None:
    """find_email_detail(...).candidate"""
    return find_email_detail(
        full_name, domain, known_emails, names, smtp_probe=smtp_probe
    ).candidate
