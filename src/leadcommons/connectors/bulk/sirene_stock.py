"""INSEE SIRENE stock files (France): every active legal unit with its head office.
Licence Ouverte / Etalab 2.0.

Download: the monthly stock files INSEE publishes on data.gouv.fr (dataset
5b7ffc618b4c4169d30727e0). The official Parquet files (`StockUniteLegale_utf8.parquet`,
`StockEtablissement_utf8.parquet`) are preferred; the zipped CSVs are the fallback. File URLs come
from the data.gouv.fr API (stable `datasets/r/<id>` links), then from files.data.gouv.fr.

Kept (documented rule):

- Legal units with `etatAdministratifUniteLegale = A` (active) and
  `statutDiffusionUniteLegale = O`. Units with a partial diffusion status (`P`, the person asked
  INSEE to hide some data) are left out; non-diffusible units are not in the stock files at all.
- Companies only. A legal unit whose legal category is "entrepreneur individuel" (1000, a
  natural person) is skipped, unless it has a company denomination (`denominationUniteLegale`,
  else its usual / trade name `denominationUsuelle1UniteLegale`) that does not contain the
  person's surname, usage name or first name. The person's own name is never written.
- Head office (`nicSiegeUniteLegale`, `etablissementSiege`) joined for street address, postal
  code, city and region; left empty when the establishment is not fully diffusible.

Mapped: name (denomination, else usual name), legal name, NAF code -> ISIC Rev.4 section (NAF
rev. 2 and NAF 2025 share NACE division numbers; older nomenclatures are left empty), workforce
band -> size_range, creation date -> founded_year, legal category -> legal_form (INSEE labels),
registry FR-SIREN + company_number = siren + siren.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from leadcommons.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from leadcommons.connectors.sirene import REGIONS, TRANCHE_TO_SIZE
from leadcommons.packs import bulk

log = logging.getLogger(__name__)

NAME = "sirene_stock"
DATASET_API = "https://www.data.gouv.fr/api/1/datasets/5b7ffc618b4c4169d30727e0/"
FALLBACK_BASE = "https://files.data.gouv.fr/insee-sirene/"
FILES = {"ul": "StockUniteLegale_utf8", "etab": "StockEtablissement_utf8"}
URL_ENVS = {"ul": "LEADCOMMONS_SIRENE_UL_URL", "etab": "LEADCOMMONS_SIRENE_ETAB_URL"}

# Département (first digits of the INSEE commune code) -> INSEE region code (REGIONS names).
REGION_OF_DEPT: dict[str, str] = {
    "01": "84", "02": "32", "03": "84", "04": "93", "05": "93", "06": "93", "07": "84",
    "08": "44", "09": "76", "10": "44", "11": "76", "12": "76", "13": "93", "14": "28",
    "15": "84", "16": "75", "17": "75", "18": "24", "19": "75", "2A": "94", "2B": "94",
    "21": "27", "22": "53", "23": "75", "24": "75", "25": "27", "26": "84", "27": "28",
    "28": "24", "29": "53", "30": "76", "31": "76", "32": "76", "33": "75", "34": "76",
    "35": "53", "36": "24", "37": "24", "38": "84", "39": "27", "40": "75", "41": "24",
    "42": "84", "43": "84", "44": "52", "45": "24", "46": "76", "47": "75", "48": "76",
    "49": "52", "50": "28", "51": "44", "52": "44", "53": "52", "54": "44", "55": "44",
    "56": "53", "57": "44", "58": "27", "59": "32", "60": "32", "61": "28", "62": "32",
    "63": "84", "64": "75", "65": "76", "66": "76", "67": "44", "68": "44", "69": "84",
    "70": "27", "71": "27", "72": "52", "73": "84", "74": "84", "75": "11", "76": "28",
    "77": "11", "78": "11", "79": "75", "80": "32", "81": "76", "82": "76", "83": "93",
    "84": "93", "85": "52", "86": "75", "87": "75", "88": "44", "89": "27", "90": "27",
    "91": "11", "92": "11", "93": "11", "94": "11", "95": "11",
    "971": "01", "972": "02", "973": "03", "974": "04", "976": "06",
}  # fmt: skip

# INSEE legal categories: level II labels, with the most common level III codes spelled out.
LEGAL_CATEGORY_L2: dict[str, str] = {
    "10": "Entrepreneur individuel",
    "21": "Indivision",
    "22": "Société créée de fait",
    "23": "Société en participation",
    "24": "Fiducie",
    "27": "Paroisse hors zone concordataire",
    "28": "Assujetti unique à la TVA",
    "29": "Autre groupement de droit privé non doté de la personnalité morale",
    "31": "Personne morale de droit étranger, immatriculée au RCS",
    "32": "Personne morale de droit étranger, non immatriculée au RCS",
    "41": "Établissement public ou régie à caractère industriel ou commercial",
    "51": "Société coopérative commerciale particulière",
    "52": "Société en nom collectif",
    "53": "Société en commandite",
    "54": "Société à responsabilité limitée (SARL)",
    "55": "Société anonyme à conseil d'administration",
    "56": "Société anonyme à directoire",
    "57": "Société par actions simplifiée (SAS)",
    "58": "Société européenne",
    "61": "Caisse d'épargne et de prévoyance",
    "62": "Groupement d'intérêt économique (GIE)",
    "63": "Société coopérative agricole",
    "64": "Société d'assurance mutuelle",
    "65": "Société civile",
    "69": "Autre personne morale de droit privé inscrite au RCS",
    "71": "Administration de l'État",
    "72": "Collectivité territoriale",
    "73": "Établissement public administratif",
    "74": "Autre personne morale de droit public administratif",
    "81": "Organisme gérant un régime de protection sociale à adhésion obligatoire",
    "82": "Organisme mutualiste",
    "83": "Comité social et économique (comité d'entreprise)",
    "84": "Organisme professionnel",
    "85": "Organisme de retraite à adhésion non obligatoire",
    "91": "Syndicat de propriétaires",
    "92": "Association loi 1901 ou assimilé",
    "93": "Fondation",
    "99": "Autre personne morale de droit privé",
}
LEGAL_CATEGORY_L3: dict[str, str] = {
    "5202": "Société en nom collectif",
    "5485": "Société d'exercice libéral à responsabilité limitée (SELARL)",
    "5498": "SARL unipersonnelle (EURL)",
    "5499": "Société à responsabilité limitée (SARL)",
    "5599": "SA à conseil d'administration",
    "5699": "SA à directoire",
    "5710": "Société par actions simplifiée (SAS)",
    "5720": "SAS à associé unique (SASU)",
    "5785": "Société d'exercice libéral par actions simplifiée (SELAS)",
    "6540": "Société civile immobilière (SCI)",
    "9220": "Association déclarée",
}

# Street type codes of the SIRENE address (typeVoieEtablissement) spelled out.
STREET_TYPES: dict[str, str] = {
    "ALL": "ALLEE", "AV": "AVENUE", "BD": "BOULEVARD", "CHE": "CHEMIN", "CHS": "CHAUSSEE",
    "CRS": "COURS", "ESP": "ESPLANADE", "FG": "FAUBOURG", "HAM": "HAMEAU", "IMP": "IMPASSE",
    "LD": "LIEU DIT", "LOT": "LOTISSEMENT", "PAS": "PASSAGE", "PL": "PLACE", "PRO": "PROMENADE",
    "QUA": "QUARTIER", "RES": "RESIDENCE", "RPT": "ROND POINT", "RTE": "ROUTE", "SQ": "SQUARE",
    "TRA": "TRAVERSE", "VLA": "VILLA",
}  # fmt: skip

UL_REQUIRED = ("siren", "etatAdministratifUniteLegale", "statutDiffusionUniteLegale",
               "categorieJuridiqueUniteLegale", "denominationUniteLegale")  # fmt: skip
ETAB_REQUIRED = ("siren", "nic", "etablissementSiege", "codePostalEtablissement",
                 "libelleCommuneEtablissement")  # fmt: skip


# --------------------------------------------------------------------------- finding the files


def pick_resources(resources: list[dict]) -> dict[str, list[str]]:
    """Stock file URLs per kind from a data.gouv.fr dataset answer: Parquet first, then zip."""
    found: dict[str, list[tuple[int, str]]] = {k: [] for k in FILES}
    for res in resources:
        url = str(res.get("url") or "")
        base = Path(urlsplit(url).path).name.lower()
        title = str(res.get("title") or "").lower().replace(" ", "")
        fmt = str(res.get("format") or "").lower()
        for kind, stem in FILES.items():
            s = stem.lower()  # "stockunitelegale_utf8"
            by_name = base in (f"{s}.parquet", f"{s}.zip")
            word = s.split("_")[0]  # a prefix of ...Historique / ...LiensSuccession too
            by_title = word in title and not re.search(r"historique|liens|doublon", title)
            if not (by_name or by_title):
                continue
            if base.endswith(".parquet") or fmt == "parquet":
                rank = 0
            elif base.endswith(".zip") or fmt == "zip":
                rank = 1
            else:
                continue
            found[kind].append((rank, str(res.get("latest") or url)))
    return {k: [u for _, u in sorted(v)] for k, v in found.items()}


def resolve_urls() -> dict[str, list[str]]:
    """Candidate URLs per stock file, best first (env override, data.gouv.fr API, fallback)."""
    urls: dict[str, list[str]] = {}
    for kind, env in URL_ENVS.items():
        if override := os.environ.get(env, "").strip():
            urls[kind] = [override]
    if len(urls) < len(FILES):
        try:
            found = pick_resources(json.loads(bulk.get_text(DATASET_API)).get("resources") or [])
        except Exception as exc:  # noqa: BLE001 - fall back to the known file locations
            log.warning("sirene: data.gouv.fr API failed (%s), using %s", exc, FALLBACK_BASE)
            found = {}
        for kind, stem in FILES.items():
            fallback = [f"{FALLBACK_BASE}{stem}.parquet", f"{FALLBACK_BASE}{stem}.zip"]
            urls.setdefault(kind, list(dict.fromkeys([*found.get(kind, []), *fallback])))
    return urls


def fetch(kind: str, candidates: list[str], work_dir: Path) -> tuple[Path, str]:
    """Download the first candidate that exists; returns (path, url)."""
    import httpx

    last: Exception | None = None
    for url in candidates:
        dest = work_dir / f"{FILES[kind]}.download"
        try:
            path = bulk.download(url, dest, label=f"{NAME} {FILES[kind]}")
            return path, url
        except httpx.HTTPStatusError as exc:
            log.warning("sirene: %s not available (%s), trying the next location", url, exc)
            last = exc
    raise RuntimeError(f"no {FILES[kind]} file could be downloaded ({last})")


def file_kind(path: Path) -> str:
    """'parquet', 'zip' or 'csv', from the file's first bytes."""
    with path.open("rb") as fh:
        head = fh.read(4)
    if head == b"PAR1":
        return "parquet"
    if head.startswith(b"PK"):
        return "zip"
    return "csv"


def open_input(con, path: Path, kind: str, work_dir: Path) -> tuple[str, bulk.Columns, Path]:
    """FROM expression and columns for a stock file (Parquet, zipped CSV or CSV)."""
    fmt = file_kind(path)
    if fmt == "parquet":
        sql = bulk.parquet(path)
        return sql, bulk.relation_columns(con, sql), path
    if fmt == "zip":
        csv_path = bulk.extract(path, work_dir / f"csv-{kind}", r".*\.csv")[0]
        bulk.discard(path, work_dir)
        path = csv_path
    sql, cols = bulk.csv_source(path, f"sirene_{kind}_rejects")
    return sql, cols, path


# --------------------------------------------------------------------------- transform


def _case(expr: str, mapping: dict[str, str]) -> str:
    whens = " ".join(f"WHEN {bulk.sql_str(k)} THEN {bulk.sql_str(v)}" for k, v in mapping.items())
    return f"CASE {expr} {whens} END"


def _text(cols: bulk.Columns, name: str) -> str:
    """Column as trimmed text, '' and INSEE's '[ND]' (not diffusible) as NULL."""
    return f"NULLIF(lc_clean({cols.get(name)}), '[ND]')"


def build(options: BuildOptions) -> ShardStats:
    urls = resolve_urls()
    ul_path, ul_url = fetch("ul", urls["ul"], options.work_dir)
    etab_path, _ = fetch("etab", urls["etab"], options.work_dir)
    con = bulk.connect(options.work_dir)
    try:
        return transform(con, ul_path, etab_path, options, url=ul_url)
    finally:
        con.close()


def transform(
    con, ul_path: Path, etab_path: Path, options: BuildOptions, *, url: str | None = None
) -> ShardStats:
    """Stock files -> shard. Inputs are staged (projected, filtered), then deleted if ours."""
    work = options.work_dir
    rejected = 0
    ul_sql, ul, ul_file = open_input(con, ul_path, "ul", work)
    ul.require(*UL_REQUIRED)
    total = bulk.count(con, ul_sql)
    con.execute(f"""
        CREATE OR REPLACE TABLE sirene_ul AS SELECT
          lpad(CAST({ul["siren"]} AS VARCHAR), 9, '0') AS siren,
          upper(CAST({ul["categorieJuridiqueUniteLegale"]} AS VARCHAR)) AS cat,
          {_text(ul, "denominationUniteLegale")} AS denomination,
          {_text(ul, "denominationUsuelle1UniteLegale")} AS usual_name,
          {_text(ul, "nomUniteLegale")} AS nom,
          {_text(ul, "nomUsageUniteLegale")} AS nom_usage,
          {_text(ul, "prenom1UniteLegale")} AS prenom1,
          {_text(ul, "prenomUsuelUniteLegale")} AS prenom_usuel,
          {_text(ul, "trancheEffectifsUniteLegale")} AS tranche,
          {_text(ul, "dateCreationUniteLegale")} AS created,
          {_text(ul, "activitePrincipaleUniteLegale")} AS naf,
          {_text(ul, "nomenclatureActivitePrincipaleUniteLegale")} AS naf_nomenclature,
          lpad(regexp_replace({_text(ul, "nicSiegeUniteLegale")}, '\\.0$', ''), 5, '0') AS nic_siege
        FROM {ul_sql}
        WHERE upper(trim(CAST({ul["etatAdministratifUniteLegale"]} AS VARCHAR))) = 'A'
          AND upper(trim(CAST({ul["statutDiffusionUniteLegale"]} AS VARCHAR))) = 'O'
    """)
    rejected += bulk.rejected_lines(con, "sirene_ul_rejects")
    bulk.discard(ul_file, work)
    log.info("%s: %d legal units read, %d active and diffusible", NAME, total,
             bulk.count(con, "sirene_ul"))  # fmt: skip

    et_sql, et, et_file = open_input(con, etab_path, "etab", work)
    et.require(*ETAB_REQUIRED)
    siege = f"lower(trim(CAST({et['etablissementSiege']} AS VARCHAR))) IN ('true', 't', '1', 'oui')"
    con.execute(f"""
        CREATE OR REPLACE TABLE sirene_siege AS SELECT
          lpad(CAST({et["siren"]} AS VARCHAR), 9, '0') AS siren,
          lpad(regexp_replace(CAST({et["nic"]} AS VARCHAR), '\\.0$', ''), 5, '0') AS nic,
          upper(coalesce(CAST({et.get("statutDiffusionEtablissement", "'O'")} AS VARCHAR), 'O'))
            AS diffusion,
          regexp_replace({_text(et, "numeroVoieEtablissement")}, '\\.0$', '') AS num,
          {_text(et, "indiceRepetitionEtablissement")} AS rep,
          upper({_text(et, "typeVoieEtablissement")}) AS street_type,
          {_text(et, "libelleVoieEtablissement")} AS street,
          {_text(et, "codePostalEtablissement")} AS postal,
          {_text(et, "libelleCommuneEtablissement")} AS commune,
          {_text(et, "libelleCommuneEtrangerEtablissement")} AS commune_abroad,
          {_text(et, "codeCommuneEtablissement")} AS commune_code
        FROM {et_sql}
        WHERE {siege}
          AND lpad(CAST({et["siren"]} AS VARCHAR), 9, '0') IN (SELECT siren FROM sirene_ul)
    """)
    rejected += bulk.rejected_lines(con, "sirene_etab_rejects")
    bulk.discard(et_file, work)
    log.info("%s: %d head offices joined", NAME, bulk.count(con, "sirene_siege"))
    return _write(con, options, total, rejected, url)


def _tokens(expr: str) -> str:
    """Upper-case words of a name, accents removed (for the natural-person name check)."""
    clean = f"regexp_replace(strip_accents(upper(coalesce({expr}, ''))), '[^A-Z0-9]+', ' ', 'g')"
    return f"string_split(trim({clean}), ' ')"


def _write(con, options: BuildOptions, total: int, rejected: int, url: str | None) -> ShardStats:
    person = "concat_ws(' ', u.nom, u.nom_usage, u.prenom1, u.prenom_usuel)"
    person_words = f"list_filter({_tokens(person)}, {bulk.list_lambda('t', 'length(t) >= 2')})"
    candidate = "coalesce(u.denomination, u.usual_name)"
    keep = (
        f"{candidate} IS NOT NULL AND (NOT starts_with(u.cat, '1') OR "
        f"(trim({person}) <> '' AND NOT list_has_any({_tokens(candidate)}, {person_words})))"
    )
    dept = "CASE WHEN starts_with(s.commune_code, '97') THEN left(s.commune_code, 3) " \
           "ELSE left(s.commune_code, 2) END"  # fmt: skip
    region_code = _case(dept, REGION_OF_DEPT)
    ok = "coalesce(diffusion, 'O') = 'O'"  # head office address fully diffusible
    street = (
        f"NULLIF(concat_ws(' ', num, rep, coalesce({_case('street_type', STREET_TYPES)}, "
        f"street_type), street), '')"
    )
    nomenclature_ok = (
        "(upper(u.naf_nomenclature) LIKE 'NAFREV2%' OR upper(u.naf_nomenclature) LIKE 'NAF20%' "
        "OR upper(u.naf_nomenclature) LIKE 'NACE%')"
    )
    section = f"CASE WHEN {nomenclature_ok} THEN lc_nace_section(u.naf) END"
    from_sql = (
        "(SELECT u.*, s.* EXCLUDE (siren), "
        f"{section} AS section, {region_code} AS region_code, {keep} AS keep_unit "
        "FROM sirene_ul u LEFT JOIN sirene_siege s ON s.siren = u.siren AND s.nic = u.nic_siege"
        ") AS j WHERE keep_unit"
    )
    exprs = {
        "name": "coalesce(denomination, usual_name)",
        "legal_name": "coalesce(denomination, usual_name)",
        "country": "'FR'",
        "region": _case("region_code", REGIONS),
        "city": f"CASE WHEN {ok} THEN lc_title(coalesce(commune, commune_abroad)) END",
        "industry": "section",
        "industry_label": "lc_section_label(section)",
        "size_range": _case("tranche", TRANCHE_TO_SIZE),
        "founded_year": "lc_year(created)",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "siren": "siren",
        "registry": "'FR-SIREN'",
        "company_number": "siren",
        "status": "'active'",
        "legal_form": f"coalesce({_case('cat', LEGAL_CATEGORY_L3)}, "
        f"{_case('left(cat, 2)', LEGAL_CATEGORY_L2)})",
        "address": f"CASE WHEN {ok} THEN {street} END",
        "postal_code": f"CASE WHEN {ok} THEN "
        "CASE WHEN regexp_full_match(postal, '[0-9]{4}') THEN lpad(postal, 5, '0') ELSE postal END "
        "END",
    }
    notes = f"{rejected} malformed CSV lines skipped" if rejected else ""
    return bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        from_sql,
        total=total,
        notes=notes,
    )


SOURCE = register(
    BulkSource(
        name=NAME,
        title="INSEE SIRENE stock (France)",
        licence="etalab-2.0",
        licence_url="https://www.etalab.gouv.fr/licence-ouverte-open-licence/",
        homepage="https://www.data.gouv.fr/datasets/base-sirene-des-entreprises-et-de-leurs-etablissements-siren-siret/",
        countries=frozenset({"FR"}),
        build=build,
        attribution="Source: INSEE, base SIRENE des entreprises (Licence Ouverte / Etalab 2.0).",
    )
)
