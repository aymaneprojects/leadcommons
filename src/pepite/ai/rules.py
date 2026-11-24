"""Rules-based natural-language query parser (offline, free, deterministic).

Conservative by design: it only emits filters it is sure about. Leftover meaningful words become
keywords; numbers and qualifiers it cannot express (founded years, revenue, negations) are
reported in ``unparsed`` instead of being guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pepite.ai import lexicon as lx
from pepite.enrich.titles import fold, has_role_keyword, map_seniority
from pepite.models import SIZE_RANGES, Seniority
from pepite.packs.isic import SECTION_NAMES
from pepite.search import CompanyFilters, PeopleFilters

Kind = Literal["companies", "people"]
MAX_KEYWORDS = 6

SENIORITY_LABELS: dict[Seniority, str] = {
    Seniority.OWNER: "Owner",
    Seniority.FOUNDER: "Founder",
    Seniority.C_SUITE: "C-suite",
    Seniority.PARTNER: "Partner",
    Seniority.VP: "VP",
    Seniority.HEAD: "Head of",
    Seniority.DIRECTOR: "Director",
    Seniority.MANAGER: "Manager",
    Seniority.SENIOR: "Senior",
    Seniority.ENTRY: "Entry level",
    Seniority.INTERN: "Intern",
}


@dataclass
class ParsedQuery:
    company: CompanyFilters = field(default_factory=CompanyFilters)
    people: PeopleFilters = field(default_factory=PeopleFilters)
    explanation: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    engine: Literal["rules", "llm"] = "rules"


# ------------------------------------------------------------------------------- sizes

_NUM = r"\d{1,3}(?:[   ,.]\d{3})+|\d+(?:[.,]\d+)?\s?k\b|\d+"
_UNIT = (
    r"(?:employ[ée]e?s?|salari[ée]e?s?|staff|people|persons?|personnes|collaborat(?:eu|ri)rs?"
    r"|collaboratrices?|mitarbeiter(?:n|innen)?|empleados|trabajadores|headcount|ppl|fte)"
)
_OPT_UNIT = rf"(?:\s*(?P<u>{_UNIT}))?"
_RANGE = re.compile(
    rf"(?<![\w.])(?:(?:between|entre|from|de|von|zwischen)\s+)?(?P<a>{_NUM})"
    rf"(?:\s*[-–—]\s*|\s+(?:to|a|à|et|and|bis|und|y)\s+)(?P<b>{_NUM}){_OPT_UNIT}(?!\w)",
    re.I,
)
_OVER = re.compile(
    rf"(?<!\w)(?:plus de|plus que|more than|over|above|greater than|at least|au moins|mas de"
    rf"|mehr als|ueber|über|>=|>)\s*(?P<n>{_NUM}){_OPT_UNIT}(?!\w)",
    re.I,
)
_UNDER = re.compile(
    rf"(?<!\w)(?:moins de|moins que|less than|under|below|fewer than|up to|jusqu'[àa]"
    rf"|at most|maximum|weniger als|unter|menos de|<=|<)\s*(?P<n>{_NUM}){_OPT_UNIT}(?!\w)",
    re.I,
)
_TRAIL = re.compile(rf"(?<![\w.])(?P<n>{_NUM})\s*\+{_OPT_UNIT}(?!\w)", re.I)
_EXACT = re.compile(rf"(?<![\w.])(?P<n>{_NUM})\s*(?P<u>{_UNIT})(?!\w)", re.I)


def _parse_num(raw: str) -> int:
    s = raw.strip().lower()
    if s.endswith("k"):
        return round(float(s[:-1].strip().replace(",", ".")) * 1000)
    return int("".join(ch for ch in s if ch.isdigit()))


def _buckets() -> list[tuple[str, int, float]]:
    out: list[tuple[str, int, float]] = []
    for label in SIZE_RANGES:
        if label.endswith("+"):
            out.append((label, int(label[:-1]), float("inf")))
        else:
            lo, hi = label.split("-")
            out.append((label, int(lo), int(hi)))
    return out


_BUCKETS = _buckets()


def _is_year(n: int) -> bool:
    return 1900 <= n <= 2100


def sizes_for_range(a: int, b: int) -> list[str]:
    """Buckets inside [a, b]; if none fits entirely, the buckets that overlap it."""
    inside = [lab for lab, lo, hi in _BUCKETS if lo >= a - 1 and hi <= b]
    return inside or [lab for lab, lo, hi in _BUCKETS if lo <= b and hi >= a]


def sizes_over(n: int) -> list[str]:
    return [lab for lab, _lo, hi in _BUCKETS if hi > n]


def sizes_under(n: int) -> list[str]:
    inside = [lab for lab, _lo, hi in _BUCKETS if hi <= n]
    return inside or [lab for lab, lo, _hi in _BUCKETS if lo <= n][:1]


def sizes_exact(n: int) -> list[str]:
    return [lab for lab, lo, hi in _BUCKETS if lo <= n <= hi]


def _extract_sizes(text: str) -> tuple[str, list[str], list[str]]:
    """Pull numeric size expressions out of `text`. Returns (rest, sizes, explanation)."""
    found: list[str] = []
    notes: list[str] = []

    def take(m: re.Match[str], sizes: list[str]) -> str:
        if not sizes:
            return m.group(0)
        found.extend(sizes)
        notes.append(f'Size: {", ".join(sizes)} employees (from "{m.group(0).strip()}")')
        return " "

    def has_unit(m: re.Match[str]) -> bool:
        return bool(m.groupdict().get("u"))

    def on_range(m: re.Match[str]) -> str:
        a, b = _parse_num(m.group("a")), _parse_num(m.group("b"))
        if b <= a or a < 1 or (not has_unit(m) and _is_year(a) and _is_year(b)):
            return m.group(0)
        return take(m, sizes_for_range(a, b))

    def single(fn):  # type: ignore[no-untyped-def]
        def inner(m: re.Match[str]) -> str:
            n = _parse_num(m.group("n"))
            if not has_unit(m) and (n < 10 or _is_year(n)):
                return m.group(0)
            return take(m, fn(n))

        return inner

    text = _RANGE.sub(on_range, text)
    text = _OVER.sub(single(sizes_over), text)
    text = _UNDER.sub(single(sizes_under), text)
    text = _TRAIL.sub(single(sizes_over), text)
    text = _EXACT.sub(lambda m: take(m, sizes_exact(_parse_num(m.group("n")))), text)
    ordered = [s for s in SIZE_RANGES if s in found]
    return text, ordered, notes


# ---------------------------------------------------------------------------- tokens

_TOKEN = re.compile(r"[^\W_]+|[,/;&]")


@dataclass
class Tok:
    orig: str
    fold: str
    used: bool = False


def _tokenize(text: str) -> list[Tok]:
    return [Tok(m.group(0), fold(m.group(0))) for m in _TOKEN.finditer(text)]


def _phrase(text: str) -> tuple[str, ...]:
    return tuple(fold(m.group(0)) for m in _TOKEN.finditer(text))


def _build_index() -> dict[str, list[tuple[tuple[str, ...], str, str]]]:
    index: dict[str, list[tuple[tuple[str, ...], str, str]]] = {}
    sources: tuple[tuple[str, dict[str, object]], ...] = (
        ("size", lx.SIZE_WORDS),
        ("country", lx.COUNTRY_WORDS),
        ("city", lx.CITY_WORDS),
        ("industry", lx.INDUSTRY_WORDS),
    )
    for kind, table in sources:
        for words, value in table.items():
            ph = _phrase(words)
            if ph:
                joined = value if isinstance(value, str) else ",".join(value)  # type: ignore[arg-type]
                index.setdefault(ph[0], []).append((ph, kind, joined))
    for entries in index.values():
        entries.sort(key=lambda e: -len(e[0]))
    return index


_INDEX = _build_index()
_DUAL_INDUSTRY_WORDS = frozenset(
    "software tech technology technologie cloud cybersecurity cybersecurite media".split()
)
_SOFTWARE_AFTER = {"software", "saas", "solutions", "platform", "platforms", "logiciel"}


def _is_role_head(fold_tok: str) -> bool:
    return bool(fold_tok) and _role_form(fold_tok, lambda x: _is_head(x, "people")) is not None


@dataclass
class _Found:
    countries: list[str] = field(default_factory=list)
    cities: dict[str, str] = field(default_factory=dict)
    sizes: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _add(lst: list[str], value: str) -> bool:
    if value in lst:
        return False
    lst.append(value)
    return True


def _lexical_pass(toks: list[Tok], found: _Found, role_kind: Kind) -> None:
    i = 0
    while i < len(toks):
        t = toks[i]
        if t.used or t.fold in (",", "/", ";", "&"):
            i += 1
            continue
        if t.orig in lx.UPPER_CODES:
            code = lx.UPPER_CODES[t.orig]
            t.used = True
            if _add(found.countries, code):
                found.notes.append(f"Country: {lx.COUNTRY_NAMES.get(code, code)}")
            i += 1
            continue
        matched = False
        for ph, kind, value in _INDEX.get(t.fold, []):
            n = len(ph)
            window = toks[i : i + n]
            if len(window) < n or any(
                w.used or w.fold != p for w, p in zip(window, ph, strict=True)
            ):
                continue
            nxt = toks[i + n].fold if i + n < len(toks) else ""
            if kind == "size" and ph == ("enterprise",) and nxt in _SOFTWARE_AFTER:
                continue
            if (
                kind == "industry"
                and role_kind == "people"
                and ph[-1] in _DUAL_INDUSTRY_WORDS
                and _is_role_head(nxt)
            ):
                continue  # "software engineers": the word belongs to the job title
            for w in window:
                w.used = True
            said = " ".join(w.orig for w in window)
            if kind == "country":
                if _add(found.countries, value):
                    found.notes.append(f"Country: {lx.COUNTRY_NAMES.get(value, value)}")
            elif kind == "city":
                found.cities[said] = value
                if _add(found.countries, value):
                    name = lx.COUNTRY_NAMES.get(value, value)
                    found.notes.append(f"Country: {name} (from city {said})")
            elif kind == "size":
                new = [s for s in value.split(",") if s not in found.sizes]
                found.sizes.extend(new)
                if new:
                    found.notes.append(f'Size: {", ".join(value.split(","))} employees ("{said}")')
            else:
                if _add(found.industries, value):
                    found.notes.append(f'Industry: {SECTION_NAMES[value]} ({value}, "{said}")')
            i += n
            matched = True
            break
        if not matched and t.orig in lx.UPPER_INDUSTRY_WORDS:
            sec = lx.UPPER_INDUSTRY_WORDS[t.orig]
            t.used = True
            if _add(found.industries, sec):
                found.notes.append(f"Industry: {SECTION_NAMES[sec]} ({sec})")
        if not matched:
            i += 1


# ------------------------------------------------------------------------------ roles

_WEAK_HEADS = frozenset(
    "staff principal lead associate member board charge chargee expert commercial personal "
    "partner partners senior junior jr sr trainee graduate".split()
)
_EXTRA_HEADS = frozenset("drh daf dsi".split())
_ADJUNCT_EXTRA = frozenset("general generale executive".split())
_CONNECTORS = frozenset("of de du des d la le les the for and et & pour y und der die von".split())
_SPLITTERS = frozenset("and et und y &".split())
_ACRONYMS = frozenset(
    "ceo cto cfo coo cmo cio cso cpo cro cdo cco chro vp svp evp avp drh daf dsi dg pdg".split()
)
_ALIASES: dict[str, list[str]] = {
    "drh": ["DRH", "directeur des ressources humaines"],
    "daf": ["DAF", "directeur administratif et financier"],
}
_COMPANY_KIND_SENIORITIES = frozenset(
    {
        Seniority.OWNER,
        Seniority.FOUNDER,
        Seniority.VP,
        Seniority.C_SUITE,
        Seniority.HEAD,
        Seniority.DIRECTOR,
    }
)


def _role_form(fold_tok: str, pred) -> str | None:  # type: ignore[no-untyped-def]
    """The singular form of a token when it satisfies `pred`, else None."""
    if pred(fold_tok):
        return fold_tok
    if fold_tok.endswith("s") and pred(fold_tok[:-1]):
        return fold_tok[:-1]
    return None


def _is_head(tok: str, kind: Kind) -> bool:
    if tok in _WEAK_HEADS:
        return False
    if tok in _EXTRA_HEADS:
        return True
    sen = map_seniority(tok)
    if sen is None:
        return False
    return kind == "people" or sen in _COMPANY_KIND_SENIORITIES


def _is_adjunct(tok: str) -> bool:
    return tok in _ADJUNCT_EXTRA or tok in _EXTRA_HEADS or has_role_keyword(tok)


def _display(tok: Tok, singular: str) -> str:
    base = tok.orig.lower()
    if tok.fold != singular and base.endswith("s"):
        base = base[:-1]
    if singular in _ACRONYMS:
        return base.upper()
    return base


def _role_pass(toks: list[Tok], kind: Kind) -> list[list[str]]:
    """Find job-title spans among unused tokens. Returns lists of display words per title."""
    forms: list[str | None] = []
    heads: list[bool] = []
    for t in toks:
        if t.used or t.fold in (",", "/", ";"):
            forms.append(None)
            heads.append(False)
            continue
        head = _role_form(t.fold, lambda x: _is_head(x, kind))
        adj = head or _role_form(t.fold, _is_adjunct)
        conn = t.fold if t.fold in _CONNECTORS else None
        forms.append(head or adj or conn)
        heads.append(head is not None)

    spans: list[list[int]] = []
    i = 0
    while i < len(toks):
        if forms[i] is None:
            i += 1
            continue
        j = i
        while j < len(toks) and forms[j] is not None:
            j += 1
        run = list(range(i, j))
        while run and forms[run[0]] in _CONNECTORS and not heads[run[0]]:
            run.pop(0)
        while run and forms[run[-1]] in _CONNECTORS and not heads[run[-1]]:
            run.pop()
        if any(heads[k] for k in run):
            spans.append(run)
        i = j

    titles: list[list[str]] = []
    for run in spans:
        current: list[int] = []
        chunks: list[list[int]] = []
        for k in run:
            f = forms[k]
            if f in _SPLITTERS and not heads[k]:
                nxt = k + 1
                if nxt <= run[-1] and heads[nxt]:
                    chunks.append(current)
                    current = []
                    continue
            if (
                current
                and heads[k]
                and heads[current[-1]]
                and len(toks[k].fold) <= 4
                and len(toks[current[-1]].fold) <= 4
            ):
                chunks.append(current)
                current = []
            current.append(k)
        chunks.append(current)
        for chunk in chunks:
            if not any(heads[k] for k in chunk):
                continue
            for k in chunk:
                toks[k].used = True
            titles.append([_display(toks[k], forms[k] or toks[k].fold) for k in chunk])
    return titles


# --------------------------------------------------------------------------- leftovers


def _leftovers(toks: list[Tok], keywords: list[str], unparsed: list[str]) -> None:
    skip_next = 0
    for idx, t in enumerate(toks):
        if t.used or t.fold in (",", "/", ";", "&"):
            continue
        if skip_next:
            skip_next -= 1
            unparsed.append(t.orig)
            continue
        if t.fold in lx.UNSUPPORTED_MARKERS:
            unparsed.append(t.orig)
            skip_next = 1 if idx + 1 < len(toks) and toks[idx + 1].fold.isdigit() else 0
            continue
        if t.fold in lx.STOP_WORDS or len(t.fold) < 2:
            continue
        if any(ch.isdigit() for ch in t.fold):
            unparsed.append(t.orig)
            continue
        word = t.orig.lower()
        if word not in keywords:
            keywords.append(word)


def _negation_pass(toks: list[Tok], unparsed: list[str]) -> None:
    """ "not in Paris", "sans banques": negations are not expressible; report, never guess."""
    i = 0
    while i < len(toks):
        if toks[i].used or toks[i].fold not in lx.NEGATIONS:
            i += 1
            continue
        j = i
        words = [toks[j].orig]
        toks[j].used = True
        j += 1
        while j < len(toks) and toks[j].fold in lx.STOP_WORDS | _CONNECTORS:
            toks[j].used = True
            words.append(toks[j].orig)
            j += 1
        if j < len(toks) and toks[j].fold not in (",", "/", ";"):
            toks[j].used = True
            words.append(toks[j].orig)
            j += 1
        unparsed.append(" ".join(words))
        i = j


def parse_rules(text: str, kind: Kind = "companies") -> ParsedQuery:
    rest, sizes, size_notes = _extract_sizes(text)
    toks = _tokenize(rest)
    found = _Found(sizes=list(sizes))
    found.notes.extend(size_notes)
    unparsed: list[str] = []
    _negation_pass(toks, unparsed)
    _lexical_pass(toks, found, kind)

    titles: list[str] = []
    seniorities: list[Seniority] = []
    role_notes: list[str] = []
    for words in _role_pass(toks, kind):
        phrase = " ".join(words)
        variants = _ALIASES.get(phrase.lower(), [phrase])
        for title in variants:
            if title not in titles:
                titles.append(title)
            sen = map_seniority(title)
            if sen is not None and sen not in seniorities:
                seniorities.append(sen)
        role_notes.append(f"Title: {', '.join(variants)}")
    for sen in seniorities:
        role_notes.append(f"Seniority: {SENIORITY_LABELS[sen]} ({sen.value})")

    keywords: list[str] = []
    _leftovers(toks, keywords, unparsed)
    if len(keywords) > MAX_KEYWORDS:
        unparsed.extend(keywords[MAX_KEYWORDS:])
        keywords = keywords[:MAX_KEYWORDS]

    company = CompanyFilters(
        countries=found.countries,
        industries=found.industries,
        size_ranges=[s for s in SIZE_RANGES if s in found.sizes],
        keywords=keywords,
    )
    people = PeopleFilters(
        titles=titles,
        seniorities=seniorities,
        company=CompanyFilters(
            countries=list(found.countries),
            industries=list(found.industries),
            size_ranges=list(company.size_ranges),
            keywords=list(keywords),
        ),
    )
    explanation = [*found.notes, *role_notes, *(f"Keyword: {k}" for k in keywords)]
    return ParsedQuery(
        company=company, people=people, explanation=explanation, unparsed=unparsed, engine="rules"
    )
