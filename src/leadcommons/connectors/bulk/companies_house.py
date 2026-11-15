"""UK Companies House "Free Company Data Product" (basic company data). OGL-UK-3.0.

Download: `BasicCompanyDataAsOneFile-YYYY-MM-DD.zip` (~0.5 GB zipped, ~2.6 GB CSV, ~5.5M live
companies), linked from download.companieshouse.gov.uk/en_output.html; the multi-part
`BasicCompanyData-YYYY-MM-DD-partN_M.zip` files are the fallback. The snapshot is published within
the first working days of each month.

Kept (documented rule): `CompanyStatus = Active` (companies in liquidation, administration,
receivership, voluntary arrangement or with a proposal to strike off are dropped), registered in
the UK (`CountryOfOrigin` United Kingdom or empty: overseas entities are covered by their own
register), with a name and a well-formed company number.

Mapped: name, company number (8 characters, zero-padded), registered office address (lines 1-2,
post town, postcode; region = the UK nation), company category -> legal_form, incorporation date
-> founded_year, first SIC code -> ISIC Rev.4 section (UK SIC 2007 divisions are NACE Rev.2
divisions; 99999 "Dormant Company" and 74990 "Non-trading company" give no industry, 98000
"Residents property management" is real estate), SIC text -> industry_label, registry GB-CH.
Companies are legal persons; officer names are not in this file.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from leadcommons.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from leadcommons.packs import bulk

log = logging.getLogger(__name__)

NAME = "companies_house"
INDEX_URL = "https://download.companieshouse.gov.uk/en_output.html"
BASE_URL = "https://download.companieshouse.gov.uk/"
URL_ENV = "LEADCOMMONS_CH_URL"  # override: one or more URLs / local files, space-separated
ENCODING_ENV = "LEADCOMMONS_CH_ENCODING"  # the file is UTF-8; "latin-1" if that ever changes

ONE_FILE = re.compile(r"BasicCompanyDataAsOneFile-(\d{4}-\d{2}-\d{2})\.zip")
PART_FILE = re.compile(r"BasicCompanyData-(\d{4}-\d{2}-\d{2})-part(\d+)_(\d+)\.zip")

REQUIRED = ("CompanyName", "CompanyNumber", "CompanyStatus", "RegAddress.PostCode")
NATIONS = {
    "ENGLAND": "England",
    "WALES": "Wales",
    "SCOTLAND": "Scotland",
    "NORTHERN IRELAND": "Northern Ireland",
    "ENGLAND AND WALES": "England",
}
UK_ORIGINS = ("", "UNITED KINGDOM", "UK", "GREAT BRITAIN", "ENGLAND", "WALES", "SCOTLAND",
              "NORTHERN IRELAND", "ENGLAND AND WALES")  # fmt: skip


def files_from_index(html: str) -> list[str]:
    """Newest snapshot in the download page: the one-file zip, else every part of it."""
    one = sorted(set(ONE_FILE.findall(html)))
    if one:
        return [BASE_URL + f"BasicCompanyDataAsOneFile-{one[-1]}.zip"]
    parts = PART_FILE.findall(html)
    if not parts:
        return []
    newest = max(d for d, _, _ in parts)
    names = sorted(
        {(int(n), f"BasicCompanyData-{d}-part{n}_{m}.zip") for d, n, m in parts if d == newest}
    )
    return [BASE_URL + name for _, name in names]


def resolve_urls() -> list[str]:
    if override := os.environ.get(URL_ENV, "").strip():
        return override.split()
    urls = files_from_index(bulk.get_text(INDEX_URL))
    if not urls:
        raise RuntimeError(f"no BasicCompanyData file found on {INDEX_URL}")
    return urls


def build(options: BuildOptions) -> ShardStats:
    urls = resolve_urls()
    csvs: list[Path] = []
    for i, url in enumerate(urls, start=1):
        name = Path(url.split("?")[0]).name or f"basic-company-data-{i}.zip"
        raw = bulk.download(url, options.work_dir / name, label=f"{NAME} {name}")
        if raw.suffix.lower() == ".zip" or zipfile_like(raw):
            csvs += bulk.extract(raw, options.work_dir / "csv", r".*\.csv")
            bulk.discard(raw, options.work_dir)
        else:
            csvs.append(raw)
    con = bulk.connect(options.work_dir)
    try:
        return transform(con, csvs, options, url=urls[0] if len(urls) == 1 else INDEX_URL)
    finally:
        con.close()


def zipfile_like(path: Path) -> bool:
    import zipfile

    return zipfile.is_zipfile(path)


def transform(
    con, csvs: list[Path], options: BuildOptions, *, url: str | None = None
) -> ShardStats:
    """Basic company data CSV(s) -> shard. The CSVs are deleted once staged when they are ours."""
    encoding = os.environ.get(ENCODING_ENV, "").strip() or "utf-8"
    src_sql, c = bulk.csv_source(csvs, "ch_rejects", encoding=encoding)
    c.require(*REQUIRED)
    con.execute(f"""
        CREATE OR REPLACE TABLE ch_raw AS SELECT
          lc_clean({c["CompanyName"]}) AS company_name,
          lc_gb_number({c["CompanyNumber"]}) AS number,
          upper(lc_clean({c["CompanyStatus"]})) AS status,
          upper(coalesce(lc_clean({c.get("CountryOfOrigin")}), '')) AS origin,
          lc_clean({c.get("RegAddress.AddressLine1")}) AS line1,
          lc_clean({c.get("RegAddress.AddressLine2")}) AS line2,
          lc_clean({c.get("RegAddress.PostTown")}) AS town,
          upper(lc_clean({c.get("RegAddress.Country")})) AS reg_country,
          upper(lc_clean({c["RegAddress.PostCode"]})) AS postcode,
          lc_clean({c.get("CompanyCategory")}) AS category,
          lc_clean({c.get("IncorporationDate")}) AS incorporated,
          lc_clean({c.get("SICCode.SicText_1")}) AS sic_text
        FROM {src_sql}
    """)
    rejected = bulk.rejected_lines(con, "ch_rejects")
    for path in csvs:
        bulk.discard(path, options.work_dir)
    total = bulk.count(con, "ch_raw")
    log.info("%s: %d companies staged (%d malformed lines skipped)", NAME, total, rejected)
    sic = "regexp_extract(sic_text, '^\\s*([0-9]{4,5})', 1)"
    from_sql = f"""(
      SELECT *, {sic} AS sic,
        CASE {sic} WHEN '99999' THEN NULL WHEN '74990' THEN NULL WHEN '98000' THEN 'L'
             WHEN '' THEN NULL ELSE lc_nace_section({sic}) END AS section,
        NULLIF(trim(regexp_replace(sic_text, '^\\s*[0-9]{{4,5}}\\s*-?\\s*', '')), '') AS sic_label
      FROM ch_raw
      WHERE status = 'ACTIVE' AND origin IN {bulk.sql_in(UK_ORIGINS)}
        AND company_name IS NOT NULL AND number IS NOT NULL
    ) AS ch"""
    nation = " ".join(f"WHEN {bulk.sql_str(k)} THEN {bulk.sql_str(v)}" for k, v in NATIONS.items())
    exprs = {
        "name": "company_name",
        "legal_name": "company_name",
        "country": "'GB'",
        "region": f"coalesce(CASE reg_country {nation} END, "
        "CASE WHEN left(number, 2) IN ('SC', 'SO', 'SL') THEN 'Scotland' "
        "WHEN left(number, 2) IN ('NI', 'NC', 'NL', 'R0') THEN 'Northern Ireland' END)",
        "city": "lc_title(town)",
        "industry": "section",
        "industry_label": "CASE WHEN section IS NOT NULL THEN "
        "coalesce(sic_label, lc_section_label(section)) END",
        "founded_year": "lc_year(incorporated)",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "registry": "'GB-CH'",
        "company_number": "number",
        "status": "'active'",
        "legal_form": "category",
        "address": "NULLIF(concat_ws(', ', line1, line2), '')",
        "postal_code": "postcode",
    }
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        from_sql,
        total=total,
        notes=f"{rejected} malformed CSV lines skipped" if rejected else "",
    )


SOURCE = register(
    BulkSource(
        name=NAME,
        title="UK Companies House (basic company data)",
        licence="OGL-UK-3.0",
        licence_url="https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
        homepage="https://download.companieshouse.gov.uk/en_output.html",
        countries=frozenset({"GB"}),
        build=build,
        attribution="Contains public sector information from Companies House licensed under "
        "the Open Government Licence v3.0.",
    )
)
