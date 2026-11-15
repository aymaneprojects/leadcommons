"""Filters of the People and Companies search screens. Owner: filters agent.

Parses the query string into a filter dict `p`, matches records in Python where the pack or the
local store cannot (people, saved rows), builds CompanyFilters for the pack, and prepares what
the side panel, the All filters window and the landing need: groups with their chips, options,
combobox values, quick filters and value suggestions. Saved personas live in store/personas.py.
"""

from __future__ import annotations

import functools
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from leadcommons.models import SIZE_RANGES, Company, EmailStatus, Person, Seniority, utcnow
from leadcommons.search import KEYWORD_FIELDS, CompanyFilters, PeopleFilters
from leadcommons.store.personas import personas_for

COUNTRIES = {
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CN": "China",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HU": "Hungary",
    "IE": "Ireland",
    "IN": "India",
    "IT": "Italy",
    "JP": "Japan",
    "LU": "Luxembourg",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SG": "Singapore",
    "US": "United States",
    "ZA": "South Africa",
    "AE": "United Arab Emirates",
    "AR": "Argentina",
    "BG": "Bulgaria",
    "CI": "Côte d'Ivoire",
    "CL": "Chile",
    "CO": "Colombia",
    "CY": "Cyprus",
    "DZ": "Algeria",
    "EE": "Estonia",
    "EG": "Egypt",
    "HK": "Hong Kong",
    "HR": "Croatia",
    "ID": "Indonesia",
    "IL": "Israel",
    "IS": "Iceland",
    "KE": "Kenya",
    "KR": "South Korea",
    "LT": "Lithuania",
    "LV": "Latvia",
    "MA": "Morocco",
    "MT": "Malta",
    "MY": "Malaysia",
    "NG": "Nigeria",
    "PE": "Peru",
    "PH": "Philippines",
    "PK": "Pakistan",
    "RS": "Serbia",
    "RU": "Russia",
    "SA": "Saudi Arabia",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "SN": "Senegal",
    "TH": "Thailand",
    "TN": "Tunisia",
    "TR": "Türkiye",
    "TW": "Taiwan",
    "UA": "Ukraine",
    "VN": "Vietnam",
}
ISIC_SECTIONS = {
    "A": "Agriculture, forestry, fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam",
    "E": "Water supply and waste",
    "F": "Construction",
    "G": "Wholesale and retail trade",
    "H": "Transport and storage",
    "I": "Accommodation and food",
    "J": "Information and communication",
    "K": "Finance and insurance",
    "L": "Real estate",
    "M": "Professional activities",
    "N": "Administrative services",
    "O": "Public administration",
    "P": "Education",
    "Q": "Health and social work",
    "R": "Arts and recreation",
    "S": "Other services",
    "T": "Households as employers",
    "U": "Extraterritorial bodies",
}


PAGE_SIZES = (25, 50, 100)  # results per page offered in Search settings
DEFAULT_PER = 25
NO_MATCH = "∅"  # impossible filter value: "everything excluded" matches nothing


SENIORITY_VALUES = frozenset(s.value for s in Seniority)


def seniority_label(value: Any) -> str:
    v = getattr(value, "value", value) or ""
    return {"c_suite": "C-suite", "vp": "VP"}.get(v) or v.replace("_", " ").title()


def status_label(value: Any) -> str:
    v = getattr(value, "value", value) or ""
    return {"catch_all": "Catch-all"}.get(v, v.title())


def qs(params: dict[str, Any], **over: Any) -> str:
    """Build a query string from list/str params, dropping empties. over=None removes a key."""
    merged = {**params, **over}
    items: list[tuple[str, str]] = []
    for k, v in merged.items():
        if v in (None, "", [], 0):
            continue
        if isinstance(v, list | tuple):
            items.extend((k, str(x)) for x in v)
        else:
            items.append((k, str(v)))
    return urlencode(items)


# ---------------------------------------------------------------- query-string parsing


def _opt_int(value: Any) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def split_values(values: Any, upper: bool = False) -> list[str]:
    """Flatten repeated and comma separated values, trimmed, de-duplicated, order kept."""
    out: list[str] = []
    for raw in values:
        for part in str(raw).split(","):
            v = part.strip().strip('"')
            v = v.upper() if upper else v
            if v and v not in out:
                out.append(v)
    return out


def list_param(g: Any, key: str, upper: bool = False) -> list[str]:
    """Values of a repeated param plus its no-JS companion field `<key>_add`."""
    return split_values([*g.getlist(key), *g.getlist(f"{key}_add")], upper)


def _add(out: list[str], value: str) -> None:
    if value and value.lower() not in {v.lower() for v in out}:
        out.append(value)


def _bounds(label: str) -> tuple[int, float]:
    if label.endswith("+"):
        return int(label[:-1]), math.inf
    lo, hi = label.split("-")
    return int(lo), int(hi)


SIZE_BOUNDS = {s: _bounds(s) for s in SIZE_RANGES}


def buckets_for_range(lo: int | None, hi: int | None) -> list[str]:
    """Size buckets overlapping a custom employee range [lo, hi] (either end may be open)."""
    if lo is None and hi is None:
        return []
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return [
        s
        for s, (a, b) in SIZE_BOUNDS.items()
        if (hi is None or a <= hi) and (lo is None or b >= lo)
    ]


def effective_sizes(p: dict[str, Any]) -> list[str]:
    """Ticked buckets united with the buckets a custom min-max range maps to."""
    chosen = set(p["size"]) | set(buckets_for_range(p["size_min"], p["size_max"]))
    return [s for s in SIZE_RANGES if s in chosen]


COUNTRY_ALIASES = {
    "USA": "US",
    "U.S.": "US",
    "U.S.A.": "US",
    "UNITED STATES OF AMERICA": "US",
    "AMERICA": "US",
    "UK": "GB",
    "U.K.": "GB",
    "ENGLAND": "GB",
    "BRITAIN": "GB",
    "GREAT BRITAIN": "GB",
    "HOLLAND": "NL",
    "DEUTSCHLAND": "DE",
    "ALLEMAGNE": "DE",
    "ESPAÑA": "ES",
    "ESPAGNE": "ES",
    "ITALIA": "IT",
    "ITALIE": "IT",
    "BELGIQUE": "BE",
    "BELGIË": "BE",
    "SUISSE": "CH",
    "SCHWEIZ": "CH",
}


def resolve_country(text: str) -> str | None:
    """ISO code for a typed country: a known code, an English name or a common alias."""
    t = " ".join(str(text).split()).strip().strip('"').upper()
    if len(t) == 2 and t in COUNTRIES:
        return t
    for code, name in COUNTRIES.items():
        if name.upper() == t:
            return code
    return COUNTRY_ALIASES.get(t)


def resolve_industry(text: str) -> str | None:
    """ISIC code for a typed industry: a section letter, a 2-digit division or a label."""
    t = str(text).strip().strip('"')
    if re.fullmatch(r"[A-Ua-u]", t) or re.fullmatch(r"\d{2}", t):
        return t.upper()
    low = t.lower()
    if not low:
        return None
    for code, label in ISIC_SECTIONS.items():
        if label.lower() == low:
            return code
    for code, label in ISIC_SECTIONS.items():
        if low in label.lower():
            return code
    return None


def parse_common(g: Any) -> dict[str, Any]:
    """Params shared by both search pages."""
    sort = g.get("sort", "relevance")
    tab = g.get("tab", "all")
    per = _opt_int(g.get("per"))
    countries = list_param(g, "country", upper=True)
    xcountries = list_param(g, "xcountry", upper=True)
    places, xplaces = split_values(g.getlist("place")), split_values(g.getlist("xplace"))
    for text in split_values(g.getlist("place_add")):
        code = resolve_country(text)
        _add(countries, code) if code else _add(places, text[:80])
    for text in split_values(g.getlist("xplace_add")):
        code = resolve_country(text)
        _add(xcountries, code) if code else _add(xplaces, text[:80])
    industries = split_values(g.getlist("industry"), upper=True)
    xindustries = split_values(g.getlist("xindustry"), upper=True)
    for key, out in (("industry_add", industries), ("xindustry_add", xindustries)):
        for text in split_values(g.getlist(key)):
            if code := resolve_industry(text):
                _add(out, code)
    kwfield = g.get("kwfield", "")
    return {
        "q": g.get("q", "").strip(),
        "country": countries,
        "xcountry": xcountries,
        "region": split_values(g.getlist("region")),
        "xregion": split_values(g.getlist("xregion")),
        "city": split_values(g.getlist("city")),
        "xcity": split_values(g.getlist("xcity")),
        "place": places,
        "xplace": xplaces,
        "industry": industries,
        "xindustry": xindustries,
        "size": [s for s in list_param(g, "size") if s in SIZE_RANGES],
        "size_min": _opt_int(g.get("size_min")),
        "size_max": _opt_int(g.get("size_max")),
        "keyword": list_param(g, "keyword"),
        "xkeyword": list_param(g, "xkeyword"),
        "kwfield": kwfield if kwfield in ("name", "industry") else "",
        "kwall": "1" if g.get("kwall") == "1" else "",
        "domain": g.get("domain", "").strip().lower(),
        "lookalike": g.get("lookalike", "").strip()[:120],
        "tech": list_param(g, "tech"),
        "job": list_param(g, "job"),
        "all": "1" if g.get("all") == "1" else "",
        "sort": sort,
        "tab": tab if tab in ("all", "new", "saved") else "all",
        "page": _opt_int(g.get("page")) or 1,
        "per": per if per in PAGE_SIZES else DEFAULT_PER,
    }


def _choice(g: Any, key: str, allowed: tuple[str, ...]) -> str:
    v = g.get(key, "")
    return v if v in allowed else ""


def parse_people_extra(g: Any) -> dict[str, Any]:
    """The People-only params: titles, levels, departments, personas, engagement."""
    valid_sen = {s.value for s in Seniority}
    valid_status = {s.value for s in EmailStatus}
    votes = g.getlist("similar")
    p: dict[str, Any] = {
        "title": split_titles([*g.getlist("title"), *g.getlist("title_add")]),
        "xtitle": split_titles([*g.getlist("xtitle"), *g.getlist("xtitle_add")]),
        "similar": not ("0" in votes and "1" not in votes),
        "tmode": _choice(g, "tmode", ("advanced",)),
        "texpr": " ".join(g.get("texpr", "").split())[:300],
        "seniority": [s for s in g.getlist("seniority") if s in valid_sen],
        "dept": [d for d in g.getlist("dept") if d in DEPT_LABELS],
        "status": [s for s in g.getlist("status") if s in valid_status],
        "plook": g.get("plook", "").strip()[:120],
        "persona": [v for v in split_values(g.getlist("persona")) if re.fullmatch(r"\w{1,32}", v)],
        "seq": [v for v in split_values(g.getlist("seq")) if re.fullmatch(r"[\w-]{1,32}", v)],
        "emailed": _choice(g, "emailed", ("yes", "no")),
        "replied": _choice(g, "replied", ("yes", "no")),
        "meeting": _choice(g, "meeting", ("yes", "no")),
        "activity": _choice(g, "activity", ("7", "30", "90", "none")),
    }
    if p["tmode"] == "advanced":
        if not p["texpr"] and (p["title"] or p["xtitle"]):
            p["texpr"] = titles_to_expr(p["title"], p["xtitle"])
        p["title"], p["xtitle"] = [], []
    else:
        p["texpr"] = ""
    return p


def url_params(p: dict[str, Any]) -> dict[str, Any]:
    """The state worth keeping in a URL: no page, no defaults, no empties."""
    out: dict[str, Any] = {}
    for k, v in p.items():
        if k == "page" or k.startswith("_"):
            continue
        if k == "similar":
            if not v:
                out["similar"] = "0"
            continue
        if (
            (k == "per" and v == DEFAULT_PER)
            or (k == "sort" and v == "relevance")
            or (k == "tab" and v == "all")
        ):
            continue
        if v in ("", [], None):
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- job titles

TITLE_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("ceo", "chief executive officer", "chief executive"),
    ("cto", "chief technology officer", "chief technical officer"),
    ("cfo", "chief financial officer"),
    ("coo", "chief operating officer"),
    ("cmo", "chief marketing officer"),
    ("cio", "chief information officer"),
    ("founder", "co-founder", "cofounder", "co founder"),
    ("vp", "vice president", "vice-president"),
    ("owner", "proprietor"),
)

# Suggested in the title boxes next to the titles already in your data.
COMMON_TITLES: tuple[str, ...] = (
    "Founder",
    "Co-founder",
    "Owner",
    "Chief Executive Officer",
    "Chief Technology Officer",
    "Chief Financial Officer",
    "Chief Operating Officer",
    "Chief Marketing Officer",
    "Managing Director",
    "General Manager",
    "President",
    "Vice President of Sales",
    "Head of Sales",
    "Sales Director",
    "Sales Manager",
    "Account Executive",
    "Business Development Manager",
    "Head of Marketing",
    "Marketing Director",
    "Marketing Manager",
    "Growth Manager",
    "Head of Engineering",
    "Engineering Manager",
    "Software Engineer",
    "Product Manager",
    "Head of Product",
    "Head of Operations",
    "Operations Manager",
    "Finance Director",
    "Head of People",
    "HR Manager",
    "Recruiter",
    "Customer Success Manager",
    "Data Analyst",
    "Designer",
    "Legal Counsel",
)


def expand_titles(titles: list[str]) -> list[str]:
    """Titles plus their well-known equivalents ("ceo" also finds "chief executive officer")."""
    out: list[str] = []
    for t in titles:
        tl = t.lower().strip()
        group = next((g for g in TITLE_SYNONYMS if tl in g), (tl,))
        out.extend(x for x in group if x and x not in out)
    return out


def title_regex(terms: list[str]) -> re.Pattern[str] | None:
    """Word-start match: "market" finds "Marketing Lead", "cto" does not find "Director"."""
    cleaned = [t.lower().strip() for t in terms if t.strip()]
    if not cleaned:
        return None
    return re.compile("|".join(r"(?<![a-z0-9])" + re.escape(t) for t in cleaned))


def _split_quoted(text: str) -> list[str]:
    parts, buf, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif ch == "," and not quoted:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def is_exact(term: str) -> bool:
    return len(term) >= 2 and term[0] == term[-1] == '"'


def unquote(term: str) -> str:
    return term[1:-1].strip() if is_exact(term) else term


def split_titles(values: Any) -> list[str]:
    """Comma separated titles, de-duplicated. A quoted title ("sales manager") keeps its quotes:
    it matches that exact title only."""
    out: list[str] = []
    for raw in values:
        for part in _split_quoted(str(raw)):
            v = " ".join(part.split())
            if is_exact(v) and v.count('"') == 2:
                inner = unquote(v)
                v = f'"{inner}"' if inner else ""
            else:
                v = v.replace('"', "").strip()
            if v:
                _add(out, v[:120])
    return out[:50]


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


@functools.lru_cache(maxsize=1024)
def _word_re(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(_norm(term)))


def title_term_hit(title_lc: str, term: str, similar: bool) -> bool:
    """Does a normalised title match one include/exclude term? Quoted terms are exact."""
    if is_exact(term):
        return title_lc == _norm(unquote(term))
    terms = expand_titles([term]) if similar else [term.lower()]
    return any(_word_re(t).search(title_lc) for t in terms)


def simple_title_match(
    title: str | None, include: list[str], exclude: list[str], similar: bool
) -> bool:
    t = _norm(title)
    if include and not (t and any(title_term_hit(t, x, similar) for x in include)):
        return False
    return not (exclude and t and any(title_term_hit(t, x, similar) for x in exclude))


def titles_to_expr(include: list[str], exclude: list[str]) -> str:
    """The Simple-mode titles written as an Advanced expression."""

    def term(t: str) -> str:
        return t if is_exact(t) or " " not in t else f'"{t}"'

    def group(ts: list[str]) -> str:
        inner = " OR ".join(term(t) for t in ts)
        return f"({inner})" if len(ts) > 1 else inner

    parts = [group(include)] if include else []
    if exclude:
        parts.append("NOT " + group(exclude))
    return " ".join(parts)


class TitleExprError(ValueError):
    """An Advanced title expression that cannot be read; the message is shown to the user."""


Node = tuple[Any, ...]
_TOKEN = re.compile(r'\s*(?:(\()|(\))|"([^"]*)"|([^\s()"]+))')
MAX_EXPR_TOKENS = 80
MAX_EXPR_DEPTH = 12


def _tokens(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    pos = 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise TitleExprError("A quotation mark is not closed.")
        pos = m.end()
        lp, rp, phrase, word = m.groups()
        if lp:
            out.append(("lp", "("))
        elif rp:
            out.append(("rp", ")"))
        elif phrase is not None:
            if phrase.strip():
                out.append(("term", phrase))
        elif word in ("AND", "OR", "NOT"):
            out.append(("op", word))
        elif word.startswith("-") and len(word) > 1:
            out += [("op", "NOT"), ("term", word[1:])]
        else:
            out.append(("term", word))
        if len(out) > MAX_EXPR_TOKENS:
            raise TitleExprError("The expression is too long. Split it into fewer terms.")
    return out


class _ExprParser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.toks, self.i, self.depth = tokens, 0, 0

    def peek(self) -> tuple[str, str] | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> tuple[str, str] | None:
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self) -> Node:
        node = self.any_of()
        if self.peek() is not None:
            raise TitleExprError("A closing bracket has no opening bracket.")
        return node

    def any_of(self) -> Node:
        items = [self.all_of()]
        while self.peek() == ("op", "OR"):
            self.take()
            items.append(self.all_of())
        return items[0] if len(items) == 1 else ("or", items)

    def all_of(self) -> Node:
        items = [self.negated()]
        while (tok := self.peek()) is not None:
            if tok == ("op", "AND"):
                self.take()
            elif not (tok[0] in ("term", "lp") or tok == ("op", "NOT")):
                break
            items.append(self.negated())  # two terms side by side mean AND
        return items[0] if len(items) == 1 else ("and", items)

    def negated(self) -> Node:
        if self.peek() == ("op", "NOT"):
            self.take()
            return ("not", self.negated())
        return self.atom()

    def atom(self) -> Node:
        tok = self.take()
        if tok is None:
            raise TitleExprError("The expression stops after an operator. Add a title.")
        kind, value = tok
        if kind == "term":
            return ("term", _norm(value))
        if kind == "lp":
            self.depth += 1
            if self.depth > MAX_EXPR_DEPTH:
                raise TitleExprError("Too many nested brackets.")
            node = self.any_of()
            if self.take() != ("rp", ")"):
                raise TitleExprError("A bracket is not closed.")
            self.depth -= 1
            return node
        if kind == "rp":
            raise TitleExprError("A closing bracket has no opening bracket.")
        raise TitleExprError(f"{value} needs a title before and after it.")


def parse_title_expr(text: str) -> Node:
    """Parse an Advanced title expression: AND, OR, NOT (capitals), brackets, "quoted phrases",
    -word as NOT word. Two terms side by side mean AND. Never evaluates code."""
    if not text.strip():
        raise TitleExprError("Type an expression, for example head AND (sales OR growth).")
    if len(text) > 300:
        raise TitleExprError("Keep the expression under 300 characters.")
    return _ExprParser(_tokens(text)).parse()


def eval_title_expr(node: Node, title: str | None) -> bool:
    t = _norm(title)
    return _eval(node, t)


def _eval(node: Node, t: str) -> bool:
    kind = node[0]
    if kind == "term":
        return bool(t) and bool(_word_re(node[1]).search(t))
    if kind == "not":
        return not _eval(node[1], t)
    if kind == "and":
        return all(_eval(n, t) for n in node[1])
    return any(_eval(n, t) for n in node[1])


# ---------------------------------------------------------------- departments

# Department of a person, inferred from the job title when the record has none. Each keyword
# matches at the start of a word (a short one, 3 letters or fewer, must be the whole word).
# A title can belong to several departments ("VP Sales & Marketing").
DEPARTMENTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("leadership", "Leadership and founders", (
        "ceo", "chief executive", "founder", "co-founder", "cofounder", "owner", "president",
        "managing director", "general manager", "chairman", "chairwoman", "managing partner",
        "directeur général", "directrice générale", "gérant", "geschäftsführer")),
    ("engineering", "Engineering", (
        "engineer", "ingénieur", "developer", "développeur", "software", "cto",
        "chief technology", "devops", "programmer", "architect", "tech lead", "technical lead",
        "sre", "qa", "quality assurance", "frontend", "front-end", "backend", "back-end",
        "full stack", "fullstack", "firmware")),
    ("it", "IT and security", (
        "it", "information technology", "cio", "chief information", "ciso", "sysadmin",
        "system administrator", "systems administrator", "network", "security",
        "infrastructure", "help desk", "helpdesk")),
    ("data", "Data and analytics", (
        "data", "analytics", "analyst", "bi", "business intelligence", "machine learning")),
    ("product", "Product", (
        "product manag", "product owner", "product lead", "product director", "head of product",
        "vp product", "vp of product", "chief product", "cpo", "product marketing")),
    ("design", "Design and UX", (
        "design", "designer", "ux", "ui", "creative director", "art director")),
    ("sales", "Sales", (
        "sales", "account executive", "account manager", "key account", "business development",
        "bdr", "sdr", "inside sales", "commercial", "chief revenue", "cro", "revenue",
        "vertrieb", "ventes")),
    ("marketing", "Marketing", (
        "marketing", "cmo", "chief marketing", "brand", "growth", "seo", "sem", "content",
        "communication", "pr", "public relations", "social media", "demand generation",
        "community manager", "product marketing")),
    ("support", "Customer success and support", (
        "customer success", "customer support", "customer service", "customer care", "support",
        "client services")),
    ("finance", "Finance and accounting", (
        "finance", "financial", "cfo", "chief financial", "accountant", "accounting",
        "controller", "controlling", "treasurer", "treasury", "audit", "tax", "payroll",
        "bookkeeper", "comptable")),
    ("hr", "People and HR", (
        "hr", "human resources", "people operations", "people partner", "head of people",
        "chief people", "talent", "recruit", "chro", "learning and development")),
    ("operations", "Operations", (
        "operations", "coo", "chief operating", "logistics", "supply chain", "procurement",
        "purchasing", "buyer", "warehouse", "fulfilment", "fulfillment", "production",
        "plant manager", "facility", "facilities")),
    ("legal", "Legal and compliance", (
        "legal", "counsel", "lawyer", "attorney", "compliance", "paralegal", "juriste",
        "avocat")),
    ("research", "Research and science", (
        "research", "researcher", "scientist", "r&d", "laboratory")),
)  # fmt: skip
DEPT_LABELS: dict[str, str] = {key: label for key, label, _ in DEPARTMENTS}


def _kw_pattern(kw: str) -> str:
    esc = re.escape(kw)
    return r"(?<![a-z0-9])" + esc + (r"(?![a-z0-9])" if len(kw) <= 3 else "")


_DEPT_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (key, re.compile("|".join(_kw_pattern(k) for k in kws))) for key, _, kws in DEPARTMENTS
)


def departments_for_title(title: str | None) -> set[str]:
    t = _norm(title)
    return {key for key, rx in _DEPT_RES if t and rx.search(t)} if t else set()


def person_departments(person: Person) -> set[str]:
    """Departments of a person: the stored department when there is one, else from the title."""
    dep = _norm(person.department)
    if dep:
        for key, label, _ in DEPARTMENTS:
            if dep in (key, label.lower()):
                return {key}
        found = departments_for_title(dep)
        if found:
            return found
    return departments_for_title(person.title)


# ---------------------------------------------------------------- companies


def company_text(c: Company, fields: str = "all") -> str:
    cols = KEYWORD_FIELDS.get(fields or "all", KEYWORD_FIELDS["all"])
    return " ".join(v for col in cols if (v := getattr(c, col, None))).lower()


def place_lists(p: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """(countries, regions, cities) to include; a free-text place may be a region or a city."""
    return p["country"], [*p["region"], *p["place"]], [*p["city"], *p["place"]]


def has_company_conditions(p: dict[str, Any], skip: str | None = None) -> bool:
    groups = {
        "location": (
            "country",
            "xcountry",
            "region",
            "xregion",
            "city",
            "xcity",
            "place",
            "xplace",
        ),
        "industry": ("industry", "xindustry", "keyword", "xkeyword"),
        "size": ("size", "size_min", "size_max"),
    }
    return any(p.get(k) for g, keys in groups.items() if g != skip for k in keys)


def company_matches(
    c: Company | None, p: dict[str, Any], skip: str | None = None, text: bool = True
) -> bool:
    """Does a company satisfy the filters in p? skip names a group to ignore (for facet counts);
    text also applies the free-text and domain filters (companies page only). Mirrors the pack
    query (packs/duck.py): places are alternatives; a record with no country or industry never
    passes a country or industry exclusion."""
    if c is None:
        return not has_company_conditions(p, skip) and not (text and (p["q"] or p["domain"]))
    if text:
        if (
            p["q"]
            and p["q"].lower()
            not in " ".join(x for x in (c.name, c.legal_name, c.domain) if x).lower()
        ):
            return False
        if p["domain"] and (c.domain or "").lower() != p["domain"]:
            return False
    if skip != "location":
        countries, regions, cities = place_lists(p)
        cc, region, city = (c.country or "").upper(), _norm(c.region), _norm(c.city)
        if (countries or regions or cities) and not (
            (cc and cc in countries)
            or (region and region in {_norm(r) for r in regions})
            or (city and city in {_norm(x) for x in cities})
        ):
            return False
        if p["xcountry"] and (not cc or cc in p["xcountry"]):
            return False
        if region and region in {_norm(r) for r in [*p["xregion"], *p["xplace"]]}:
            return False
        if city and city in {_norm(x) for x in [*p["xcity"], *p["xplace"]]}:
            return False
    if skip != "industry":
        if p["industry"] or p["xindustry"]:
            ind = (c.industry or "").upper()
            if not ind or (p["industry"] and ind not in p["industry"]) or ind in p["xindustry"]:
                return False
        hay = company_text(c, p.get("kwfield") or "all")
        hits = [k.lower() in hay for k in p["keyword"]]
        if hits and not (all(hits) if p.get("kwall") else any(hits)):
            return False
        if any(k.lower() in hay for k in p["xkeyword"]):
            return False
    if skip != "size":
        sizes = effective_sizes(p)
        if sizes and c.size_range not in sizes:
            return False
    return True


def build_company_filters(
    p: dict[str, Any], domains: list[str], skip: str | None = None
) -> CompanyFilters:
    """CompanyFilters for the pack. skip ("location" | "industry" | "size") leaves one group out
    (facet counts); domains is the domain restriction (Company filter, lookalikes)."""
    countries, regions, cities = place_lists(p)
    loc = skip != "location"
    ind = skip != "industry"
    return CompanyFilters(
        text=p["q"] or None,
        countries=list(countries) if loc else [],
        regions=list(regions) if loc else [],
        cities=list(cities) if loc else [],
        exclude_countries=list(p["xcountry"]) if loc else [],
        exclude_regions=[*p["xregion"], *p["xplace"]] if loc else [],
        exclude_cities=[*p["xcity"], *p["xplace"]] if loc else [],
        industries=list(p["industry"]) if ind else [],
        exclude_industries=list(p["xindustry"]) if ind else [],
        keywords=list(p["keyword"]) if ind else [],
        exclude_keywords=list(p["xkeyword"]) if ind else [],
        keyword_mode="all" if p.get("kwall") else "any",
        keyword_fields=p.get("kwfield") or "all",
        size_ranges=[] if skip == "size" else effective_sizes(p),
        domains=domains,
    )


# ---------------------------------------------------------------- people


@dataclass
class PeopleContext:
    """What person matching needs beyond the record: the parsed title expression, the saved
    personas that are ticked and the engagement facts (loaded only for active filters)."""

    expr: Node | None = None
    expr_error: str = ""
    personas: dict[str, Any] = field(default_factory=dict)
    persona_exprs: dict[str, Node | None] = field(default_factory=dict)
    sets: dict[str, set[str]] = field(default_factory=dict)
    last_activity: dict[str, datetime] = field(default_factory=dict)


def people_context(store: Any, p: dict[str, Any]) -> PeopleContext:
    ctx = PeopleContext()
    if p.get("tmode") == "advanced" and p.get("texpr"):
        try:
            ctx.expr = parse_title_expr(p["texpr"])
        except TitleExprError as e:
            ctx.expr_error = str(e)
    if p.get("persona"):
        repo = personas_for(store)
        for pid in p["persona"]:
            persona = repo.get(pid)
            if persona is None:
                continue
            ctx.personas[pid] = persona
            expr = None
            if persona.filters.get("tmode") == "advanced" and persona.filters.get("texpr"):
                try:
                    expr = parse_title_expr(persona.filters["texpr"])
                except TitleExprError:
                    expr = None
            ctx.persona_exprs[pid] = expr
    if any(p.get(k) for k in ("seq", "emailed", "replied", "meeting", "activity")):
        load_engagement(store, p, ctx)
    return ctx


def _title_filters_match(x: Person, f: dict[str, Any], expr: Node | None, advanced: bool) -> bool:
    if advanced:
        if expr is not None and not eval_title_expr(expr, x.title):
            return False
    elif not simple_title_match(
        x.title, f.get("title") or [], f.get("xtitle") or [], f.get("similar", True)
    ):
        return False
    levels = f.get("seniority") or []
    if levels and getattr(x.seniority, "value", x.seniority) not in levels:
        return False
    depts = f.get("dept") or []
    return not (depts and not (person_departments(x) & set(depts)))


def person_matches(x: Person, p: dict[str, Any], ctx: PeopleContext) -> bool:
    """People-only filters checked in Python: titles (Simple or Advanced), levels, departments,
    personas and engagement. Company attributes are checked by company_matches."""
    if not _title_filters_match(x, p, ctx.expr, p.get("tmode") == "advanced"):
        return False
    if ctx.personas and not any(
        _title_filters_match(
            x,
            persona.filters,
            ctx.persona_exprs.get(pid),
            persona.filters.get("tmode") == "advanced",
        )
        for pid, persona in ctx.personas.items()
    ):
        return False
    return engagement_matches(x.id, p, ctx)


def store_title_terms(p: dict[str, Any]) -> list[str]:
    """Titles the local store may pre-filter on (a superset of what person_matches keeps)."""
    if p.get("tmode") == "advanced":
        return []
    out: list[str] = []
    for t in p.get("title") or []:
        terms = [unquote(t)] if is_exact(t) else (expand_titles([t]) if p["similar"] else [t])
        out.extend(x.lower() for x in terms if x.lower() not in out)
    return out


# ---------------------------------------------------------------- engagement (People)


def load_engagement(store: Any, p: dict[str, Any], ctx: PeopleContext) -> None:
    """Person ids behind the engagement filters, read from the engage and pipeline tables."""
    try:
        from sqlalchemy import func, or_, select

        from leadcommons.store.engage import EnrollmentRow, MessageRow, engine_of
        from leadcommons.store.pipeline import ActivityRow, MeetingRow

        engine = engine_of(store)
        for table in (EnrollmentRow, MessageRow, ActivityRow, MeetingRow):
            table.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        with engine.connect() as con:
            if p.get("seq"):
                q = select(EnrollmentRow.person_id)
                wanted = [s for s in p["seq"] if s != "none"]
                if "none" in p["seq"]:
                    ctx.sets["seq_any"] = set(con.scalars(q))
                if wanted:
                    q = q.where(EnrollmentRow.sequence_id.in_(wanted))
                    ctx.sets["seq"] = set(con.scalars(q))
            if p.get("emailed"):
                q = select(MessageRow.person_id).where(
                    MessageRow.direction == "out", MessageRow.status == "sent"
                )
                ctx.sets["emailed"] = {v for v in con.scalars(q) if v}
            if p.get("replied"):
                q = select(MessageRow.person_id).where(
                    or_(
                        MessageRow.replied_at.is_not(None),
                        (MessageRow.direction == "in") & (MessageRow.status == "received"),
                    )
                )
                ctx.sets["replied"] = {v for v in con.scalars(q) if v}
            if p.get("meeting"):
                q = select(MeetingRow.person_id).where(MeetingRow.person_id.is_not(None))
                ctx.sets["meeting"] = {v for v in con.scalars(q) if v}
            if p.get("activity"):
                q = select(ActivityRow.person_id, func.max(ActivityRow.created_at)).where(
                    ActivityRow.person_id.is_not(None)
                )
                ctx.last_activity = {
                    pid: when for pid, when in con.execute(q.group_by(ActivityRow.person_id))
                }
    except Exception:  # an engage or pipeline table that is not ready never breaks the page
        for key in ("seq", "seq_any", "emailed", "replied", "meeting"):
            ctx.sets.setdefault(key, set())


def engagement_matches(pid: str, p: dict[str, Any], ctx: PeopleContext) -> bool:
    if p.get("seq"):
        wanted = [s for s in p["seq"] if s != "none"]
        in_seq = bool(wanted) and pid in ctx.sets.get("seq", set())
        in_none = "none" in p["seq"] and pid not in ctx.sets.get("seq_any", set())
        if not (in_seq or in_none):
            return False
    for key in ("emailed", "replied", "meeting"):
        if p.get(key) and (pid in ctx.sets.get(key, set())) != (p[key] == "yes"):
            return False
    if p.get("activity"):
        when = ctx.last_activity.get(pid)
        if p["activity"] == "none":
            return when is None
        if when is None:
            return False
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when >= utcnow() - timedelta(days=int(p["activity"]))
    return True


def sequence_options(store: Any) -> list[tuple[str, str]]:
    try:
        from leadcommons.store.engage import repo_for

        return [(s.id, s.name) for s in repo_for(store).sequences()]
    except Exception:
        return []


# ---------------------------------------------------------------- filter groups

GROUPS: tuple[dict[str, Any], ...] = (
    {"key": "titles", "label": "Job titles", "icon": "briefcase", "col": "person",
     "kinds": ("people",), "keys": ("title", "xtitle", "texpr", "tmode", "seniority", "dept")},
    {"key": "persona", "label": "Persona", "icon": "user-round", "col": "person",
     "kinds": ("people",), "keys": ("persona",)},
    {"key": "email", "label": "Email status", "icon": "mail", "col": "person",
     "kinds": ("people",), "keys": ("status",)},
    {"key": "name", "label": "Name", "icon": "user-round", "col": "person",
     "kinds": ("people",), "keys": ("q",), "panel": False},
    {"key": "plook", "label": "People lookalikes", "icon": "user-search", "col": "person",
     "kinds": ("people",), "keys": ("plook",)},
    {"key": "company", "label": "Company", "icon": "building-2", "col": "company",
     "kinds": ("companies", "people"), "keys": ("domain", "q")},
    {"key": "lookalikes", "label": "Company lookalikes", "icon": "building-2", "col": "company",
     "kinds": ("companies", "people"), "keys": ("lookalike",)},
    {"key": "location", "label": "Location", "icon": "map-pin", "col": "company",
     "kinds": ("companies", "people"),
     "keys": ("country", "xcountry", "region", "xregion", "city", "xcity", "place", "xplace")},
    {"key": "size", "label": "# Employees", "icon": "users", "col": "company",
     "kinds": ("companies", "people"), "keys": ("size", "size_min", "size_max")},
    {"key": "industry", "label": "Industry & keywords", "icon": "factory", "col": "company",
     "kinds": ("companies", "people"),
     "keys": ("industry", "xindustry", "keyword", "xkeyword", "kwfield", "kwall")},
    {"key": "tech", "label": "Technologies", "icon": "layers", "col": "company",
     "kinds": ("companies", "people"), "keys": ("tech",)},
    {"key": "jobs", "label": "Job postings", "icon": "briefcase", "col": "company",
     "kinds": ("companies", "people"), "keys": ("job",)},
    {"key": "seq", "label": "Sequence", "icon": "send", "col": "engage",
     "kinds": ("people",), "keys": ("seq",)},
    {"key": "emailed", "label": "Email sent", "icon": "mail", "col": "engage",
     "kinds": ("people",), "keys": ("emailed",)},
    {"key": "replied", "label": "Replied", "icon": "circle-check", "col": "engage",
     "kinds": ("people",), "keys": ("replied",)},
    {"key": "activity", "label": "Last activity", "icon": "clock", "col": "engage",
     "kinds": ("people",), "keys": ("activity",)},
    {"key": "meeting", "label": "Meeting booked", "icon": "calendar", "col": "engage",
     "kinds": ("people",), "keys": ("meeting",)},
)  # fmt: skip
PIN_DEFAULTS = {
    "companies": ["company", "lookalikes", "location", "size", "industry"],
    "people": ["titles", "plook", "company", "location", "industry"],
}
SOON_FILTERS: tuple[dict[str, str], ...] = (
    {"label": "Education", "badge": "No open data", "icon": "graduation-cap", "col": "person",
     "tip": "Education history is not in any open dataset we can use lawfully."},
    {"label": "Awards and certifications", "badge": "No open data", "icon": "graduation-cap",
     "col": "person",
     "tip": "No open dataset lists people's awards or certifications."},
    {"label": "Time zone", "badge": "No data", "icon": "clock", "col": "person",
     "tip": "People have no time zone on record. Filter by country instead."},
    {"label": "Phone", "badge": "Not collected", "icon": "phone", "col": "person",
     "tip": "LeadCommons does not collect personal phone numbers."},
    {"label": "Work URLs", "badge": "Not collected", "icon": "link", "col": "person",
     "tip": "Profile links are not collected."},
    {"label": "Market segments", "badge": "No data", "icon": "layers", "col": "company",
     "tip": "The open packs classify companies by ISIC industry only. Use Industry & keywords."},
    {"label": "SIC and NAICS", "badge": "Use ISIC", "icon": "hash", "col": "company",
     "tip": "The public registers give ISIC industry codes, not SIC or NAICS. Pick them in "
            "Industry & keywords."},
    {"label": "# Employees by department", "badge": "No open data", "icon": "users",
     "col": "company",
     "tip": "Head counts per department are not published in open data."},
    {"label": "Funding", "icon": "dollar-sign", "col": "company",
     "tip": "Needs funding data in company insights; see the Funding tab of a company."},
    {"label": "Headcount growth", "icon": "trending-up", "col": "company",
     "tip": "Needs employee trends from insights, which are not stored in a searchable way yet."},
    {"label": "Buying intent", "badge": "No free source", "icon": "zap", "col": "company",
     "tip": "Buying intent comes from paid third-party signals. LeadCommons has no free source."},
    {"label": "Revenue", "icon": "dollar-sign", "col": "company",
     "tip": "Revenue is not in the open data packs; insights show what a company publishes."},
    {"label": "Website visitors", "icon": "globe", "col": "company",
     "tip": "Needs a tracking script on your own website. Not built yet."},
    {"label": "Email opened", "badge": "Not tracked", "icon": "mail", "col": "engage",
     "tip": "LeadCommons never adds a tracking pixel to your emails, so opens are not recorded."},
    {"label": "Link clicked", "badge": "Not tracked", "icon": "link", "col": "engage",
     "tip": "Links in your emails are sent as written, so clicks are not recorded."},
    {"label": "Workflow", "icon": "workflow", "col": "engage",
     "tip": "Filtering by workflow runs is not built yet. Each workflow lists its runs on the "
            "Workflows page."},
)  # fmt: skip
ENGAGE_CHOICES = {
    "emailed": (("yes", "Emailed"), ("no", "Never emailed")),
    "replied": (("yes", "Replied"), ("no", "No reply yet")),
    "meeting": (("yes", "Has a meeting"), ("no", "No meeting yet")),
    "activity": (
        ("7", "In the last 7 days"),
        ("30", "In the last 30 days"),
        ("90", "In the last 90 days"),
        ("none", "No activity yet"),
    ),
}
DISABLED_REASONS = {
    "contact_location": (
        "People records keep no personal location. LeadCommons only knows where each "
        "person's company is based, so use Company HQ."
    ),
    "radius": (
        "Needs postal codes and map coordinates, which the open data packs do not include yet."
    ),
    "past_titles": (
        "LeadCommons keeps only the current title of each person, so past titles cannot be "
        "searched."
    ),
}


def remove_url(
    base: str, params: dict[str, Any], key: str | tuple[str, ...], value: Any = None
) -> str:
    """The current URL without one value of key (or without the key(s) when value is None)."""
    keys = (key,) if isinstance(key, str) else key
    over: dict[str, Any] = {"page": None}
    for k in keys:
        current = params.get(k)
        over[k] = [x for x in current if x != value] if isinstance(current, list) else None
    q = qs(params, **over)
    return f"{base}?{q}" if q else base


def place_label(name: str, value: str) -> str:
    return COUNTRIES.get(value, value) if name in ("country", "xcountry") else value


def place_chips(p: dict[str, Any], base: str, params: dict[str, Any], exclude: bool) -> list:
    """Chips of the location boxes: countries, regions, cities and free-text places."""
    names = ("xcountry", "xregion", "xcity", "xplace") if exclude else (
        "country", "region", "city", "place")  # fmt: skip
    kinds = {"country": "country", "region": "region", "city": "city", "place": "place"}
    out = []
    for name in names:
        for v in p.get(name) or []:
            kind = kinds[name.lstrip("x")]
            out.append(
                {
                    "name": name,
                    "value": v,
                    "text": place_label(name, v),
                    "hint": "" if kind in ("country", "place") else kind,
                    "url": remove_url(base, params, name, v),
                }
            )
    return out


def group_state(
    kind: str,
    g: dict[str, Any],
    p: dict[str, Any],
    base: str,
    params: dict[str, Any],
    labels: dict[str, str],
    personas: dict[str, str],
) -> dict[str, Any]:
    """Active-value count, the removable chips shown under the group header and the link
    that clears the group."""
    chips: list[dict[str, Any]] = []

    def add(title: str, vals: list[dict[str, str]], exclude: bool = False) -> None:
        if vals:
            chips.append({"title": title, "exclude": exclude, "vals": vals})

    def listed(title: str, key: str, exclude: bool = False, shown: Any = None) -> None:
        add(
            title,
            [
                {"text": (shown or {}).get(v, v), "url": remove_url(base, params, key, v)}
                for v in p.get(key) or []
            ],
            exclude,
        )

    def single(title: str, key: str, text: str | None = None) -> None:
        if p.get(key):
            add(title, [{"text": text or str(p[key]), "url": remove_url(base, params, key)}])

    key = g["key"]
    if key == "titles":
        listed("Titles", "title")
        listed("Not titles", "xtitle", True)
        if p.get("tmode") == "advanced" and p.get("texpr"):
            single("Expression", "texpr")
        listed("Level", "seniority", shown={s.value: seniority_label(s) for s in Seniority})
        listed("Department", "dept", shown=DEPT_LABELS)
    elif key == "persona":
        listed("Persona", "persona", shown=personas)
    elif key == "email":
        listed("Email status", "status", shown={s.value: status_label(s) for s in EmailStatus})
    elif key == "name":
        single("Search", "q")
    elif key == "company":
        single("Domain", "domain")
        if kind == "companies":
            single("Search", "q")
    elif key == "location":
        for exclude, title in ((False, "Places"), (True, "Not in")):
            vals = [
                {"text": c["text"], "url": c["url"]} for c in place_chips(p, base, params, exclude)
            ]
            add(title, vals, exclude)
    elif key == "size":
        listed("Employees", "size")
        if p["size_min"] or p["size_max"]:
            text = f"{p['size_min'] or 'any'} to {p['size_max'] or 'any'}"
            url = remove_url(base, params, ("size_min", "size_max"))
            add("Range", [{"text": text, "url": url}])
    elif key == "lookalikes":
        single("Similar to", "lookalike")
    elif key == "plook":
        single("Like", "plook")
    elif key == "tech":
        listed("Uses", "tech")
    elif key == "jobs":
        listed("Hiring for", "job")
    elif key == "industry":
        listed("Industries", "industry", shown=labels)
        listed("Not industries", "xindustry", True, shown=labels)
        joiner = "All of" if p.get("kwall") else "Keywords"
        listed(joiner, "keyword")
        listed("Not keywords", "xkeyword", True)
    elif key == "seq":
        listed("Sequence", "seq", shown={"none": "In no sequence", **personas})
    elif key in ENGAGE_CHOICES:
        single(g["label"], key, dict(ENGAGE_CHOICES[key]).get(p.get(key, "")))
    count = sum(len(c["vals"]) for c in chips)
    q = qs(params, page=None, **dict.fromkeys(g["keys"]))
    return {"count": count, "chips": chips, "clear_url": f"{base}?{q}" if q else base}


def persona_summary(filters: dict[str, Any]) -> str:
    parts = []
    if filters.get("tmode") == "advanced" and filters.get("texpr"):
        parts.append(filters["texpr"])
    if filters.get("title"):
        parts.append(", ".join(filters["title"]))
    if filters.get("xtitle"):
        parts.append("not " + ", ".join(filters["xtitle"]))
    if filters.get("seniority"):
        parts.append(", ".join(seniority_label(s) for s in filters["seniority"]))
    if filters.get("dept"):
        parts.append(", ".join(DEPT_LABELS.get(d, d) for d in filters["dept"]))
    return " · ".join(parts) or "No title filters"


def filter_ui(
    kind: str,
    p: dict[str, Any],
    store: Any,
    opts: dict[str, list[tuple[str, int]]],
    ind_labels: dict[str, str],
) -> dict[str, Any]:
    """The filter side of the search screens: groups with their chips, options for every
    control, combobox values and the landing quick filters."""
    base = "/companies" if kind == "companies" else "/people"
    params = url_params(p)
    country_opts = [(c, COUNTRIES.get(c, c), n) for c, n in opts.get("country", [])]
    industry_opts = [
        (c, ISIC_SECTIONS.get(c) or ind_labels.get(c) or f"ISIC division {c}", n)
        for c, n in opts.get("industry", [])
    ]
    labels = {c: name for c, name, _ in country_opts}
    labels.update({c: name for c, name, _ in industry_opts})
    for code in [*p["industry"], *p["xindustry"]]:
        labels.setdefault(code, ISIC_SECTIONS.get(code) or f"ISIC division {code}")
    size_map = dict(opts.get("size_range", []))
    personas: list[dict[str, Any]] = []
    sequences: list[tuple[str, str]] = []
    if kind == "people":
        personas = [
            {"id": x.id, "name": x.name, "summary": persona_summary(x.filters)}
            for x in personas_for(store).list()
        ]
        sequences = sequence_options(store)
    names = {x["id"]: x["name"] for x in personas}
    names.update(
        dict.fromkeys([i for i in p.get("persona", []) if i not in names], "Deleted persona")
    )
    seq_names = dict(sequences)
    groups = []
    for g in GROUPS:
        shown = seq_names if g["key"] == "seq" else names
        st = group_state(kind, g, p, base, params, labels, shown)
        if g["key"] == "lookalikes" and kind == "companies":
            g = {**g, "label": "Lookalikes"}
        groups.append(
            {
                **g,
                "supported": kind in g["kinds"],
                "panel": g.get("panel", True) and kind in g["kinds"],
                "pinned": g["key"] in PIN_DEFAULTS[kind],
                "state": st,
            }
        )

    def chips(key: str, shown: dict[str, str] | None = None) -> list[dict[str, str]]:
        return [
            {
                "name": key,
                "value": v,
                "text": (shown or {}).get(v, v),
                "url": remove_url(base, params, key, v),
            }
            for v in p.get(key) or []
        ]

    suggest = f"/search/suggest?kind={kind}&field="
    return {
        "groups": groups,
        "soon_filters": SOON_FILTERS,
        "active_total": sum(g["state"]["count"] for g in groups if g["supported"]),
        "clear_all_url": base,
        "countries": country_opts,
        "industries": industry_opts,
        "sizes": [(s, size_map.get(s, 0)) for s in SIZE_RANGES],
        "range_buckets": buckets_for_range(p["size_min"], p["size_max"]),
        "pin_defaults": PIN_DEFAULTS[kind],
        "seniorities": list(Seniority),
        "levels": [(s.value, seniority_label(s)) for s in Seniority],
        "statuses": list(EmailStatus),
        "departments": [(k, label) for k, label, _ in DEPARTMENTS],
        "personas": personas,
        "sequences": sequences,
        "engage_choices": ENGAGE_CHOICES,
        "reasons": DISABLED_REASONS,
        "expr_error": p.get("_texpr_error", ""),
        "suggest": suggest,
        "vals": {
            "title": chips("title"),
            "xtitle": chips("xtitle"),
            "place": place_chips(p, base, params, False),
            "xplace": place_chips(p, base, params, True),
            "industry": chips("industry", labels),
            "xindustry": chips("xindustry", labels),
            "keyword": chips("keyword"),
            "xkeyword": chips("xkeyword"),
            "tech": chips("tech"),
            "job": chips("job"),
        },
        "can_persona": kind == "people"
        and any(p.get(k) for k in ("title", "xtitle", "texpr", "seniority", "dept")),
        "tab_urls": {
            key: f"{base}?{qs(params, tab=(key if key != 'all' else None), page=None) or 'all=1'}"
            for key in ("all", "new", "saved")
        },
    }


def is_landing(p: dict[str, Any], active_total: int) -> bool:
    """No filter, no search, default tab and sort, and the full list was not asked for."""
    return not (active_total or p["q"] or p["all"] or p["tab"] != "all" or p["sort"] != "relevance")


def quick_filters(
    kind: str, opts: dict[str, list[tuple[str, int]]], top_titles: list[tuple[str, int]]
) -> list[dict[str, Any]]:
    """Rows of one-click chips for the landing: the most common values in your data."""
    base = "/companies" if kind == "companies" else "/people"

    def chip(key: str, value: str, text: str, n: int | None = None) -> dict[str, Any]:
        return {"text": text, "n": n, "url": f"{base}?{urlencode({key: value})}"}

    rows: list[dict[str, Any]] = []
    countries = [(c, n) for c, n in opts.get("country", []) if n][:3]
    if countries:
        rows.append(
            {
                "title": "Locations",
                "chips": [chip("country", c, COUNTRIES.get(c, c), n) for c, n in countries],
            }
        )
    if kind == "people":
        rows.append(
            {
                "title": "Email status",
                "chips": [
                    chip("status", s.value, status_label(s))
                    for s in (EmailStatus.VALID, EmailStatus.CATCH_ALL, EmailStatus.UNKNOWN)
                ],
            }
        )
        titles = top_titles[:3] or [
            (t, None) for t in ("Founder", "Sales Manager", "Marketing Director")
        ]
        rows.append({"title": "Job titles", "chips": [chip("title", t, t, n) for t, n in titles]})
    else:
        sizes = dict(opts.get("size_range", []))
        rows.append(
            {
                "title": "Employee count",
                "chips": [chip("size", s, s, sizes.get(s, 0)) for s in SIZE_RANGES[:3]],
            }
        )
    industries = [(c, n) for c, n in opts.get("industry", []) if n][:3]
    if industries:
        rows.append(
            {
                "title": "Industry",
                "chips": [
                    chip("industry", c, ISIC_SECTIONS.get(c) or f"ISIC division {c}", n)
                    for c, n in industries
                ],
            }
        )
    return rows


# ---------------------------------------------------------------- suggestions

_FACET_CACHE: dict[tuple[int, str, int], tuple[float, dict[str, int]]] = {}
FACET_TTL = 600.0


def facet_totals(search: Any, facet: str, limit: int = 1000) -> dict[str, int]:
    """Unfiltered counts of one facet, cached for a few minutes (pack data rarely changes)."""
    key = (id(search), facet, limit)
    hit = _FACET_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < FACET_TTL:
        return hit[1]
    try:
        counts = {
            str(k): int(n) for k, n in search.facet_counts(facet, CompanyFilters(), limit).items()
        }
    except Exception:
        counts = {}
    _FACET_CACHE[key] = (now, counts)
    return counts


def _starts(text: str, term: str) -> bool:
    low = text.lower()
    return low.startswith(term) or f" {term}" in low


def _hint(n: int | None, what: str) -> str:
    return f"{n:,} {what}" if n else ""


def suggest(
    kind: str, field_name: str, text: str, search: Any, store: Any, limit: int = 10
) -> list[dict[str, Any]]:
    """Values for a filter box: titles from your people plus common titles, places and
    industries from the company packs. Each item says which field it fills (name)."""
    term = " ".join(text.lower().split())[:80]
    exclude = field_name.startswith("x")
    base = field_name[1:] if exclude else field_name
    prefix = "x" if exclude else ""
    items: list[dict[str, Any]] = []
    if base == "title":
        counts: Counter[str] = Counter()
        try:
            res = store.search_people(PeopleFilters(titles=[term] if term else []), 1, 1000)
            for person in res.items:
                if person.title and (not term or term in person.title.lower()):
                    counts[" ".join(person.title.split())] += 1
        except Exception:
            pass
        for title, n in counts.most_common(limit):
            items.append(
                {
                    "name": field_name,
                    "value": title,
                    "label": title,
                    "hint": _hint(n, "people" if n > 1 else "person"),
                }
            )
        seen = {i["value"].lower() for i in items}
        for title in COMMON_TITLES:
            if len(items) >= limit:
                break
            if title.lower() not in seen and (not term or _starts(title, term)):
                items.append(
                    {"name": field_name, "value": title, "label": title, "hint": "Common title"}
                )
    elif base == "place":
        totals = facet_totals(search, "country", 400)
        for code, name in sorted(COUNTRIES.items(), key=lambda kv: -totals.get(kv[0], 0)):
            if not term:
                if not totals.get(code):
                    continue
            elif not (_starts(name, term) or code.lower() == term):
                continue
            items.append(
                {
                    "name": prefix + "country",
                    "value": code,
                    "label": name,
                    "hint": _hint(totals.get(code), "companies") or "Country",
                }
            )
            if len(items) >= (4 if term else limit):
                break
        if term:
            found: list[tuple[str, str, str, int]] = []
            finder = getattr(search, "suggest_places", None)
            if finder is not None:
                try:
                    found = list(finder(term, limit))
                except Exception:
                    found = []
            else:
                for kind_name in ("city", "region"):
                    for value, n in facet_totals(search, kind_name, 2000).items():
                        if _starts(value, term):
                            found.append((kind_name, value, "", n))
                found.sort(key=lambda r: -r[3])
            for place_kind, value, cc, n in found:
                if place_kind == "country":
                    continue
                where = COUNTRIES.get(cc, cc)
                what = "City" if place_kind == "city" else "Region"
                hint = f"{what}{' in ' + where if where else ''} · {n:,}"
                items.append(
                    {"name": prefix + place_kind, "value": value, "label": value, "hint": hint}
                )
                if len(items) >= limit:
                    break
    elif base == "industry":
        totals = facet_totals(search, "industry", 300)
        codes = list(ISIC_SECTIONS) + [c for c in totals if c not in ISIC_SECTIONS]
        ranked = sorted(codes, key=lambda c: -totals.get(c, 0))
        for code in ranked:
            label = ISIC_SECTIONS.get(code) or f"ISIC division {code}"
            if term and not (_starts(label, term) or code.lower() == term):
                continue
            if not term and not totals.get(code):
                continue
            items.append(
                {
                    "name": field_name,
                    "value": code,
                    "label": label,
                    "hint": _hint(totals.get(code), "companies"),
                }
            )
            if len(items) >= limit:
                break
    return items[:limit]
