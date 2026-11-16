"""SEC EDGAR bulk submissions (`submissions.zip`): US-registered filers. Public domain.

Download: https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip (rebuilt
nightly, ~1.5 GB, one `CIK##########.json` per filer, people included). The SEC asks every
automated client to declare a contact: the User-Agent carries PEPITE_CONTACT_EMAIL (see
`pepite.connectors.edgar.edgar_user_agent`) and the source refuses to run without it.

The zip is read in batches: a batch of JSON files is extracted, DuckDB reads the whole batch
(`read_json`, only the fields we need) into a staging table, and the batch is deleted. Overflow
files (`CIK##########-submissions-NNN.json`, older filings) are skipped.

Kept (documented rule): operating companies, i.e. `entityType = operating` (this drops people
who file insider forms, funds and other non-operating filers), that have at least one ticker or
filed a periodic report (10-K, 10-Q, 20-F, 40-F, their amendments and transition reports) in
the last two years; and whose business address (mailing address when the business address is
empty) is in the US or in a country we can name from EDGAR's state/country code.

Mapped: name, CIK (zero-padded to 10 digits) in `cik` and as registry US-SEC + company_number,
business address (street, ZIP, city, state as region), SIC -> ISIC section, SIC description ->
industry_label, website and its domain, business phone (E.164 for North American numbers and
numbers written with a country code), LEI when the filer gives one.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import zipfile
from datetime import date, timedelta
from pathlib import Path

from pepite.connectors.base import USER_AGENT
from pepite.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from pepite.connectors.edgar import CONTACT_ENV, US_STATES, edgar_user_agent
from pepite.packs import bulk

log = logging.getLogger(__name__)

NAME = "edgar_bulk"
SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"
URL_ENV = "PEPITE_EDGAR_URL"  # override: URL or local submissions.zip
MEMBER = re.compile(r"CIK\d{10}\.json")
BATCH_FILES = 20000
RECENT_DAYS = 730
PERIODIC_FORMS = ("10-K", "10-K/A", "10-KT", "10-KT/A", "10-Q", "10-Q/A", "10-QT", "10-QT/A",
                  "20-F", "20-F/A", "40-F", "40-F/A")  # fmt: skip

# EDGAR state/country codes for places outside the 50 states (Canadian provinces first).
CA_PROVINCES = {
    "A0": "AB", "A1": "BC", "A2": "MB", "A3": "NB", "A4": "NL", "A5": "NS", "A6": "ON",
    "A7": "PE", "A8": "QC", "A9": "SK", "B0": "YT",
}  # fmt: skip
EDGAR_COUNTRIES = {
    **dict.fromkeys(CA_PROVINCES, "CA"),
    "X0": "GB", "K3": "HK", "F4": "CN", "L3": "IL", "D0": "BM", "E9": "KY", "D8": "VG",
    "M0": "JP", "I0": "FR", "2M": "DE", "P7": "NL", "V8": "CH", "L2": "IE", "U0": "SG",
    "C3": "AU", "D5": "BR", "O5": "MX", "K7": "IN", "M5": "KR", "F5": "TW", "V7": "SE",
    "G7": "DK", "Q8": "NO", "H9": "FI", "L6": "IT", "U3": "ES", "C9": "BE", "N4": "LU",
    "C4": "AT", "T3": "ZA", "Q2": "NZ", "F3": "CL", "C1": "AR", "Y9": "JE", "Y7": "GG",
    "Y8": "IM", "G4": "CY", "J3": "GR", "O1": "MT", "R1": "PA", "C5": "BS",
}  # fmt: skip

_ADDRESS = (
    "STRUCT(street1 VARCHAR, street2 VARCHAR, city VARCHAR, stateOrCountry VARCHAR, "
    "zipCode VARCHAR, stateOrCountryDescription VARCHAR)"
)
JSON_COLUMNS = {
    "cik": "VARCHAR",
    "entityType": "VARCHAR",
    "sic": "VARCHAR",
    "sicDescription": "VARCHAR",
    "name": "VARCHAR",
    "tickers": "VARCHAR[]",
    "lei": "VARCHAR",
    "website": "VARCHAR",
    "phone": "VARCHAR",
    "addresses": f"STRUCT(mailing {_ADDRESS}, business {_ADDRESS})",
    "filings": "STRUCT(recent STRUCT(form VARCHAR[], filingDate VARCHAR[]))",
}


def contact_user_agent(options: BuildOptions) -> str:
    """User-Agent with the declared contact; refuses to go on without one (SEC fair access)."""
    env = os.environ.get(CONTACT_ENV, "").strip()
    contact = (options.contact or env).strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact):
        raise RuntimeError(
            f"SEC EDGAR asks every automated client to declare a contact email: set {CONTACT_ENV} "
            "(for example your GitHub no-reply address) before fetching edgar_bulk."
        )
    return edgar_user_agent() if contact == env else f"{USER_AGENT} contact:{contact}"


def build(options: BuildOptions) -> ShardStats:
    user_agent = contact_user_agent(options)
    url = os.environ.get(URL_ENV, "").strip() or SUBMISSIONS_URL
    raw = bulk.download(url, options.work_dir / "submissions.zip", user_agent=user_agent,
                        label=NAME)  # fmt: skip
    con = bulk.connect(options.work_dir)
    try:
        total = stage_zip(con, raw, options.work_dir)
        bulk.discard(raw, options.work_dir)
        return write(con, options, total, url=url)
    finally:
        con.close()


def _columns_sql() -> str:
    return (
        "{"
        + ", ".join(f"{bulk.sql_str(k)}: {bulk.sql_str(v)}" for k, v in JSON_COLUMNS.items())
        + "}"
    )


def stage_zip(con, zip_path: Path, work_dir: Path, batch_files: int = BATCH_FILES) -> int:
    """Read every CIK##########.json of the zip into table edgar_sub (operating filers only).

    Returns the number of filer files read.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE edgar_sub (
          cik VARCHAR, name VARCHAR, sic VARCHAR, sic_label VARCHAR, n_tickers INTEGER,
          lei VARCHAR, website VARCHAR, phone VARCHAR,
          business {_ADDRESS}, mailing {_ADDRESS}, last_periodic VARCHAR)
    """)
    batch_dir = work_dir / "json-batch"
    periodic = bulk.sql_in(PERIODIC_FORMS)
    pattern = bulk.sql_str(str(batch_dir / "*.json"))
    has_text = bulk.list_lambda("t", "lc_clean(t) IS NOT NULL")
    insert = f"""
        INSERT INTO edgar_sub SELECT
          lpad(coalesce(NULLIF(lc_digits(j.cik), ''),
                        regexp_extract(j.filename, 'CIK([0-9]{{10}})', 1)), 10, '0'),
          lc_clean(j.name),
          lc_clean(j.sic),
          lc_clean(j.sicDescription),
          coalesce(len(list_filter(j.tickers, {has_text})), 0),
          upper(lc_clean(j.lei)),
          lc_clean(j.website),
          lc_clean(j.phone),
          j.addresses.business,
          j.addresses.mailing,
          (SELECT max(d) FROM (SELECT unnest(j.filings.recent.form) AS f,
                                      unnest(j.filings.recent.filingDate) AS d)
           WHERE f IN {periodic})
        FROM read_json({pattern}, columns={_columns_sql()}, format='unstructured',
                       maximum_object_size=268435456, filename=true) AS j
        WHERE lower(trim(j.entityType)) = 'operating'
    """
    files = 0
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.infolist() if MEMBER.fullmatch(Path(m.filename).name)]
        log.info("%s: %d filer files in %s (%d entries in all)", NAME, len(members), zip_path.name,
                 len(zf.infolist()))  # fmt: skip
        for start in range(0, len(members), batch_files):
            shutil.rmtree(batch_dir, ignore_errors=True)
            batch_dir.mkdir(parents=True)
            for m in members[start : start + batch_files]:
                with zf.open(m) as src, (batch_dir / Path(m.filename).name).open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            con.execute(insert)
            files += min(batch_files, len(members) - start)
            log.info("%s: %d / %d filer files read, %d operating companies so far", NAME, files,
                     len(members), bulk.count(con, "edgar_sub"))  # fmt: skip
    shutil.rmtree(batch_dir, ignore_errors=True)
    return files


def write(
    con, options: BuildOptions, total: int, *, url: str | None = None, today: date | None = None
) -> ShardStats:
    cutoff = ((today or date.today()) - timedelta(days=RECENT_DAYS)).isoformat()
    us = bulk.sql_in(US_STATES)
    countries = " ".join(
        f"WHEN {bulk.sql_str(k)} THEN {bulk.sql_str(v)}" for k, v in EDGAR_COUNTRIES.items()
    )
    provinces = " ".join(
        f"WHEN {bulk.sql_str(k)} THEN {bulk.sql_str(v)}" for k, v in CA_PROVINCES.items()
    )
    base = f"""(
      SELECT *, upper(lc_clean(addr.stateOrCountry)) AS state,
        CASE WHEN upper(lc_clean(addr.stateOrCountry)) IN {us} THEN 'US'
             ELSE CASE upper(lc_clean(addr.stateOrCountry)) {countries} END END AS country_code
      FROM (SELECT *, CASE WHEN lc_clean(business.street1) IS NOT NULL
                            OR lc_clean(business.city) IS NOT NULL THEN business
                           ELSE mailing END AS addr
            FROM edgar_sub
            WHERE name IS NOT NULL AND cik <> '0000000000'
              AND (n_tickers > 0 OR last_periodic >= {bulk.sql_str(cutoff)}))
    ) AS e"""
    from_sql = f"{base} WHERE country_code IS NOT NULL"
    unmapped = con.execute(
        f"SELECT state, count(*) AS n FROM {base} WHERE country_code IS NULL "
        "GROUP BY state ORDER BY n DESC LIMIT 15"
    ).fetchall()
    if unmapped:
        log.info("%s: kept filers dropped for an unmapped EDGAR state/country code: %s", NAME,
                 ", ".join(f"{code or '(none)'}={n}" for code, n in unmapped))  # fmt: skip
    phone = (
        "CASE WHEN country_code IN ('US', 'CA') THEN lc_phone_nanp(phone) "
        "WHEN starts_with(trim(phone), '+') AND length(lc_digits(phone)) BETWEEN 8 AND 15 "
        "THEN '+' || lc_digits(phone) END"
    )
    website = (
        "CASE WHEN website IS NULL THEN NULL "
        "WHEN regexp_matches(website, '^[a-zA-Z][a-zA-Z0-9+.-]*://') THEN website "
        "ELSE 'http://' || website END"
    )
    exprs = {
        "name": "name",
        "legal_name": "name",
        "domain": "lc_domain(website)",
        "country": "country_code",
        "region": f"CASE WHEN country_code = 'US' THEN state ELSE CASE state {provinces} END END",
        "city": "lc_title(lc_clean(addr.city))",
        "industry": "lc_sic_section(sic)",
        "industry_label": "sic_label",
        "lei": "CASE WHEN regexp_full_match(lei, '[A-Z0-9]{20}') THEN lei END",
        "website": f"CASE WHEN lc_domain(website) IS NOT NULL THEN {website} END",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "cik": "cik",
        "registry": "'US-SEC'",
        "company_number": "cik",
        "status": "'active'",
        "address": "NULLIF(concat_ws(', ', lc_clean(addr.street1), lc_clean(addr.street2)), '')",
        "postal_code": "upper(lc_clean(addr.zipCode))",
        "phone": phone,
    }
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        from_sql,
        total=total,
        order="country, cik",
    )


SOURCE = register(
    BulkSource(
        name=NAME,
        title="SEC EDGAR bulk submissions (US filers)",
        licence="public-domain",
        licence_url="https://www.sec.gov/privacy#dissemination",
        homepage="https://www.sec.gov/edgar/sec-api-documentation",
        countries=None,  # mostly US; foreign private issuers land in their own country
        build=build,
        attribution="SEC EDGAR data: U.S. Securities and Exchange Commission (public domain).",
    )
)
