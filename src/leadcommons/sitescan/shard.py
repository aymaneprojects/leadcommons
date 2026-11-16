"""Findings (Parquet from `sitescan run`) -> a bulk-source shard `sitescan.parquet`.

One row per (domain, register number), for registers the packs already use. The merge
(`leadcommons pack merge`) joins the row to the register's company through `registry` +
`company_number` (FR-SIREN also fills `siren`) and attaches `domain`, `website` and `phone` to it
(the "web" tier, see packs/merge.py). The shard has no company name: rows that join nothing are
dropped by the merge.

Ambiguity rule: a domain that shows several different numbers of the same register (a group site
listing its subsidiaries, a franchise network, a web agency's references) says nothing about which
company owns it, so none of its rows is written for that register. They are counted in the stats.

Only company facts are written: domain, the URL that answered, register number, switchboard phone,
the page where the number was read. Never a name of a person.
"""

from __future__ import annotations

import logging
from pathlib import Path

from leadcommons.connectors.bulk import shard_select, write_shard
from leadcommons.packs import bulk as packs_bulk

log = logging.getLogger(__name__)

SOURCE = "sitescan"
LICENCE = "n/a (public web pages, facts only)"
SHARD_NAME = "sitescan.parquet"

# Registers the packs use (registry code -> country). Numbers of other registers are not emitted.
REGISTRY_COUNTRY: dict[str, str] = {
    "FR-SIREN": "FR",
    "GB-CH": "GB",
    "NO-BRREG": "NO",
    "AU-ABN": "AU",
    "CA-CORP": "CA",
    "US-SEC": "US",
}


def build_shard(findings: Path, out: Path) -> dict:
    """Write `sitescan.parquet` into the directory `out`; returns the stats.

    `findings` is a directory of `findings-*.parquet` (also in part-K/ subdirectories) or one file.
    """
    import duckdb

    findings = Path(findings)
    # A directory holds findings-*.parquet, directly or in part-K/ subdirectories (`run --part`).
    pattern = findings / "**" / "findings-*.parquet" if findings.is_dir() else findings
    if findings.is_dir() and not any(findings.glob("**/findings-*.parquet")):
        raise FileNotFoundError(f"no findings-*.parquet in {findings}")
    con = duckdb.connect()
    try:
        packs_bulk.install_macros(con)
        src = packs_bulk.sql_str(str(pattern))
        # Latest finding per domain (a rescan in the same directory replaces the earlier one).
        con.execute(
            f"CREATE TABLE latest AS SELECT * FROM read_parquet({src}) "
            "QUALIFY row_number() OVER (PARTITION BY domain ORDER BY scanned_at DESC) = 1"
        )
        scanned = _count(con, "latest")
        ok = _count(con, "latest WHERE status = 'ok'")
        con.execute(
            """
            CREATE TABLE found AS
            SELECT domain, home_url, evidence_url, phones, scanned_at,
                   i.registry AS registry,
                   CASE WHEN i.registry = 'FR-SIREN' AND length(i.number) = 14
                        THEN left(i.number, 9) ELSE i.number END AS number
            FROM (
              SELECT *, unnest(from_json(ids, '[{"registry":"VARCHAR","number":"VARCHAR"}]')) AS i
              FROM latest WHERE status = 'ok' AND ids IS NOT NULL AND ids <> '[]'
            )
            """
        )
        with_ids = int(con.execute("SELECT count(DISTINCT domain) FROM found").fetchone()[0])
        regs = packs_bulk.sql_in(REGISTRY_COUNTRY)
        con.execute(f"CREATE TABLE usable AS SELECT DISTINCT * FROM found WHERE registry IN {regs}")
        unsupported = int(
            con.execute(
                f"SELECT count(DISTINCT domain) FROM found WHERE registry NOT IN {regs}"
            ).fetchone()[0]
        )
        con.execute(
            "CREATE TABLE ambiguous AS SELECT domain, registry FROM usable "
            "GROUP BY domain, registry HAVING count(DISTINCT number) > 1"
        )
        ambiguous_domains = int(
            con.execute("SELECT count(DISTINCT domain) FROM ambiguous").fetchone()[0]
        )
        con.execute(
            "CREATE TABLE emit AS SELECT u.* FROM usable u "
            "ANTI JOIN ambiguous a ON a.domain = u.domain AND a.registry = u.registry"
        )
        country = packs_bulk.sql_str  # (readability: used below in the CASE)
        case = " ".join(f"WHEN {country(r)} THEN {country(c)}" for r, c in REGISTRY_COUNTRY.items())
        exprs = {
            "id": "lc_company_id(registry, number, NULL, NULL)",
            "domain": "domain",
            "website": "coalesce(home_url, 'https://' || domain || '/')",
            "country": f"CASE registry {case} END",
            "registry": "registry",
            "company_number": "number",
            "siren": "CASE WHEN registry = 'FR-SIREN' THEN number END",
            "phone": "json_extract_string(phones, '$[0]')",
            "sources_json": (
                "to_json([struct_pack(source := 'sitescan', "
                f"licence := {country(LICENCE)}, "
                "url := coalesce(evidence_url, home_url), fetched_at := scanned_at)])"
            ),
        }
        out.mkdir(parents=True, exist_ok=True)
        path = out / SHARD_NAME
        rows = write_shard(con, shard_select(exprs, "emit ORDER BY registry, number, domain"), path)
        by_registry = dict(
            con.execute(
                "SELECT registry, count(*) FROM emit GROUP BY registry ORDER BY registry"
            ).fetchall()
        )
        stats = {
            "file": str(path),
            "rows": rows,
            "domains_scanned": scanned,
            "domains_ok": ok,
            "domains_with_ids": with_ids,
            "domains_ambiguous": ambiguous_domains,
            "domains_with_other_registers": unsupported,
            "domains_emitted": int(
                con.execute("SELECT count(DISTINCT domain) FROM emit").fetchone()[0]
            ),
            "by_registry": by_registry,
        }
    finally:
        con.close()
    log.info("sitescan shard: %s", stats)
    return stats


def _count(con, from_sql: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {from_sql}").fetchone()[0])


__all__ = ["LICENCE", "REGISTRY_COUNTRY", "SHARD_NAME", "SOURCE", "build_shard"]
