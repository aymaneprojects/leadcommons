"""How sure we are that an address is right: one probability, then a status and a 0-100 score.

    prior       P(address) for each candidate, from patterns.rank_guesses
    evidence    likelihood ratio (LR) of each verification answer, see evidence_ratio:
                    mailbox accepted, server rejects random addresses   ACCEPT_RATIO by provider
                    mailbox accepted, catch-all test inconclusive       UNSURE_RATIO
                    server accepts any address (catch-all)              1 (says nothing)
                    mailbox refused (5xx)                               REJECT_RATIO
                    not probed, greylisted, blocked, unreachable        1
    posterior   P(a | answers) = prior(a) * LR(a) / (sum over b of prior(b) * LR(b) + rest),
                where `rest` is the probability that the address is none of our candidates
    calibrated  a monotone piecewise-linear map of the posterior to the accuracy observed on
                the dev gold set (tests/fixtures/email_gold/dev.csv), fitted by isotonic
                regression with eval.fit_calibration; CALIBRATION holds its knots. It caps
                guesses resting on the format alone at the share of people whose address
                follows the format at all, whatever the evidence.
    status      valid      calibrated >= VALID_PROBABILITY and mailbox-level evidence (the
                           server accepted it, not a catch-all; or the person's full-name
                           address is published by the company): on the gold sets at least
                           90% of the addresses shown as valid are right. The format alone
                           is never enough (its best guesses: 88-93% right).
                invalid    bad syntax, no mail server, or the server refused the mailbox
                catch_all  the server accepts any address and the evidence is below the bar
                unknown    everything else
    confidence  round(100 * calibrated); 0 when invalid. Role mailboxes and disposable domains
                are scaled down and never valid.

Google Workspace and Microsoft 365 get a small ACCEPT_RATIO ("limited" in providers.py): their
acceptance moves a well-supported format over the bar, never a bare guess (assess enforces it
with PATTERN_FLOOR). Single-name formats ({first}, {last}) belong to namesakes more often, so
their acceptance counts less.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pepite.enrich.providers import provider
from pepite.models import EmailStatus

VALID_PROBABILITY = 0.90
# (raw posterior, observed accuracy) knots; refit with `pepite eval emails --fit`.
CALIBRATION: tuple[tuple[float, float], ...] = (
    (0.1942, 0.3),
    (0.3902, 0.4071),
    (0.4973, 0.65),
    (0.604, 0.7214),
    (0.6832, 0.8357),
    (0.7811, 0.931),
    (0.8704, 0.9381),
    (0.9154, 0.9571),
    (0.9691, 0.9762),
)
DEFAULT_PRIOR = 0.25  # a lone address checked with no pattern context (verify_email)

ACCEPT_RATIO = {"reliable": 30.0, "limited": 4.0, "hidden": 1.5}
UNSURE_RATIO = {"reliable": 3.0, "limited": 2.0, "hidden": 1.0}
REJECT_RATIO = {"reliable": 0.02, "limited": 0.05, "hidden": 0.2}
SHORT_PATTERNS = frozenset({"{first}", "{last}"})
SHORT_DISCOUNT = 0.4  # share of the acceptance evidence kept for single-name formats
MX_FACTOR = {"ok": 1.0, "a": 0.85, "error": 0.8}
PATTERN_FLOOR = 0.5  # limited/hidden providers: format evidence needed before RCPT can count
ROLE_FACTOR = 0.5
DISPOSABLE_FACTOR = 0.1


def evidence_ratio(checks: Mapping[str, str], pattern: str | None = None) -> float:
    """Likelihood ratio of this address's SMTP answer for "it is the person's address"."""
    trust = provider(checks.get("provider")).rcpt
    smtp, catch_all = checks.get("smtp"), checks.get("catch_all")
    if smtp == "ok":
        if catch_all == "yes":
            return 1.0
        ratio = ACCEPT_RATIO[trust] if catch_all == "no" else UNSURE_RATIO[trust]
        if pattern in SHORT_PATTERNS:
            ratio = 1 + (ratio - 1) * SHORT_DISCOUNT
        return ratio
    if smtp == "fail":
        return REJECT_RATIO[trust]
    return 1.0


def combine(
    priors: Sequence[float], ratios: Sequence[float], rest: float | None = None
) -> list[float]:
    """Posterior of each candidate given its likelihood ratio (see module docstring)."""
    if rest is None:
        rest = max(0.0, 1.0 - sum(priors))
    weighted = [p * r for p, r in zip(priors, ratios, strict=True)]
    total = sum(weighted) + rest
    return [w / total for w in weighted] if total > 0 else [0.0] * len(weighted)


def calibrate(probability: float) -> float:
    """Map a raw posterior to the accuracy observed on the gold set (linear between knots,
    flat after the last one, straight to 0 below the first)."""
    p = min(max(probability, 0.0), 1.0)
    (x0, y0) = CALIBRATION[0]
    if p <= x0:
        return y0 * p / x0 if x0 > 0 else y0
    for (xa, ya), (xb, yb) in zip(CALIBRATION, CALIBRATION[1:], strict=False):
        if p <= xb:
            return ya + (yb - ya) * (p - xa) / (xb - xa) if xb > xa else yb
    return CALIBRATION[-1][1]


def assess(
    probability: float,
    checks: Mapping[str, str],
    pattern_only: float | None = None,
    published: bool = False,
) -> tuple[EmailStatus, int]:
    """Status and 0-100 confidence for a calibrated probability and the verification checks.

    Valid needs the bar AND mailbox-level evidence: the mail server accepted the address
    (not a catch-all), or the person's full-name address is published on the company's own
    pages (`published`). The format alone never makes an address valid: on the gold sets the
    best format-only guesses were right 88-93% of the time, which is not reliably 90%.

    `pattern_only` is the calibrated probability from the format evidence alone (None for a
    lone address). An acceptance by a provider whose RCPT answers prove little (Google
    Workspace, Microsoft 365, gateways) never makes an address valid unless that format
    evidence already reaches PATTERN_FLOOR: below the bar, the score stays under it."""
    if checks.get("syntax") == "fail" or checks.get("mx") == "fail" or checks.get("smtp") == "fail":
        return EmailStatus.INVALID, 0
    p = probability
    if checks.get("smtp") != "ok":  # an accepting server answers "does it receive mail?"
        p *= MX_FACTOR.get(checks.get("mx", ""), 1.0)
    weak_rcpt = provider(checks.get("provider")).rcpt != "reliable" and checks.get("smtp") == "ok"
    if weak_rcpt and (pattern_only is None or pattern_only < PATTERN_FLOOR):
        p = min(p, VALID_PROBABILITY - 0.01)
    role = checks.get("role") == "yes"
    disposable = checks.get("disposable") == "yes"
    if role:
        p *= ROLE_FACTOR
    if disposable:
        p *= DISPOSABLE_FACTOR
    confidence = max(0, min(100, round(100 * p)))
    mailbox_evidence = published or (
        checks.get("smtp") == "ok" and checks.get("catch_all") != "yes"
    )
    if (
        p >= VALID_PROBABILITY
        and mailbox_evidence
        and checks.get("mx") in {"ok", "a"}
        and not (role or disposable)
    ):
        return EmailStatus.VALID, confidence
    if checks.get("catch_all") == "yes":
        return EmailStatus.CATCH_ALL, confidence
    return EmailStatus.UNKNOWN, confidence


def confidence_of(probability: float) -> int:
    """0-100 score shown for an unverified candidate (calibrated probability)."""
    return max(0, min(100, round(100 * calibrate(probability))))
