"""Email pattern inference ({first}.{last}, {f}{last}, ...) and candidate generation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from leadcommons.enrich.names import name_variants
from leadcommons.enrich.score import EXACT_POINTS, pattern_points

# Rough share of companies using each pattern; the order is the no-evidence ranking.
PRIORS: dict[str, float] = {
    "{first}.{last}": 0.40,
    "{first}": 0.16,
    "{f}{last}": 0.13,
    "{first}{last}": 0.08,
    "{f}.{last}": 0.06,
    "{last}": 0.04,
    "{first}_{last}": 0.03,
    "{first}-{last}": 0.03,
    "{first}{l}": 0.03,
    "{last}.{first}": 0.02,
    "{last}{f}": 0.02,
}
PATTERNS: tuple[str, ...] = tuple(PRIORS)
_MAX_PRIOR = max(PRIORS.values())
SHAPE_WEIGHT = 0.5  # an address whose shape fits a pattern but matches no known name
MAX_GUESSES = 20
_VARIANT_DECAY = 0.9  # alternative spellings of a name score a little lower

# Shared mailboxes, not people: ignored for inference and flagged by verification.
ROLE_MAILBOXES = frozenset(
    """
abuse accounting accounts accueil admin administrator administration billing bonjour booking
buchhaltung careers commercial communication compta comptabilite contact contacto contatti
customerservice datenschutz direction dpo email enquiries events facturation factures feedback
finance gdpr hallo hello help hi hiring hola hr impressum info infos inquiries invoices jobs
karriere kontakt kundenservice legal mail marketing media news newsletter noreply no-reply
office orders partenariats partners postmaster presse press privacy recrutement recruiting
reception reservations rgpd rh sales secretariat security service shop stage store support
talent team ventas vertrieb webmaster welcome
""".split()
)
_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[a-z]\.[a-z]{2,}"), "{f}.{last}"),
    (re.compile(r"[a-z]{2,}\.[a-z]{2,}"), "{first}.{last}"),
    (re.compile(r"[a-z]{2,}_[a-z]{2,}"), "{first}_{last}"),
)


@dataclass(frozen=True)
class PatternScore:
    pattern: str
    evidence: float  # weighted count of known addresses that fit the pattern
    prior: float


@dataclass(frozen=True)
class Guess:
    address: str
    pattern: str
    points: float  # pattern-evidence part of the confidence score (0-50)


def is_role_mailbox(local: str) -> bool:
    """info, contact, sales-fr, jobs2 ... are shared mailboxes, not a person's address."""
    local = local.lower()
    head = re.split(r"[._+-]", local)[0]
    return (
        local in ROLE_MAILBOXES
        or head in ROLE_MAILBOXES
        or local.rstrip("0123456789") in (ROLE_MAILBOXES)
    )


def render(pattern: str, first: str, last: str) -> str | None:
    """Local part for `pattern`, or None when the pattern needs a missing surname."""
    if not first or (not last and ("{last}" in pattern or "{l}" in pattern)):
        return None
    return pattern.format(first=first, last=last, f=first[0], l=last[:1])


def german_domain(domain: str) -> bool:
    """German-speaking ccTLD: prefer "ae/oe/ue" spellings of umlauts."""
    return domain.endswith((".de", ".at", ".ch"))


def _person_locals(known_emails: Iterable[str], domain: str) -> list[str]:
    out: list[str] = []
    for email in known_emails:
        local, _, host = email.strip().lower().rpartition("@")
        on_domain = host == domain or host.endswith("." + domain)
        if local and on_domain and not is_role_mailbox(local) and local not in out:
            out.append(local)
    return out


def infer_patterns(
    known_emails: Iterable[str], domain: str, names: Iterable[str] = ()
) -> list[PatternScore]:
    """All patterns ranked by evidence, then prior frequency.

    A known address that renders from a site name under some pattern adds 1 (split between
    patterns when several fit); an address that matches no name but has a telling shape
    (j.doe, jane.doe, jane_doe) adds SHAPE_WEIGHT. Role mailboxes are ignored.
    """
    german = german_domain(domain)
    variants = [v for name in names for v in name_variants(name, german_first=german)]
    evidence = dict.fromkeys(PATTERNS, 0.0)
    for local in _person_locals(known_emails, domain):
        matched = {p for f, last in variants for p in PATTERNS if render(p, f, last) == local}
        for p in matched:
            evidence[p] += 1 / len(matched)
        if not matched:
            shape = next((p for rx, p in _SHAPES if rx.fullmatch(local)), None)
            if shape:
                evidence[shape] += SHAPE_WEIGHT
    scores = [PatternScore(p, evidence[p], PRIORS[p]) for p in PATTERNS]
    return sorted(scores, key=lambda s: (-s.evidence, -s.prior))


def rank_guesses(
    full_name: str, domain: str, known_emails: Sequence[str] = (), names: Iterable[str] = ()
) -> list[Guess]:
    """Candidate addresses for one person, best first.

    An address of the person already published on the site comes first; then every pattern
    (by inferred rank) applied to every spelling variant of the name.
    """
    variants = name_variants(full_name, german_first=german_domain(domain))
    if not variants:
        return []
    scores = infer_patterns(known_emails, domain, [*names, full_name])
    any_evidence = any(s.evidence > 0 for s in scores)
    known = {e.strip().lower() for e in known_emails}
    guesses: list[Guess] = []
    seen: set[str] = set()

    def add(address: str, pattern: str, points: float) -> None:
        if address not in seen:
            seen.add(address)
            guesses.append(Guess(address, pattern, points))

    rendered = [
        (s, i, f"{local}@{domain}")
        for s in scores
        for i, (f, last) in enumerate(variants)
        if (local := render(s.pattern, f, last))
    ]
    for s, _, address in rendered:
        if address in known:
            add(address, s.pattern, EXACT_POINTS)
    for s, i, address in rendered:
        points = pattern_points(s.evidence, s.prior / _MAX_PRIOR, any_evidence)
        add(address, s.pattern, points * _VARIANT_DECAY**i)
    return guesses[:MAX_GUESSES]
