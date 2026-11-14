"""Wikidata connector (CC0): companies per country via the public SPARQL endpoint."""

from __future__ import annotations

import re
from collections.abc import Iterator

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.models import Company, Provenance, make_id
from leadcommons.packs.domains import normalize_domain
from leadcommons.packs.isic import isic_section
from leadcommons.packs.normalize import size_bucket

ENDPOINT = "https://query.wikidata.org/sparql"
COMPANY_CLASSES = (
    "Q4830453",  # business
    "Q783794",  # company
    "Q6881511",  # enterprise
    "Q891723",  # public company
    "Q1589009",  # privately held company
    "Q167037",  # corporation
    "Q658255",  # subsidiary
)
LANGS = "[AUTO_LANGUAGE],en,fr,de,es,it,nl"
_QID = re.compile(r"Q\d+$")


def build_ids_query(
    country: str | None, limit: int, offset: int, website: bool | None = True
) -> str:
    """Cheap query listing one page of company item ids (no ORDER BY: it times out)."""
    classes = " ".join(f"wd:{q}" for q in COMPANY_CLASSES)
    if website is None:
        site = ""
    else:
        site = "?item wdt:P856 ?site ." if website else "FILTER NOT EXISTS { ?item wdt:P856 ?w }"
    if country:
        if not re.fullmatch(r"[A-Za-z]{2}", country):
            raise ValueError(f"bad country code: {country!r}")
        ctry = f'?c wdt:P297 "{country.upper()}" .'
    else:
        ctry = "?c wdt:P297 ?cc ."
    return (
        f"SELECT DISTINCT ?item WHERE {{ VALUES ?class {{ {classes} }} "
        f"?item wdt:P31 ?class . ?item wdt:P17 ?c . {ctry} {site} }} "
        f"LIMIT {int(limit)} OFFSET {int(offset)}"
    )


def build_details_query(qids: list[str]) -> str:
    """Details for explicit items; GROUP BY ?item keeps one row per company."""
    if not all(_QID.match(q) for q in qids):
        raise ValueError("invalid item id")
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""
SELECT ?item (SAMPLE(?itemLabel) AS ?label) (SAMPLE(?site) AS ?website)
       (SAMPLE(?cc) AS ?country) (SAMPLE(?cityLabel) AS ?city)
       (SAMPLE(?ind) AS ?industry) (SAMPLE(?indLabel) AS ?industryLabel)
       (MAX(?emp) AS ?employees) (MIN(YEAR(?inc)) AS ?founded) (SAMPLE(?lei) AS ?leiCode)
       (SAMPLE(?siren) AS ?sirenCode) (SAMPLE(?cik) AS ?cikCode)
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P17 ?c . ?c wdt:P297 ?cc . }}
  OPTIONAL {{ ?item wdt:P856 ?site . }}
  OPTIONAL {{ ?item wdt:P159 ?hq . }}
  OPTIONAL {{ ?item wdt:P452 ?ind . }}
  OPTIONAL {{ ?item wdt:P1128 ?emp . }}
  OPTIONAL {{ ?item wdt:P571 ?inc . }}
  OPTIONAL {{ ?item wdt:P1278 ?lei . }}
  OPTIONAL {{ ?item wdt:P1616 ?siren . }}
  OPTIONAL {{ ?item wdt:P5531 ?cik . }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "{LANGS}" .
    ?item rdfs:label ?itemLabel .
    ?hq rdfs:label ?cityLabel .
    ?ind rdfs:label ?indLabel .
  }}
}}
GROUP BY ?item
""".strip()


def _val(row: dict, key: str) -> str | None:
    cell = row.get(key)
    if not cell:
        return None
    value = cell.get("value")
    return value if value not in (None, "") else None


def _int(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def parse_binding(row: dict, fetched_country: str | None = None) -> Company | None:
    """Normalise one SPARQL result row into a Company (None if unusable)."""
    uri = _val(row, "item")
    label = _val(row, "label")
    if not uri or not label:
        return None
    qid = uri.rsplit("/", 1)[-1]
    if not _QID.match(qid) or _QID.match(label):  # unlabeled items label as their QID
        return None
    website = _val(row, "website")
    domain = normalize_domain(website)
    lei = (_val(row, "leiCode") or "").strip().upper() or None
    ind_label = _val(row, "industryLabel")
    ind_uri = _val(row, "industry")
    ind_qid = ind_uri.rsplit("/", 1)[-1] if ind_uri else None
    country = (fetched_country or _val(row, "country") or "").upper() or None
    founded = _int(_val(row, "founded"))
    if founded is not None and not 1000 <= founded <= 2100:
        founded = None
    company_id = make_id("co", "lei", lei) if lei else make_id("co", "wd", qid)
    registry = {"wikidata": qid}
    if lei:
        registry["lei"] = lei
    siren = "".join(ch for ch in (_val(row, "sirenCode") or "") if ch.isdigit())
    if len(siren) == 9:
        registry["siren"] = siren
    cik = (_val(row, "cikCode") or "").strip()
    if cik.isdigit() and int(cik) > 0:
        registry["cik"] = cik.zfill(10)
    return Company(
        id=company_id,
        name=label.strip(),
        domain=domain,
        country=country,
        city=_val(row, "city"),
        industry=isic_section(ind_label, ind_qid),
        industry_label=ind_label,
        size_range=size_bucket(_int(_val(row, "employees"))),
        founded_year=founded,
        registry_ids=registry,
        links={"website": website} if website else {},
        sources=[Provenance("wikidata", "CC0-1.0", uri)],
    )


class WikidataConnector:
    """Pulls companies per country from Wikidata (CC0)."""

    name = "wikidata"
    licence = "CC0-1.0"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        page_size: int = 500,
        detail_batch: int = 150,
        min_interval: float = 2.0,
        prefer_website: bool = True,
        sleep=None,
    ) -> None:
        kwargs = {"sleep": sleep} if sleep else {}
        self._http = PoliteClient(client, min_interval=min_interval, **kwargs)
        self.page_size = page_size
        self.detail_batch = detail_batch
        # True: companies with a website first, then the rest. False: websites only.
        self.prefer_website = prefer_website

    def iter_companies(
        self, *, country: str | None = None, limit: int | None = None
    ) -> Iterator[Company]:
        """Yield companies, paging with LIMIT/OFFSET until exhausted or `limit` reached."""
        seen: set[str] = set()
        emitted = 0
        for website in (True, False) if self.prefer_website else (True,):
            offset = 0
            while limit is None or emitted < limit:
                size = self.page_size
                ids_q = build_ids_query(country, size, offset, website)
                uris = [r["item"]["value"] for r in self._query(ids_q) if "item" in r]
                qids = [u.rsplit("/", 1)[-1] for u in uris]
                qids = [q for q in dict.fromkeys(qids) if _QID.match(q) and q not in seen]
                for i in range(0, len(qids), self.detail_batch):
                    for row in self._query(build_details_query(qids[i : i + self.detail_batch])):
                        company = parse_binding(row, country)
                        if company is None:
                            continue
                        seen.add(company.registry_ids["wikidata"])
                        emitted += 1
                        yield company
                        if limit is not None and emitted >= limit:
                            return
                if len(uris) < size:
                    break
                offset += size

    def _query(self, query: str) -> list[dict]:
        data = self._http.get_json(
            ENDPOINT, {"query": query, "format": "json"}, "application/sparql-results+json"
        )
        return data.get("results", {}).get("bindings", [])
