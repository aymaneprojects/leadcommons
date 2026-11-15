"""Canada: Corporations Canada "Federal Corporations" dataset (open.canada.ca). OGL-Canada-2.0.

Download: the open.canada.ca dataset 0032ce54-c5dd-4b66-99a0-320a7b5e99f2 (CKAN API
`package_show`). Since April 2026 it is split in four CSV files, each in English and French:
active / inactive x CBCA (business corporations) / other federal acts (not-for-profit
corporations, co-operatives, boards of trade), served from a CloudFront host and refreshed daily,
e.g. `corporations-active-cbca-en.csv` (~100 MB, ~645,000 rows). The English *active* files are
read; the inactive ones are not downloaded. Header (18 columns, verified on the CBCA file):

    Corporation number, Business number (BN), Corporate name - form 1, Corporate name - form 2,
    Governing legislation, Status, Status Detail, Anniversary date, Year of last annual filing,
    Date of last annual meeting, Street, Street 2, City/town, Province/territory, Country,
    Postal code, Minimum number of directors, Maximum number of directors

Each file is read with its own header (columns are looked up by name, so a file with fewer or
reordered columns still loads).

Kept (documented rule): `Status` = Active, and not on the way out: a `Status Detail` mentioning a
dissolution ("Intent to dissolve filed", "Dissolution pending (non-compliance)") or a revocation
drops the corporation. Federal corporations created under financial legislation (banks,
insurers, trust companies) and provincial corporations are not in this dataset.

Mapped: corporation number (digits only, "1112588-5" -> "11125885") -> registry CA-CORP +
company_number; name: the English form when the corporation has both an English and a French
name (form 1 is used unless it reads French, e.g. "Ltée", "Limitée", "Société", and form 2 does
not), else the only form; registered office street lines, city, province -> region, postal code
("K1A0A1" -> "K1A 0A1"); anniversary date (date of incorporation, amalgamation or continuance)
-> founded_year; governing legislation -> legal_form. The files carry director *counts* only, no
director names.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from leadcommons.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from leadcommons.connectors.bulk import _rules as rules
from leadcommons.packs import bulk

log = logging.getLogger(__name__)

NAME = "canada_corps"
DATASET_ID = "0032ce54-c5dd-4b66-99a0-320a7b5e99f2"
DATASET_API = f"https://open.canada.ca/data/api/action/package_show?id={DATASET_ID}"
DATASET_PAGE = f"https://open.canada.ca/data/en/dataset/{DATASET_ID}"
# Verified file (2026); used when the dataset API cannot be read.
FALLBACK_URLS = ("https://d4bf66bykfyaf.cloudfront.net/corporations-active-cbca-en.csv",)
URL_ENV = "LEADCOMMONS_CANADA_CORPS_URL"  # override: URLs or local CSV files, space-separated

REQUIRED = ("Corporation number", "Corporate name - form 1", "Status")
PROVINCES: dict[str, str] = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}
# French legal words: a name with one of them is the French form of the corporate name.
FRENCH_NAME = (
    r"(?i)(LTÉE|LIMITÉE|INCORPORÉE|SOCIÉTÉ|COMPAGNIE|\bLTEE\b|\bLES\b|\bDES\b|\bDU\b|\bDE LA\b)"
)
LEAVING = r"(?i)(dissol|revok|revoc)"


def csv_urls(package: dict) -> list[str]:
    """English CSV files of active corporations in the dataset (CKAN package_show answer)."""
    resources = (package.get("result") or {}).get("resources") or []
    urls = []
    for r in resources:
        url = str(r.get("url", "")) if isinstance(r, dict) else ""
        base = url.split("?")[0].rsplit("/", 1)[-1].lower()
        if base.endswith("-en.csv") and "active" in base and "inactive" not in base:
            urls.append(url)
    # business corporations (CBCA) first, then the other acts
    return sorted(dict.fromkeys(urls), key=lambda u: ("cbca" not in u.lower(), u))


def resolve_urls() -> list[str]:
    if override := os.environ.get(URL_ENV, "").strip():
        return override.split()
    try:
        urls = csv_urls(json.loads(bulk.get_text(DATASET_API, user_agent=rules.user_agent())))
    except (RuntimeError, ValueError) as exc:
        log.warning("%s: dataset API unreadable (%s); using the known CBCA file", NAME, exc)
        urls = []
    if not urls:
        log.warning("%s: no active-corporations CSV listed; falling back to %s", NAME,
                    FALLBACK_URLS[0])  # fmt: skip
        urls = list(FALLBACK_URLS)
    return urls


def build(options: BuildOptions) -> ShardStats:
    urls = resolve_urls()
    paths = []
    for i, url in enumerate(urls, start=1):
        name = Path(url.split("?")[0]).name or f"corporations-{i}.csv"
        paths.append(bulk.download(url, options.work_dir / name, user_agent=rules.user_agent(),
                                   label=f"{NAME} {name}"))  # fmt: skip
    con = bulk.connect(options.work_dir)
    try:
        url = urls[0] if len(urls) == 1 and bulk.is_url(urls[0]) else DATASET_PAGE
        return transform(con, paths, options, url=url)
    finally:
        con.close()


def _select(src_sql: str, c: bulk.Columns) -> str:
    c.require(*REQUIRED)
    return f"""SELECT
          lc_digits({c["Corporation number"]}) AS number,
          lc_clean({c["Corporate name - form 1"]}) AS name1,
          lc_clean({c.get("Corporate name - form 2")}) AS name2,
          lc_clean({c.get("Governing legislation")}) AS act,
          lc_clean({c["Status"]}) AS status,
          lc_clean({c.get("Status Detail")}) AS status_detail,
          lc_clean({c.get("Anniversary date")}) AS anniversary,
          lc_clean({c.get("Street")}) AS street1,
          lc_clean({c.get("Street 2")}) AS street2,
          lc_clean({c.get("City/town")}) AS city,
          upper(lc_clean({c.get("Province/territory")})) AS province,
          upper(lc_clean({c.get("Country")})) AS office_country,
          upper(replace(lc_clean({c.get("Postal code")}), ' ', '')) AS postcode
        FROM {src_sql}"""


def transform(
    con, paths: list[Path], options: BuildOptions, *, url: str | None = None
) -> ShardStats:
    """Active corporations CSV files -> shard (canada_corps.parquet)."""
    selects = []
    rejects = []
    for i, path in enumerate(paths):
        src_sql, cols = bulk.csv_source(path, f"ca_rejects_{i}")
        selects.append(_select(src_sql, cols))
        rejects.append(f"ca_rejects_{i}")
    # whole files even for a test run: its sample is by register-key hash (bulk.SAMPLE_ORDER)
    con.execute(
        "CREATE OR REPLACE TABLE ca_raw AS " + " UNION ALL ".join(f"({s})" for s in selects)
    )
    rejected = sum(bulk.rejected_lines(con, r) for r in rejects)
    for path in paths:
        bulk.discard(path, options.work_dir)
    total = bulk.count(con, "ca_raw")
    log.info("%s: %d corporations staged (%d malformed lines skipped)", NAME, total, rejected)
    french1 = f"regexp_matches(name1, {bulk.sql_str(FRENCH_NAME)})"
    french2 = f"regexp_matches(name2, {bulk.sql_str(FRENCH_NAME)})"
    con.execute(f"""
        CREATE OR REPLACE TABLE ca_stage AS SELECT *,
          CASE WHEN name1 IS NULL THEN name2
               WHEN name2 IS NOT NULL AND {french1} AND NOT {french2} THEN name2
               ELSE name1 END AS display_name,
          CASE
            WHEN number IS NULL OR number = '' OR coalesce(name1, name2) IS NULL
              THEN 'no number or name'
            WHEN upper(coalesce(status, '')) <> 'ACTIVE' THEN 'not active'
            WHEN regexp_matches(coalesce(status_detail, ''), {bulk.sql_str(LEAVING)})
              THEN 'dissolution pending'
          END AS dropped,
          coalesce(office_country, 'CA') IN ('CA', 'CAN', 'CANADA') AS in_canada
        FROM ca_raw
        QUALIFY row_number() OVER (PARTITION BY number ORDER BY anniversary DESC NULLS LAST) = 1
    """)
    con.execute("DROP TABLE ca_raw")
    counts = con.execute(
        "SELECT dropped, count(*) FROM ca_stage WHERE dropped IS NOT NULL "
        "GROUP BY dropped ORDER BY count(*) DESC, dropped"
    ).fetchall()
    notes = "; ".join(
        x
        for x in (
            ("dropped: " + ", ".join(f"{n:,} {why}" for why, n in counts)) if counts else "",
            f"{rejected} malformed CSV lines skipped" if rejected else "",
        )
        if x
    )
    postcode = (
        "CASE WHEN regexp_full_match(postcode, '[A-Z][0-9][A-Z][0-9][A-Z][0-9]') "
        "THEN left(postcode, 3) || ' ' || right(postcode, 3) ELSE postcode END"
    )
    exprs = {
        "name": "display_name",
        "legal_name": "display_name",
        "country": "'CA'",
        "region": "CASE WHEN in_canada THEN "
        + rules.map_case_sql("province", PROVINCES, "province")
        + " END",
        "city": "CASE WHEN in_canada THEN lc_title(city) END",
        "founded_year": "lc_year(anniversary)",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "registry": "'CA-CORP'",
        "company_number": "number",
        "status": "'active'",
        "legal_form": "act",
        "address": "CASE WHEN in_canada THEN NULLIF(concat_ws(', ', street1, street2), '') END",
        "postal_code": f"CASE WHEN in_canada THEN {postcode} END",
    }
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        "(SELECT * FROM ca_stage WHERE dropped IS NULL) AS ca_kept",
        total=total,
        order="company_number",
        notes=notes,
    )


SOURCE = register(
    BulkSource(
        name=NAME,
        title="Corporations Canada, Federal Corporations",
        licence="OGL-Canada-2.0",
        licence_url="https://open.canada.ca/en/open-government-licence-canada",
        homepage=DATASET_PAGE,
        countries=frozenset({"CA"}),
        build=build,
        attribution="Contains information licensed under the Open Government Licence – Canada "
        "(Corporations Canada, Innovation, Science and Economic Development Canada).",
    )
)
