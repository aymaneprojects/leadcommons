"""Merging bulk shards into per-country packs: linking, field priority, ids, outputs.

The shards here are synthetic but follow the contract of connectors/bulk (exactly PACK_COLUMNS,
one provenance entry per row). `test_merge_at_scale` is the scale check; it is skipped unless
LEADCOMMONS_SCALE_ROWS is set (see its docstring for the measured numbers).
"""

from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from leadcommons.connectors import bulk
from leadcommons.models import make_id
from leadcommons.packs.builder import pack_schema
from leadcommons.packs.duck import DuckDBCompanySearch
from leadcommons.packs.linking import install_macros
from leadcommons.packs.merge import merge_shards
from leadcommons.packs.quality import compute_quality
from leadcommons.search import PACK_COLUMNS

BUILT = datetime(2026, 12, 1, 3, 40, tzinfo=UTC)
LICENCES = {
    "sirene_stock": "etalab-2.0",
    "companies_house": "OGL-UK-3.0",
    "gleif_golden": "CC0-1.0",
    "wikidata": "CC0-1.0",
    "edgar_bulk": "public-domain",
    "brreg": "NLOD-2.0",
    "abr": "CC-BY-4.0",
    "canada_corps": "OGL-Canada-2.0",
}
ABR_ATTRIBUTION = "Contains ABR data, Commonwealth of Australia, licensed under CC BY 4.0."

S_DANONE, S_TOTAL, S_A, S_B = "552032534", "542051180", "111111118", "222222226"
L_DANONE, L_TOTAL, L_A = "969500KSV493XWY0PS54", "529900S21EQ1BO4ESM68", "9695001AAAAAAAAAAA11"
L_ACME, L_BETA, L_GAMMA = "213800AAAAAAAAAAAA22", "213800BBBBBBBBBBBB33", "529900CCCCCCCCCCCC44"


def write_shard(directory: Path, source: str, rows: list[dict], cc: str | None = None) -> Path:
    """A shard as the source agents write it: PACK_COLUMNS, provenance on every row."""
    prov = json.dumps(
        [
            {
                "source": source,
                "licence": LICENCES[source],
                "url": f"https://data.example.org/{source}",
                "fetched_at": "2026-11-30T00:00:00+00:00",
            }
        ]
    )
    full = [
        {c: r.get(c) for c in PACK_COLUMNS} | {"id": f"src_{source}_{i}", "sources_json": prov}
        for i, r in enumerate(rows)
    ]
    path = directory / (f"{source}-{cc}.parquet" if cc else f"{source}.parquet")
    pq.write_table(pa.Table.from_pylist(full, schema=pack_schema()), path)
    return path


def fr(siren: str, name: str, **kw) -> dict:
    return {"name": name, "country": "FR", "registry": "FR-SIREN", "company_number": siren,
            "siren": siren, "status": "active", **kw}  # fmt: skip


def ch(number: str, name: str, **kw) -> dict:
    return {"name": name, "legal_name": name, "country": "GB", "registry": "GB-CH",
            "company_number": number, "status": "active", **kw}  # fmt: skip


SHARDS: dict[str, list[dict]] = {
    "sirene_stock": [
        fr(S_DANONE, "DANONE", legal_name="DANONE", postal_code="75009", city="PARIS 9",
           address="17 BOULEVARD HAUSSMANN", legal_form="SA a conseil d'administration",
           size_range="10001+", industry="C", founded_year=1919),
        fr(S_TOTAL, "TOTALENERGIES SE", postal_code="92400", city="COURBEVOIE"),
        fr(S_A, "ALPHA SERVICES", postal_code="75002", city="PARIS"),
        fr(S_B, "BRAVO SERVICES", postal_code="75003", city="PARIS"),
        fr("333333334", "BOULANGERIE MARTIN SARL", postal_code="69001", city="LYON"),
        fr("444444442", "DUPONT CONSEIL", postal_code="75001", city="PARIS"),
        fr("555555557", "DUPONT CONSEIL", postal_code="75001", city="PARIS"),
        fr("666666665", "ANCIENNE SOCIETE", status="inactive"),
        fr("777777773", "ACME FRANCE", postal_code="13001", city="MARSEILLE"),
        {"name": "SIREN SEULEMENT", "country": "FR", "siren": "888 888 881", "status": "active"},
        fr("999999999", "Jean Dupont", postal_code="33000", city="BORDEAUX"),
    ],
    "gleif_golden": [
        {"lei": L_DANONE, "name": "DANONE", "legal_name": "DANONE", "country": "FR",
         "registry": "FR-SIREN", "company_number": S_DANONE, "legal_form": "SA",
         "address": "17 BD HAUSSMANN", "postal_code": "75009", "city": "Paris", "status": "active"},
        {"lei": L_TOTAL, "name": "TOTALENERGIES SE", "country": "FR", "registry": "FR-SIREN",
         "company_number": "542 051 180", "legal_form": "SE", "status": "active"},
        {"lei": L_A, "name": "ALPHA SERVICES", "country": "FR", "registry": "FR-SIREN",
         "company_number": S_A, "status": "active"},
        {"lei": "969500ACMEFRANCE0099", "name": "ACME FRANCE", "country": "FR",
         "registry": "FR-SIREN", "company_number": "777777773", "status": "inactive"},
        {"lei": "969500GONE0000000077", "name": "DISSOUTE SA", "country": "FR",
         "registry": "FR-SIREN", "company_number": "123456782", "status": "active"},
        {"lei": L_ACME, "name": "ACME WIDGETS LTD", "country": "GB", "postal_code": "EC1A 1BB",
         "city": "London", "status": "active", "legal_form": "Private limited company"},
        {"lei": L_BETA, "name": "BETA TOOLS LTD", "country": "GB", "postal_code": "M1 1AA",
         "city": "Manchester", "status": "active"},
        {"lei": L_GAMMA, "name": "Gamma GmbH", "country": "DE", "city": "Berlin",
         "status": "active"},
        {"lei": "529900DDDDDDDDDDDD55", "name": "Delta Gone GmbH", "country": "DE",
         "status": "inactive"},
    ],
    "wikidata": [
        {"wikidata_id": "Q1", "siren": S_DANONE, "name": "Danone", "country": "FR",
         "website": "https://www.danone.com", "domain": "danone.com", "employees": 96000,
         "city": "Paris"},
        {"wikidata_id": "Q2", "lei": L_TOTAL, "name": "TotalEnergies", "country": "FR",
         "website": "https://totalenergies.com", "domain": "totalenergies.com"},
        {"wikidata_id": "Q3", "siren": S_B, "lei": L_A, "name": "Bravo", "country": "FR",
         "website": "https://bravo.example", "domain": "bravo.example"},
        {"wikidata_id": "Q5", "name": "Boulangerie Martin", "country": "FR", "city": "Lyon",
         "website": "https://boulangerie-martin.example", "domain": "boulangerie-martin.example"},
        {"wikidata_id": "Q6", "name": "Dupont Conseil", "country": "FR", "city": "Paris"},
        {"wikidata_id": "Q7", "registry": "GB-CH", "company_number": "1234567",
         "name": "Example UK", "country": "GB", "website": "https://example.co.uk",
         "domain": "example.co.uk"},
        {"wikidata_id": "Q8", "cik": "320193", "name": "Apple", "country": "US",
         "website": "https://www.apple.com", "domain": "apple.com", "employees": 150000},
        {"wikidata_id": "Q9", "registry": "NO-BRREG", "company_number": "923 609 016",
         "name": "Equinor", "country": "NO", "website": "https://equinor.no",
         "domain": "equinor.no"},
    ],
    "companies_house": [
        ch("01234567", "EXAMPLE UK LIMITED", postal_code="SW1A 1AA", city="LONDON"),
        ch("07654321", "ACME WIDGETS LIMITED", postal_code="EC1A1BB", city="LONDON"),
        ch("05555555", "BETA TOOLS LIMITED", postal_code="M2 2BB", city="MANCHESTER"),
    ],
    "edgar_bulk": [
        {"cik": "0000320193", "registry": "US-SEC", "company_number": "0000320193",
         "name": "APPLE INC", "country": "US", "postal_code": "95014", "city": "CUPERTINO",
         "employees": 161000, "revenue": 383285000000.0, "revenue_currency": "USD",
         "revenue_year": 2023, "status": "active"},
    ],
    "brreg": [
        {"registry": "NO-BRREG", "company_number": "923609016", "name": "EQUINOR ASA",
         "country": "NO", "website": "https://www.equinor.com", "domain": "equinor.com",
         "employees": 21000, "status": "active"},
    ],
    "abr": [
        {"registry": "AU-ABN", "company_number": "51 824 753 556", "name": "EXAMPLE PTY LTD",
         "country": "AU", "postal_code": "2000", "status": "active"},
    ],
}  # fmt: skip


def make_shards(directory: Path, sources=None, shuffle: int | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for source, rows in SHARDS.items():
        if sources and source not in sources:
            continue
        rows = list(rows)
        if shuffle is not None:
            random.Random(shuffle).shuffle(rows)
        write_shard(directory, source, rows)
    return directory


@pytest.fixture
def abr_registered(monkeypatch):
    src = bulk.BulkSource(
        "abr", "Australian Business Register", "CC-BY-4.0",
        "https://creativecommons.org/licenses/by/4.0/", "https://data.gov.au/abn-bulk-extract",
        frozenset({"AU"}), lambda o: bulk.ShardStats("abr"), attribution=ABR_ATTRIBUTION,
    )  # fmt: skip
    monkeypatch.setattr(bulk, "load_all", lambda: bulk.SOURCES)
    monkeypatch.setitem(bulk.SOURCES, "abr", src)
    return src


@pytest.fixture(scope="module")
def merged(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("merge")
    out = base / "packs-out"
    merge_shards(make_shards(base / "shards"), out, built_at=BUILT)
    return out


def rows(out: Path, cc: str) -> dict[str, dict]:
    """Rows of a country file keyed by company_number, else wikidata_id, else lei."""
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{out}/companies-{cc}-20261201.parquet')")
    names = [d[0] for d in cur.description]
    data = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    return {r["company_number"] or r["wikidata_id"] or r["lei"]: r for r in data}


def sources_of(row: dict) -> list[str]:
    return [p["source"] for p in json.loads(row["sources_json"])]


# -- linking -------------------------------------------------------------------------------


def test_strong_keys_join_register_gleif_and_wikidata(merged: Path) -> None:
    danone = rows(merged, "FR")[S_DANONE]
    assert danone["id"] == make_id("co", "reg", f"FR-SIREN:{S_DANONE}")
    assert danone["lei"] == L_DANONE and danone["wikidata_id"] == "Q1"
    assert danone["domain"] == "danone.com" and danone["website"] == "https://www.danone.com"
    assert sources_of(danone) == ["sirene_stock", "gleif_golden", "wikidata"]


def test_transitive_link_brings_the_website(merged: Path) -> None:
    # Wikidata knows only the LEI; GLEIF maps the LEI to the SIREN (written with spaces).
    total = rows(merged, "FR")[S_TOTAL]
    assert total["lei"] == L_TOTAL and total["wikidata_id"] == "Q2"
    assert total["domain"] == "totalenergies.com"
    assert total["name"] == "TotalEnergies" and total["legal_name"] == "TOTALENERGIES SE"
    assert total["legal_form"] == "SE"  # SIRENE has none: GLEIF fills it


def test_normalised_numbers_join_other_registers(merged: Path) -> None:
    gb = rows(merged, "GB")
    assert gb["01234567"]["wikidata_id"] == "Q7"  # "1234567" on Wikidata
    assert gb["01234567"]["domain"] == "example.co.uk"
    apple = rows(merged, "US")["0000320193"]
    assert apple["id"] == make_id("co", "reg", "US-SEC:0000320193")
    assert apple["domain"] == "apple.com" and apple["wikidata_id"] == "Q8"
    assert rows(merged, "AU")["51824753556"]["id"] == make_id("co", "reg", "AU-ABN:51824753556")


def test_never_merges_two_numbers_of_one_register(merged: Path) -> None:
    # GLEIF: L_A -> SIREN A. Wikidata Q3: SIREN B and (wrongly) L_A. A and B stay apart.
    fr_rows = rows(merged, "FR")
    a, b = fr_rows[S_A], fr_rows[S_B]
    assert a["id"] != b["id"]
    assert a["lei"] == L_A and b["lei"] is None  # the LEI stays where GLEIF put it
    assert b["wikidata_id"] == "Q3" and b["domain"] == "bravo.example"
    assert a["wikidata_id"] is None


def test_fallback_on_name_and_place(merged: Path) -> None:
    fr_rows, gb = rows(merged, "FR"), rows(merged, "GB")
    martin = fr_rows["333333334"]  # Wikidata item without ids, same name + city
    assert martin["wikidata_id"] == "Q5" and martin["domain"] == "boulangerie-martin.example"
    acme = gb["07654321"]  # GLEIF row without registration authority, same name + postcode
    assert acme["lei"] == L_ACME and acme["legal_form"] == "Private limited company"


def test_near_misses_do_not_merge(merged: Path) -> None:
    fr_rows, gb = rows(merged, "FR"), rows(merged, "GB")
    # Same name, different postcode: two companies.
    assert gb["05555555"]["lei"] is None and gb[L_BETA]["company_number"] is None
    # Two registered companies share the name and the city: the Wikidata item stays alone.
    assert fr_rows["444444442"]["wikidata_id"] is None
    assert fr_rows["555555557"]["wikidata_id"] is None
    assert fr_rows["Q6"]["id"] == make_id("co", "wd", "Q6")


# -- fields --------------------------------------------------------------------------------


def test_field_priority(merged: Path) -> None:
    danone = rows(merged, "FR")[S_DANONE]
    assert danone["name"] == "Danone" and danone["legal_name"] == "DANONE"
    assert danone["legal_form"] == "SA a conseil d'administration"  # register before GLEIF
    assert danone["address"] == "17 BOULEVARD HAUSSMANN" and danone["city"] == "PARIS 9"
    assert danone["employees"] == 96000 and danone["size_range"] == "10001+"
    apple = rows(merged, "US")["0000320193"]
    assert apple["employees"] == 161000  # EDGAR before Wikidata
    assert (apple["revenue"], apple["revenue_currency"], apple["revenue_year"]) == (
        383285000000.0, "USD", 2023)  # fmt: skip
    assert apple["size_range"] == "10001+"  # derived from employees
    equinor = rows(merged, "NO")["923609016"]
    assert equinor["domain"] == "equinor.com"  # Brønnøysund before Wikidata
    assert equinor["wikidata_id"] == "Q9"


def test_ids_registry_and_siren_columns(merged: Path) -> None:
    only = rows(merged, "FR")["888888881"]  # SIREN column only, written with spaces
    assert only["registry"] == "FR-SIREN" and only["siren"] == "888888881"
    assert only["id"] == make_id("co", "reg", "FR-SIREN:888888881")
    gamma = rows(merged, "DE")[L_GAMMA]
    assert gamma["id"] == make_id("co", "lei", L_GAMMA)


def test_inactive_companies_are_dropped(merged: Path) -> None:
    fr_rows = rows(merged, "FR")
    assert "666666665" not in fr_rows  # inactive in the register
    assert "123456782" not in fr_rows  # GLEIF names a SIREN that SIRENE does not hold
    assert fr_rows["777777773"]["status"] == "active"  # register wins over GLEIF
    assert list(rows(merged, "DE")) == [L_GAMMA]  # GLEIF-only inactive row dropped


def test_files_hold_pack_columns_sorted_by_name(merged: Path) -> None:
    con = duckdb.connect()
    f = merged / "companies-FR-20261201.parquet"
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{f}'").fetchall()]
    assert cols == list(PACK_COLUMNS)
    names = [r[0] for r in con.execute(f"SELECT name FROM '{f}'").fetchall()]
    assert names == sorted(names)
    kv = dict(con.execute(f"SELECT key::VARCHAR, value::VARCHAR FROM parquet_kv_metadata('{f}')")
              .fetchall())  # fmt: skip
    assert kv["country"] == "FR" and kv["release"] == "packs-2026-12"
    assert kv["sources"] == "sirene_stock,gleif_golden,wikidata"
    # The app reads the merged files as they are.
    co = DuckDBCompanySearch(merged).get_company(make_id("co", "reg", f"FR-SIREN:{S_DANONE}"))
    assert co is not None and co.links["website"] == "https://www.danone.com"


# -- stable ids ----------------------------------------------------------------------------


def test_ids_are_stable_across_runs_and_row_order(tmp_path: Path) -> None:
    first = tmp_path / "a"
    shards = make_shards(tmp_path / "s1", sources={"sirene_stock", "gleif_golden"})
    merge_shards(shards, first, built_at=BUILT)
    second = tmp_path / "b"
    merge_shards(make_shards(tmp_path / "s2", shuffle=7), second, built_at=BUILT)
    one, two = rows(first, "FR"), rows(second, "FR")
    for siren in (S_DANONE, S_TOTAL, S_A, "333333334", "888888881"):
        assert one[siren]["id"] == two[siren]["id"] == make_id("co", "reg", f"FR-SIREN:{siren}")
    third = tmp_path / "c"
    merge_shards(make_shards(tmp_path / "s3", shuffle=99), third, built_at=BUILT)
    for cc in ("FR", "GB", "US", "NO", "DE", "AU"):
        assert rows(second, cc) == rows(third, cc)


def test_sql_ids_match_make_id() -> None:
    con = duckdb.connect()
    install_macros(con)
    for kind, value in (("reg", "FR-SIREN:552032534"), ("lei", L_DANONE), ("wd", "Q42"),
                        ("cik", "0000320193"), ("domain", "example.co.uk"),
                        ("name", "boulangerie martin|FR")):  # fmt: skip
        got = con.execute("SELECT lk_make_id(?, ?)", [kind, value]).fetchone()[0]
        assert got == make_id("co", kind, value)


# -- outputs -------------------------------------------------------------------------------


def test_manifest(merged: Path) -> None:
    import hashlib

    m = json.loads((merged / "manifest.json").read_text())
    assert m["schema"] == 3 and m["release"] == "packs-2026-12"
    assert m["built_at"] == "2026-12-01T03:40:00+00:00"
    assert m["files"] == {"attribution": "ATTRIBUTION.md", "quality": "QUALITY.md",
                          "quality_json": "quality.json",
                          "demo": "companies-0-demo-20261201.parquet"}  # fmt: skip
    # The demo file: every country's companies, websites first; its name sorts before the
    # country files so app versions before schema 3 pick it, and it is not a country entry.
    demo = m["demo"]
    assert demo["file"] == "companies-0-demo-20261201.parquet" and demo["rows"] > 0
    demo_path = merged / demo["file"]
    assert demo["sha256"] == hashlib.sha256(demo_path.read_bytes()).hexdigest()
    assert sorted(p.name for p in merged.glob("companies-*.parquet"))[0] == demo["file"]
    kv = dict(
        duckdb.connect()
        .execute(f"SELECT key::VARCHAR, value::VARCHAR FROM parquet_kv_metadata('{demo_path}')")
        .fetchall()
    )
    assert kv["built_at"].startswith("2026-12-01") and "sirene_stock" in kv["sources"]
    by_cc = {c["country"]: c for c in m["countries"]}
    assert sorted(by_cc) == ["AU", "DE", "FR", "GB", "NO", "US"]
    fr_entry = by_cc["FR"]
    assert set(fr_entry) == {"country", "file", "rows", "bytes", "sha256", "sources",
                             "with_domain"}  # fmt: skip
    path = merged / fr_entry["file"]
    assert fr_entry["file"] == "companies-FR-20261201.parquet"
    assert fr_entry["bytes"] == path.stat().st_size
    assert fr_entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert fr_entry["rows"] == len(rows(merged, "FR")) == 11
    assert fr_entry["sources"] == ["sirene_stock", "gleif_golden", "wikidata"]
    assert fr_entry["with_domain"] == 4


def test_attribution_uses_source_licences(tmp_path: Path, abr_registered) -> None:
    out = tmp_path / "out"
    merge_shards(make_shards(tmp_path / "shards"), out, built_at=BUILT)
    text = (out / "ATTRIBUTION.md").read_text()
    assert ABR_ATTRIBUTION in text  # the source's own CC-BY attribution sentence
    assert "Australian Business Register" in text
    assert "etalab-2.0" in text and "OGL-UK-3.0" in text and "NLOD-2.0" in text
    assert "never people" in text


def test_attribution_without_registered_sources() -> None:
    from leadcommons.packs.merge import CountryResult, attribution_markdown

    au = CountryResult("AU", "companies-AU-20261201.parquet", 2, 1, "x", ["abr", "wikidata"], 0,
                       {"rows_by_source": {"abr": 2, "wikidata": 1}})  # fmt: skip
    prov = {"abr": {"licence": "CC-BY-4.0", "url": "https://data.gov.au/abn"},
            "wikidata": {"licence": "CC0-1.0", "url": "https://www.wikidata.org/"}}  # fmt: skip
    text = attribution_markdown("packs-2026-12", BUILT.isoformat(), [au], prov, registered={})
    assert "Contains data from Australian Business Register (https://data.gov.au/abn), " \
           "licensed under CC-BY-4.0." in text  # fmt: skip
    assert "Released under CC0-1.0; no attribution required" in text


def test_quality_report(merged: Path) -> None:
    q = json.loads((merged / "quality.json").read_text())
    assert q["status"] == "warn"  # one name looks like a person
    checks = {c["check"]: c["status"] for c in q["checks"]}
    assert checks["ids_unique"] == checks["no_inactive_rows"] == checks["manifest"] == "ok"
    assert checks["pack_columns"] == checks["sources_json_valid"] == "ok"
    assert checks["person_like_names"] == "warn"
    fr_q = next(c for c in q["countries"] if c["country"] == "FR")
    assert fr_q["person_like_names"] == 1
    assert fr_q["person_like_sample_ids"] == [make_id("co", "reg", "FR-SIREN:999999999")]
    assert fr_q["merges"] == {"lei": 1, "name_city": 1, "siren": 6}
    assert fr_q["input_rows"] == {"gleif_golden": 5, "sirene_stock": 11, "wikidata": 5}
    assert fr_q["duplicates_removed"] == 8 and fr_q["inactive_dropped"] == 1
    assert fr_q["not_in_register_dropped"] == 1
    assert fr_q["rows_by_source"] == {"gleif_golden": 4, "sirene_stock": 10, "wikidata": 5}
    assert fr_q["fill_rate"]["siren"] == pytest.approx(10 / 11, abs=1e-4)
    assert set(fr_q["fill_rate"]) == set(PACK_COLUMNS)
    assert q["overall"]["merges"]["name_postcode"] == 1
    assert q["overall"]["merges"]["registry"] == 2  # Q7 (GB-CH), Q9 (NO-BRREG)
    assert q["overall"]["merges"]["cik"] == 1
    assert q["overall"]["rows"] == sum(c["rows"] for c in q["countries"])
    md = (merged / "QUALITY.md").read_text()
    assert "## Health checks" in md and "## Fill rate per column" in md
    assert "Jean Dupont" not in md and "Jean Dupont" not in json.dumps(q)
    again = compute_quality(merged)
    assert again["overall"] == q["overall"]


def test_quality_flags_a_tampered_pack(tmp_path: Path, merged: Path) -> None:
    import shutil

    copy = tmp_path / "copy"
    shutil.copytree(merged, copy)
    f = copy / "companies-GB-20261201.parquet"
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * REPLACE ('inactive' AS status) FROM '{f}') TO '{tmp_path / 'x.parquet'}'"
    )
    shutil.move(tmp_path / "x.parquet", f)
    q = compute_quality(copy)
    checks = {c["check"]: c["status"] for c in q["checks"]}
    assert checks["no_inactive_rows"] == "fail" and checks["manifest"] == "fail"
    assert q["status"] == "fail"


def test_cli_merge_and_quality(tmp_path: Path) -> None:
    from leadcommons.cli import app

    shards = make_shards(tmp_path / "shards")
    out = tmp_path / "out"
    runner = CliRunner()
    res = runner.invoke(app, ["pack", "merge", str(shards), "--out", str(out),
                              "--release", "packs-2027-01"])  # fmt: skip
    assert res.exit_code == 0, res.output
    assert "companies in 6 countries" in res.output
    assert json.loads((out / "manifest.json").read_text())["release"] == "packs-2027-01"
    res = runner.invoke(app, ["pack", "quality", str(out), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["overall"]["countries"] == 6
    res = runner.invoke(app, ["pack", "quality", str(out)])
    assert res.exit_code == 0 and "person_like_names" in res.output


def test_french_overseas_and_numbers_without_leading_zeros(tmp_path: Path) -> None:
    shards = tmp_path / "shards"
    shards.mkdir()
    write_shard(
        shards,
        "sirene_stock",
        [fr("356000000", "SOCIETE ANTILLAISE", postal_code="97110", city="POINTE-A-PITRE")],
    )
    write_shard(shards, "gleif_golden", [
        {"lei": "969500ANTILLES000011", "name": "SOCIETE ANTILLAISE", "country": "GP",
         "registry": "FR-SIREN", "company_number": "356000000", "status": "active"},
        {"lei": "969500REUNION0000022", "name": "Reunion Peche", "country": "RE",
         "city": "Saint-Denis", "status": "active"},
        {"lei": "5493000CANADA0000033", "name": "MAPLE INC", "country": "CA",
         "registry": "CA-CORP", "company_number": "123456", "status": "active"},
    ])  # fmt: skip
    write_shard(shards, "canada_corps", [
        {"name": "MAPLE INC.", "country": "CA", "registry": "CA-CORP",
         "company_number": "0123456", "status": "active"},
    ])  # fmt: skip
    out = tmp_path / "out"
    merge_shards(shards, out, built_at=BUILT)
    fr_rows = rows(out, "FR")
    assert fr_rows["356000000"]["lei"] == "969500ANTILLES000011"  # GP joins FR by SIREN
    assert fr_rows["969500REUNION0000022"]["country"] == "FR"
    assert fr_rows["969500REUNION0000022"]["region"] == "RE"  # territory kept as the region
    maple = rows(out, "CA")["0123456"]  # stored as the register writes it
    assert maple["lei"] == "5493000CANADA0000033"
    assert maple["id"] == make_id("co", "reg", "CA-CORP:123456")
    assert not list(out.glob("companies-GP-*")) and not list(out.glob("companies-RE-*"))


def test_keep_unregistered_for_limited_runs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    merge_shards(make_shards(tmp_path / "shards"), out, built_at=BUILT, keep_unregistered=True)
    gone = rows(out, "FR")["123456782"]
    assert gone["id"] == make_id("co", "reg", "FR-SIREN:123456782")
    assert sources_of(gone) == ["gleif_golden"]


def test_merge_without_shards_fails(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        merge_shards(tmp_path, tmp_path / "out")


# -- scale ---------------------------------------------------------------------------------


def generate_scale_shards(directory: Path, total: int, seed: int = 1) -> dict[str, int]:
    """Synthetic shards of about `total` rows, shaped like the real registers: half French
    (SIRENE), then UK, Australia, Canada, Norway and the US, plus GLEIF (8% of register rows,
    most carrying the register number) and Wikidata (3%, by SIREN / CH number / CIK / LEI, a
    few by name only), so every join path runs. Written with DuckDB, no Python loop."""
    directory.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    prov = {
        s: json.dumps([{"source": s, "licence": lic, "url": f"https://data.example.org/{s}",
                        "fetched_at": "2026-11-30T00:00:00+00:00"}])
        for s, lic in LICENCES.items()
    }  # fmt: skip
    share = {"sirene_stock": 0.50, "companies_house": 0.18, "abr": 0.10, "canada_corps": 0.05,
             "brreg": 0.04, "edgar_bulk": 0.02}  # fmt: skip
    # (country, register, SQL of the register number of row i): unique, register-shaped.
    regs = {"sirene_stock": ("FR", "FR-SIREN", "(100000000 + i)::VARCHAR"),
            "companies_house": ("GB", "GB-CH", "lpad(i::VARCHAR, 8, '0')"),
            "abr": ("AU", "AU-ABN", "(10000000000 + i)::VARCHAR"),
            "canada_corps": ("CA", "CA-CORP", "(1000000 + i)::VARCHAR"),
            "brreg": ("NO", "NO-BRREG", "(900000000 + i)::VARCHAR"),
            "edgar_bulk": ("US", "US-SEC", "lpad(i::VARCHAR, 10, '0')")}  # fmt: skip
    cols = ", ".join(PACK_COLUMNS)
    counts = {}

    def copy(select: str, name: str) -> None:
        con.execute(f"COPY ({select}) TO '{directory / name}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        counts[name] = con.execute(f"SELECT count(*) FROM '{directory / name}'").fetchone()[0]

    for src, part in share.items():
        cc, reg, num = regs[src]
        n = int(total * part)
        copy(f"""
          SELECT {cols} FROM (
            SELECT 'x' || i AS id,
              'COMPANY ' || (hash(i + {seed}) % {max(n // 3, 1)}) || ' ' || {cc!r} AS name,
              NULL AS legal_name, NULL AS domain, {cc!r} AS country, 'R' || (i % 13) AS region,
              {TOWN} AS city, chr(65 + (i % 21)::INT) AS industry,
              NULL AS industry_label, NULL AS size_range, (1900 + i % 120)::INT AS founded_year,
              NULL AS lei, NULL AS wikidata_id,
              CASE WHEN {src!r} = 'brreg' AND i % 4 = 0 THEN 'https://c' || i || '.no' END
                AS website,
              {lit(prov[src])} AS sources_json,
              CASE WHEN {src!r} = 'sirene_stock' THEN {num} END AS siren,
              CASE WHEN {src!r} = 'edgar_bulk' THEN {num} END AS cik,
              {reg!r} AS registry, {num} AS company_number,
              CASE WHEN i % 50 = 0 THEN 'inactive' ELSE 'active' END AS status,
              'LF' || (i % 7) AS legal_form, (i % 977) || ' MAIN STREET' AS address,
              'P' || (i % 5000) AS postal_code, NULL AS phone,
              CASE WHEN i % 9 = 0 THEN (i % 400)::INT END AS employees,
              CASE WHEN i % 31 = 0 THEN (i % 100000) * 1000.0 END AS revenue,
              CASE WHEN i % 31 = 0 THEN 'EUR' END AS revenue_currency,
              CASE WHEN i % 31 = 0 THEN 2025 END AS revenue_year
            FROM range(1, {n + 1}) t(i)
          )
        """, f"{src}.parquet")  # fmt: skip
    # GLEIF: 8% of register rows with the register number, plus 10% as many abroad without.
    gl = []
    for src, part in share.items():
        cc, reg, num = regs[src]
        n = int(total * part)
        gl.append(f"""
          SELECT 'COMPANY ' || (hash(i + {seed}) % {max(n // 3, 1)}) || ' ' || {cc!r} AS name,
            {cc!r} AS country, {reg!r} AS registry, {num} AS company_number,
            'P' || (i % 5000) AS postal_code, i AS i
          FROM range(1, {n + 1}, 12) t(i)""")
    copy(f"""
      SELECT {cols} FROM (
        SELECT 'g' || row_number() OVER () AS id, name, name AS legal_name, NULL AS domain,
          country, NULL AS region, NULL AS city, NULL AS industry, NULL AS industry_label,
          NULL AS size_range, NULL::INT AS founded_year,
          upper(substr(md5(registry || company_number), 1, 18)) || '00' AS lei,
          NULL AS wikidata_id, NULL AS website, {lit(prov['gleif_golden'])} AS sources_json,
          NULL AS siren, NULL AS cik,
          CASE WHEN i % 10 <> 0 THEN registry END AS registry,
          CASE WHEN i % 10 <> 0 THEN company_number END AS company_number,
          'active' AS status, 'ELF' AS legal_form, NULL AS address, postal_code, NULL AS phone,
          NULL::INT AS employees, NULL::DOUBLE AS revenue, NULL AS revenue_currency,
          NULL::INT AS revenue_year
        FROM ({" UNION ALL ".join(gl)})
        UNION ALL
        SELECT 'gx' || i, 'GMBH ' || i, 'GMBH ' || i, NULL,
          ['DE', 'IT', 'ES', 'NL', 'LU', 'BE', 'CH', 'AT'][1 + (i % 8)::INT], NULL, 'CITY ' || i,
          NULL, NULL, NULL, NULL, upper(substr(md5('x' || i), 1, 18)) || '00', NULL, NULL,
          {lit(prov['gleif_golden'])}, NULL, NULL, NULL, NULL, 'active', NULL, NULL,
          'Z' || (i % 900), NULL, NULL, NULL, NULL, NULL
        FROM range(1, {int(total * 0.008) + 1}) t(i)
      )
    """, "gleif_golden.parquet")  # fmt: skip
    # Wikidata: SIREN / CH number / CIK bridges, LEI-only (transitive) and name-only items.
    nfr, ngb, nus = (
        int(total * share[s]) for s in ("sirene_stock", "companies_house", "edgar_bulk")
    )
    copy(f"""
      SELECT {cols} FROM (
        SELECT 'w' || q AS id, 'Item ' || q AS name, NULL AS legal_name,
          'site' || q || '.example' AS domain, country, NULL AS region, NULL AS city,
          NULL AS industry, NULL AS industry_label, NULL AS size_range, NULL::INT AS founded_year,
          lei, 'Q' || q AS wikidata_id, 'https://site' || q || '.example' AS website,
          {lit(prov['wikidata'])} AS sources_json, siren, cik, registry, company_number,
          NULL AS status, NULL AS legal_form, NULL AS address, NULL AS postal_code,
          NULL AS phone, CASE WHEN q % 3 = 0 THEN (q % 5000)::INT END AS employees,
          NULL::DOUBLE AS revenue, NULL AS revenue_currency, NULL::INT AS revenue_year
        FROM (
          SELECT i AS q, 'FR' AS country, (100000000 + i)::VARCHAR AS siren, NULL AS cik,
            NULL AS lei, NULL AS registry, NULL AS company_number
          FROM range(1, {nfr + 1}, 40) t(i)
          UNION ALL
          SELECT 100000000 + i, 'GB', NULL, NULL, NULL, 'GB-CH', i::VARCHAR  -- unpadded
          FROM range(1, {ngb + 1}, 40) t(i)
          UNION ALL
          SELECT 200000000 + i, 'US', NULL, i::VARCHAR, NULL, NULL, NULL
          FROM range(1, {nus + 1}, 5) t(i)
          UNION ALL  -- LEI only: joins the register through GLEIF
          SELECT 300000000 + i, 'FR', NULL, NULL,
            upper(substr(md5('FR-SIREN' || (100000000 + i)::VARCHAR), 1, 18)) || '00', NULL, NULL
          FROM range(1, {nfr + 1}, 12 * 7) t(i)
        )
        UNION ALL  -- no identifier: only the name fallback can place them
        SELECT 'wn' || i, 'COMPANY ' || (hash(i + {seed}) % {max(nfr // 3, 1)}) || ' FR', NULL,
          'named' || i || '.example', 'FR', NULL, {TOWN}, NULL, NULL, NULL,
          NULL, NULL, 'Q' || (400000000 + i), NULL, {lit(prov['wikidata'])}, NULL, NULL, NULL,
          NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL
        FROM range(1, {nfr + 1}, 500) t(i)
      )
    """, "wikidata.parquet")  # fmt: skip
    return counts


# A town name made of letters only (digits are dropped from city keys, as in "PARIS 9").
TOWN = (
    "'TOWN ' || chr(65 + (i % 26)::INT) || chr(65 + (i // 26 % 26)::INT) "
    "|| chr(65 + (i // 676 % 5)::INT)"
)


def lit(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


@pytest.mark.skipif(not os.environ.get("LEADCOMMONS_SCALE_ROWS"),
                    reason="scale check: set LEADCOMMONS_SCALE_ROWS (e.g. 30000000)")  # fmt: skip
def test_merge_at_scale(tmp_path: Path) -> None:
    """Scale check (not run in CI). Run in a subprocess to measure peak memory:

        LEADCOMMONS_SCALE_ROWS=30000000 .venv/bin/pytest -q -s tests/test_pack_merge.py -k scale

    Measured on a 4-core / 15 GB sandbox with LEADCOMMONS_SCALE_ROWS=30000000 (30.0M input rows,
    France 15M): 393 s wall and 9.8 GiB peak RSS with the default memory limit, 403 s and
    4.2 GiB with LEADCOMMONS_MERGE_MEMORY=4GB (details in the packs/merge.py docstring).
    """
    import resource
    import subprocess
    import sys
    import time

    total = int(os.environ["LEADCOMMONS_SCALE_ROWS"])
    base = Path(os.environ.get("LEADCOMMONS_SCALE_DIR") or tmp_path)
    t0 = time.monotonic()
    counts = generate_scale_shards(base / "shards", total)
    gen = time.monotonic() - t0
    t0 = time.monotonic()
    out = base / "out"
    cmd = [sys.executable, "-m", "leadcommons", "pack", "merge", str(base / "shards"),
           "--out", str(out)]  # fmt: skip
    subprocess.run(cmd, check=True)  # its own process: peak memory is the merge's alone
    took = time.monotonic() - t0
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024 / 1024  # Linux: KiB
    q = json.loads((out / "quality.json").read_text())
    link = q["link_stats"]
    print(
        f"\nscale: {sum(counts.values()):,} input rows {counts} generated in {gen:.0f} s; "
        f"merged into {q['overall']['rows']:,} rows in {took:.0f} s, peak RSS {peak:.1f} GiB; "
        f"link {link['timings']}; merges {q['overall']['merges']}; status {q['status']}"
    )
    assert q["status"] in ("ok", "warn")
    assert q["overall"]["rows"] > 0


def test_files_hold_only_their_country_and_former_countries_are_dropped(tmp_path: Path) -> None:
    shards = tmp_path / "shards"
    shards.mkdir()
    # A register row without a country, linked by its number to a GLEIF row naming another
    # country: it lands in one file, and that file's country is written on the row.
    write_shard(shards, "companies_house", [ch("01234567", "Mixed Ltd", country=None),
                                            ch("07654321", "Plain Ltd")])  # fmt: skip
    gleif = {"name": "Mixed Ltd", "country": "IE", "registry": "GB-CH",
             "company_number": "01234567", "lei": "5493001KJTIIGC8Y1R12",
             "status": "active"}  # fmt: skip
    write_shard(shards, "gleif_golden", [gleif])
    write_shard(
        shards, "wikidata", [{"name": "Old Kombinat", "country": "DD", "wikidata_id": "Q1"}]
    )
    out = tmp_path / "out"
    merge_shards(shards, out, built_at=BUILT)
    files = sorted(p.name for p in out.glob("companies-[A-Z][A-Z]-*.parquet"))
    assert files and not any("-DD-" in f for f in files)
    con = duckdb.connect()
    for f in files:
        cc = f.split("-")[1]
        sql = f"SELECT count(*) FROM read_parquet('{out / f}') WHERE country IS DISTINCT FROM ?"
        assert con.execute(sql, [cc]).fetchone()[0] == 0
    assert compute_quality(out)["status"] != "fail"
