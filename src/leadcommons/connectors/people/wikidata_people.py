"""Wikidata (CC0): current CEO, founders, chair, directors and board members of one company.

The company item is found by Wikidata id, else by LEI (P1278), SIREN (P1616), UK company number
(P2622), CIK (P5531), Norwegian organisation number (P2333) or website (P856). Only living
people with a statement that has not ended are returned.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.connectors.people import CompanyRefs, OfficialPerson, tidy_case

ENDPOINT = "https://query.wikidata.org/sparql"
ENTITY_PAGE = "https://www.wikidata.org/wiki/{qid}"

# property -> role title (English; the register-style wording titles.py understands)
ROLES: dict[str, str] = {
    "P169": "Chief executive officer",
    "P112": "Founder",
    "P488": "Chairperson",
    "P1037": "Director/manager",
    "P3320": "Board member",
}
_QID = re.compile(r"Q\d+$")
_SAFE = re.compile(r"[A-Za-z0-9 .\-]{3,30}$")


def _lit(value: str) -> str:
    """SPARQL string literal; only a conservative alphabet is ever allowed in."""
    if not _SAFE.match(value):
        raise ValueError(f"unsafe identifier: {value!r}")
    return f'"{value}"'


def website_variants(domain: str) -> list[str]:
    domain = domain.strip().lower().removeprefix("www.")
    out = []
    for scheme in ("https", "http"):
        for host in (domain, f"www.{domain}"):
            out += [f"{scheme}://{host}", f"{scheme}://{host}/"]
    return out


def build_resolve_query(refs: CompanyRefs) -> str:
    """Items carrying one of the company's identifiers; ?kind says which one matched."""
    parts: list[str] = []
    if refs.lei:
        parts.append(f'?item wdt:P1278 {_lit(refs.lei.upper())} . BIND("lei" AS ?kind)')
    if refs.siren:
        parts.append(f"?item wdt:P1616 {_lit(refs.siren)} . " + 'BIND("siren" AS ?kind)')
    if refs.gb_number:
        n = refs.gb_number.strip().upper()
        values = " ".join(_lit(v) for v in dict.fromkeys([n, n.lstrip("0") or n, n.zfill(8)]))
        parts.append(f'VALUES ?gb {{ {values} }} ?item wdt:P2622 ?gb . BIND("gb" AS ?kind)')
    if refs.cik:
        digits = refs.cik.strip().lstrip("0") or "0"
        values = " ".join(_lit(v) for v in dict.fromkeys([digits, digits.zfill(10)]))
        parts.append(f'VALUES ?ck {{ {values} }} ?item wdt:P5531 ?ck . BIND("cik" AS ?kind)')
    if refs.no_orgnr:
        n = re.sub(r"\D", "", refs.no_orgnr)
        spaced = f"{n[:3]} {n[3:6]} {n[6:]}"
        values = " ".join(_lit(v) for v in dict.fromkeys([n, spaced]))
        parts.append(f'VALUES ?no {{ {values} }} ?item wdt:P2333 ?no . BIND("no" AS ?kind)')
    if refs.domain:
        sites = " ".join(
            f"<{u}>" for u in website_variants(refs.domain) if re.fullmatch(r"[\w:/.\-]+", u)
        )
        if sites:
            parts.append(
                f"VALUES ?site {{ {sites} }} ?item wdt:P856 ?site . "
                "?item wdt:P31/wdt:P279* wd:Q43229 . "  # organisations only, not products
                'BIND("website" AS ?kind)'
            )
    if not parts:
        raise ValueError("no identifier to resolve")
    body = " UNION ".join(f"{{ {p} }}" for p in parts)
    return f"SELECT DISTINCT ?item ?kind WHERE {{ {body} }} LIMIT 20"


def build_people_query(qid: str) -> str:
    """Current people for one company item, with role property and start/end dates."""
    if not _QID.match(qid):
        raise ValueError("invalid item id")
    props = " ".join(f"p:{p}" for p in ROLES)
    return f"""
SELECT ?prop ?person ?personLabel ?start ?end ?rank WHERE {{
  VALUES ?prop {{ {props} }}
  wd:{qid} ?prop ?st .
  ?st ?ps ?person .
  FILTER(STRSTARTS(STR(?ps), "http://www.wikidata.org/prop/statement/P"))
  ?st wikibase:rank ?rank .
  FILTER(?rank != wikibase:DeprecatedRank)
  ?person wdt:P31 wd:Q5 .
  FILTER NOT EXISTS {{ ?person wdt:P570 ?died }}
  OPTIONAL {{ ?st pq:P580 ?start }}
  OPTIONAL {{ ?st pq:P582 ?end }}
  FILTER(!BOUND(?end) || ?end > NOW())
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en,fr,de,es,it,nl,no,[AUTO_LANGUAGE]" .
  }}
}}
""".strip()


def _val(row: dict, key: str) -> str | None:
    cell = row.get(key)
    value = cell.get("value") if cell else None
    return value or None


def _day(text: str | None) -> date | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date() if text else None
    except ValueError:
        return None


def parse_people(rows: list[dict], qid: str, today: date | None = None) -> list[OfficialPerson]:
    """Rows of build_people_query -> people, current roles only (also re-checked here)."""
    today = today or datetime.now(UTC).date()
    out: list[OfficialPerson] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        prop = (_val(row, "prop") or "").rsplit("/", 1)[-1]
        name = _val(row, "personLabel")
        person_uri = _val(row, "person") or ""
        if prop not in ROLES or not name or _QID.match(name) or len(name.split()) < 2:
            continue
        end = _day(_val(row, "end"))
        if end is not None and end <= today:
            continue
        key = (person_uri, prop)
        if key in seen:
            continue
        seen.add(key)
        start = _day(_val(row, "start"))
        out.append(
            OfficialPerson(
                name=tidy_case(name),
                title=ROLES[prop],
                source="wikidata",
                source_url=ENTITY_PAGE.format(qid=qid) + f"#{prop}",
                since=start.isoformat() if start else None,
            )
        )
    return out


class WikidataPeople:
    name = "wikidata"
    label = "Wikidata"
    licence = "CC0-1.0"

    def __init__(self, client: httpx.Client | None = None, *, min_interval: float = 1.0) -> None:
        self._http = PoliteClient(client, min_interval=min_interval, timeout=60.0)

    def applies(self, refs: CompanyRefs) -> bool:
        return any(
            (
                refs.wikidata_id,
                refs.lei,
                refs.siren,
                refs.gb_number,
                refs.cik,
                refs.no_orgnr,
                refs.domain,
            )
        )

    def _query(self, query: str) -> list[dict]:
        data = self._http.get_json(
            ENDPOINT, {"query": query, "format": "json"}, "application/sparql-results+json"
        )
        return data.get("results", {}).get("bindings", [])

    def resolve(self, refs: CompanyRefs) -> str | None:
        """Company item id: the given one, else the item matching the strongest identifier."""
        if refs.wikidata_id and _QID.match(refs.wikidata_id):
            return refs.wikidata_id
        rows = self._query(build_resolve_query(refs))
        by_kind: dict[str, list[str]] = {}
        for row in rows:
            uri, kind = _val(row, "item"), _val(row, "kind")
            if uri and kind and _QID.match(uri.rsplit("/", 1)[-1]):
                by_kind.setdefault(kind, []).append(uri.rsplit("/", 1)[-1])
        for kind in ("lei", "siren", "gb", "cik", "no", "website"):
            qids = list(dict.fromkeys(by_kind.get(kind, [])))
            if len(qids) == 1:  # several items for one id/website: ambiguous, try the next id
                return qids[0]
        return None

    def fetch(self, refs: CompanyRefs) -> list[OfficialPerson]:
        qid = self.resolve(refs)
        if qid is None:
            return []
        return parse_people(self._query(build_people_query(qid)), qid)
