"""Wikidata organisations bridge (CC0): official websites and register numbers, in bulk.

Registers rarely carry a website; Wikidata carries both a website (P856) and the register numbers
of many organisations. Each row of this source holds the identifiers an item has, so the merge
can attach the website (and Wikidata id, inception, head count, industry, HQ city) to the
register rows it joins through them:

| Property | Register | Pack columns |
| --- | --- | --- |
| P1616 SIREN | FR-SIREN | registry + company_number, siren |
| P2622 Companies House | GB-CH | registry + company_number |
| P2333 Norwegian organisation number | NO-BRREG | registry + company_number |
| P3548 ABN (or P3549 ACN turned into the ABN) | AU-ABN | registry + company_number |
| P13850 Corporation Number in Canada | CA-CORP | registry + company_number |
| P5531 SEC CIK | US-SEC | registry + company_number, cik |
| P1278 LEI | (GLEIF links it to its register) | lei |

`registry` / `company_number` hold the number of the item's own country register when it has one
(a French item with a SIREN and a CIK -> FR-SIREN), else the first national number it has (a Swiss
company filing with the SEC -> US-SEC). The country is P17 (the one implied by a national number
when an item has several, or none); items without a country are dropped.

**What is fetched.** Phase 1 lists item ids per *selector* (cheap `SELECT DISTINCT ?item`):
every item with an LEI, a Companies House, CIK, Norwegian, ABN, ACN or Canadian corporation
number; items with a SIREN *and* a website (Wikidata has ~460k SIREN items, ~450k of them a bulk
import of French organisations without a website, which add nothing over SIRENE itself); and
organisations with a website that are an instance of a company class (business, company,
enterprise, public / private company, corporation, subsidiary) or have an industry (P452).
Phase 2 fetches the details of those ids, 400 per query (`VALUES` + `GROUP BY`). Dropped: items
that are humans, dissolved (P576), unlabelled, or without a country.

**Size (measured on query.wikidata.org, November 2026).** Selectors: LEI 53,265 items (21,653
with a website), SIREN with a website 13,598, Companies House 8,358 (4,797), CIK 3,682 (3,445),
Norway 11,570 (8,030), ABN 3,157 (2,889), ACN 534 (401), Canada 79 (54); company classes with a
website ~170,000, industry with a website ~134,000. Unique items: ~300,000, ~260,000 rows after
the drops, ~15-20 MB of Parquet over ~200 country files.

**Endpoint limits, and how they are kept.** query.wikidata.org stops a query after 60 s, allows
one minute of query time per minute and per client, and answers 429 (with Retry-After) beyond
that; a query that times out while streaming can come back as HTTP 200 with a truncated JSON
body. So: queries run one at a time, at least 1 s apart, with a User-Agent naming the project
(and the declared contact); 429 waits for Retry-After; network errors are retried twice with
backoff, a timed-out query (truncated answer, HTTP 500 / 504) once. A listing that still fails is
split by the last digits of the item number (`FILTER(STRENDS(STR(?item), "7"))`, then "07",
"17"... down to two digits) and a details batch is halved down to single items. The worldwide run
lists the two big website selectors in ten partitions from the start (~17k ids each, 10-40 s
measured); a `--country` run resolves the ISO code to the country's item (Australia: Q408) and
lists each selector in one query with `?item wdt:P17 wd:Q408` (measured: 74 s for all of
Australia's listings, against 735 s with partitions and a join through P297). After 8 failed
queries in a row the run stops (endpoint down or blocking us). Every finished chunk is written to
the work dir, so a rerun resumes where it stopped.

**Runtime.** Worldwide: phase 1 ~40 queries, ~8 minutes; phase 2 ~750 queries of 1-5 s, ~60
minutes; ~70 minutes in all. One country: Norway (11,570 items, test mode) 3 minutes, Australia
(5,362 items, full) 2 minutes. A test run (`--limit`) lists the national-number selectors only,
in full (~40,000 items, ~10 minutes), so its hash sample overlaps the register shards' samples.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pepite.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from pepite.connectors.bulk import _rules as rules
from pepite.connectors.wikidata import COMPANY_CLASSES
from pepite.packs import bulk
from pepite.packs.isic import isic_section

log = logging.getLogger(__name__)

NAME = "wikidata_orgs"
ENDPOINT = "https://query.wikidata.org/sparql"
ENDPOINT_ENV = "PEPITE_WIKIDATA_SPARQL"  # override: another WDQS-compatible endpoint
LABEL_LANGS = "en,mul,fr,de,es,it,nl,nb,nn,no,sv,da,fi,pt,pl,cs"
DETAIL_BATCH = 400
MIN_INTERVAL = 1.0  # seconds between two queries
MAX_SUFFIX = 2  # deepest id partition: last two digits of the item number
MAX_CONSECUTIVE_FAILURES = 8
SLEEP: Callable[[float], None] = time.sleep  # tests replace it

# country, register, column, Wikidata property
NATIONAL_IDS: tuple[tuple[str, str, str, str], ...] = (
    ("FR", "FR-SIREN", "siren", "P1616"),
    ("GB", "GB-CH", "gb_ch", "P2622"),
    ("NO", "NO-BRREG", "no_orgnr", "P2333"),
    ("AU", "AU-ABN", "abn", "P3548"),
    ("CA", "CA-CORP", "ca_corp", "P13850"),
    ("US", "US-SEC", "cik", "P5531"),
)
FIELDS: tuple[str, ...] = (
    "qid",
    "label",
    "website",
    "countries",
    "lei",
    "siren",
    "gb_ch",
    "cik",
    "no_orgnr",
    "abn",
    "acn",
    "ca_corp",
    "inception",
    "dissolved",
    "employees",
    "industry",
    "hq",
    "human",
)
_QID = re.compile(r"Q[1-9][0-9]*")


@dataclass(frozen=True)
class Selector:
    name: str
    where: str  # SPARQL patterns binding ?item
    country: str | None = None  # the country its items belong to (skipped when not requested)
    split: bool = False  # big: list it in ten partitions from the start


# Left out of test runs (limit): LEI-only and website-only items rarely meet a register sample.
TEST_RUN_SKIPS = frozenset({"lei", "company_site", "industry_site"})

_CLASSES = " ".join(f"wd:{q}" for q in COMPANY_CLASSES)
SELECTORS: tuple[Selector, ...] = (
    Selector("lei", "?item wdt:P1278 ?id .", split=True),
    Selector("siren_site", "?item wdt:P1616 ?id . ?item wdt:P856 ?site .", country="FR"),
    Selector("gb_ch", "?item wdt:P2622 ?id .", country="GB"),
    Selector("cik", "?item wdt:P5531 ?id ."),
    Selector("no_orgnr", "?item wdt:P2333 ?id .", country="NO"),
    Selector("abn", "?item wdt:P3548 ?id .", country="AU"),
    Selector("acn", "?item wdt:P3549 ?id .", country="AU"),
    Selector("ca_corp", "?item wdt:P13850 ?id .", country="CA"),
    Selector(
        "company_site",
        f"VALUES ?class {{ {_CLASSES} }} ?item wdt:P31 ?class . ?item wdt:P856 ?site .",
        split=True,
    ),
    Selector("industry_site", "?item wdt:P452 ?ind . ?item wdt:P856 ?site .", split=True),
)


# --------------------------------------------------------------------------- queries


def ids_query(
    selector: Selector,
    suffix: str = "",
    country_items: Iterable[str] | None = None,
    limit: int | None = None,
) -> str:
    """List the item ids of one selector: optionally one id partition (item numbers ending in
    `suffix`) and the items of some countries (`country_items`: the countries' own item ids,
    e.g. Q408 for Australia; `?item wdt:P17 wd:Q408` after the selector's patterns is fast, a
    join through the ISO code property P297 is not)."""
    if suffix and not suffix.isdigit():
        raise ValueError(f"bad partition suffix {suffix!r}")
    parts = [selector.where]
    if country_items:
        qids = sorted(set(country_items))
        if not all(_QID.fullmatch(q) for q in qids):
            raise ValueError(f"bad country items: {qids}")
        if len(qids) == 1:
            parts.append(f"?item wdt:P17 wd:{qids[0]} .")
        else:
            values = " ".join(f"wd:{q}" for q in qids)
            parts.append(f"?item wdt:P17 ?ctry . VALUES ?ctry {{ {values} }}")
    if suffix:
        parts.append(f'FILTER(STRENDS(STR(?item), "{suffix}"))')
    query = "SELECT DISTINCT ?item WHERE { " + " ".join(parts) + " }"
    return query + (f" LIMIT {int(limit)}" if limit else "")


def countries_query(codes: Iterable[str]) -> str:
    """Items whose ISO 3166-1 alpha-2 code (P297) is one of `codes`."""
    codes = sorted({c.upper() for c in codes})
    if not codes or not all(re.fullmatch(r"[A-Z]{2}", c) for c in codes):
        raise ValueError(f"bad country codes: {codes}")
    values = " ".join(f'"{c}"' for c in codes)
    return (
        f"SELECT ?country ?code WHERE {{ VALUES ?code {{ {values} }} ?country wdt:P297 ?code . }}"
    )


def details_query(qids: list[str]) -> str:
    """One row per item: label, website, countries, register numbers and firmographics."""
    if not qids or not all(_QID.fullmatch(q) for q in qids):
        raise ValueError("invalid item id")
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""
SELECT ?item (SAMPLE(?itemLabel) AS ?label) (MIN(STR(?site)) AS ?website)
  (GROUP_CONCAT(DISTINCT ?cc; separator=" ") AS ?countries)
  (MIN(?lei0) AS ?lei) (MIN(?siren0) AS ?siren) (MIN(?gbch0) AS ?gb_ch) (MIN(?cik0) AS ?cik)
  (MIN(?no0) AS ?no_orgnr) (MIN(?abn0) AS ?abn) (MIN(?acn0) AS ?acn) (MIN(?ca0) AS ?ca_corp)
  (MIN(YEAR(?inc)) AS ?inception) (MIN(YEAR(?dis)) AS ?dissolved)
  (GROUP_CONCAT(DISTINCT CONCAT(COALESCE(STR(?empAt), ""), "|", STR(?emp)); separator=" ")
    AS ?employees)
  (MIN(CONCAT(STRAFTER(STR(?ind), "entity/"), "|", COALESCE(?indLabel, ""))) AS ?industry)
  (MIN(?hqLabel) AS ?hq) (MAX(?human0) AS ?human)
WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P856 ?site . }}
  OPTIONAL {{ ?item wdt:P17/wdt:P297 ?cc . }}
  OPTIONAL {{ ?item wdt:P1278 ?lei0 . }}
  OPTIONAL {{ ?item wdt:P1616 ?siren0 . }}
  OPTIONAL {{ ?item wdt:P2622 ?gbch0 . }}
  OPTIONAL {{ ?item wdt:P5531 ?cik0 . }}
  OPTIONAL {{ ?item wdt:P2333 ?no0 . }}
  OPTIONAL {{ ?item wdt:P3548 ?abn0 . }}
  OPTIONAL {{ ?item wdt:P3549 ?acn0 . }}
  OPTIONAL {{ ?item wdt:P13850 ?ca0 . }}
  OPTIONAL {{ ?item wdt:P571 ?inc . }}
  OPTIONAL {{ ?item wdt:P576 ?dis . }}
  OPTIONAL {{ ?item wdt:P31 wd:Q5 . BIND(1 AS ?human0) }}
  OPTIONAL {{ ?item p:P1128 ?es . ?es ps:P1128 ?emp ; wikibase:rank ?er .
             FILTER(?er != wikibase:DeprecatedRank) OPTIONAL {{ ?es pq:P585 ?empAt . }} }}
  OPTIONAL {{ ?item wdt:P452 ?ind .
             OPTIONAL {{ ?ind rdfs:label ?indLabel . FILTER(LANG(?indLabel) = "en") }} }}
  OPTIONAL {{ ?item wdt:P159 ?hqi . ?hqi rdfs:label ?hqLabel . FILTER(LANG(?hqLabel) = "en") }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "{LABEL_LANGS}" . ?item rdfs:label ?itemLabel . }}
}}
GROUP BY ?item
""".strip()


def flatten(binding: dict) -> dict | None:
    """A SPARQL result row of details_query() -> {field: text or None} (None if no item)."""
    uri = (binding.get("item") or {}).get("value", "")
    qid = uri.rsplit("/", 1)[-1]
    if not _QID.fullmatch(qid):
        return None
    row: dict[str, str | None] = {"qid": qid}
    for f in FIELDS[1:]:
        value = (binding.get(f) or {}).get("value")
        row[f] = value if value not in (None, "") else None
    return row


# --------------------------------------------------------------------------- SPARQL client


class SparqlError(Exception):
    """A query that still failed after its retries (the caller splits it or skips it)."""


@dataclass
class Sparql:
    """Sequential, polite SPARQL client for query.wikidata.org."""

    client: httpx.Client
    endpoint: str = ENDPOINT
    user_agent: str = rules.user_agent()
    min_interval: float = MIN_INTERVAL
    attempts: int = 3
    timeout_attempts: int = 2  # a query that ran out of time gets one retry, then it is split
    sleep: Callable[[float], None] = time.sleep
    queries: int = 0
    failures_in_a_row: int = 0
    _last: float = field(default=0.0, repr=False)

    def select(self, query: str) -> list[dict]:
        problem = ""
        budget = self.attempts
        attempt = 0
        while attempt < budget:
            attempt += 1
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            pause = min(120.0, 5.0 * 3 ** (attempt - 1))
            try:
                resp = self.client.post(
                    self.endpoint,
                    data={"query": query},
                    headers={"User-Agent": self.user_agent,
                             "Accept": "application/sparql-results+json"},
                )  # fmt: skip
                self._last = time.monotonic()
                self.queries += 1
                if resp.status_code == 200:
                    try:
                        bindings = resp.json()["results"]["bindings"]
                    except (ValueError, KeyError, TypeError):
                        problem = "truncated answer (query timeout)"
                        budget = min(budget, self.timeout_attempts)
                    else:
                        self.failures_in_a_row = 0
                        return bindings
                elif resp.status_code == 429:
                    problem = "HTTP 429 (rate limited)"
                    pause = max(pause, _retry_after(resp) or 60.0)
                elif resp.status_code in (400, 414):
                    raise SparqlError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    problem = f"HTTP {resp.status_code}"
                    if resp.status_code in (500, 504):  # the endpoint's query timeout
                        budget = min(budget, self.timeout_attempts)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                self._last = time.monotonic()
                problem = f"{type(exc).__name__}: {exc}"
            if attempt < budget:
                log.warning("%s: %s; retrying in %.0f s", NAME, problem, pause)
                self.sleep(min(pause, 300.0))
        self.failures_in_a_row += 1
        if self.failures_in_a_row >= MAX_CONSECUTIVE_FAILURES:
            raise RuntimeError(
                f"{NAME}: {self.failures_in_a_row} queries failed in a row ({problem}); "
                "the endpoint is down or refusing us. Rerun later: finished chunks are kept."
            )
        raise SparqlError(problem)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After", "")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return None


def http_client() -> httpx.Client:
    """HTTP client used by build() (tests swap it for a mock transport)."""
    return httpx.Client(timeout=httpx.Timeout(90.0, connect=30.0), follow_redirects=True)


# --------------------------------------------------------------------------- fetching


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


@dataclass
class Fetch:
    """Phase 1 (item ids per selector) and phase 2 (details), cached in `work_dir`."""

    sparql: Sparql
    work_dir: Path
    countries: frozenset[str] | None = None
    limit: int | None = None
    failed: list[str] = field(default_factory=list)

    def scope(self) -> str:
        return "all" if not self.countries else "cc-" + "-".join(sorted(self.countries))

    def selectors(self) -> list[Selector]:
        """Selectors to run. A test run (limit) lists the national-number selectors only, in
        full: the shards keep the rows with the smallest hash of the register key
        (packs.bulk.SAMPLE_ORDER), as the register shards do, so the samples overlap."""
        return [
            s
            for s in SELECTORS
            if not (self.countries and s.country and s.country not in self.countries)
            and not (self.limit and s.name in TEST_RUN_SKIPS)
        ]

    def country_items(self) -> dict[str, list[str]]:
        """{ISO code: the country's item ids} for the requested countries (cached)."""
        path = self.work_dir / "ids" / self.scope() / "countries.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        found: dict[str, list[str]] = {c: [] for c in sorted(self.countries or ())}
        for row in self.sparql.select(countries_query(found)):
            qid = (row.get("country") or {}).get("value", "").rsplit("/", 1)[-1]
            code = (row.get("code") or {}).get("value", "")
            if _QID.fullmatch(qid) and code in found:
                found[code] = sorted({*found[code], qid}, key=lambda q: int(q[1:]))
        _write_atomic(path, json.dumps(found))
        return found

    def chunks(self, sel: Selector) -> list[tuple[str, tuple[str, ...]]]:
        """(label, country items) per id listing of a selector. A country run lists each
        country in one query; a worldwide run lists the big selectors in ten partitions."""
        if not self.countries or sel.country:
            return [("", ())]
        items = self.country_items()
        missing = sorted(c for c, q in items.items() if not q)
        if missing:
            log.warning("%s: no Wikidata item for country codes %s", NAME, missing)
        return [(cc, tuple(q)) for cc, q in items.items() if q]

    def item_ids(self) -> list[str]:
        ids_dir = self.work_dir / "ids" / self.scope()
        found: dict[str, None] = {}
        t0 = time.monotonic()
        for sel in self.selectors():
            got_sel = 0
            for cc, country in self.chunks(sel):
                split = sel.split and not country
                pending = deque(list("0123456789") if split else [""])
                while pending:
                    suffix = pending.popleft()
                    tag = "-".join(x for x in (sel.name, cc, suffix or "all") if x)
                    path = ids_dir / f"{tag}.txt"
                    if path.exists():
                        qids = path.read_text(encoding="utf-8").split()
                    else:
                        try:
                            rows = self.sparql.select(ids_query(sel, suffix, country))
                        except SparqlError as exc:
                            if len(suffix) < MAX_SUFFIX:
                                log.warning("%s: ids %s failed (%s); splitting it", NAME, tag, exc)
                                pending.extendleft(reversed([d + suffix for d in "0123456789"]))
                            else:
                                log.error("%s: ids %s skipped (%s)", NAME, tag, exc)
                                self.failed.append(f"ids {tag}")
                            continue
                        qids = [
                            q
                            for q in ((r.get("item") or {}).get("value", "").rsplit("/", 1)[-1]
                                      for r in rows)
                            if _QID.fullmatch(q)
                        ]  # fmt: skip
                        _write_atomic(path, "\n".join(qids) + "\n")
                    got_sel += len(qids)
                    found.update(dict.fromkeys(qids))
            log.info("%s: selector %s: %s ids (%s unique so far, %.0f s)", NAME, sel.name,
                     f"{got_sel:,}", f"{len(found):,}", time.monotonic() - t0)  # fmt: skip
        return list(found)

    def details(self, qids: list[str], batch: int = DETAIL_BATCH) -> list[Path]:
        out: list[Path] = []
        t0 = time.monotonic()
        total = len(qids)
        for start in range(0, total, batch):
            out += self._details(qids[start : start + batch])
            done = min(start + batch, total)
            if (start // batch) % 25 == 0 or done == total:
                log.info("%s: details %s/%s items, %d queries, %.0f s", NAME, f"{done:,}",
                         f"{total:,}", self.sparql.queries, time.monotonic() - t0)  # fmt: skip
        return out

    def _details(self, chunk: list[str]) -> list[Path]:
        key = hashlib.sha1(" ".join(chunk).encode()).hexdigest()[:20]
        path = self.work_dir / "details" / f"{key}.jsonl"
        if path.exists():
            return [path]
        try:
            rows = self.sparql.select(details_query(chunk))
        except SparqlError as exc:
            if len(chunk) == 1:
                log.warning("%s: details of %s skipped (%s)", NAME, chunk[0], exc)
                self.failed.append(f"details {chunk[0]}")
                return []
            mid = len(chunk) // 2
            log.warning("%s: details batch of %d failed (%s); halving it", NAME, len(chunk), exc)
            return self._details(chunk[:mid]) + self._details(chunk[mid:])
        flat = [r for r in (flatten(b) for b in rows) if r is not None]
        _write_atomic(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in flat))
        return [path]


# --------------------------------------------------------------------------- build


def build(options: BuildOptions) -> ShardStats:
    endpoint = os.environ.get(ENDPOINT_ENV, "").strip() or ENDPOINT
    client = http_client()
    try:
        sparql = Sparql(
            client,
            endpoint=endpoint,
            user_agent=rules.user_agent(options.contact),
            min_interval=MIN_INTERVAL,
            sleep=SLEEP,
        )
        fetch = Fetch(sparql, options.work_dir, options.countries, options.limit)
        qids = fetch.item_ids()
        log.info("%s: %s items to describe", NAME, f"{len(qids):,}")
        files = fetch.details(qids)
    finally:
        client.close()
    con = bulk.connect(options.work_dir)
    try:
        return transform(con, files, options, failed=fetch.failed)
    finally:
        con.close()


def _install_number_macros(con) -> None:
    """ABN check digits and ACN -> ABN, shared with the GLEIF source."""
    from pepite.connectors.bulk import gleif_golden

    for stmt in gleif_golden.macro_sql():
        con.execute(stmt)


def transform(
    con, files: list[Path], options: BuildOptions, *, failed: list[str] | None = None
) -> ShardStats:
    """Fetched details (JSON lines) -> one shard per country (wikidata_orgs-<CC>.parquet)."""
    _install_number_macros(con)
    columns = "{" + ", ".join(f"'{f}': 'VARCHAR'" for f in FIELDS) + "}"
    if files:
        file_list = "[" + ", ".join(bulk.sql_str(str(p)) for p in files) + "]"
        con.execute(
            f"CREATE OR REPLACE TABLE wd_raw AS SELECT DISTINCT ON (qid) * FROM read_json("
            f"{file_list}, format='newline_delimited', columns={columns}) ORDER BY qid"
        )
    else:
        cols = ", ".join(f"CAST(NULL AS VARCHAR) AS {f}" for f in FIELDS)
        con.execute(f"CREATE OR REPLACE TABLE wd_raw AS SELECT {cols} LIMIT 0")
    total = bulk.count(con, "wd_raw")
    log.info("%s: %d items fetched", NAME, total)
    _industry_table(con)
    cc_ok = bulk.list_lambda("c", "regexp_full_match(c, '[A-Z]{2}')")
    cik = "TRY_CAST(lc_digits(cik) AS BIGINT)"
    con.execute(f"""
        CREATE OR REPLACE TABLE wd_norm AS SELECT
          qid,
          lc_clean(label) AS label,
          lc_clean(website) AS website,
          list_sort(list_filter(string_split(coalesce(countries, ''), ' '), {cc_ok})) AS ccs,
          CASE WHEN regexp_full_match(upper(trim(lei)), '[A-Z0-9]{{20}}') THEN upper(trim(lei))
               END AS lei,
          CASE WHEN length(lc_digits(siren)) = 9 THEN lc_digits(siren) END AS siren,
          lc_gb_number(gb_ch) AS gb_ch,
          CASE WHEN {cik} > 0 THEN lpad(CAST({cik} AS VARCHAR), 10, '0') END AS cik,
          CASE WHEN length(lc_digits(no_orgnr)) = 9 THEN lc_digits(no_orgnr) END AS no_orgnr,
          coalesce(lc_abn(lc_digits(abn)), lc_acn_abn(lc_digits(acn))) AS abn,
          CASE WHEN NOT regexp_full_match(lc_digits(ca_corp), '0*') THEN lc_digits(ca_corp)
               END AS ca_corp,
          TRY_CAST(inception AS INTEGER) AS inception,
          dissolved, human, employees, industry,
          lc_clean(regexp_replace(hq, ' (Municipality|kommune|kommun|Kommune)$', '')) AS hq
        FROM wd_raw
    """)
    # Countries of the national numbers an item has, in NATIONAL_IDS order. Among several P17
    # values they pick the country (a CIK prefers US: CME Group is P17 US + GB); with no P17 they
    # give it, except a CIK (foreign companies file with the SEC too).
    hints = ", ".join(
        f"CASE WHEN {col} IS NOT NULL THEN '{cc}' END" for cc, _, col, _ in NATIONAL_IDS
    )
    prefer = f"list_filter([{hints}], {bulk.list_lambda('c', 'c IS NOT NULL')})"
    in_ccs = bulk.list_lambda("c", "list_contains(ccs, c)")
    not_us = bulk.list_lambda("c", "c <> 'US'")
    country = (
        f"CASE WHEN len(ccs) = 1 THEN ccs[1] "
        f"WHEN len(ccs) > 1 THEN coalesce(list_filter(prefer, {in_ccs})[1], ccs[1]) "
        f"WHEN len(list_filter(prefer, {not_us})) = 1 THEN list_filter(prefer, {not_us})[1] END"
    )
    own = " ".join(
        f"WHEN country = '{cc}' AND {col} IS NOT NULL THEN '{reg}'"
        for cc, reg, col, _ in NATIONAL_IDS
    )
    first = " ".join(f"WHEN {col} IS NOT NULL THEN '{reg}'" for _, reg, col, _ in NATIONAL_IDS)
    number = " ".join(f"WHEN '{reg}' THEN {col}" for _, reg, col, _ in NATIONAL_IDS)
    emp_pair = bulk.list_lambda(
        "x",
        "struct_pack(d := split_part(x, '|', 1), v := TRY_CAST(split_part(x, '|', 2) AS DOUBLE))",
    )
    emp_ok = bulk.list_lambda("p", "p.v >= 1")
    pairs = f"list_filter(list_transform(string_split(employees, ' '), {emp_pair}), {emp_ok})"
    con.execute(f"""
        CREATE OR REPLACE TABLE wd_stage AS
        WITH c AS (SELECT *, {prefer} AS prefer FROM wd_norm),
        k AS (SELECT *, {country} AS country FROM c),
        r AS (SELECT *, CASE {own} {first} END AS registry FROM k)
        SELECT r.*, CASE registry {number} END AS company_number,
          CAST(list_sort({pairs}, 'DESC')[1].v AS INTEGER) AS staff,
          i.section, NULLIF(split_part(r.industry, '|', 2), '') AS industry_text,
          CASE
            WHEN human IS NOT NULL THEN 'a person'
            WHEN label IS NULL OR label = qid THEN 'no label'
            WHEN dissolved IS NOT NULL THEN 'dissolved'
            WHEN country IS NULL THEN 'no country'
          END AS dropped
        FROM r LEFT JOIN wd_industries i ON i.industry = r.industry
    """)
    con.execute("DROP TABLE wd_norm")
    counts = con.execute(
        "SELECT dropped, count(*) FROM wd_stage WHERE dropped IS NOT NULL "
        "GROUP BY dropped ORDER BY count(*) DESC, dropped"
    ).fetchall()
    con.execute(
        "CREATE OR REPLACE TABLE wd_kept AS SELECT * FROM wd_stage WHERE dropped IS NULL"
        + (f" AND {bulk.country_clause(options)}" if options.countries else "")
    )
    year = "year(current_date)"
    exprs = {
        "id": "coalesce(lc_company_id(registry, company_number, lei, cik), "
        "'co_' || left(sha1('wd|' || lower(qid)), 16))",
        "name": "label",
        "domain": rules.domain_sql("website"),
        "country": "country",
        "city": "hq",
        "industry": "section",
        "industry_label": "industry_text",
        "size_range": rules.size_range_sql("staff"),
        "founded_year": f"CASE WHEN inception BETWEEN 1000 AND {year} THEN inception END",
        "lei": "lei",
        "wikidata_id": "qid",
        "website": rules.website_sql("website"),
        "sources_json": bulk.sql_str(provenance_json(SOURCE, "https://www.wikidata.org/")),
        "siren": "siren",
        "cik": "cik",
        "registry": "registry",
        "company_number": "company_number",
        "employees": "staff",
    }
    stats = ShardStats(source=NAME)
    by_country = con.execute(
        "SELECT country, count(*) FROM wd_kept GROUP BY country ORDER BY country"
    ).fetchall()
    for cc, _ in by_country:
        part = bulk.write_source_shard(
            con,
            SOURCE,
            options,
            exprs,
            f"(SELECT * FROM wd_kept WHERE country = {bulk.sql_str(cc)}) AS wd_cc",
            order="wikidata_id",
            file_name=f"{NAME}-{cc}.parquet",
        )
        stats.rows += part.rows
        stats.files += part.files
    with_site = con.execute(
        f"SELECT count(*) FROM wd_kept WHERE {rules.domain_sql('website')} IS NOT NULL"
    ).fetchone()[0]
    stats.skipped = total - sum(n for _, n in by_country)
    top = sorted(by_country, key=lambda r: -r[1])[:8]
    notes = [
        f"{stats.rows:,} rows in {len(stats.files)} country files, {with_site:,} with a domain",
        "largest: " + ", ".join(f"{cc} {n:,}" for cc, n in top) if top else "",
        ("dropped: " + ", ".join(f"{n:,} {why}" for why, n in counts)) if counts else "",
        f"{len(failed)} chunks failed: {', '.join(failed[:5])}" if failed else "",
    ]
    stats.notes = "; ".join(n for n in notes if n)
    log.info("%s: %s", NAME, stats.notes)
    return stats


def _industry_table(con) -> None:
    """wd_industries(industry, section): ISIC section of each distinct "Qid|label" industry."""
    con.execute("CREATE OR REPLACE TABLE wd_industries (industry VARCHAR, section VARCHAR)")
    values = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT industry FROM wd_raw WHERE industry IS NOT NULL"
        ).fetchall()
    ]
    rows = []
    for value in values:  # a few thousand distinct industries
        qid, _, label = value.partition("|")
        section = isic_section(label or None, qid or None)
        if section:
            rows.append((value, section))
    if rows:
        con.executemany("INSERT INTO wd_industries VALUES (?, ?)", rows)


SOURCE = register(
    BulkSource(
        name=NAME,
        title="Wikidata organisations (websites and register numbers)",
        licence="CC0-1.0",
        licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
        homepage="https://www.wikidata.org/",
        countries=None,
        build=build,
        attribution="Organisation data from Wikidata (https://www.wikidata.org/), CC0 1.0.",
    )
)
