"""Benchmark company search at pack scale on synthetic data (docs/dev/search-performance.md).

Generate a pack-shaped dataset (one Parquet file per country plus manifest.json, PACK_COLUMNS,
realistic name / city / industry / size distributions), then time the query paths the UI uses:

    python tools/bench_search.py generate --rows 12000000 --out /tmp/bench-packs
    python tools/bench_search.py run --data /tmp/bench-packs            # current storage
    python tools/bench_search.py run --data /tmp/bench-packs --raw      # plain Parquet (before)

`run` installs the files into a scratch data directory the way a download does (so the local
search database is built and timed), then measures each scenario with distinct filters (cold:
no result cache can help) and prints p50 / p95 / max per query path plus process memory.
Everything is local: no network, no DuckDB extension downloads.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pepite.search import CompanyFilters  # noqa: E402

# Share of the rows per country (FR is the "one big country" case: ~60% of the rows).
COUNTRY_SHARE = {
    "FR": 0.58,
    "GB": 0.25,
    "US": 0.08,
    "DE": 0.04,
    "NO": 0.025,
    "BE": 0.01,
    "ES": 0.008,
    "IT": 0.007,
}
REGISTRY = {"FR": "FR-SIREN", "GB": "GB-CH", "US": "US-EDGAR", "NO": "NO-BRREG"}
DIGITS = {"FR": 9, "GB": 8, "US": 10, "NO": 9}
TLD = {"FR": "fr", "GB": "co.uk", "US": "com", "DE": "de", "NO": "no", "BE": "be", "ES": "es"}

FR_WORDS = (
    "Martin Bernard Dubois Thomas Robert Richard Petit Durand Leroy Moreau Simon Laurent "
    "Lefebvre Michel Garcia David Bertrand Roux Vincent Fournier Morel Girard Andre Mercier "
    "Dupont Lambert Bonnet Francois Martinez Legrand Garnier Faure Rousseau Blanc Guerin Muller "
    "Henry Roussel Nicolas Perrin Morin Mathieu Clement Gauthier Dumont Lopez Fontaine Chevalier "
    "Robin Masson Sanchez Gerard Nguyen Boyer Denis Lemaire Duval Joly Gautier Roger Roche Roy "
    "Noel Meyer Lucas Meunier Jean Perez Marchand Dufour Blanchard Marie Barbier Brun Dumas "
    "Brunet Schmitt Leroux Colin Fernandez Pierre Renard Arnaud Rolland Caron Aubert Giraud "
    "Leclerc Vidal Bourgeois Renaud Lemoine Picard Gaillard Philippe Leclercq Lacroix Fabre "
    "Dupuis Olivier Rodriguez Da Silva Hubert Louis Charles Guillot Riviere Le Gall Guillaume "
    "Adam Rey Moulin Gonzalez Berger Lecomte Menard Fleury Deschamps Carpentier Julien Benoit "
    "Paris Maillard Marchal Aubry Vasseur Le Roux Renault Jacquet Collet Prevost Poirier "
    "Charpentier Royer Huet Baron Dupuy Pons Paul Laine Carre Breton Remy Schneider Perrot "
    "Guyot Barre Marty Cousin Atlas Horizon Zenith Azur Alpha Delta Omega Nova Opale Iris "
    "Phoenix Eole Helios Lumiere Etoile Soleil Oceane Vallee Moulin Tilleuls Chenes Pins Source "
    "Riviere Colline Prairie Montagne Lac Foret Jardin Verger Vignoble Chateau Manoir Bastide"
).split()
FR_BIZ = (
    "Conseil Services Immobilier Batiment Consulting Digital Solutions Distribution Industrie "
    "Technologies Renovation Logistique Patrimoine Invest Developpement Ingenierie Transport "
    "Construction Energie Sante Formation Communication Design Habitat Gestion Finance Auto "
    "Electricite Plomberie Menuiserie Peinture Nettoyage Securite Informatique Medical Optique "
    "Coiffure Beaute Restauration Traiteur Boulangerie Patisserie Boucherie Fleurs Voyages "
    "Location Expertise Audit Avocats Architecture Paysage Elevage Agricole Viticole Textile"
).split()
FR_PREFIX = (
    "SCI SARL SAS EURL SASU Garage Boulangerie Cabinet Restaurant Pharmacie Transports Holding "
    "Societe Atelier Agence Etablissements Groupe Maison Ferme Domaine Clinique Hotel Cafe "
    "Boucherie Salon Institut Laboratoire Entreprise"
).split()
FR_FORMS = "SAS SARL SA SCI EURL SASU SNC SCP".split()
EN_WORDS = (
    "Smith Jones Taylor Brown Williams Wilson Johnson Davies Robinson Wright Thompson Evans "
    "Walker White Roberts Green Hall Wood Jackson Clarke Patel Khan Lewis James Phillips Mason "
    "Mitchell Rose Davis Rodriguez Cox Alexander Morgan Moore Martin Anderson Lee Harris Clark "
    "Young King Scott Baker Adams Nelson Hill Campbell Carter Turner Parker Collins Edwards "
    "Stewart Morris Murphy Cook Rogers Peterson Cooper Reed Bailey Bell Gomez Kelly Howard Ward "
    "Watson Brooks Gray Hughes Price Sanders Myers Long Ross Foster Powell Jenkins Perry Russell "
    "Sullivan Fisher Henderson Coleman Simmons Patterson Jordan Reynolds Hamilton Graham Wallace "
    "Atlas Horizon Zenith Summit Apex Pinnacle Meridian Beacon Harbour Oak Willow Cedar Maple "
    "Riverside Highland Northern Southern Eastern Western Royal Crown Phoenix Falcon Eagle Lion"
).split()
EN_BIZ = (
    "Consulting Services Properties Holdings Group Solutions Technologies Digital Media Capital "
    "Investments Logistics Engineering Construction Builders Developments Partners Associates "
    "Software Systems Energy Healthcare Care Trading Imports Foods Bakery Motors Garage Cleaning "
    "Security Design Marketing Recruitment Training Finance Accounting Legal Estates Lettings "
    "Transport Haulage Electrical Plumbing Roofing Landscaping Studio Labs Analytics Ventures"
).split()
EN_FORMS = {"GB": "Ltd Limited PLC LLP".split(), "US": "Inc LLC Corp Co".split()}
SYL = (
    "Ber Mon Val Saint Bel Fon Mar Roche Ville Cha Gre Cler Lan Pon Bour Cour Cham Mau Sau Vil "
    "Ar Bri Cas Dor Esc Fla Gui Hau Jou Lor Mer Neu Or Pla Que Ros Sar Tou Ver"
).split()
SUF = (
    "ville court mont bourg dorf ham ton by stad wick field ford sur-mer les-bains la-foret "
    "en-provence vic heim berg lund"
).split()
BIG_CITIES = {
    "FR": "Paris Marseille Lyon Toulouse Nice Nantes Montpellier Strasbourg Bordeaux Lille "
    "Rennes Reims Toulon Grenoble Dijon Angers Nimes Villeurbanne Clermont-Ferrand Le Mans "
    "Aix-en-Provence Brest Tours Amiens Limoges Annecy Perpignan Metz Besancon Orleans Rouen "
    "Caen Nancy Argenteuil",
    "GB": "London Birmingham Manchester Leeds Glasgow Liverpool Bristol Sheffield Edinburgh "
    "Cardiff Leicester Coventry Bradford Belfast Nottingham Hull Newcastle Southampton Reading "
    "Derby",
    "US": "New York|Los Angeles|Chicago|Houston|Phoenix|Philadelphia|San Antonio|San Diego|Dallas|"
    "Austin|San Jose|Seattle|Denver|Boston|Miami|Atlanta",
    "DE": "Berlin Hamburg Munchen Koln Frankfurt Stuttgart Dusseldorf Leipzig Dortmund Essen "
    "Bremen",
    "NO": "Oslo Bergen Trondheim Stavanger Drammen Fredrikstad Kristiansand Tromso",
}
REGIONS = {
    "FR": "Ile-de-France|Auvergne-Rhone-Alpes|Nouvelle-Aquitaine|Occitanie|Hauts-de-France|"
    "Provence-Alpes-Cote d'Azur|Grand Est|Pays de la Loire|Bretagne|Normandie|"
    "Bourgogne-Franche-Comte|Centre-Val de Loire|Corse",
    "GB": "England|Scotland|Wales|Northern Ireland",
    "US": "California|Texas|New York|Florida|Illinois|Pennsylvania|Ohio|Georgia|Washington|"
    "Massachusetts|Colorado|Arizona",
    "DE": "Bayern|Nordrhein-Westfalen|Baden-Wurttemberg|Berlin|Hamburg|Hessen|Sachsen",
    "NO": "Oslo|Viken|Vestland|Rogaland|Trondelag|Innlandet",
}
# ISIC sections with a register-like share (cumulative thresholds are built from these).
INDUSTRY = [
    ("G", 0.17, "Wholesale and retail trade"),
    ("M", 0.14, "Professional, scientific and technical activities"),
    ("F", 0.12, "Construction"),
    ("L", 0.10, "Real estate activities"),
    ("I", 0.07, "Accommodation and food service activities"),
    ("N", 0.07, "Administrative and support service activities"),
    ("Q", 0.06, "Human health and social work activities"),
    ("S", 0.06, "Other service activities"),
    ("C", 0.05, "Manufacturing"),
    ("K", 0.04, "Financial and insurance activities"),
    ("J", 0.035, "Information and communication"),
    ("H", 0.025, "Transportation and storage"),
    ("P", 0.015, "Education"),
    ("R", 0.015, "Arts, entertainment and recreation"),
    ("A", 0.01, "Agriculture, forestry and fishing"),
    ("E", 0.003, "Water supply; sewerage, waste management"),
    ("D", 0.002, "Electricity, gas, steam and air conditioning supply"),
    ("B", 0.001, "Mining and quarrying"),
    ("O", 0.001, "Public administration and defence"),
]
SIZES = [
    ("1-10", 0.30),
    ("11-50", 0.09),
    ("51-200", 0.035),
    ("201-500", 0.01),
    ("501-1000", 0.004),
    ("1001-5000", 0.003),
    ("5001-10000", 0.0005),
    ("10001+", 0.0003),
]


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _list(items: list[str] | tuple[str, ...]) -> str:
    return "[" + ", ".join(_q(x) for x in items) + "]"


def _u(k: int) -> str:
    """Uniform [0, 1) from the row number, independent per k."""
    return f"((hash(i, {k}) % 1000003) / 1000003.0)"


def _pick(items: list[str] | tuple[str, ...], k: int, skew: float = 1.0) -> str:
    n = len(items)
    return f"{_list(items)}[1 + least({n - 1}, floor({n} * pow({_u(k)}, {skew}))::INT)]"


def _case(choices: list[tuple[str, float]], k: int, other: str = "NULL") -> str:
    parts, cum = [], 0.0
    for value, share in choices:
        cum += share
        parts.append(f"WHEN {_u(k)} < {cum:.6f} THEN {_q(value)}")
    return "CASE " + " ".join(parts) + f" ELSE {other} END"


def country_sql(cc: str, rows: int, offset: int) -> str:
    fr = cc in ("FR", "BE")
    words, biz = (FR_WORDS, FR_BIZ) if fr else (EN_WORDS, EN_BIZ)
    forms = FR_FORMS if fr else EN_FORMS.get(cc, ["GmbH", "AG", "AS", "SpA", "SL"])
    prefix = FR_PREFIX if fr else ["The", "Royal", "North", "First", "United", "New"]
    big = BIG_CITIES.get(cc, "Capital City|Second City|Harbour Town").replace("|", " | ")
    big_list = [c.strip() for c in (big.split(" | ") if "|" in big else big.split())]
    regions = REGIONS.get(cc, "North|South|East|West|Centre").split("|")
    ncity = 35000 if cc == "FR" else 20000 if cc in ("GB", "US", "DE") else 4000
    tld = TLD.get(cc, "com")
    reg = REGISTRY.get(cc, f"{cc}-REG")
    digits = DIGITS.get(cc, 9)
    w1 = _pick(words, 1, 1.6)
    w2 = _pick(biz, 2, 1.3)
    w3 = _pick(words, 3, 1.2)
    pre = _pick(prefix, 4, 1.4)
    form = _pick(forms, 5, 1.5)
    shape = _u(6)
    name = (
        f"CASE WHEN {shape} < 0.25 THEN {pre} || ' ' || {w1} "
        f"WHEN {shape} < 0.50 THEN {w1} || ' ' || {w2} "
        f"WHEN {shape} < 0.70 THEN {w1} || ' ' || {w2} || ' ' || {form} "
        f"WHEN {shape} < 0.82 THEN {w1} || ' & ' || {w3} "
        f"WHEN {shape} < 0.92 THEN {pre} || ' ' || {w3} || ' ' || {w2} "
        f"ELSE {w1} || ' ' || {w2} || ' ' || (i % 97)::VARCHAR END"
    )
    ck = f"floor({ncity} * pow({_u(7)}, 4))::INT"
    city = (
        f"CASE WHEN {_u(8)} < 0.03 THEN NULL WHEN ck < {len(big_list)} "
        f"THEN {_list(big_list)}[ck + 1] ELSE {_list(SYL)}[1 + ck % {len(SYL)}] || "
        f"lower({_list(SYL)}[1 + (ck // {len(SYL)}) % {len(SYL)}]) || "
        f"{_list(SUF)}[1 + (ck // {len(SYL) ** 2}) % {len(SUF)}] END"
    )
    region = f"{_list(regions)}[1 + (hash(ck) % {len(regions)})::INT]"
    industry = _case([(c, s) for c, s, _ in INDUSTRY], 9)
    labels = "CASE industry " + " ".join(f"WHEN {_q(c)} THEN {_q(lbl)}" for c, _, lbl in INDUSTRY)
    size = _case(SIZES, 10)
    has_domain = f"{_u(11)} < 0.18"
    number = f"lpad(({offset} + i)::VARCHAR, {digits}, '0')"
    return f"""
    WITH base AS (
      SELECT i, {name} AS raw_name, {ck} AS ck, {w1} AS w1, {w2} AS w2, {form} AS form
      FROM range({rows}) t(i)
    ), named AS (
      SELECT *, CASE WHEN {_u(12)} < 0.4 THEN upper(raw_name) ELSE raw_name END AS name,
        {city} AS city, {region} AS region, {industry} AS industry, {size} AS size_range,
        {number} AS company_number
      FROM base
    )
    SELECT
      'co_' || substr(md5('{cc}' || i::VARCHAR), 1, 16) AS id,
      name,
      CASE WHEN {_u(13)} < 0.6 THEN upper(raw_name) || ' ' || form END AS legal_name,
      CASE WHEN {has_domain} THEN regexp_replace(lower(w1 || w2), '[^a-z0-9]', '', 'g')
        || (i % 100000)::VARCHAR || '.{tld}' END AS domain,
      '{cc}' AS country, region, city, industry,
      {labels} END AS industry_label,
      size_range,
      (1950 + floor(75 * sqrt({_u(14)})))::INTEGER AS founded_year,
      CASE WHEN {_u(15)} < 0.015 THEN upper(substr(md5('lei' || i::VARCHAR), 1, 20)) END AS lei,
      CASE WHEN {_u(16)} < 0.003 THEN 'Q' || (1000000 + i)::VARCHAR END AS wikidata_id,
      CASE WHEN {has_domain} THEN 'https://' || regexp_replace(lower(w1 || w2), '[^a-z0-9]', '',
        'g') || (i % 100000)::VARCHAR || '.{tld}' END AS website,
      '[{{"source": "register", "licence": "CC0-1.0", "url": "https://register.example/'
        || company_number || '", "fetched_at": "2026-12-01T03:00:00+00:00"}}]' AS sources_json,
      CASE WHEN '{cc}' = 'FR' THEN company_number END AS siren,
      CASE WHEN '{cc}' = 'US' THEN company_number END AS cik,
      '{reg}' AS registry, company_number, 'active' AS status, form AS legal_form,
      (1 + i % 180)::VARCHAR || ' ' || w1 || ' Street' AS address,
      lpad(((hash(ck) % 95000) + 1000)::VARCHAR, 5, '0') AS postal_code,
      CASE WHEN {_u(17)} < 0.05 THEN '+33 1 ' || lpad((i % 100000000)::VARCHAR, 8, '0') END
        AS phone,
      CASE WHEN size_range IS NOT NULL AND {_u(18)} < 0.4 THEN (1 + i % 40)::INTEGER END
        AS employees,
      CASE WHEN {_u(19)} < 0.03 THEN round(10000 + {_u(20)} * 5e7, 0)::DOUBLE END AS revenue,
      CASE WHEN {_u(19)} < 0.03 THEN 'EUR' END AS revenue_currency,
      CASE WHEN {_u(19)} < 0.03 THEN 2025 END AS revenue_year
    FROM named
    ORDER BY id
    """


def generate(rows: int, out: Path, stamp: str = "20261201") -> None:
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    # spill next to the output, not into '.tmp' of the current directory
    con.execute(f"SET temp_directory = {_q(str(out / '.duckdb-tmp'))}")
    countries = []
    offset = 100_000_000
    for cc, share in COUNTRY_SHARE.items():
        n = max(1000, int(rows * share))
        path = out / f"companies-{cc}-{stamp}.parquet"
        t0 = time.perf_counter()
        con.execute(
            f"COPY ({country_sql(cc, n, offset)}) TO {_q(str(path))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        offset += n
        sha = _sha256(path)
        countries.append(
            {
                "country": cc,
                "file": path.name,
                "rows": n,
                "bytes": path.stat().st_size,
                "sha256": sha,
                "sources": ["register"],
                "with_domain": int(n * 0.18),
            }
        )
        print(f"{path.name}: {n:,} rows, {path.stat().st_size / 1e6:,.0f} MB, "
              f"{time.perf_counter() - t0:.1f} s", flush=True)  # fmt: skip
    manifest = {
        "schema": 3,
        "release": "packs-2026-12",
        "built_at": "2026-12-01T03:40:00+00:00",
        "countries": countries,
        "files": {},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -- measuring ------------------------------------------------------------------------------


def rss_mb() -> tuple[float, float]:
    """(current RSS, peak RSS) of this process in MB (Linux /proc; peak only elsewhere)."""
    try:
        status = Path("/proc/self/status").read_text()
        vals = {
            line.split(":")[0]: float(line.split()[1]) / 1024
            for line in status.splitlines()
            if line.startswith(("VmRSS", "VmHWM"))
        }
        return vals.get("VmRSS", 0.0), vals.get("VmHWM", 0.0)
    except OSError:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1 << 20) if sys.platform == "darwin" else peak / 1024
        return 0.0, peak_mb


def timed(fn: Callable[[], Any]) -> tuple[float, Any]:
    t0 = time.perf_counter()
    out = fn()
    return time.perf_counter() - t0, out


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[k]


def sample_values(data: Path) -> dict[str, list[str]]:
    """Real ids / domains / registry numbers / names to look up (picked from the files)."""
    con = duckdb.connect()
    files = sorted(str(p) for p in data.glob("companies-*.parquet"))
    lst = "[" + ", ".join(_q(f) for f in files) + "]"
    rows = con.execute(
        f"SELECT id, domain, company_number, name FROM read_parquet({lst}) USING SAMPLE 400 ROWS"
    ).fetchall()
    return {
        "ids": [r[0] for r in rows][:60],
        "domains": [r[1] for r in rows if r[1]][:60],
        "numbers": [r[2] for r in rows if r[2]][:60],
        "names": [r[3] for r in rows if r[3]][:30],
    }


def scenarios(samples: dict[str, list[str]]) -> dict[str, list[CompanyFilters]]:
    F = CompanyFilters
    rnd = random.Random(7)
    names = samples["names"]
    return {
        "browse": [F()],
        "country": [F(countries=[c]) for c in ("FR", "GB", "US", "DE", "NO")]
        + [F(countries=["FR", "BE"]), F(exclude_countries=["FR"])],
        "country+industry+size": [
            F(countries=["FR"], industries=["F"], size_ranges=["11-50", "51-200"]),
            F(countries=["GB"], industries=["J"]),
            F(industries=["M", "J"], size_ranges=["1-10"]),
            F(countries=["US"], industries=["K"], size_ranges=["10001+"]),
            F(countries=["FR"], industries=["I"], exclude_industries=["G"]),
            F(size_ranges=["201-500", "501-1000"]),
            F(countries=["DE", "NO"], industries=["C"]),
        ],
        "region/city": [
            F(cities=["paris"]),
            F(cities=["lyon"], industries=["F"]),
            F(regions=["bretagne"]),
            F(cities=["manchester"], size_ranges=["11-50"]),
            F(countries=["FR"], exclude_cities=["paris"]),
            F(regions=["ile-de-france"], industries=["M"]),
            F(cities=["oslo", "bergen"]),
        ],
        "text": [
            F(text=t)
            for t in (
                "boulangerie",
                "martin",
                "conseil",
                "zenith",
                "digital",
                "garage du",
                "holdings",
                "xqz",
                "smith & ",
            )
        ]
        + [F(text=n) for n in rnd.sample(names, min(5, len(names)))]
        + [F(text="immobilier", countries=["FR"]), F(text="consulting", countries=["GB"])],
        "keywords": [
            F(keywords=["software", "digital"]),
            F(keywords=["construction"], keyword_fields="industry"),
            F(keywords=["bakery", "boulangerie"], countries=["FR", "GB"]),
            F(keywords=["sante", "medical"], keyword_mode="all"),
            F(countries=["FR"], exclude_keywords=["sci"]),
            F(keywords=["transport"], keyword_fields="name", size_ranges=["11-50"]),
        ],
    }


def run(
    data: Path,
    work: Path,
    raw: bool,
    pages: tuple[int, ...] = (1, 2, 40),
    memory_limit: str | None = None,
    threads: int | None = None,
    keep: bool = False,
) -> dict[str, Any]:
    if memory_limit:
        os.environ["PEPITE_DUCKDB_MEMORY"] = memory_limit
    if threads:
        os.environ["PEPITE_DUCKDB_THREADS"] = str(threads)
    from pepite.packs import DuckDBCompanySearch

    report: dict[str, Any] = {"raw": raw, "rows": 0, "timings": {}, "memory": {}}
    samples = sample_values(data)
    if raw:
        data_dir = data
        build_s = 0.0
    else:
        data_dir = work
        if work.exists() and not keep:
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        build_s = install_local(data, work)
    report["build_seconds"] = round(build_s, 1)
    report["memory"]["after_build"] = rss_mb()

    t_open, search = timed(lambda: DuckDBCompanySearch(data_dir))
    report["open_seconds"] = round(t_open, 3)
    report["memory"]["after_open"] = rss_mb()
    rows = sum(int(p.get("rows") or 0) for p in search.pack_info())
    report["rows"] = rows
    print(f"{rows:,} rows; open {t_open:.2f} s; build {build_s:.1f} s", flush=True)

    t: dict[str, list[float]] = {}

    def add(key: str, seconds: float) -> None:
        t.setdefault(key, []).append(seconds)

    facet_specs = (("country", 300), ("industry", 300), ("size_range", 20))
    for group, flts in scenarios(samples).items():
        for flt in flts:
            for page in pages if group in ("browse", "country") else (1,):
                # The /companies page: total (per_page=1), the page of 25, then the facets.
                c_s, _ = timed(lambda flt=flt: search.search_companies(flt, 1, 1).total)
                p_s, res = timed(lambda flt=flt, page=page: search.search_companies(flt, page, 25))
                f_s = 0.0
                if page == 1:
                    for facet, limit in facet_specs:
                        s, _ = timed(lambda flt=flt, facet=facet, limit=limit: search.facet_counts(
                            facet, _skip(flt, facet), limit))  # fmt: skip
                        f_s += s
                    add("facets (3 lists)", f_s)
                    add(f"facets: {group}", f_s)
                add("count", c_s)
                add("results page (count + 25 rows)", c_s + p_s)
                add(f"results page: {group}", c_s + p_s)
                if page == 1:
                    add("full /companies request", c_s + p_s + f_s)
                print(f"  {group:22} p{page:<3} total={res.total:>10,} count={c_s * 1000:7.0f} ms "
                      f"page={p_s * 1000:7.0f} ms facets={f_s * 1000:7.0f} ms  {_desc(flt)}",
                      flush=True)  # fmt: skip
    # sort by name / size (pages.scan_companies reads up to 5,000 rows in batches of 500)
    for flt in (CompanyFilters(countries=["FR"], industries=["F"]), CompanyFilters(text="martin")):

        def scan(flt: CompanyFilters = flt) -> int:
            got, page = 0, 1
            while True:
                res = search.search_companies(flt, page, 500)
                got += len(res.items)
                if not res.items or got >= res.total or got >= 5000:
                    return got
                page += 1

        s, n = timed(scan)
        add("sorted by name (scan 5,000)", s)
        print(f"  sorted scan {n} rows {s * 1000:.0f} ms  {_desc(flt)}", flush=True)
    for cid in samples["ids"][:40]:
        s, c = timed(lambda cid=cid: search.get_company(cid))
        assert c is not None, cid
        add("get_company(id)", s)
    for dom in samples["domains"][:40]:
        s, res = timed(lambda dom=dom: search.search_companies(CompanyFilters(domains=[dom]), 1, 1))
        assert res.total >= 1, dom
        add("lookup by domain", s)
    finder = getattr(search, "find_by_number", None)
    for num in samples["numbers"][:40]:
        if finder is not None:
            s, found = timed(lambda num=num: finder(num))
            assert found, num
        else:  # before: no registry lookup in the API; the closest is a scan on the column
            s, _ = timed(lambda num=num: search._query(
                "SELECT id FROM companies WHERE company_number = ? LIMIT 1", [num]))  # fmt: skip
        add("lookup by registry number", s)
    if hasattr(search, "suggest_places"):
        for term in ("par", "lyo", "man", "ber", "saint", "mon"):
            s, _ = timed(lambda term=term: search.suggest_places(term, 12))
            add("place suggestions", s)
    report["memory"]["after_queries"] = rss_mb()
    report["timings"] = {
        k: {
            "n": len(v),
            "p50_ms": round(statistics.median(v) * 1000),
            "p95_ms": round(pct(v, 95) * 1000),
            "max_ms": round(max(v) * 1000),
        }
        for k, v in t.items()
    }
    return report


def _skip(flt: CompanyFilters, facet: str) -> CompanyFilters:
    """What pages.company_facets passes: the filters without the facet's own group."""
    from dataclasses import replace

    if facet == "country":
        return replace(flt, countries=[], regions=[], cities=[], exclude_countries=[],
                       exclude_regions=[], exclude_cities=[])  # fmt: skip
    if facet == "industry":
        return replace(flt, industries=[], exclude_industries=[])
    return replace(flt, size_ranges=[])


def _desc(flt: CompanyFilters) -> str:
    d = {k: v for k, v in vars(flt).items() if v and k not in ("keyword_mode", "keyword_fields")}
    return json.dumps(d, ensure_ascii=False)[:90]


def install_local(src: Path, data_dir: Path) -> float:
    """Copy the release files into data_dir and build the local search database, as a
    download does. Returns the seconds spent building (copy excluded)."""
    from pepite.packs import index as pack_index

    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["countries"]:
        dest = data_dir / entry["file"]
        if not dest.exists() or dest.stat().st_size != (src / entry["file"]).stat().st_size:
            shutil.copy2(src / entry["file"], dest)  # same mtime: a kept index stays valid
    t0 = time.perf_counter()
    pack_index.ensure_index(data_dir)
    return time.perf_counter() - t0


def print_report(rep: dict[str, Any]) -> None:
    print()
    print(f"rows: {rep['rows']:,}  raw parquet: {rep['raw']}  build: {rep['build_seconds']} s  "
          f"open: {rep['open_seconds']} s")  # fmt: skip
    print(f"| {'query path':40} | {'n':>3} | {'p50 ms':>7} | {'p95 ms':>7} | {'max ms':>7} |")
    print(f"|{'-' * 42}|{'-' * 5}|{'-' * 9}|{'-' * 9}|{'-' * 9}|")
    for k, v in rep["timings"].items():
        print(f"| {k:40} | {v['n']:>3} | {v['p50_ms']:>7} | {v['p95_ms']:>7} | {v['max_ms']:>7} |")
    for k, (cur, peak) in rep["memory"].items():
        print(f"memory {k}: RSS {cur:,.0f} MB (peak {peak:,.0f} MB)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write a synthetic release (Parquet per country)")
    g.add_argument("--rows", type=int, default=12_000_000)
    g.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run", help="time the UI query paths")
    r.add_argument("--data", type=Path, required=True, help="directory written by generate")
    r.add_argument("--work", type=Path, default=None, help="scratch data dir (default: data/_app)")
    r.add_argument("--raw", action="store_true", help="query the Parquet files directly")
    r.add_argument("--memory-limit", default=None, help="DuckDB memory limit, e.g. 1GB")
    r.add_argument("--threads", type=int, default=None)
    r.add_argument("--json", type=Path, default=None, help="also write the report as JSON")
    r.add_argument("--keep", action="store_true", help="reuse the indexes built by a last run")
    args = ap.parse_args(argv)
    if args.cmd == "generate":
        generate(args.rows, args.out)
        return 0
    rep = run(
        args.data,
        args.work or args.data / "_app",
        args.raw,
        memory_limit=args.memory_limit,
        threads=args.threads,
        keep=args.keep,
    )
    print_report(rep)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
