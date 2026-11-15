"""Merge bulk register shards into one clean Parquet file per country (pack schema v0.3).

    leadcommons pack merge shards/ --out packs-out/ [--release packs-YYYY-MM]

writes the merged-pack layout of docs/dev/data-agents.md:

    companies-<CC>-<YYYYMMDD>.parquet   one per country, exactly PACK_COLUMNS, zstd, sorted by name
    manifest.json                       schema 3: rows, bytes, sha256, sources, with_domain per file
    ATTRIBUTION.md                      from each source's licence and attribution (BulkSource)
    QUALITY.md, quality.json            packs/quality.py

Steps (all set-based DuckDB SQL, in an on-disk work database that spills to disk):

1. `linking.load_rows`: read every shard once, normalise keys.
2. `linking.link`: strong keys, register guard, name fallback -> clusters (see linking.py).
3. Read the shards again with each row's cluster and the cluster's country, into one table of
   the work database (one pass); each country's queries then filter on the country.
4. Per country: single-row companies are projected as they are; rows of the same cluster are
   folded with `arg_min(value, (source tier, natural key))`, field by field, following
   FIELD_PRIORITY; `sources_json` is the union of the rows' provenance; LEIs, CIKs and Wikidata
   ids stay on one company each; the stable id is computed; inactive companies are dropped; the
   file is written sorted by `name, id` (so DuckDB zone maps on `name` are tight), with the merge
   statistics in the Parquet key-value metadata (`merge_stats`), which quality.py reads.
   A company carrying the number of a register whose shard is in the merge, but with no row from
   that register, is not active there (register shards hold every active company: typically a
   dissolved company whose LEI merely lapsed); it is dropped unless `keep_unregistered` (for
   test runs on `--limit`ed shards).

Field priority (first source tier with a value wins; ties go to the smallest natural key, so the
result does not depend on file or row order). Tiers: `register` = national registers (SIRENE,
Companies House, Brønnøysund, ABR, Corporations Canada, any other register), `filings` = SEC
EDGAR, `lei` = GLEIF, `wiki` = Wikidata.

| Field(s) | Priority |
| --- | --- |
| name | wiki > register > lei > filings (common name; the legal name is in legal_name) |
| legal_name | register > lei > filings > wiki (Wikidata only gives an official name, P1448) |
| country | register > filings > lei > wiki |
| address + postal_code + city (one unit) | register > filings > lei > wiki |
| region | register > filings > lei > wiki |
| status | register > lei > filings > wiki (inactive companies are dropped) |
| legal_form, founded_year | register > lei > filings > wiki |
| registry + company_number | the country's register, then register > filings > lei > wiki |
| siren | register > lei > wiki > filings |
| lei | lei > wiki > register > filings (GLEIF row whose name matches the register name first) |
| cik | filings > wiki > lei > register |
| wikidata_id | wiki > lei > register > filings |
| domain + website (one unit) | register (Brønnøysund) > wiki > filings > lei |
| industry + industry_label (one unit) | register > filings > wiki > lei |
| size_range | register > filings > wiki > lei, else derived from employees |
| employees | register > filings > wiki > lei (exact head counts only) |
| revenue + revenue_currency + revenue_year (one unit) | register > filings > wiki > lei |
| phone | register > filings > wiki > lei |
| sources_json | union of every row's provenance, registers first |

Stable id (docs/dev/data-agents.md): `make_id("co", kind, value)` with the first of
`reg` (`<registry>:<company_number>`, a SIREN being `FR-SIREN:<siren>`, an EDGAR CIK being
`US-SEC:<cik>`), `lei`, `cik`, `wd`, `domain`, `name` (`<normalised name>|<country>`).
Register numbers in the id are in their compared form: FR-SIREN 9 digits, GB-CH 8 characters,
US-SEC 10 digits; other purely numeric numbers without leading zeros (CA-CORP "0123456" ->
`CA-CORP:123456`), while `company_number` keeps the register's own spelling.

Scale (tests/test_pack_merge.py::test_merge_at_scale; 4 cores / 15 GB like a GitHub runner,
DuckDB 2.0, Python 3.13, synthetic shards that follow the contract: 30.0M input rows, France
15M, UK 5.4M, Australia 3M, GLEIF 2.5M, Wikidata 0.84M; 26.4M companies written in 14 files):

- default memory limit (70% of RAM): 393 s wall, peak RSS 9.8 GiB (DuckDB fills its limit as
  cache); load 44 s, link 46 s, rows placed by country 72 s, France (15M rows) about 2 min,
  quality 49 s. The sandbox was shared with other jobs (load average ~7 on 4 cores) during the
  run; on an idle machine the steps took about half as long.
- LEADCOMMONS_MERGE_MEMORY=4GB: 403 s wall, peak RSS 4.2 GiB (spills to the work directory).
- Disk: the work directory peaks at about 2-3x the shards' size; it is removed at the end.
- 3M rows: 28 s, peak RSS 2.6 GiB.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from leadcommons.models import SIZE_RANGES
from leadcommons.packs.linking import (
    COUNTRY_ALIASES,
    NATIONAL_REGISTERS,
    LinkStats,
    Shard,
    find_shards,
    install_macros,
    link,
    lit,
    load_rows,
    norm_from_sql,
    shards_union_sql,
    source_tier,
    tier_rank_sql,
)
from leadcommons.search import PACK_COLUMNS

log = logging.getLogger(__name__)

SCHEMA = 3

REG, FIL, LEI, WIKI = "register", "filings", "lei", "wiki"
FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "name": (WIKI, REG, LEI, FIL),
    "legal_name": (REG, LEI, FIL, WIKI),
    "country": (REG, FIL, LEI, WIKI),
    "address": (REG, FIL, LEI, WIKI),  # address, postal_code, city together
    "region": (REG, FIL, LEI, WIKI),
    "status": (REG, LEI, FIL, WIKI),
    "legal_form": (REG, LEI, FIL, WIKI),
    "founded_year": (REG, LEI, FIL, WIKI),
    "registry": (REG, FIL, LEI, WIKI),  # after the country's national register
    "siren": (REG, LEI, WIKI, FIL),
    "lei": (LEI, WIKI, REG, FIL),
    "cik": (FIL, WIKI, LEI, REG),
    "wikidata_id": (WIKI, LEI, REG, FIL),
    "website": (REG, WIKI, FIL, LEI),  # domain and website together
    "industry": (REG, FIL, WIKI, LEI),  # industry and industry_label together
    "size_range": (REG, FIL, WIKI, LEI),
    "employees": (REG, FIL, WIKI, LEI),
    "revenue": (REG, FIL, WIKI, LEI),  # revenue, currency and year together
    "phone": (REG, FIL, WIKI, LEI),
}
# (group, output columns, priority profile). Columns of one group come from the same row.
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("name", ("name",), "name"),
    ("legal_name", ("legal_name",), "legal_name"),
    ("country", ("country",), "country"),
    ("addr", ("address", "postal_code", "city"), "address"),
    ("region", ("region",), "region"),
    ("status", ("status",), "status"),
    ("legal_form", ("legal_form",), "legal_form"),
    ("founded_year", ("founded_year",), "founded_year"),
    ("siren", ("siren",), "siren"),
    ("industry", ("industry", "industry_label"), "industry"),
    ("size_range", ("size_range",), "size_range"),
    ("employees", ("employees",), "employees"),
    ("revenue", ("revenue", "revenue_currency", "revenue_year"), "revenue"),
    ("phone", ("phone",), "phone"),
)
# Identifiers kept on one company only; the tier of the source they came from is tracked.
TRACKED_GROUPS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("lei", ("lei",), "lei"),
    ("cik", ("cik",), "cik"),
    ("wikidata_id", ("wikidata_id",), "wikidata_id"),
    ("web", ("domain", "website"), "website"),
)
INACTIVE = "inactive"
_SIZE_BOUNDS = (10, 50, 200, 500, 1000, 5000, 10000)

# Titles used in ATTRIBUTION.md when a source module is not registered in this process.
KNOWN_TITLES: dict[str, str] = {
    "sirene": "INSEE SIRENE (France)",
    "companies_house": "UK Companies House",
    "gleif": "GLEIF LEI golden copy",
    "edgar": "SEC EDGAR (United States)",
    "brreg": "Brønnøysund Register Centre (Norway)",
    "abr": "Australian Business Register",
    "canada": "Corporations Canada",
    "wikidata": "Wikidata",
}
_ATTRIBUTION_LICENCES = ("CC-BY", "OGL", "NLOD", "ETALAB", "LICENCE OUVERTE", "ODBL")


@dataclass
class CountryResult:
    country: str
    file: str
    rows: int
    bytes: int
    sha256: str
    sources: list[str]
    with_domain: int
    stats: dict = field(default_factory=dict)

    def manifest_entry(self) -> dict:
        return {
            "country": self.country,
            "file": self.file,
            "rows": self.rows,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "sources": self.sources,
            "with_domain": self.with_domain,
        }


@dataclass
class MergeResult:
    out_dir: Path
    release: str
    built_at: str
    countries: list[CountryResult]
    link: LinkStats
    input_rows: dict[str, int]
    dropped_no_country: int
    seconds: float
    quality: dict = field(default_factory=dict)

    @property
    def rows(self) -> int:
        return sum(c.rows for c in self.countries)


# -- helpers -------------------------------------------------------------------------------


def _memory_limit() -> str | None:
    if env := os.environ.get("LEADCOMMONS_MERGE_MEMORY"):
        return env
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return None
    return f"{max(1, int(total * 0.7 / 2**30))}GB"


def _connect(work: Path, memory_limit: str | None, threads: int | None):
    import duckdb

    con = duckdb.connect(str(work / "merge.duckdb"))
    con.execute(f"SET temp_directory = {lit(str(work / 'tmp'))}")
    con.execute("SET preserve_insertion_order = false")
    if limit := memory_limit or _memory_limit():
        con.execute(f"SET memory_limit = {lit(limit)}")
    if threads := threads or int(os.environ.get("LEADCOMMONS_MERGE_THREADS", "0") or 0):
        con.execute(f"SET threads = {int(threads)}")
    install_macros(con)
    return con


def _rank(group_profile: str) -> str:
    return tier_rank_sql(FIELD_PRIORITY[group_profile])


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _size_bucket_sql(col: str) -> str:
    whens = " ".join(
        f"WHEN {col} <= {b} THEN '{label}'"
        for b, label in zip(_SIZE_BOUNDS, SIZE_RANGES, strict=False)
    )
    return f"(CASE WHEN {col} IS NULL OR {col} < 1 THEN NULL {whens} ELSE '{SIZE_RANGES[-1]}' END)"


def _sort_sources(names: Sequence[str]) -> list[str]:
    order = {t: i for i, t in enumerate(FIELD_PRIORITY["country"])}
    return sorted(set(names), key=lambda s: (order.get(source_tier(s), 9), s))


# -- per-row candidates --------------------------------------------------------------------

_SIZES = ", ".join(lit(s) for s in SIZE_RANGES)
_OVERSEAS = ", ".join(lit(c) for c in COUNTRY_ALIASES)


def _candidates_sql(rows_sql: str, year: int) -> str:
    """Per-row candidate values (`<col>_v`) and priority ranks over one country's rows."""
    ranks = ",\n        ".join(
        f"{_rank(p)} AS rk_{p}" for p in sorted(FIELD_PRIORITY) if p != "country"
    )
    return f"""
      SELECT cid, multi, how, src, tier, natkey, rk_legal,
        lk_txt(name) AS name_v,
        CASE WHEN tier = 'wiki' THEN lk_txt(legal_name)
             ELSE coalesce(lk_txt(legal_name), lk_txt(name)) END AS legal_name_v,
        country_n AS country_v,
        lk_txt(address) AS address_v, lk_txt(postal_code) AS postal_code_v,
        lk_txt(city) AS city_v,
        coalesce(lk_txt(region), CASE WHEN upper(trim(country)) IN ({_OVERSEAS})
                                      THEN upper(trim(country)) END) AS region_v,
        lk_status(status) AS status_v, lk_txt(legal_form) AS legal_form_v,
        CASE WHEN founded_year BETWEEN 1000 AND {year + 1} THEN founded_year END AS founded_year_v,
        reg_registry, reg_number, reg_disp, siren_n AS siren_v, cik_n AS cik_v, lei_n AS lei_v,
        wd_n AS wikidata_id_v, domain_n AS domain_v, lk_txt(website) AS website_v,
        upper(lk_txt(industry)) AS industry_v, lk_txt(industry_label) AS industry_label_v,
        CASE WHEN lk_txt(size_range) IN ({_SIZES}) THEN lk_txt(size_range) END AS size_range_v,
        CASE WHEN employees >= 0 THEN employees END AS employees_v,
        revenue AS revenue_v, upper(lk_txt(revenue_currency)) AS revenue_currency_v,
        CASE WHEN revenue IS NOT NULL THEN revenue_year END AS revenue_year_v,
        lk_txt(phone) AS phone_v,
        CASE WHEN json_valid(sources_json) AND starts_with(trim(sources_json), '[')
                  AND length(trim(sources_json)) > 2
             THEN trim(trim(sources_json)[2:-2])
             ELSE '{{"source": "' || src || '"}}' END AS prov_v,
        {_rank("country")} AS rk_country,
        {ranks}
      FROM {rows_sql}
    """


def _single_sql() -> str:
    """Companies made of one row: the candidates as they are."""
    cols = []
    for _, outs, _ in FIELD_GROUPS + TRACKED_GROUPS:
        cols += [f"{c}_v AS {c}" for c in outs]
    return f"""
      SELECT cid, {", ".join(cols)},
        reg_registry AS registry, reg_disp AS company_number, reg_number AS reg_key,
        '[' || prov_v || ']' AS sources_json, [src] AS srcs,
        CASE WHEN lei_v IS NOT NULL THEN tier END AS lei_tier,
        CASE WHEN cik_v IS NOT NULL THEN tier END AS cik_tier,
        CASE WHEN wikidata_id_v IS NOT NULL THEN tier END AS wd_tier,
        CASE WHEN domain_v IS NOT NULL OR website_v IS NOT NULL THEN src END AS web_src,
        CASE WHEN domain_v IS NOT NULL OR website_v IS NOT NULL THEN tier END AS web_tier,
        tier IN ('register', 'filings') AS has_reg
      FROM c WHERE NOT multi
    """


def _multi_sql(national: str | None) -> str:
    """Companies made of several rows: each field group from its most trusted row."""

    def pick(name: str, outs: Sequence[str], profile: str, extra: Sequence[str] = ()) -> str:
        val = ", ".join(f"{c} := {c}_v" for c in outs) + "".join(f", {e}" for e in extra)
        cond = " OR ".join(f"{c}_v IS NOT NULL" for c in outs)
        order = f"struct_pack(r := rk_{profile}, k := natkey)"
        if profile == "lei":  # the LEI of the GLEIF row named like the company first
            order = "struct_pack(r := rk_lei, m := (nk IS DISTINCT FROM cnk), k := natkey)"
        return f"arg_min(struct_pack({val}), {order}) FILTER (WHERE {cond}) AS g_{name}"

    aggs = [pick(n, o, p) for n, o, p in FIELD_GROUPS]
    aggs += [pick(n, o, p, ("t := tier", "s := src")) for n, o, p in TRACKED_GROUPS]
    nat = lit(national) if national else "NULL"
    aggs.append(
        "arg_min(struct_pack(r := reg_registry, n := reg_number, d := reg_disp), "
        f"struct_pack(a := (reg_registry IS DISTINCT FROM {nat}), r := rk_registry, k := natkey)) "
        "FILTER (WHERE reg_registry IS NOT NULL) AS g_reg"
    )
    aggs.append("list(DISTINCT src ORDER BY src) AS srcs")
    aggs.append("bool_or(tier IN ('register', 'filings')) AS has_reg")
    unpack = []
    for name, outs, _ in FIELD_GROUPS + TRACKED_GROUPS:
        unpack += [f"g_{name}.{c} AS {c}" for c in outs]
    return f"""
      WITH m AS (
        SELECT *, lk_name_key(coalesce(legal_name_v, name_v)) AS nk FROM c WHERE multi
      ),
      m2 AS (
        SELECT *, arg_min(nk, struct_pack(r := rk_legal, k := natkey)) OVER (PARTITION BY cid)
                  AS cnk
        FROM m
      ),
      agg AS (SELECT cid, {", ".join(aggs)} FROM m2 GROUP BY cid),
      prov AS (
        SELECT cid, '[' || string_agg(prov_v, ', ' ORDER BY rk_legal, prov_v) || ']'
                    AS sources_json
        FROM (SELECT DISTINCT cid, rk_legal, prov_v FROM m) GROUP BY cid
      )
      SELECT cid, {", ".join(unpack)},
        g_reg.r AS registry, g_reg.d AS company_number, g_reg.n AS reg_key,
        prov.sources_json, srcs,
        g_lei.t AS lei_tier, g_cik.t AS cik_tier, g_wikidata_id.t AS wd_tier,
        g_web.s AS web_src, g_web.t AS web_tier, has_reg
      FROM agg JOIN prov USING (cid)
    """


# -- per country ---------------------------------------------------------------------------


def _merge_country(
    con,
    cc: str,
    rows_sql: str,
    out_path: Path,
    kv: dict[str, str],
    year: int,
    stats_in: dict,
    present: Sequence[str] = (),
    drop_unregistered: bool = True,
) -> tuple[dict, list[str]]:
    """Merge one country's rows into out_path (not written when no row is left).
    Returns its statistics and its sources.

    `present`: registers whose own shard is part of the merge. A company that carries one of
    their numbers but has no row from that register is not active there (the register shards
    hold every active company); it is counted and, with `drop_unregistered`, dropped."""
    t0 = time.monotonic()
    con.execute(f"CREATE OR REPLACE TEMP VIEW c AS {_candidates_sql(rows_sql, year)}")
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE out AS
      {_single_sql()}
      UNION ALL BY NAME
      SELECT * FROM ({_multi_sql(NATIONAL_REGISTERS.get(cc))})
    """)
    clusters = int(con.execute("SELECT count(*) FROM out").fetchone()[0])
    # LEI, CIK and Wikidata id stay on one company: the one whose value came from its authority.
    cleared = {}
    for col, tier_col, profile in (
        ("lei", "lei_tier", "lei"),
        ("cik", "cik_tier", "cik"),
        ("wikidata_id", "wd_tier", "wikidata_id"),
    ):
        n = con.execute(f"""
          UPDATE out SET {col} = NULL WHERE cid IN (
            SELECT cid FROM (
              SELECT cid, row_number() OVER (PARTITION BY {col} ORDER BY
                {tier_rank_sql(FIELD_PRIORITY[profile], tier_col)},
                coalesce(registry || ':' || company_number, ''), name, cid) AS rn
              FROM out WHERE {col} IN (
                SELECT {col} FROM out WHERE {col} IS NOT NULL GROUP BY {col} HAVING count(*) > 1)
            ) WHERE rn > 1)
        """).fetchone()
        cleared[col] = int(n[0]) if n else 0
    inactive = int(
        con.execute(f"SELECT count(*) FROM out WHERE status = '{INACTIVE}'").fetchone()[0]
    )
    regs = ", ".join(lit(r) for r in present) or "NULL"
    unregistered_sql = f"(registry IN ({regs}) AND NOT has_reg)"
    unregistered = int(
        con.execute(
            f"SELECT count(*) FROM out WHERE status IS DISTINCT FROM '{INACTIVE}' "
            f"AND {unregistered_sql}"
        ).fetchone()[0]
    )
    keep = f"NOT coalesce({unregistered_sql}, false)" if drop_unregistered else "true"
    con.execute(f"""
      CREATE OR REPLACE TEMP TABLE out2 AS
      SELECT *,
        CASE WHEN id_kind = 'reg' THEN lk_make_id('reg', registry || ':' || reg_key)
             WHEN id_kind = 'lei' THEN lk_make_id('lei', lei)
             WHEN id_kind = 'cik' THEN lk_make_id('cik', cik)
             WHEN id_kind = 'wd' THEN lk_make_id('wd', wikidata_id)
             WHEN id_kind = 'domain' THEN lk_make_id('domain', domain)
             ELSE lk_make_id('name', coalesce(nullif(lk_name_key(name), ''), lower(name))
                                     || '|' || {lit(cc)}) END AS id
      FROM (
        SELECT * REPLACE (coalesce(name, legal_name) AS name,
                          coalesce(size_range, {_size_bucket_sql("employees")}) AS size_range),
          CASE
          WHEN registry IS NOT NULL AND company_number IS NOT NULL THEN 'reg'
          WHEN lei IS NOT NULL THEN 'lei' WHEN cik IS NOT NULL THEN 'cik'
          WHEN wikidata_id IS NOT NULL THEN 'wd' WHEN domain IS NOT NULL THEN 'domain'
          ELSE 'name' END AS id_kind
        FROM out WHERE status IS DISTINCT FROM '{INACTIVE}' AND {keep}
          AND coalesce(name, legal_name) IS NOT NULL
      )
    """)
    dropped_unreg = unregistered if drop_unregistered else 0
    out2_rows = int(con.execute("SELECT count(*) FROM out2").fetchone()[0])
    no_name = clusters - inactive - dropped_unreg - out2_rows
    # Ids are unique: two companies can only share a domain or name id; keep the fuller one.
    filled = " + ".join(f"({c} IS NOT NULL)::INT" for c in PACK_COLUMNS if c != "id")
    collisions = con.execute(f"""
      DELETE FROM out2 WHERE cid IN (
        SELECT cid FROM (
          SELECT cid, row_number() OVER (PARTITION BY id ORDER BY {filled} DESC, name, cid) AS rn
          FROM out2 WHERE id IN (SELECT id FROM out2 GROUP BY id HAVING count(*) > 1)
        ) WHERE rn > 1)
    """).fetchone()
    id_collisions = int(collisions[0]) if collisions else 0
    con.execute("DROP TABLE out")

    def counts(sql: str) -> dict[str, int]:
        return {str(k): int(v) for k, v in con.execute(sql).fetchall() if k is not None}

    rows_by_source = counts(
        "SELECT s, count(*) FROM (SELECT unnest(srcs) AS s FROM out2) GROUP BY s ORDER BY s"
    )
    domains_by_source = counts(
        "SELECT web_src, count(*) FROM out2 WHERE domain IS NOT NULL GROUP BY web_src ORDER BY 1"
    )
    domain_via_ids = int(
        con.execute(
            "SELECT count(*) FROM out2 WHERE domain IS NOT NULL AND registry IS NOT NULL "
            "AND web_tier IS DISTINCT FROM 'register'"
        ).fetchone()[0]
    )
    rows = int(con.execute("SELECT count(*) FROM out2").fetchone()[0])
    sources = _sort_sources(rows_by_source)
    stats = {
        **stats_in,
        "clusters": clusters,
        "duplicates_removed": stats_in["input_rows_total"] - clusters,
        "inactive_dropped": inactive,
        "not_in_register": unregistered,
        "not_in_register_dropped": dropped_unreg,
        "no_name_dropped": no_name,
        "id_collisions_dropped": id_collisions,
        "identifiers_cleared": cleared,
        "rows": rows,
        "rows_by_source": rows_by_source,
        "domains_by_source": domains_by_source,
        "domain_via_registry_ids": domain_via_ids,
        "seconds": round(time.monotonic() - t0, 2),
    }
    if rows:
        kv = {
            **kv,
            "country": cc,
            "sources": ",".join(sources),
            "merge_stats": json.dumps(stats, sort_keys=True),
        }
        kv_sql = ", ".join(f"{k}: {lit(v)}" for k, v in kv.items())
        tmp = out_path.with_name(out_path.name + ".tmp")
        con.execute(f"""
          COPY (SELECT {", ".join(PACK_COLUMNS)} FROM out2 ORDER BY name, id)
          TO {lit(str(tmp))} (FORMAT PARQUET, COMPRESSION ZSTD, KV_METADATA {{{kv_sql}}})
        """)
        tmp.replace(out_path)
    con.execute("DROP TABLE out2")
    return stats, sources


# -- attribution ---------------------------------------------------------------------------


def _registered_sources() -> dict:
    try:
        from leadcommons.connectors.bulk import SOURCES, load_all
    except ImportError:  # pragma: no cover - the package is part of leadcommons
        return {}
    try:
        load_all()
    except Exception as exc:  # noqa: BLE001 - a broken source module must not stop the merge
        log.warning("could not import every bulk source module: %s", exc)
    return dict(SOURCES)


def _provenance(con, shards: Sequence[Shard]) -> dict[str, dict[str, str]]:
    """Licence and URL found in each source's sources_json (first shard row)."""
    out: dict[str, dict[str, str]] = {}
    for s in shards:
        if s.source in out:
            continue
        try:
            row = con.execute(
                "SELECT json_extract_string(sources_json, '$[0].licence'), "
                "json_extract_string(sources_json, '$[0].url') "
                f"FROM read_parquet({lit(str(s.path))}) WHERE sources_json IS NOT NULL LIMIT 1"
            ).fetchone()
        except Exception:  # noqa: BLE001 - provenance is best effort
            row = None
        if row:
            out[s.source] = {"licence": row[0] or "", "url": row[1] or ""}
    return out


def attribution_markdown(
    release: str,
    built_at: str,
    countries: Sequence[CountryResult],
    provenance: dict[str, dict[str, str]],
    registered: dict | None = None,
) -> str:
    """ATTRIBUTION.md: every source used, its licence and the attribution it asks for."""
    registered = registered if registered is not None else {}
    per_source: dict[str, dict[str, int]] = {}
    for c in countries:
        for s, n in c.stats.get("rows_by_source", {}).items():
            per_source.setdefault(s, {})[c.country] = n
    lines = [
        "# Data attribution",
        "",
        f"LeadCommons company pack `{release}`, built {built_at} from public, openly licensed "
        "registers. It holds company-level data only, never people.",
        "",
        "Each row's `sources_json` lists the sources its fields were taken from. The licences "
        "below apply to the data taken from each source.",
        "",
        "| Source | Licence | Countries | Rows |",
        "| --- | --- | --- | --- |",
    ]
    details = []
    for name in _sort_sources(per_source):
        src = registered.get(name)
        prov = provenance.get(name, {})
        title = getattr(src, "title", None) or KNOWN_TITLES.get(name, name)
        licence = getattr(src, "licence", None) or prov.get("licence") or "see source"
        licence_url = getattr(src, "licence_url", None) or ""
        homepage = getattr(src, "homepage", None) or prov.get("url") or ""
        sentence = getattr(src, "attribution", "") or ""
        if not sentence:
            if any(k in licence.upper() for k in _ATTRIBUTION_LICENCES):
                sentence = (
                    f"Contains data from {title}"
                    + (f" ({homepage})" if homepage else "")
                    + f", licensed under {licence}"
                    + (f" ({licence_url})." if licence_url else ".")
                )
            else:
                sentence = f"Released under {licence}; no attribution required, credited anyway."
        counts = per_source[name]
        lic = f"[{licence}]({licence_url})" if licence_url else licence
        lines.append(
            f"| {title} (`{name}`) | {lic} | {', '.join(sorted(counts))} | "
            f"{sum(counts.values()):,} |"
        )
        details += [f"### {title}", "", f"- Licence: {lic}"]
        if homepage:
            details.append(f"- Data: {homepage}")
        details += [f"- Attribution: {sentence}", ""]
    lines += ["", "## Sources and required attributions", "", *details]
    lines += [
        "## Countries",
        "",
        "| Country | File | Rows | Sources |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {c.country} | {c.file} | {c.rows:,} | {', '.join(c.sources)} |" for c in countries
    ]
    lines.append("")
    return "\n".join(lines)


# -- entry point ---------------------------------------------------------------------------


def _link_country_stats(con) -> tuple[dict, dict, dict]:
    """Input rows per (country, source), merges per (country, key) and same-source duplicates
    per (country, source), from the link tables."""
    inputs: dict[str, dict[str, int]] = {}
    for cc, src, n in con.execute("""
        SELECT coalesce(c.cc, r.country_n) AS cc, r.src, count(*)
        FROM rk r LEFT JOIN links l USING (rid) LEFT JOIN ccountry c ON c.cid = l.cid
        WHERE coalesce(c.cc, r.country_n) IS NOT NULL GROUP BY ALL
    """).fetchall():
        inputs.setdefault(cc, {})[src] = int(n)
    merges: dict[str, dict[str, int]] = {}
    for cc, how, n in con.execute("""
        SELECT c.cc, coalesce(x.how, 'other'), count(*) FROM (
          SELECT l.cid, l.how, row_number() OVER (PARTITION BY l.cid
            ORDER BY (l.how IS NOT NULL), r.rk_legal, r.natkey) AS rn
          FROM links l JOIN rk r USING (rid)
        ) x JOIN ccountry c USING (cid) WHERE x.rn > 1 AND c.cc IS NOT NULL GROUP BY ALL
    """).fetchall():
        merges.setdefault(cc, {})[how] = int(n)
    dups: dict[str, dict[str, int]] = {}
    for cc, src, n in con.execute("""
        SELECT c.cc, x.src, sum(x.n - 1) FROM (
          SELECT l.cid, r.src, count(*) AS n FROM links l JOIN rk r USING (rid)
          GROUP BY l.cid, r.src HAVING count(*) > 1
        ) x JOIN ccountry c USING (cid) WHERE c.cc IS NOT NULL GROUP BY ALL
    """).fetchall():
        dups.setdefault(cc, {})[src] = int(n)
    return inputs, merges, dups


DEMO_ROWS = int(os.environ.get("LEADCOMMONS_DEMO_ROWS", "250000"))


def _write_demo(out_dir: Path, results: list, now: datetime) -> dict | None:
    """A small all-countries file for the public demo and for app versions before schema 3.

    Old apps take the first companies-*.parquet of the newest release (the name sorts first);
    the hosted demo has a size limit. Companies with a website come first, then larger ones.
    """
    import duckdb

    name = f"companies-0-demo-{now:%Y%m%d}.parquet"
    path = out_dir / name
    files = ", ".join(lit(str(out_dir / c.file)) for c in results)
    con = duckdb.connect()
    try:
        con.execute(
            f"""COPY (
                SELECT * FROM (
                    SELECT * FROM read_parquet([{files}], union_by_name=true)
                    ORDER BY (domain IS NULL), (employees IS NULL), employees DESC, name, id
                    LIMIT {DEMO_ROWS}
                ) ORDER BY name, id
            ) TO {lit(str(path))} (FORMAT PARQUET, COMPRESSION ZSTD)"""
        )
        rows = int(
            con.execute(f"SELECT count(*) FROM read_parquet({lit(str(path))})").fetchone()[0]
        )
    finally:
        con.close()
    log.info("merge: demo file %s, %d rows", name, rows)
    return {"file": name, "rows": rows, "bytes": path.stat().st_size, "sha256": _file_sha256(path)}


def merge_shards(
    shards_dir: Path,
    out_dir: Path,
    *,
    release: str | None = None,
    built_at: datetime | None = None,
    work_dir: Path | None = None,
    memory_limit: str | None = None,
    threads: int | None = None,
    write_quality: bool = True,
    keep_unregistered: bool = False,
) -> MergeResult:
    """Link, dedupe and merge every shard of shards_dir into out_dir (see module docstring)."""
    started = time.monotonic()
    shards_dir, out_dir = Path(shards_dir), Path(out_dir)
    shards = find_shards(shards_dir)
    if not shards:
        raise ValueError(f"no *.parquet shards in {shards_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    now = (built_at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    built = now.isoformat()
    release = release or f"packs-{now:%Y-%m}"
    base = Path(work_dir) if work_dir else out_dir
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".merge-", dir=base))
    log.info("merge: %d shards from %s -> %s (work %s)", len(shards), shards_dir, out_dir, work)
    con = _connect(work, memory_limit, threads)
    try:
        input_rows = load_rows(con, shards)
        st = link(con)
        inputs, merges, dups = _link_country_stats(con)
        dropped = int(
            con.execute("""
              SELECT count(*) FROM rk r LEFT JOIN links l USING (rid)
              LEFT JOIN ccountry c ON c.cid = l.cid WHERE coalesce(c.cc, r.country_n) IS NULL
            """).fetchone()[0]
        )
        if dropped:
            log.warning("merge: %d rows without a country dropped", dropped)
        provenance = _provenance(con, shards)
        present = [
            str(r[0])
            for r in con.execute(
                "SELECT DISTINCT reg_registry FROM rk WHERE tier IN ('register', 'filings') "
                "AND reg_registry IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]

        # One pass over the shards: every row with its cluster and the cluster's country, into
        # a table of the work database. Shards are mostly one country each (GLEIF's is sorted
        # by country), so zone maps on `cc` let each country's queries skip the other rows.
        # (A Parquet PARTITION_BY write spilled heavily and ran 3x longer at 30M rows.)
        t0 = time.monotonic()
        raw = shards_union_sql(con, shards)
        raw_cols = ", ".join(c for c in PACK_COLUMNS if c != "id")
        con.execute("""
          CREATE OR REPLACE TABLE lc AS
          SELECT l.rid, l.cid, l.how, c.cc FROM links l JOIN ccountry c USING (cid)
        """)
        for t in ("rk", "links", "ccountry", "e", "fb"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        con.execute("CHECKPOINT")
        con.execute(f"""
          CREATE OR REPLACE TABLE m AS
            SELECT n.rid, n.src, n.tier, {raw_cols}, n.country_n, n.reg_registry, n.reg_number,
              n.reg_disp,
              n.siren_n, n.cik_n, n.lei_n, n.wd_n, n.domain_n,
              {tier_rank_sql(FIELD_PRIORITY["country"], "n.tier")} AS rk_legal,
              coalesce(n.reg_registry || ':' || n.reg_number, n.lei_n, n.cik_n, n.wd_n,
                       n.domain_n, lower(lk_txt(n.name)), '') || '|' || n.src AS natkey,
              coalesce(l.cid, n.rid) AS cid, l.cid IS NOT NULL AS multi, l.how,
              coalesce(l.cc, n.country_n) AS cc
            FROM {norm_from_sql(raw)} n LEFT JOIN lc l USING (rid)
            WHERE coalesce(l.cc, n.country_n) IS NOT NULL
        """)
        con.execute("DROP TABLE lc")
        con.execute("CHECKPOINT")
        log.info("merge: rows placed by country (%.1f s)", time.monotonic() - t0)

        countries = [
            str(r[0]) for r in con.execute("SELECT DISTINCT cc FROM m ORDER BY 1").fetchall()
        ]
        kv = {
            "schema": str(SCHEMA),
            "release": release,
            "built_at": built,
            "sorted_by": "name,id",
            "link_stats": json.dumps(st.as_dict(), sort_keys=True),
        }
        results: list[CountryResult] = []
        for cc in countries:
            name = f"companies-{cc}-{now:%Y%m%d}.parquet"
            stats_in = {
                "input_rows": dict(sorted(inputs.get(cc, {}).items())),
                "merges": dict(sorted(merges.get(cc, {}).items())),
                "same_source_duplicates": dict(sorted(dups.get(cc, {}).items())),
            }
            stats_in["input_rows_total"] = sum(stats_in["input_rows"].values())
            path = out_dir / name
            stats, sources = _merge_country(
                con,
                cc,
                f"(SELECT * FROM m WHERE cc = {lit(cc)})",
                path,
                kv,
                now.year,
                stats_in,
                present,
                not keep_unregistered,
            )
            if not stats["rows"]:
                log.info("merge: %s has no active company, no file", cc)
                continue
            with_domain = int(
                con.execute(f"SELECT count(domain) FROM read_parquet({lit(str(path))})").fetchone()[
                    0
                ]
            )
            results.append(
                CountryResult(
                    country=cc,
                    file=name,
                    rows=stats["rows"],
                    bytes=path.stat().st_size,
                    sha256=_file_sha256(path),
                    sources=sources,
                    with_domain=with_domain,
                    stats=stats,
                )
            )
            log.info("merge: %s %d rows (%d in, %d merged, %d inactive) %.1f s", cc,
                     stats["rows"], stats["input_rows_total"], stats["duplicates_removed"],
                     stats["inactive_dropped"], stats["seconds"])  # fmt: skip
    finally:
        con.close()
        if os.environ.get("LEADCOMMONS_KEEP_WORK") != "1":
            shutil.rmtree(work, ignore_errors=True)

    demo = _write_demo(out_dir, results, now) if results else None
    written = {c.file for c in results} | ({demo["file"]} if demo else set())
    stale = sorted(p.name for p in out_dir.glob("companies-*.parquet") if p.name not in written)
    if stale:
        log.warning("merge: %s also holds files outside this release: %s", out_dir, stale)
    (out_dir / "ATTRIBUTION.md").write_text(
        attribution_markdown(release, built, results, provenance, _registered_sources()),
        encoding="utf-8",
    )
    manifest = {
        "schema": SCHEMA,
        "release": release,
        "built_at": built,
        "countries": [c.manifest_entry() for c in results],
        "files": {
            "attribution": "ATTRIBUTION.md",
            "quality": "QUALITY.md",
            "quality_json": "quality.json",
            **({"demo": demo["file"]} if demo else {}),
        },
        **({"demo": demo} if demo else {}),
    }
    tmp_manifest = out_dir / "manifest.json.tmp"
    tmp_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tmp_manifest.replace(out_dir / "manifest.json")
    result = MergeResult(
        out_dir=out_dir,
        release=release,
        built_at=built,
        countries=results,
        link=st,
        input_rows=input_rows,
        dropped_no_country=dropped,
        seconds=round(time.monotonic() - started, 2),
    )
    if write_quality:
        from leadcommons.packs.quality import write_quality as _write_quality

        result.quality = _write_quality(out_dir)
        result.seconds = round(time.monotonic() - started, 2)
    log.info("merge: %d rows in %d countries, %.1f s", result.rows, len(results), result.seconds)
    return result


__all__ = [
    "FIELD_GROUPS",
    "FIELD_PRIORITY",
    "SCHEMA",
    "CountryResult",
    "MergeResult",
    "attribution_markdown",
    "merge_shards",
]
