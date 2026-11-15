"""Email format inference: learn a company's address format from its public addresses, then
rank candidate addresses for one person with probabilities.

Model (all numbers are probabilities, not scores):

1. Each personal address published on the company's site (role mailboxes such as info@ or
   contact@ are ignored) is matched against the names on the site, under every pattern and
   every spelling of each name (names.email_variants). A match is one unit of evidence, shared
   between the (pattern, spelling) pairs that explain it in proportion to their prior
   probability, refined by one EM pass. An address that matches no name still tells its shape
   (a.durand, anna.berg, jdoe) and counts SHAPE_WEIGHT, or UNSEPARATED_WEIGHT when it has no
   separator and the shape is a guess.
2. Pattern shares for a new person at that company are the posterior predictive of a
   Dirichlet prior centred on the worldwide shares PRIORS:
       P(pattern) = (evidence[pattern] + CONCENTRATION * prior[pattern])
                    / (total evidence + CONCENTRATION)
   so a consistent company converges on its format, a company with two formats keeps both.
3. House rules (umlauts as ae or a, hyphens kept or dropped, particles, which surname, name
   order, Cyrillic spelling) are learned the same way from the spellings that matched, and
   re-weight the person's own spellings.
4. P(address) = (1 - IRREGULAR) * sum over (pattern, spelling) rendering that address of
   P(pattern) * P(spelling). IRREGULAR is the share of people whose address follows no pattern
   (nickname, previous surname, namesake suffix, legacy handle); that mass is never assigned.
5. An address of this person already published on the site gets PUBLISHED (PUBLISHED_SHORT
   for formats with initials or a single name), shared when several people could own it.

The probabilities are raw; score.calibrate maps them to observed accuracy on the gold set.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache

from leadcommons.enrich.names import (
    GIVEN_NAMES,
    NameVariant,
    country_hint,
    email_variants,
    name_key,
)

# Worldwide share of each format (an assumption from published breakdowns; the gold set
# measures how far it carries). The order is also the no-evidence ranking.
PRIORS: dict[str, float] = {
    "{first}.{last}": 0.40,
    "{first}": 0.14,
    "{f}{last}": 0.13,
    "{first}{last}": 0.075,
    "{f}.{last}": 0.065,
    "{last}": 0.04,
    "{first}_{last}": 0.03,
    "{first}{l}": 0.025,
    "{last}.{first}": 0.025,
    "{first}-{last}": 0.02,
    "{last}{f}": 0.015,
    "{last}{first}": 0.015,
    "{first}.{l}": 0.01,
}
PATTERNS: tuple[str, ...] = tuple(PRIORS)
FULL_NAME_PATTERNS = frozenset(p for p in PRIORS if "{first}" in p and "{last}" in p)
_PRIOR = {p: v / sum(PRIORS.values()) for p, v in PRIORS.items()}

CONCENTRATION = 0.4  # pseudo-addresses behind the worldwide prior
RULE_CONCENTRATION = 0.5  # same, for house rules
IRREGULAR = 0.06  # people whose address follows no pattern
PUBLISHED = 0.95  # the person's own address is on the site
PUBLISHED_SHORT = 0.8  # ... in a format with initials or one name: a namesake may own it
SHAPE_WEIGHT = 0.5  # an address with a separator that matches no name on the site
UNSEPARATED_WEIGHT = 0.25  # "jdoe", "annab": the shape alone is ambiguous
MAX_GUESSES = 20

# Shared mailboxes, not people: ignored for inference and flagged by verification.
ROLE_MAILBOXES = frozenset(
    """
abuse accounting accounts accueil achats admin administracion administration administrator
agence alerts ap ar assistance atelier billing bonjour booking bookings boutique bounce bounces
buchhaltung bureau careers ceo commande commandes commercial communication compliance compta
comptabilite contact contacto contatti cto customercare customerservice datenschutz design
dev developers devis direction dmarc donations dpo editor editorial einkauf email enquiries
enquiry events export facturation factures feedback finance gdpr hallo hello help helpdesk hi
hiring hola hostmaster hr impressum import info infos inquiries inquiry international investors
invoice invoices ir it jobs karriere kontakt kundenservice legal list logistics logistique mail
mailer-daemon marketing media membership news newsletter noc noreply no-reply notifications
office ops orders partenariats partners partnerships postmaster pr presse press privacy
procurement purchasing quote quotes reception recepcion recrutement recruiting recruitment
redaction redaktion reservation reservations rfq rgpd rh sales secretaria secretariat security
service shipping shop social stage store studio subscribe support talent team tech unsubscribe
ventas vertrieb webmaster welcome
""".split()
)
_DIGITS = re.compile(r"\d+$")
_SEPARATED = re.compile(r"^([a-z]+)([._-])([a-z]+)$")


@dataclass(frozen=True)
class PatternScore:
    pattern: str
    evidence: float  # weighted public addresses that fit the pattern
    prior: float  # worldwide share
    probability: float  # predictive share for a new person at this company


@dataclass(frozen=True)
class Guess:
    address: str
    pattern: str
    probability: float  # raw P(this is the person's address), before verification


@dataclass
class DomainFormat:
    """What a company's public addresses say about its format (no names kept)."""

    domain: str
    evidence: dict[str, float] = field(default_factory=dict)  # per pattern
    rules: dict[str, dict[str, float]] = field(default_factory=dict)  # house-rule evidence
    personal: int = 0  # personal addresses seen on the site
    matched: int = 0  # of which matched to a name on the site

    @property
    def total(self) -> float:
        return sum(self.evidence.values())

    def probability(self, pattern: str) -> float:
        n = self.evidence.get(pattern, 0.0)
        return (n + CONCENTRATION * _PRIOR[pattern]) / (self.total + CONCENTRATION)

    def rule_probability(self, rule: str, choice: str, prior: float) -> float:
        seen = self.rules.get(rule, {})
        return (seen.get(choice, 0.0) + RULE_CONCENTRATION * prior) / (
            sum(seen.values()) + RULE_CONCENTRATION
        )

    def ranked(self) -> list[PatternScore]:
        scores = [
            PatternScore(p, self.evidence.get(p, 0.0), PRIORS[p], self.probability(p))
            for p in PATTERNS
        ]
        return sorted(scores, key=lambda s: (-s.probability, -s.prior))

    def describe(self, pattern: str) -> str:
        """Plain words for the evidence behind `pattern`, for the UI."""
        n = self.evidence.get(pattern, 0.0)
        if self.personal == 0:
            return f"{pattern} is the most common format; no personal address on the site"
        if n < 0.5:
            return f"{pattern} is not the format of the {self.personal} address(es) on the site"
        share = round(n)
        if share >= self.personal or n >= self.total - 0.5:
            return f"{pattern} fits all {self.personal} personal address(es) on the site"
        return f"{pattern} fits {share} of {self.personal} personal addresses on the site"


def is_role_mailbox(local: str) -> bool:
    """info, contact, sales-fr, jobs2 ... are shared mailboxes, not a person's address."""
    local = local.lower()
    head = re.split(r"[._+-]", local)[0]
    return (
        local in ROLE_MAILBOXES
        or head in ROLE_MAILBOXES
        or local.rstrip("0123456789") in ROLE_MAILBOXES
        or local.replace("-", "").replace("_", "") in ROLE_MAILBOXES
    )


def render(pattern: str, first: str, last: str) -> str | None:
    """Local part for `pattern`, or None when the pattern needs a missing surname."""
    if not first or (not last and ("{last}" in pattern or "{l}" in pattern)):
        return None
    return pattern.format(first=first, last=last, f=first[0], l=last[:1])


def german_domain(domain: str) -> bool:
    """German-speaking ccTLD (umlauts are usually spelled ae/oe/ue there)."""
    return country_hint(domain) in {"de", "at", "ch"}


def _person_locals(known_emails: Iterable[str], domain: str) -> list[str]:
    company = domain.split(".")[0]
    out: list[str] = []
    for email in known_emails:
        local, _, host = email.strip().lower().rpartition("@")
        on_domain = host == domain or host.endswith("." + domain)
        if (
            local
            and on_domain
            and not is_role_mailbox(local)
            and local != company
            and not local.isdigit()
            and local not in out
        ):
            out.append(local)
    return out


@lru_cache(maxsize=8192)
def _variants(name: str, domain: str) -> tuple[NameVariant, ...]:
    return tuple(email_variants(name, domain))


@lru_cache(maxsize=256)
def _render_map(
    names: tuple[str, ...], domain: str
) -> dict[str, tuple[tuple[int, NameVariant, str], ...]]:
    """local part -> every (name index, spelling, pattern) that renders it."""
    out: dict[str, list[tuple[int, NameVariant, str]]] = defaultdict(list)
    for i, name in enumerate(names):
        for v in _variants(name, domain):
            for p in PATTERNS:
                local = render(p, v.first, v.last)
                if local:
                    out[local].append((i, v, p))
    return {k: tuple(v) for k, v in out.items()}


def shape_patterns(local: str) -> dict[str, float]:
    """Evidence weights per pattern from the shape of an address no site name explains."""
    base = _DIGITS.sub("", local)
    scale = 0.5 if base != local else 1.0  # "jsmith2": a namesake suffix
    m = _SEPARATED.match(base)
    if m:
        a, sep, b = m.groups()
        if sep == ".":
            if len(a) == 1 and len(b) >= 2:
                return {"{f}.{last}": SHAPE_WEIGHT * scale}
            if len(b) == 1 and len(a) >= 2:
                return {"{first}.{l}": SHAPE_WEIGHT * scale}
            if len(a) >= 2 and len(b) >= 2:
                if b in GIVEN_NAMES and a not in GIVEN_NAMES:
                    return {"{last}.{first}": SHAPE_WEIGHT * scale}
                if a in GIVEN_NAMES:
                    return {"{first}.{last}": SHAPE_WEIGHT * scale}
                return {"{first}.{last}": 0.9 * SHAPE_WEIGHT * scale}
        elif len(a) >= 2 and len(b) >= 2:
            pattern = "{first}_{last}" if sep == "_" else "{first}-{last}"
            return {pattern: SHAPE_WEIGHT * scale}
        return {}
    if not base.isalpha() or len(base) < 3:
        return {}
    w = UNSEPARATED_WEIGHT * scale
    if base in GIVEN_NAMES:
        return {"{first}": w}
    for n in range(min(len(base) - 1, 9), 2, -1):  # longest given name at the start
        if base[:n] in GIVEN_NAMES:
            rest = len(base) - n
            return {"{first}{l}": w} if rest == 1 else {"{first}{last}": w}
    for n in range(min(len(base) - 2, 9), 2, -1):
        if base[-n:] in GIVEN_NAMES:
            return {"{last}{first}": w * 0.6}
    return {"{f}{last}": w * 0.6, "{last}": w * 0.25, "{last}{f}": w * 0.15}


def _spelling_probability(v: NameVariant, fmt: DomainFormat) -> float:
    """P(this spelling) under the company's learned house rules (sum over the ways to it)."""
    total = 0.0
    for combo in v.combos or ((),):
        w = 1.0
        for rule, choice, prior in combo:
            w *= fmt.rule_probability(rule, choice, prior)
        total += w
    return total


def learn_format(
    known_emails: Iterable[str], domain: str, names: Iterable[str] = ()
) -> DomainFormat:
    """Learn a company's address format from its public addresses and the names on its site."""
    domain = domain.lower()
    name_list = tuple(dict.fromkeys(n for n in names if n and n.strip()))
    rmap = _render_map(name_list, domain)
    fmt = DomainFormat(domain)
    locals_ = _person_locals(known_emails, domain)
    fmt.personal = len(locals_)
    hits: list[tuple[tuple[tuple[int, NameVariant, str], ...], float]] = []
    shapes: dict[str, float] = defaultdict(float)
    for local in locals_:
        found = rmap.get(local)
        weight = 1.0
        if not found and _DIGITS.search(local):  # "jsmith2": a namesake of a site name
            found, weight = rmap.get(_DIGITS.sub("", local)), 0.7
        if found:
            hits.append((found, weight))
            fmt.matched += 1
        else:
            for p, w in shape_patterns(local).items():
                shapes[p] += w

    def tally(share: dict[str, float]) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        counts: dict[str, float] = defaultdict(float, shapes)
        rules: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for found, weight in hits:
            ws = [share[p] * v.weight for _, v, p in found]
            total = sum(ws) or 1.0
            for (_, v, p), w in zip(found, ws, strict=True):
                r = weight * w / total
                counts[p] += r
                for rule, choice, _ in v.choices:
                    rules[rule][choice] += r
        return counts, rules

    counts, rules = tally(_PRIOR)
    fmt.evidence = dict(counts)
    if hits:  # one EM pass: explain ambiguous addresses with this company's own shares
        counts, rules = tally({p: fmt.probability(p) for p in PATTERNS})
        fmt.evidence = dict(counts)
    fmt.rules = {k: dict(v) for k, v in rules.items()}
    return fmt


def infer_patterns(
    known_emails: Iterable[str], domain: str, names: Iterable[str] = ()
) -> list[PatternScore]:
    """All patterns ranked by their predictive probability at this company."""
    return learn_format(known_emails, domain, names).ranked()


def rank_guesses(
    full_name: str,
    domain: str,
    known_emails: Sequence[str] = (),
    names: Iterable[str] = (),
    *,
    fmt: DomainFormat | None = None,
) -> list[Guess]:
    """Candidate addresses for one person, most probable first (see module docstring)."""
    domain = domain.lower()
    variants = _variants(full_name, domain)
    if not variants:
        return []
    names = tuple(names)
    all_names = tuple(dict.fromkeys([*names, full_name]))
    if fmt is None:
        fmt = learn_format(known_emails, domain, all_names)
    weights = [_spelling_probability(v, fmt) for v in variants]
    probs: dict[str, float] = defaultdict(float)
    best: dict[str, tuple[float, str]] = {}
    for v, w in zip(variants, weights, strict=True):
        for p in PATTERNS:
            local = render(p, v.first, v.last)
            if not local:
                continue
            address = f"{local}@{domain}"
            share = (1 - IRREGULAR) * fmt.probability(p) * w
            probs[address] += share
            if share > best.get(address, (0.0, ""))[0]:
                best[address] = (share, p)
    known = {e.strip().lower() for e in known_emails}
    published = [a for a in probs if a in known]
    if published:
        rmap = _render_map(all_names, domain)
        # Namesakes listed separately on the site each count as a possible owner.
        listed = Counter(name_key(n) for n in names)
        owners = {
            a: max(
                1,
                sum(
                    max(1, listed[name_key(all_names[i])])
                    for i in {i for i, _, _ in rmap.get(a.rpartition("@")[0], ())}
                ),
            )
            for a in published
        }
        claimed = {
            a: (PUBLISHED if best[a][1] in FULL_NAME_PATTERNS else PUBLISHED_SHORT)
            / owners[a]
            / len(published)
            for a in published
        }
        rest = 1 - sum(claimed.values())
        others = sum(v for a, v in probs.items() if a not in claimed) or 1.0
        free = 1 - IRREGULAR
        for a in probs:
            probs[a] = claimed[a] if a in claimed else probs[a] / others * rest * free
    ranked = sorted(probs.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_GUESSES]
    return [Guess(a, best[a][1], p) for a, p in ranked]
