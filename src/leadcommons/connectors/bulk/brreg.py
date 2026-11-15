"""Norway: Enhetsregisteret bulk download from Brønnøysundregistrene. NLOD-2.0.

Download: `GET https://data.brreg.no/enhetsregisteret/api/enheter/lastned` with
`Accept: application/vnd.brreg.enhetsregisteret.enhet.v2+gzip;charset=UTF-8` returns every main
entity ("enhet", ~1.2M) as one gzipped JSON array (~150-200 MB gzipped, ~1.5 GB of JSON), the
same objects as the `/enheter/{orgnr}` API: `organisasjonsnummer`, `navn`, `organisasjonsform
{kode, beskrivelse}`, `hjemmeside`, `forretningsadresse` / `postadresse {adresse[], postnummer,
poststed, kommune, kommunenummer, land, landkode}`, `naeringskode1 {kode, beskrivelse}`,
`antallAnsatte`, `stiftelsesdato`, `registreringsdatoEnhetsregisteret`, `konkurs`,
`underAvvikling`, `underTvangsavviklingEllerTvangsopplosning`, `slettedato`. Updated nightly.
Sub-units (underenheter, the establishments) are a separate file and not used.

Kept (documented rule):

- registered and active: not deleted (`slettedato`), not bankrupt (`konkurs`), not being wound up
  voluntarily or by force (`underAvvikling`, `underTvangsavviklingEllerTvangsopplosning`);
- estates are dropped: bankruptcy estates (KBO) and other estates (BO, which include the estates
  of deceased persons);
- sole proprietorships (ENK) are natural persons in business, and their name must contain the
  owner's surname. An ENK is kept only when its name is a trade name: it has a trade word ("BYGG",
  "RØRLEGGER", "CONSULTING"...) and no given name or initial (`_rules.trade_name_sql`), so
  "NORDMANN RØRLEGGERSERVICE" is kept while "OLA NORDMANN" and "FOTOGRAF KARI NORDMANN" are not;
- any other entity whose name looks like persons' names (a given name or an initial and no trade
  word, e.g. a co-ownership "PER HANSEN OG KARI HANSEN") is dropped too.

Mapped: organisation number -> registry NO-BRREG + company_number; name; business address (else
postal address): street lines, postal code, post town -> city, municipality number -> county
(region); NACE (SN2007) code -> ISIC section; employees -> employees + size_range (0 -> empty);
founding date (`stiftelsesdato`), else the registration date when after 1995 (the register opened
in 1995, so earlier registration dates are not founding dates) -> founded_year; organisational
form -> legal_form (as the register writes it, e.g. "Aksjeselskap"); `hjemmeside` -> website and
domain (a profile page on a shared host such as facebook.com gives no domain).
"""

from __future__ import annotations

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

NAME = "brreg"
DOWNLOAD_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/lastned"
ACCEPT = "application/vnd.brreg.enhetsregisteret.enhet.v2+gzip;charset=UTF-8"
URL_ENV = "LEADCOMMONS_BRREG_URL"  # override: URL or local .json / .json.gz file

_ADDRESS = (
    "STRUCT(adresse VARCHAR[], postnummer VARCHAR, poststed VARCHAR, kommune VARCHAR, "
    "kommunenummer VARCHAR, land VARCHAR, landkode VARCHAR)"
)
JSON_COLUMNS: dict[str, str] = {
    "organisasjonsnummer": "VARCHAR",
    "navn": "VARCHAR",
    "organisasjonsform": "STRUCT(kode VARCHAR, beskrivelse VARCHAR)",
    "hjemmeside": "VARCHAR",
    "forretningsadresse": _ADDRESS,
    "postadresse": _ADDRESS,
    "naeringskode1": "STRUCT(kode VARCHAR, beskrivelse VARCHAR)",
    "antallAnsatte": "BIGINT",
    "stiftelsesdato": "VARCHAR",
    "registreringsdatoEnhetsregisteret": "VARCHAR",
    "konkurs": "BOOLEAN",
    "underAvvikling": "BOOLEAN",
    "underTvangsavviklingEllerTvangsopplosning": "BOOLEAN",
    "slettedato": "VARCHAR",
}

ESTATE_FORMS = ("KBO", "BO")
# County (fylke) from the first two digits of the municipality number: 2024 numbers, then the
# 2020-2023 ones (Viken, Vestfold og Telemark, Troms og Finnmark) still found in older records.
COUNTIES: dict[str, str] = {
    "03": "Oslo",
    "11": "Rogaland",
    "15": "Møre og Romsdal",
    "18": "Nordland",
    "21": "Svalbard",
    "31": "Østfold",
    "32": "Akershus",
    "33": "Buskerud",
    "34": "Innlandet",
    "39": "Vestfold",
    "40": "Telemark",
    "42": "Agder",
    "46": "Vestland",
    "50": "Trøndelag",
    "55": "Troms",
    "56": "Finnmark",
    "30": "Viken",
    "38": "Vestfold og Telemark",
    "54": "Troms og Finnmark",
}


def resolve_url() -> str:
    return os.environ.get(URL_ENV, "").strip() or DOWNLOAD_URL


def build(options: BuildOptions) -> ShardStats:
    url = resolve_url()
    raw = bulk.download(
        url,
        options.work_dir / "enheter_alle.json.gz",
        headers={"Accept": ACCEPT},
        user_agent=rules.user_agent(options.contact),
        label=f"{NAME} enheter",
    )
    con = bulk.connect(options.work_dir)
    try:
        return transform(con, raw, options, url=url if bulk.is_url(url) else DOWNLOAD_URL)
    finally:
        con.close()


def is_gzip(path: Path) -> bool:
    with Path(path).open("rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def json_source(path: Path) -> str:
    """read_json(...) over the download: a JSON array of entities, gzipped or not."""
    columns = "{" + ", ".join(f"{bulk.sql_str(k)}: {bulk.sql_str(v)}" for k, v in
                              JSON_COLUMNS.items()) + "}"  # fmt: skip
    compression = "gzip" if is_gzip(path) else "none"
    return (
        f"read_json({bulk.sql_str(str(path))}, format='array', compression='{compression}', "
        f"columns={columns}, maximum_object_size=16777216)"
    )


def transform(con, raw: Path, options: BuildOptions, *, url: str | None = None) -> ShardStats:
    """Enhetsregisteret JSON -> shard (brreg.parquet)."""
    if options.limit:  # a test run reads the head of the file only
        source_sql = f"(SELECT * FROM {json_source(raw)} LIMIT {int(options.limit) * 4 + 1000})"
    else:
        source_sql = json_source(raw)
    has_business_address = (
        "(forretningsadresse.postnummer IS NOT NULL OR forretningsadresse.poststed IS NOT NULL "
        "OR len(forretningsadresse.adresse) > 0)"
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE no_raw AS SELECT
          lc_digits(organisasjonsnummer) AS orgnr,
          lc_clean(navn) AS navn,
          upper(lc_clean(organisasjonsform.kode)) AS form,
          lc_clean(organisasjonsform.beskrivelse) AS form_text,
          lc_clean(hjemmeside) AS homepage,
          CASE WHEN {has_business_address} THEN forretningsadresse ELSE postadresse END AS addr,
          lc_clean(naeringskode1.kode) AS nace,
          antallAnsatte AS ansatte,
          stiftelsesdato,
          registreringsdatoEnhetsregisteret AS regdato,
          coalesce(konkurs, false) AS konkurs,
          coalesce(underAvvikling, false)
            OR coalesce(underTvangsavviklingEllerTvangsopplosning, false) AS avvikling,
          lc_clean(slettedato) AS slettedato
        FROM {source_sql}
    """)
    total = bulk.count(con, "no_raw")
    log.info("%s: %d entities staged", NAME, total)
    con.execute(f"""
        CREATE OR REPLACE TABLE no_stage AS SELECT *,
          CASE
            WHEN orgnr IS NULL OR length(orgnr) <> 9 OR navn IS NULL THEN 'no number or name'
            WHEN slettedato IS NOT NULL THEN 'deleted'
            WHEN konkurs THEN 'bankrupt'
            WHEN avvikling THEN 'being wound up'
            WHEN form IN {bulk.sql_in(ESTATE_FORMS)} THEN 'estate'
            WHEN form = 'ENK' AND NOT {rules.trade_name_sql("navn")}
              THEN 'sole proprietorship named after a person'
            WHEN form <> 'ENK' AND {rules.personal_name_sql("navn")} THEN 'named after persons'
          END AS dropped,
          coalesce(addr.landkode, 'NO') = 'NO' AS in_norway
        FROM no_raw
    """)
    con.execute("DROP TABLE no_raw")
    notes = drop_notes(con, "no_stage")
    from_sql = "(SELECT * FROM no_stage WHERE dropped IS NULL) AS no_kept"
    county = rules.map_case_sql("left(addr.kommunenummer, 2)", COUNTIES)
    reg_year = "CASE WHEN lc_year(regdato) >= 1996 THEN lc_year(regdato) END"
    exprs = {
        "name": "navn",
        "legal_name": "navn",
        "domain": rules.domain_sql("homepage"),
        "country": "'NO'",
        "region": f"CASE WHEN in_norway THEN {county} END",
        "city": "CASE WHEN in_norway THEN lc_title(lc_clean(addr.poststed)) END",
        "industry": "lc_nace_section(nace)",
        "industry_label": "lc_section_label(lc_nace_section(nace))",
        "size_range": rules.size_range_sql("ansatte"),
        "founded_year": f"coalesce(lc_year(stiftelsesdato), {reg_year})",
        "website": rules.website_sql("homepage"),
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "registry": "'NO-BRREG'",
        "company_number": "orgnr",
        "status": "'active'",
        "legal_form": "coalesce(form_text, form)",
        "address": "CASE WHEN in_norway THEN NULLIF(array_to_string(list_transform(addr.adresse, "
        f"{bulk.list_lambda('a', 'lc_clean(a)')}), ', '), '') END",
        "postal_code": "CASE WHEN in_norway THEN lc_clean(addr.postnummer) END",
        "employees": "CASE WHEN ansatte > 0 THEN ansatte END",
    }
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        from_sql,
        total=total,
        best_first="(domain IS NOT NULL) DESC, employees DESC NULLS LAST, company_number",
        order="company_number",
        notes=notes,
    )


def drop_notes(con, table: str) -> str:
    """'dropped: 12 bankrupt, 3 estate' from the `dropped` column of a staging table."""
    counts = con.execute(
        f"SELECT dropped, count(*) FROM {table} WHERE dropped IS NOT NULL "
        "GROUP BY dropped ORDER BY count(*) DESC, dropped"
    ).fetchall()
    if not counts:
        return ""
    return "dropped: " + ", ".join(f"{n:,} {why}" for why, n in counts)


SOURCE = register(
    BulkSource(
        name=NAME,
        title="Brønnøysundregistrene, Enhetsregisteret (Norway)",
        licence="NLOD-2.0",
        licence_url="https://data.norge.no/nlod/en/2.0",
        homepage="https://data.brreg.no/enhetsregisteret/api/dokumentasjon/en/index.html",
        countries=frozenset({"NO"}),
        build=build,
        attribution="Contains data under the Norwegian licence for Open Government data (NLOD) "
        "distributed by Brønnøysundregistrene (The Brønnøysund Register Centre).",
    )
)
