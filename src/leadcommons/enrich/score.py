"""Confidence score (0-100) of an email candidate.

    confidence = 0                              if status is INVALID
               = 90 + P / 5                     if status is VALID (SMTP accepted the mailbox
                                                and the server rejects random addresses)
               = P + D                          otherwise (UNKNOWN or CATCH_ALL)
    minus 10 for a role mailbox and 30 for a disposable domain, clamped to 0..100.

P, pattern evidence (0-50):
    50                     the exact address is published on the company's own pages
    50 - 30 * 0.5 ** n     n > 0 weighted public addresses confirm the pattern
                           (1 -> 35, 2 -> 42.5, 3 -> 46.25; a shape-only match counts 0.5)
    20 * prior share       no evidence at all: rank by global pattern frequency
                           ({first}.{last} -> 20, {first} -> 8, ...)
    10 * prior share       evidence exists, but for other patterns
    Alternative spellings of a name (umlauts, particles, hyphens) get 0.9 ** variant_index.

D, deliverability (0-20):
    20 domain has MX records, 10 only an A record, 0 DNS not checked or unreachable;
    5 less when the server is catch-all (any address is accepted, so nothing is confirmed).

So: verified mailbox 90-100 > published address 70 > pattern seen on the site 55-66 >
best global guess 40 > invalid 0. Unverified guesses (no DNS yet) score P alone.
"""

from __future__ import annotations

from collections.abc import Mapping

from leadcommons.models import EmailStatus

EXACT_POINTS = 50.0
_DELIVERABILITY = {"ok": 20.0, "a": 10.0}


def pattern_points(evidence: float, prior_share: float, any_evidence: bool) -> float:
    """P: points for a pattern given its evidence and prior share (prior / top prior)."""
    if evidence > 0:
        return 50.0 - 30.0 * 0.5**evidence
    return (10.0 if any_evidence else 20.0) * prior_share


def confidence(points: float, status: EmailStatus, checks: Mapping[str, str]) -> int:
    """Combine pattern points with verification results (see module docstring)."""
    if status is EmailStatus.INVALID:
        return 0
    if status is EmailStatus.VALID:
        score = 90.0 + points / 5
    else:
        score = points + _DELIVERABILITY.get(checks.get("mx", ""), 0.0)
        if status is EmailStatus.CATCH_ALL:
            score -= 5.0
    if checks.get("role") == "yes":
        score -= 10.0
    if checks.get("disposable") == "yes":
        score -= 30.0
    return max(0, min(100, round(score)))
