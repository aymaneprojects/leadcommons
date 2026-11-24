"""GLEIF golden copy (LEI-CDF 3.1, CSV): every legal entity with an LEI, worldwide. CC0-1.0.

Download: the latest "lei2" full file (`*-gleif-goldencopy-lei2-golden-copy.csv.zip`, ~0.5 GB
zipped, ~3-4 GB of CSV, ~2.9M records), found through the golden copy API. One shard,
`gleif_golden.parquet`, sorted by country.

Kept (documented rule):

- `Entity.EntityStatus = ACTIVE`, and `Registration.RegistrationStatus` in ISSUED, LAPSED,
  PENDING_TRANSFER, PENDING_ARCHIVAL. A LAPSED LEI only means the yearly renewal fee was not paid;
  the entity itself is usually alive, and the record still names its register. RETIRED, MERGED,
  ANNULLED, DUPLICATE, TRANSFERRED and CANCELLED records are dropped.
- Entity categories GENERAL, RESIDENT_GOVERNMENT_ENTITY, INTERNATIONAL_ORGANIZATION (and records
  without a category). Dropped: FUND (investment funds: no staff, nothing to prospect),
  BRANCH (part of a company that has its own record) and SOLE_PROPRIETOR (the name is usually a
  natural person's name; packs hold companies only).

Mapped: legal name, legal address (street, postal code, city, region, country), legal form text
(`OtherLegalForm`; ELF codes are not translated), creation year, LEI. Address: the legal
(registered) address, which is what national registers publish and the merge matches on; for US
entities the headquarters address is used instead, because a US legal address is usually the
registered agent in Delaware or Nevada.

Register links (how the merge joins GLEIF to national registers): the registration authority
code (`Entity.RegistrationAuthority.RegistrationAuthorityID`, or the validation authority when
the registration authority is not one we know) and the entity's number in that register become
`registry` + `company_number`, see RA_REGISTRIES. Codes checked against GLEIF's registration
authorities list as published (Companies House: GLEIF's "LEI linking to registration
authorities" page; the others: GLEIF list entries as shown in LEI records).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pepite.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from pepite.packs import bulk

log = logging.getLogger(__name__)

NAME = "gleif_golden"
LATEST_API = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/latest"
LATEST_CSV = "https://goldencopy.gleif.org/api/v2/golden-copies/publishes/lei2/latest.csv"
URL_ENV = "PEPITE_GLEIF_URL"  # override: URL or local .csv / .csv.zip

KEEP_REGISTRATION = ("ISSUED", "LAPSED", "PENDING_TRANSFER", "PENDING_ARCHIVAL")
DROP_CATEGORIES = ("FUND", "BRANCH", "SOLE_PROPRIETOR")

# GLEIF registration authority code -> (registry, how the entity number is normalised).
RA_REGISTRIES: dict[str, tuple[str, str]] = {
    "RA000189": ("FR-SIREN", "siren"),  # France, INSEE SIRENE (9-digit SIREN)
    "RA000585": ("GB-CH", "gb"),  # UK Companies House, England and Wales
    "RA000586": ("GB-CH", "gb"),  # UK Companies House, Northern Ireland
    "RA000587": ("GB-CH", "gb"),  # UK Companies House, Scotland
    "RA000472": ("NO-BRREG", "digits9"),  # Norway, Brønnøysund: Foretaksregisteret
    "RA000473": ("NO-BRREG", "digits9"),  # Norway, Brønnøysund: Enhetsregisteret
    "RA000013": ("AU-ABN", "abn"),  # Australian Business Register (11-digit ABN)
    "RA000014": ("AU-ABN", "acn"),  # ASIC register of companies: ACN, turned into the ABN
    "RA000072": ("CA-CORP", "ca"),  # Corporations Canada: "728007-6" -> "7280076"
    "RA000463": ("NL-KVK", "digits8"),  # Netherlands, Kamer van Koophandel
    "RA000170": ("DK-CVR", "digits8"),  # Denmark, Det Centrale Virksomhedsregister
}

# Columns read from the golden copy CSV (names as in the LEI-CDF 3.1 CSV export).
COLUMNS = {
    "lei": "LEI",
    "legal_name": "Entity.LegalName",
    "l_line1": "Entity.LegalAddress.FirstAddressLine",
    "l_line2": "Entity.LegalAddress.AdditionalAddressLine.1",
    "l_city": "Entity.LegalAddress.City",
    "l_region": "Entity.LegalAddress.Region",
    "l_country": "Entity.LegalAddress.Country",
    "l_postal": "Entity.LegalAddress.PostalCode",
    "h_line1": "Entity.HeadquartersAddress.FirstAddressLine",
    "h_line2": "Entity.HeadquartersAddress.AdditionalAddressLine.1",
    "h_city": "Entity.HeadquartersAddress.City",
    "h_region": "Entity.HeadquartersAddress.Region",
    "h_country": "Entity.HeadquartersAddress.Country",
    "h_postal": "Entity.HeadquartersAddress.PostalCode",
    "ra_id": "Entity.RegistrationAuthority.RegistrationAuthorityID",
    "ra_entity": "Entity.RegistrationAuthority.RegistrationAuthorityEntityID",
    "va_id": "Registration.ValidationAuthority.ValidationAuthorityID",
    "va_entity": "Registration.ValidationAuthority.ValidationAuthorityEntityID",
    "category": "Entity.EntityCategory",
    "legal_form_code": "Entity.LegalForm.EntityLegalFormCode",
    "legal_form_text": "Entity.LegalForm.OtherLegalForm",
    "entity_status": "Entity.EntityStatus",
    "created": "Entity.EntityCreationDate",
    "reg_status": "Registration.RegistrationStatus",
}
REQUIRED = ("LEI", "Entity.LegalName", "Entity.LegalAddress.Country", "Entity.EntityStatus",
            "Registration.RegistrationStatus")  # fmt: skip

_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def _weighted(digits: str, weights: tuple[int, ...], first_minus_one: bool = False) -> str:
    terms = []
    for i, w in enumerate(weights, start=1):
        digit = f"CAST(substr({digits}, {i}, 1) AS INTEGER)"
        if i == 1 and first_minus_one:
            digit = f"({digit} - 1)"
        terms.append(f"{digit} * {w}")
    return "(" + " + ".join(terms) + ")"


def macro_sql() -> list[str]:
    """Register-number normalisers (ABN check digits, ACN -> ABN) as DuckDB macros."""
    abn_sum = _weighted("d", _ABN_WEIGHTS, first_minus_one=True)
    acn_sum = _weighted("d", _ABN_WEIGHTS[2:])
    return [
        # 11-digit ABN with valid check digits (ATO algorithm: weighted sum divisible by 89)
        f"CREATE OR REPLACE MACRO lc_abn(d) AS CASE WHEN regexp_full_match(d, '[0-9]{{11}}') "
        f"AND {abn_sum} % 89 = 0 THEN d END",
        # A company's ABN is its 9-digit ACN behind two check digits. When the weighted sum is a
        # multiple of 89 two prefixes would be valid (10 and 99), so no ABN is derived then.
        f"CREATE OR REPLACE MACRO lc_acn_abn(d) AS CASE WHEN regexp_full_match(d, '[0-9]{{9}}') "
        f"AND {acn_sum} % 89 <> 0 THEN CAST(89 - {acn_sum} % 89 + 10 AS VARCHAR) || d END",
    ]


def _ra_values(alias: str) -> str:
    rows = ", ".join(
        f"({bulk.sql_str(code)}, {bulk.sql_str(reg)}, {bulk.sql_str(kind)})"
        for code, (reg, kind) in sorted(RA_REGISTRIES.items())
    )
    return f"(VALUES {rows}) AS {alias}(code, registry, kind)"


def number_sql(kind: str, raw: str) -> str:
    """SQL normalising a register number of `kind` (NULL when it does not have the shape)."""
    d = f"lc_digits({raw})"
    return (
        f"CASE {kind} "
        f"WHEN 'siren' THEN CASE WHEN length({d}) = 9 THEN {d} "
        f"WHEN length({d}) = 14 THEN left({d}, 9) END "  # a SIRET starts with the SIREN
        f"WHEN 'gb' THEN lc_gb_number({raw}) "
        f"WHEN 'digits9' THEN CASE WHEN length({d}) = 9 THEN {d} END "
        f"WHEN 'digits8' THEN CASE WHEN length({d}) = 8 THEN {d} END "
        f"WHEN 'abn' THEN lc_abn({d}) "
        f"WHEN 'acn' THEN coalesce(lc_acn_abn({d}), CASE WHEN length({d}) = 9 THEN {d} END) "
        f"WHEN 'ca' THEN CASE WHEN NOT regexp_full_match({d}, '0*') THEN {d} END END"
    )


def resolve_url() -> str:
    """URL of the latest lei2 golden copy CSV zip (override with PEPITE_GLEIF_URL)."""
    import os

    if override := os.environ.get(URL_ENV, "").strip():
        return override
    try:
        data = json.loads(bulk.get_text(LATEST_API))
        url = _find_csv_url(data)
        if url:
            return url
        log.warning("gleif: no lei2 CSV in the golden copy API answer, using %s", LATEST_CSV)
    except Exception as exc:  # noqa: BLE001 - the redirect URL below still works
        log.warning("gleif: golden copy API failed (%s), using %s", exc, LATEST_CSV)
    return LATEST_CSV


def _find_csv_url(data: object) -> str | None:
    """The lei2 full-file CSV URL in a golden copy API answer (data.lei2.full_file.csv.url)."""
    try:
        url = data["data"]["lei2"]["full_file"]["csv"]["url"]  # type: ignore[index]
        if isinstance(url, str) and url:
            return url
    except (KeyError, TypeError):
        pass
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and re.search(r"lei2-golden-copy\.csv\.zip$", node):
            found.append(node)

    walk(data)
    return found[0] if found else None


def build(options: BuildOptions) -> ShardStats:
    url = resolve_url()
    raw = bulk.download(url, options.work_dir / "gleif-lei2-golden-copy.csv.zip", label=NAME)
    if zipfile_like(raw):
        csv_path = bulk.extract(raw, options.work_dir / "csv", r".*\.csv")[0]
        bulk.discard(raw, options.work_dir)
    else:
        csv_path = raw
    con = bulk.connect(options.work_dir)
    try:
        return transform(con, csv_path, options, url=url)
    finally:
        con.close()


def zipfile_like(path: Path) -> bool:
    import zipfile

    return zipfile.is_zipfile(path)


def transform(con, csv_path: Path, options: BuildOptions, *, url: str | None = None) -> ShardStats:
    """Golden copy CSV -> shard (set-based; the CSV is deleted once staged when it is ours)."""
    for stmt in macro_sql():
        con.execute(stmt)
    src_sql, cols = bulk.csv_source(csv_path, "gleif_rejects")
    cols.require(*REQUIRED)
    select = ", ".join(f"{cols.get(name)} AS {alias}" for alias, name in COLUMNS.items())
    con.execute(f"CREATE OR REPLACE TABLE gleif_raw AS SELECT {select} FROM {src_sql}")
    rejected = bulk.rejected_lines(con, "gleif_rejects")
    bulk.discard(csv_path, options.work_dir)
    total = bulk.count(con, "gleif_raw")
    log.info("%s: %d records staged (%d malformed lines skipped)", NAME, total, rejected)

    keep_reg = bulk.sql_in(KEEP_REGISTRATION)
    drop_cat = bulk.sql_in(DROP_CATEGORIES)
    hq = (
        "(upper(l_country) = 'US' AND upper(h_country) = 'US' AND lc_clean(h_city) IS NOT NULL) "
        "OR (lc_clean(l_city) IS NULL AND lc_clean(h_city) IS NOT NULL "
        "AND upper(h_country) = upper(l_country))"
    )
    base = f"""(
      SELECT g.*,
        CASE WHEN {hq} THEN true ELSE false END AS use_hq,
        coalesce(r1.registry, r2.registry) AS reg,
        CASE WHEN r1.registry IS NOT NULL THEN {number_sql("r1.kind", "g.ra_entity")}
             WHEN r2.registry IS NOT NULL THEN {number_sql("r2.kind", "g.va_entity")} END AS num,
        coalesce(r1.kind, r2.kind) AS kind
      FROM gleif_raw g
      LEFT JOIN {_ra_values("r1")} ON r1.code = upper(trim(g.ra_id))
      LEFT JOIN {_ra_values("r2")} ON r2.code = upper(trim(g.va_id))
      WHERE upper(trim(g.entity_status)) = 'ACTIVE'
        AND upper(trim(g.reg_status)) IN {keep_reg}
        AND coalesce(upper(trim(g.category)), '') NOT IN {drop_cat}
        AND regexp_full_match(upper(trim(g.lei)), '[A-Z0-9]{{20}}')
        AND lc_clean(g.legal_name) IS NOT NULL
        AND regexp_full_match(upper(trim(g.l_country)), '[A-Z]{{2}}')
        AND {bulk.country_clause(options, "upper(trim(g.l_country))")}
    ) AS b"""
    # An ACN whose ABN check digits are ambiguous stays an ACN (registry AU-ACN).
    registry = (
        "CASE WHEN reg = 'AU-ABN' AND kind = 'acn' AND length(num) = 9 THEN 'AU-ACN' "
        "WHEN num IS NOT NULL THEN reg END"
    )
    pick = "CASE WHEN use_hq THEN {h} ELSE {l} END"
    exprs = {
        "name": "lc_clean(legal_name)",
        "legal_name": "lc_clean(legal_name)",
        "country": "upper(trim(l_country))",
        "region": "regexp_replace(lc_clean("
        + pick.format(h="h_region", l="l_region")
        + "), '^[A-Za-z]{2}-', '')",
        "city": "lc_title(lc_clean(" + pick.format(h="h_city", l="l_city") + "))",
        "founded_year": "lc_year(created)",
        "lei": "upper(trim(lei))",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "siren": "CASE WHEN reg = 'FR-SIREN' THEN num END",
        "registry": registry,
        "company_number": f"CASE WHEN ({registry}) IS NOT NULL THEN num END",
        "status": "'active'",
        "legal_form": "lc_clean(legal_form_text)",
        "address": "NULLIF(concat_ws(', ', "
        + ", ".join(
            "lc_clean(" + pick.format(h=h, l=lf) + ")"
            for h, lf in (("h_line1", "l_line1"), ("h_line2", "l_line2"))
        )
        + "), '')",
        "postal_code": "upper(lc_clean(" + pick.format(h="h_postal", l="l_postal") + "))",
    }
    notes = f"{rejected} malformed CSV lines skipped" if rejected else ""
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        base,
        total=total,
        order="country, name",
        notes=notes,
    )


SOURCE = register(
    BulkSource(
        name=NAME,
        title="GLEIF golden copy (LEI records)",
        licence="CC0-1.0",
        licence_url="https://creativecommons.org/publicdomain/zero/1.0/",
        homepage="https://www.gleif.org/en/lei-data/gleif-golden-copy",
        countries=None,
        build=build,
        attribution="LEI data: Global Legal Entity Identifier Foundation (GLEIF), CC0 1.0.",
    )
)
