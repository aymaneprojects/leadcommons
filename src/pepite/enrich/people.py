"""Extract people (name + title) from crawled pages.

Sources, in priority order: schema.org JSON-LD (Person, Organization.employee/founder/member),
microdata Person, team-card heuristics (a name element followed by a short role text) and
legal-notice lines (mentions légales, Impressum). All are heuristics over public HTML.
"""

from __future__ import annotations

import html
import json
import logging
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from selectolax.lexbor import LexborHTMLParser, LexborNode

from pepite.connectors.people import (
    CompanyRefs,
    OfficialPerson,
    PeopleSource,
    SourceReport,
    official_people,
)
from pepite.enrich.crawl import Page
from pepite.enrich.names import PARTICLES, clean_name, fold, name_key, squeeze
from pepite.enrich.titles import ROLE_WORDS, guess_department, has_role_keyword, map_seniority
from pepite.models import Company, Person, Seniority, make_id

log = logging.getLogger(__name__)

# Tokens that show a capitalised phrase is navigation, a heading or a company, not a person.
_STOPWORDS = frozenset(
    """
our the us we you team about contact privacy policy terms conditions cookie cookies settings
read more learn see view all home news blog careers career jobs join meet profile services
service solutions products company group office offices mission vision values story history
clients customers case studies events press media resources support help faq login sign
subscribe newsletter follow linkedin twitter facebook instagram youtube github email mail
phone address map legal notice imprint impressum copyright rights reserved back top next
previous menu search close download english deutsch francais espanol italiano nederlands
welcome hello thanks free new get started book demo request quote pricing features platform
overview leadership management board directors founders people staff employees open positions
accessibility sitemap site web website united kingdom states republic world global
notre nos votre equipe qui sommes nous propos mentions legales accueil actualites contactez
plan politique confidentialite generales donnees personnelles societe entreprise recrutement
savoir plus lire suite voir tous droits reserves bureau bureaux valeurs
unser unsere uber ueber uns startseite datenschutz kontakt karriere erfahren alle rechte
vorbehalten leistungen produkte unternehmen geschichte aktuelles standort standorte
nuestro nuestra nuestros equipo quienes somos inicio contacto aviso politica privacidad
servicios empresa leer nostro squadra contatti over ons onze het
gmbh ag kg ohg ug mbh sarl sas sasu sa eurl sci ltd limited llc inc corp corporation plc llp
bv nv srl spa sl oy ab aps co
""".split()
)
_CITIES = frozenset(
    c.strip()
    for c in (
        "new york, san francisco, los angeles, las vegas, san diego, san jose, hong kong, "
        "tel aviv, cape town, buenos aires, sao paulo, rio de janeiro, mexico city, kuala lumpur, "
        "new delhi, den haag, the hague, frankfurt am main, aix en provence, saint etienne, "
        "la rochelle, le mans, le havre, palma de mallorca, santa cruz, new zealand, "
        "south africa, costa rica, puerto rico, sri lanka, saudi arabia, el salvador, abu dhabi, "
        "salt lake city, st louis, st petersburg"
    ).split(",")
)
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
# JSON-LD keys holding people, with the title to assume when the person has no jobTitle.
_PERSON_KEYS = dict.fromkeys(("employee", "employees", "member", "members"), None) | {
    "founder": "Founder",
    "founders": "Founder",
}
# Subtrees about other people (customers' reviews, people mentioned in an article...).
_SKIP_KEYS = frozenset(
    "review reviews comment comments aggregateRating mentions about citation subjectOf knows "
    "spouse parent children sibling relatedTo follows audience".split()
)
_ORG_TYPES = frozenset(
    "organization corporation localbusiness website brand ngo professionalservice "
    "onlinebusiness store".split()
)
_CARD_SELECTOR = "h2, h3, h4, h5, h6, strong, b, dt, [class*=name i]"
_TITLE_TRIM = " \t-–—,|:·•/"

# (label regex, fixed title or None to keep the label as written)
_LEGAL_LABELS: tuple[tuple[str, str | None], ...] = (
    (r"(?:directeur|directrice)\s+de\s+(?:la\s+)?publication", None),
    (r"responsable\s+de\s+(?:la\s+)?publication", None),
    (r"directeur\s+g[ée]n[ée]ral|directrice\s+g[ée]n[ée]rale", None),
    (r"(?:co-?)?g[ée]rante?s?", None),
    (r"pr[ée]sidente?", None),
    (r"p\.?d\.?g\.?", None),
    (r"repr[ée]sentante?\s+l[ée]gale?", None),
    (r"gesch[äa]ftsf[üu]hr(?:er(?:in)?|ung|ende[rn]?\s+gesellschafter(?:in)?)", None),
    (
        r"vertreten\s+durch|vertretungsberechtigte?r?(?:\s+gesch[äa]ftsf[üu]hrer(?:in)?)?",
        "Gesetzlicher Vertreter",
    ),
    (r"inhaber(?:in)?", None),
    (r"vorstand(?:svorsitzende[rn]?)?", None),
    (r"verantwortlich(?:er)?\s+f[üu]r\s+den\s+inhalt", None),
    (r"v\.\s?i\.\s?s\.\s?d\.\s?p\.?", None),
    (r"ceo|chief\s+executive\s+officer", None),
    (r"managing\s+directors?|directors?", None),
    (r"owner", None),
    (r"represented\s+by", "Legal representative"),
    (r"administrador(?:a)?(?:\s+[úu]nic[oa])?", None),
    (r"amministratore\s+(?:unico|delegato)|legale\s+rappresentante", None),
)
_LEGAL_RE = re.compile(
    r"^\s*(?:(?:le|la|der|die|the|el)\s+|l['’]\s*)?(?P<label>"
    + "|".join(f"(?:{p})" for p, _ in _LEGAL_LABELS)
    + r")\b"
    r"[^:：\n]{0,60}?\s*(?:[:：]|\s(?:est|ist|is)\s)\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_LEGAL_FIXED = [(re.compile(p, re.IGNORECASE), t) for p, t in _LEGAL_LABELS if t]
_LEGAL_CUT = re.compile(
    r"\S+@\S+|https?://|www\.|\+?\d[\d\s().-]{5,}|\b(?:t[ée]l|fax|e-?mail|telefon)\b",
    re.IGNORECASE,
)
_LEGAL_SPLIT = re.compile(r"\s*(?:[,;/&]|\b(?:und|et|and|y|e)\b)\s*", re.IGNORECASE)
_REPRESENTED = re.compile(
    r"^.*?\b(?:repr[ée]sent[ée]e?s?\s+par|vertreten\s+durch|represented\s+by)\s+", re.I
)


def is_plausible_name(name: str, brands: frozenset[str] = frozenset()) -> bool:
    """2-4 capitalised name tokens (particles aside), no digits, role words, cities or brand."""
    tokens = name.split()
    core = [t for t in tokens if t.lower() not in PARTICLES]
    if len(name) > 60 or len(tokens) > 6 or not 2 <= len(core) <= 4:
        return False
    if sum(len(t.rstrip(".")) >= 2 for t in core) < 2:
        return False
    for token in core:
        body = token[2:] if len(token) > 2 and token[0] in "dDlL" and token[1] in "'’" else token
        if body.endswith("."):
            if len(body) != 2:  # only initials may carry a dot
                return False
            body = body[:-1]
        if not body[:1].isupper() or not _WORD.fullmatch(body):
            return False
        word = fold(body).lower()
        if word in _STOPWORDS or word in ROLE_WORDS:
            return False
    return name_key(name) not in _CITIES and squeeze(name) not in brands


def _clean_title(text: str | None) -> str | None:
    if not text:
        return None
    title = " ".join(html.unescape(text).split()).strip(_TITLE_TRIM)
    return title[:100] or None


def _squash(node: LexborNode) -> str:
    return " ".join((node.text(separator=" ") or "").split())


# ---------------------------------------------------------------- JSON-LD


def _load_json(text: str) -> object:
    text = text.strip().removeprefix("<!--").removesuffix("-->").strip()
    text = text.removeprefix("//<![CDATA[").removesuffix("//]]>").strip()
    try:
        return json.loads(text, strict=False)
    except ValueError:
        return None


def _types(node: dict) -> set[str]:
    raw = node.get("@type") or []
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower() for v in values}


def _ld_text(value: object) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    return html.unescape(value).strip() if isinstance(value, str) and value.strip() else None


def _ld_name(node: dict) -> str | None:
    name = _ld_text(node.get("name"))
    if name:
        return name
    parts = [_ld_text(node.get("givenName")), _ld_text(node.get("familyName"))]
    return " ".join(p for p in parts if p) or None


def _walk_ld(node: object, default_title: str | None) -> Iterator[tuple[str, str | None, bool]]:
    """Yield (name, title, weak) for every Person; weak = title is only a default."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_ld(item, default_title)
        return
    if not isinstance(node, dict):
        return
    if "person" in _types(node) and (name := _ld_name(node)):
        title = _ld_text(node.get("jobTitle"))
        yield name, title or default_title, title is None
    for key, value in node.items():
        if key in _SKIP_KEYS:
            continue
        sub_default = _PERSON_KEYS.get(key)
        if key in _PERSON_KEYS:
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and item.strip():
                    yield html.unescape(item).strip(), sub_default, True
        if isinstance(value, dict | list):
            yield from _walk_ld(value, sub_default)


def _json_ld_objects(tree: LexborHTMLParser) -> Iterator[object]:
    for script in tree.css('script[type="application/ld+json"]'):
        data = _load_json(script.text() or "")
        if data is not None:
            yield data


def _json_ld_people(tree: LexborHTMLParser) -> Iterator[tuple[str, str | None, bool]]:
    for data in _json_ld_objects(tree):
        yield from _walk_ld(data, None)


def _brand_names(trees: Sequence[LexborHTMLParser], domain: str) -> frozenset[str]:
    """Squeezed company/brand names: domain label, og:site_name, JSON-LD organisations."""
    names = {domain.split(".")[0]}

    def collect(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                collect(item)
        elif isinstance(node, dict):
            if _types(node) & _ORG_TYPES:
                for key in ("name", "legalName", "alternateName"):
                    if text := _ld_text(node.get(key)):
                        names.add(text)
            for value in node.values():
                collect(value)

    for tree in trees:
        meta = tree.css_first('meta[property="og:site_name"]')
        if meta is not None and (content := meta.attributes.get("content")):
            names.add(content)
        for data in _json_ld_objects(tree):
            collect(data)
    out = set()
    for name in names:
        words = [w for w in re.findall(r"\w+", fold(name).lower()) if w not in _STOPWORDS]
        out.update({squeeze(name), "".join(words)})
    return frozenset(n for n in out if n)


# ---------------------------------------------------------------- microdata


def _own_prop(scope: LexborNode, prop: str) -> str | None:
    """First itemprop=`prop` value that belongs to `scope` itself, not to a nested item."""
    for node in scope.css(f'[itemprop~="{prop}"]'):
        parent = node.parent
        while parent is not None and parent.mem_id != scope.mem_id:
            if "itemscope" in parent.attributes:
                break
            parent = parent.parent
        if parent is not None and parent.mem_id == scope.mem_id:
            value = node.attributes.get("content") or _squash(node)
            if value:
                return value
    return None


def _microdata_people(tree: LexborHTMLParser) -> Iterator[tuple[str, str | None]]:
    for scope in tree.css("[itemscope][itemtype]"):
        if "schema.org/person" not in (scope.attributes.get("itemtype") or "").lower():
            continue
        name = _own_prop(scope, "name") or " ".join(
            p for p in (_own_prop(scope, "givenName"), _own_prop(scope, "familyName")) if p
        )
        if name:
            yield name, _own_prop(scope, "jobTitle")


# ---------------------------------------------------------------- team cards


def _looks_like_role(text: str) -> bool:
    return (
        len(text) <= 70
        and len(text.split()) <= 10
        and not any(c.isdigit() for c in text)
        and has_role_keyword(text)
    )


def _looks_like_name(text: str) -> bool:
    name = clean_name(text)
    return bool(name) and is_plausible_name(name)


def _starts_with_name(text: str) -> bool:
    """True for "Jane Doe - CEO" (a name, a separator, more): another person's card."""
    name = clean_name(text)
    return bool(name) and name != text and is_plausible_name(name)


def _following_role(node: LexborNode) -> str | None:
    """Role text in the next siblings of `node`, climbing at most 3 small containers."""
    current = node
    for _ in range(3):
        sibling, checked = current.next, 0
        while sibling is not None and checked < 3:
            if sibling.tag not in {"script", "style", "-comment"}:
                text = _squash(sibling)
                if text:
                    checked += 1
                    if _looks_like_role(text.strip(_TITLE_TRIM)) and not _starts_with_name(text):
                        return text
                    if _looks_like_name(text):
                        return None  # reached the next card
            sibling = sibling.next
        parent = current.parent
        if parent is None or parent.tag in {"body", "html"} or len(_squash(parent)) > 300:
            return None
        current = parent
    return None


def _card_people(tree: LexborHTMLParser) -> Iterator[tuple[str, str | None]]:
    for node in tree.css(_CARD_SELECTOR):
        text = _squash(node)
        if not text or len(text) > 120:
            continue
        parts = re.split(r"\s+[-–—|·•]\s+|,\s+", text, maxsplit=1)
        if len(parts) == 2 and _looks_like_name(parts[0]) and _looks_like_role(parts[1]):
            yield parts[0], parts[1]
        elif _looks_like_name(text) and (role := _following_role(node)):
            yield text, role


# ---------------------------------------------------------------- legal notices


def _legal_title(label: str) -> str:
    for pattern, title in _LEGAL_FIXED:
        if pattern.fullmatch(label):
            return title
    label = " ".join(label.split())
    return label[:1].upper() + label[1:]


def _legal_people(text: str) -> Iterator[tuple[str, str | None]]:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _LEGAL_RE.match(line) if len(line) <= 200 else None
        if not m:
            continue
        rest = m.group("rest").strip()
        if not rest and i + 1 < len(lines) and len(lines[i + 1]) <= 120:
            rest = lines[i + 1]
        title = _legal_title(m.group("label"))
        rest = _LEGAL_CUT.split(rest, maxsplit=1)[0]
        rest = re.sub(r"\([^)]*\)", lambda b: re.sub(r"[,;/&]", " ", b.group(0)), rest)
        for part in _LEGAL_SPLIT.split(rest)[:6]:
            role = re.search(r"\(([^)]*)\)", part)
            part_title = role.group(1) if role and has_role_keyword(role.group(1)) else title
            yield _REPRESENTED.sub("", part), part_title


# ---------------------------------------------------------------- merge


@dataclass
class _Found:
    person: Person
    weak_title: bool


def extract_people(pages: Sequence[Page], domain: str) -> list[Person]:
    """People found on `pages`, deduplicated by normalised name, best source first."""
    trees = [page.tree() for page in pages]
    brands = _brand_names(trees, domain)
    found: dict[str, _Found] = {}

    def add(raw: str, title: str | None, url: str, *, typed: bool, weak: bool = False) -> None:
        name = clean_name(raw)
        if not name or not is_plausible_name(name, frozenset() if typed else brands):
            return
        title = _clean_title(title)
        key = name_key(name)
        if key not in found:
            person = Person(id=make_id("pe", domain, name), full_name=name, company_domain=domain)
            found[key] = _Found(person, weak_title=True)
        entry = found[key]
        if title and (entry.person.title is None or (entry.weak_title and not weak)):
            entry.person.title, entry.weak_title = title, weak
        if url not in entry.person.source_urls:
            entry.person.source_urls.append(url)

    for page, tree in zip(pages, trees, strict=True):
        for name, title, weak in _json_ld_people(tree):
            add(name, title, page.url, typed=True, weak=weak)
    for page, tree in zip(pages, trees, strict=True):
        for name, title in _microdata_people(tree):
            add(name, title, page.url, typed=True)
    for page, tree in zip(pages, trees, strict=True):
        for name, title in _card_people(tree):
            add(name, title, page.url, typed=False)
    for page in pages:
        for name, title in _legal_people(page.text):
            add(name, title, page.url, typed=False)

    people = [entry.person for entry in found.values()]
    for person in people:
        person.seniority = map_seniority(person.title)
        person.department = guess_department(person.title)
    return people


# ---------------------------------------------------------------- official sources + website

# Official sources in the order their title wins when they disagree: the most specific first
# (SEC officerTitle), then the company registers, then Wikidata.
OFFICIAL_ORDER = ("edgar", "companies-house", "brreg", "wikidata")
WEBSITE = "website"
MAX_TITLE_ROLES = 3
_SENIORITY_RANK = {level: i for i, level in enumerate(Seniority)}


@dataclass
class _Merged:
    names: dict[str, str] = field(default_factory=dict)  # source -> name as that source writes it
    titles: dict[str, list[str]] = field(default_factory=dict)  # source -> distinct titles
    urls: list[str] = field(default_factory=list)  # official first, then website pages

    def add(self, source: str, name: str, title: str | None, urls: Iterable[str]) -> None:
        self.names.setdefault(source, name)
        if title and title not in (titles := self.titles.setdefault(source, [])):
            titles.append(title)
        for url in urls:
            if url not in self.urls:
                self.urls.append(url)


def _name_parts(name: str) -> tuple[str, str]:
    tokens = name_key(name).split()
    return (tokens[0], tokens[-1]) if tokens else ("", "")


def _same_person(a: str, b: str) -> bool:
    """Same surname and compatible first names: "Tim Cook" = "Timothy D. Cook" = "T. Cook"."""
    (fa, la), (fb, lb) = _name_parts(a), _name_parts(b)
    if not fa or not fb or la != lb or len(a.split()) < 2 or len(b.split()) < 2:
        return False
    return fa.startswith(fb) or fb.startswith(fa)


def _official_name(raw: str) -> str | None:
    name = clean_name(raw)
    if not name or any(c.isdigit() for c in name) or len(name.split()) < 2:
        return None
    return name


def _join_titles(titles: list[str]) -> str:
    ranked = sorted(
        titles, key=lambda t: _SENIORITY_RANK.get(map_seniority(t), len(_SENIORITY_RANK))
    )
    return ", ".join(ranked[:MAX_TITLE_ROLES])[:100].strip(" ,")


def merge_people(
    official: Sequence[OfficialPerson],
    website: Sequence[Person],
    domain: str | None,
    *,
    company_id: str | None = None,
) -> list[Person]:
    """One Person per human: duplicates merged by name, official title preferred, every source
    URL kept (official pages first). Name shown: the website's, else Wikidata's, else the
    shortest official spelling (it matches how the person is addressed and how emails are built).
    """
    entries: list[_Merged] = []

    def entry_for(name: str) -> _Merged:
        key = name_key(name)
        for e in entries:
            if any(name_key(n) == key for n in e.names.values()):
                return e
        for e in entries:
            if any(_same_person(n, name) for n in e.names.values()):
                return e
        entries.append(_Merged())
        return entries[-1]

    ordered = sorted(
        official, key=lambda p: OFFICIAL_ORDER.index(p.source) if p.source in OFFICIAL_ORDER else 99
    )
    for op in ordered:
        if name := _official_name(op.name):
            entry_for(name).add(op.source, name, _clean_title(op.title), [op.source_url])
    for wp in website:
        entry_for(wp.full_name).add(WEBSITE, wp.full_name, wp.title, wp.source_urls)

    preference = (WEBSITE, "wikidata")
    people = []
    for e in entries:
        name = min(
            e.names.items(),
            key=lambda kv: (
                preference.index(kv[0]) if kv[0] in preference else 9,
                len(kv[1].split()),
            ),
        )[1]
        title = next(
            (_join_titles(e.titles[s]) for s in (*OFFICIAL_ORDER, WEBSITE) if e.titles.get(s)),
            None,
        )
        people.append(
            Person(
                id=make_id("pe", domain or company_id or "", name),
                full_name=name,
                company_domain=domain or None,
                company_id=company_id,
                title=title,
                seniority=map_seniority(title),
                department=guess_department(title),
                source_urls=list(e.urls),
            )
        )
    return people


def gather_people(
    domain: str | None,
    website: Callable[[], list[Person]] | None,
    *,
    company: Company | None = None,
    sources: Iterable[PeopleSource] | None = None,
    is_suppressed: Callable[[str], bool] | None = None,
    known_addresses: Callable[[str], Iterable[str]] | None = None,
    report: list[SourceReport] | None = None,
) -> list[Person]:
    """Official sources that apply to `company` plus the website, merged.

    Nothing is fetched when the domain is on the suppression list; people with a suppressed
    address on file are dropped. `report` (if given) receives one SourceReport per source.
    """
    reports = report if report is not None else []
    if domain and is_suppressed and is_suppressed(domain):
        reports.append(
            SourceReport(WEBSITE, "Company website", "n/a", "skipped", 0, f"{domain} opted out.")
        )
        return []
    official: list[OfficialPerson] = []
    if company is not None:  # registers are asked about a known company only
        official, official_reports = official_people(
            CompanyRefs.from_company(company, domain), sources
        )
        reports.extend(official_reports)

    site_people: list[Person] = []
    site_error: Exception | None = None
    if website is not None:
        try:
            site_people = website()
        except Exception as exc:  # the registers may still have answered
            log.warning("website crawl of %s failed: %s", domain, exc)
            site_error = exc
            reason = f"The website could not be read ({type(exc).__name__})."
            reports.append(SourceReport(WEBSITE, "Company website", "n/a", "error", 0, reason))
        else:
            reports.append(SourceReport(WEBSITE, "Company website", "n/a", "ok", len(site_people)))

    people = merge_people(official, site_people, domain, company_id=company.id if company else None)
    if site_error is not None and not people:
        raise site_error
    if is_suppressed and known_addresses:
        people = [
            p for p in people if not any(is_suppressed(a) for a in known_addresses(p.id) if a)
        ]
    return people
