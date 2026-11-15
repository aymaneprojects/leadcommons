"""Bulk register sources A: pipeline core (packs/bulk.py), GLEIF golden copy, SIRENE stock,
Companies House and SEC EDGAR bulk, against fixtures that copy the real file formats."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from leadcommons.cli import app
from leadcommons.connectors.bulk import (
    SOURCES,
    BuildOptions,
    edgar_bulk,
    gleif_golden,
    load_all,
    sirene_stock,
)
from leadcommons.connectors.bulk import companies_house as ch
from leadcommons.models import make_id
from leadcommons.packs import bulk
from leadcommons.packs.domains import normalize_domain
from leadcommons.packs.isic import naf_section, sic_section
from leadcommons.search import PACK_COLUMNS

FIX = Path(__file__).parent / "fixtures" / "bulk"
TODAY = date(2026, 11, 15)


def rows(path: Path) -> list[dict]:
    con = duckdb.connect()
    cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def columns(path: Path) -> list[str]:
    con = duckdb.connect()
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()]


def opts(tmp_path: Path, **kw) -> BuildOptions:
    return BuildOptions(work_dir=tmp_path / "work", out_dir=tmp_path / "shards", **kw)


def con_with_macros():
    con = duckdb.connect()
    bulk.install_macros(con)
    return con


# --------------------------------------------------------------------------- core helpers


def test_macros_agree_with_the_python_helpers() -> None:
    con = con_with_macros()
    for div in range(0, 100):
        code = f"{div:02d}.10Z"
        assert con.execute("SELECT lc_nace_section(?)", [code]).fetchone()[0] == naf_section(code)
    for sic in [100, 999, 1311, 1521, 2834, 3571, 4813, 4911, 4941, 4955, 5812, 5961, 6022,
                6513, 6798, 7011, 7311, 7372, 7389, 8062, 8711, 9995, 9999]:  # fmt: skip
        got = con.execute("SELECT lc_sic_section(?)", [str(sic)]).fetchone()[0]
        assert got == sic_section(sic), sic
    urls = ["https://www.Example.co.uk/path?q=1", "http://shop.example.com", "example.org", "foo",
            "", "https://user@sub.example.com.au:8443/x", "www.example.fr."]  # fmt: skip
    for url in urls:
        assert con.execute("SELECT lc_domain(?)", [url]).fetchone()[0] == normalize_domain(url), url
    for value in ["PARIS 8", "SAINT-ETIENNE", "L'ISLE-ADAM", "NEWCASTLE UPON TYNE"]:
        assert con.execute("SELECT lc_title(?)", [value]).fetchone()[0] == value.title()
    assert con.execute("SELECT lc_title('McLean')").fetchone()[0] == "McLean"  # mixed case kept
    assert con.execute(
        "SELECT lc_year('06/06/2012'), lc_year('2999-01-01'), lc_year(NULL)"
    ).fetchone() == (
        2012,
        None,
        None,
    )
    assert con.execute(
        "SELECT lc_gb_number('sc35033'), lc_gb_number('1234567'), lc_gb_number('x')"
    ).fetchone() == (
        "SC035033",
        "01234567",
        None,
    )
    assert con.execute(
        "SELECT lc_phone_nanp('(415) 555-0100'), lc_phone_nanp('123')"
    ).fetchone() == (
        "+14155550100",
        None,
    )


def test_stable_ids_match_make_id() -> None:
    con = con_with_macros()
    cases = [("FR-SIREN", "552120222", None, None), (None, None, "969500FAKE00000000A1", None),
             (None, None, None, "0000320193"), ("GB-CH", "01234567", "X" * 20, None)]  # fmt: skip
    for reg, num, lei, cik in cases:
        sql = con.execute("SELECT lc_company_id(?, ?, ?, ?)", [reg, num, lei, cik]).fetchone()[0]
        assert sql == bulk.stable_company_id(reg, num, lei, cik)
    assert bulk.stable_company_id("FR-SIREN", "552120222") == make_id(
        "co", "reg", "FR-SIREN:552120222"
    )


def test_csv_source_skips_malformed_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.csv"
    path.write_text('"a"," b"\n"1","x"\n"2 "bad","y"\n"3","z, with comma"\n', encoding="utf-8")
    con = duckdb.connect()
    sql, cols = bulk.csv_source(path, "rj")
    got = con.execute(f"SELECT {cols['a']}, {cols['B']} FROM {sql} ORDER BY 1").fetchall()
    assert got == [("1", "x"), ("3", "z, with comma")]
    assert bulk.rejected_lines(con, "rj") == 1


class RangeServer:
    """httpx mock transport serving one payload, with optional Range support and failures."""

    def __init__(self, payload: bytes, *, ranges: bool = True, fail_first: int = 0,
                 cut_first_at: int | None = None, etag: str | None = None) -> None:  # fmt: skip
        self.payload, self.ranges, self.fail_first = payload, ranges, fail_first
        self.cut, self.etag = cut_first_at, etag
        self.calls: list[str | None] = []
        self.if_range: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("Range")
        self.calls.append(rng)
        self.if_range.append(request.headers.get("If-Range"))
        assert "contact" in request.headers["User-Agent"]
        if len(self.calls) <= self.fail_first:
            return httpx.Response(503)
        if rng and request.headers.get("If-Range") not in (None, self.etag):
            rng = None  # the file changed since the partial download: send it whole
        total = len(self.payload)
        start = int(rng.split("=")[1].rstrip("-")) if rng and self.ranges else 0
        if start >= total and rng:
            return httpx.Response(416, headers={"Content-Range": f"bytes */{total}"})
        body = self.payload[start:]
        headers = {"Content-Length": str(len(body))}
        if self.etag:
            headers["ETag"] = self.etag
        if self.cut is not None and len(self.calls) == 1:
            body = body[: self.cut]  # connection dropped mid-way
        if rng and self.ranges:
            headers["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
            return httpx.Response(206, stream=httpx.ByteStream(body), headers=headers)
        return httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)


@pytest.mark.parametrize("ranges", [True, False])
def test_download_resumes_a_partial_file(tmp_path: Path, ranges: bool) -> None:
    payload = bytes(range(256)) * 4000
    dest = tmp_path / "big.zip"
    (tmp_path / "big.zip.part").write_bytes(payload[:300_000])
    server = RangeServer(payload, ranges=ranges)
    client = httpx.Client(transport=httpx.MockTransport(server))
    got = bulk.download(
        "https://example.org/big.zip", dest, client=client, user_agent="t contact:x@y.z"
    )
    assert got == dest and dest.read_bytes() == payload
    assert server.calls == ["bytes=300000-"]
    assert not (tmp_path / "big.zip.part").exists()


def test_download_retries_and_continues_after_a_cut(tmp_path: Path) -> None:
    payload = b"0123456789" * 50_000
    server = RangeServer(payload, fail_first=1, cut_first_at=None)
    client = httpx.Client(transport=httpx.MockTransport(server))
    dest = bulk.download("https://example.org/f", tmp_path / "f", client=client,
                         user_agent="t contact:x@y.z", sleep=lambda s: None)  # fmt: skip
    assert dest.read_bytes() == payload and server.calls[0] is None
    cut = RangeServer(payload, cut_first_at=123_456)
    client = httpx.Client(transport=httpx.MockTransport(cut))
    dest = bulk.download("https://example.org/g", tmp_path / "g", client=client,
                         user_agent="t contact:x@y.z", sleep=lambda s: None)  # fmt: skip
    assert dest.read_bytes() == payload
    assert cut.calls == [None, "bytes=123456-"]


def test_download_resume_checks_the_file_did_not_change(tmp_path: Path) -> None:
    old, new = b"A" * 200_000, b"B" * 250_000
    first = RangeServer(old, cut_first_at=50_000, etag='"v1"', fail_first=0)
    client = httpx.Client(transport=httpx.MockTransport(first))
    with pytest.raises(RuntimeError, match="giving up"):
        bulk.download("https://example.org/latest", tmp_path / "f", client=client, retries=0,
                      user_agent="t contact:x@y.z", sleep=lambda s: None)  # fmt: skip
    assert (tmp_path / "f.part").stat().st_size == 50_000
    changed = RangeServer(new, etag='"v2"')  # "latest" now points to a newer file
    client = httpx.Client(transport=httpx.MockTransport(changed))
    dest = bulk.download("https://example.org/latest", tmp_path / "f", client=client,
                         user_agent="t contact:x@y.z", sleep=lambda s: None)  # fmt: skip
    assert changed.if_range == ['"v1"'] and dest.read_bytes() == new
    assert not (tmp_path / "f.part.validator").exists()


def test_download_complete_part_and_local_files(tmp_path: Path) -> None:
    payload = b"x" * 1000
    (tmp_path / "done.part").write_bytes(payload)
    client = httpx.Client(transport=httpx.MockTransport(RangeServer(payload)))
    dest = bulk.download("https://example.org/done", tmp_path / "done", client=client,
                         user_agent="t contact:x@y.z")  # fmt: skip
    assert dest.read_bytes() == payload
    local = FIX / "BasicCompanyData.csv"
    assert bulk.download(str(local), tmp_path / "unused") == local
    with pytest.raises(FileNotFoundError):
        bulk.download(str(tmp_path / "missing.zip"), tmp_path / "m")


def test_discard_never_deletes_files_outside_the_work_dir(tmp_path: Path) -> None:
    outside = tmp_path / "keep.csv"
    outside.write_text("x")
    bulk.discard(outside, tmp_path / "work")
    assert outside.exists()
    inside = tmp_path / "work" / "raw.csv"
    inside.parent.mkdir()
    inside.write_text("x")
    bulk.discard(inside, tmp_path / "work")
    assert not inside.exists()


def test_run_source_errors_and_skips(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValueError, match="unknown bulk source"):
        bulk.run_source("nope", opts(tmp_path))
    stats = bulk.run_source("sirene_stock", opts(tmp_path, countries=frozenset({"gb"})))
    assert stats.rows == 0 and not stats.files and "FR" in stats.notes
    empty = tmp_path / "empty.csv"
    empty.write_text((FIX / "BasicCompanyData.csv").read_text().splitlines()[0] + "\n")
    monkeypatch.setenv(ch.URL_ENV, str(empty))
    with pytest.raises(RuntimeError, match="no rows"):
        bulk.run_source("companies_house", opts(tmp_path))


def test_registry_lists_the_four_sources() -> None:
    load_all()
    licences = {"gleif_golden": "CC0-1.0", "sirene_stock": "etalab-2.0",
                "companies_house": "OGL-UK-3.0", "edgar_bulk": "public-domain"}  # fmt: skip
    for name, licence in licences.items():
        assert SOURCES[name].licence == licence
    assert SOURCES["sirene_stock"].countries == {"FR"}
    assert SOURCES["companies_house"].countries == {"GB"}


# --------------------------------------------------------------------------- GLEIF


@pytest.fixture
def gleif_rows(tmp_path: Path, monkeypatch) -> list[dict]:
    zipped = tmp_path / "golden.csv.zip"
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(
            FIX / "gleif-golden-copy.csv", "20261115-0000-gleif-goldencopy-lei2-golden-copy.csv"
        )
    monkeypatch.setenv(gleif_golden.URL_ENV, str(zipped))
    stats = bulk.run_source("gleif_golden", opts(tmp_path))
    assert stats.files == [tmp_path / "shards" / "gleif_golden.parquet"]
    assert columns(stats.files[0]) == list(PACK_COLUMNS)
    assert stats.skipped == 5
    assert zipped.exists()  # a local input is never deleted
    assert not (tmp_path / "work" / "gleif_golden").exists()  # work dir cleaned up
    return rows(stats.files[0])


def test_gleif_keeps_active_companies_only(gleif_rows: list[dict]) -> None:
    names = {r["name"] for r in gleif_rows}
    assert len(gleif_rows) == 13
    assert "EXAMPLE TRADING LIMITED" in names  # LAPSED registration, active entity
    for dropped in ["EXAMPLE GLOBAL EQUITY FUND", "GONE TRADING GMBH", "RETIRED EXAMPLE BV",
                    "JANE DOE", "EXAMPLE BANK AG, LONDON BRANCH"]:  # fmt: skip
        assert dropped not in names
    assert all(r["status"] == "active" for r in gleif_rows)
    src = json.loads(gleif_rows[0]["sources_json"])
    assert src[0]["source"] == "gleif_golden" and src[0]["licence"] == "CC0-1.0"


def test_gleif_registration_authority_links(gleif_rows: list[dict]) -> None:
    by_lei = {r["lei"]: r for r in gleif_rows}
    expected = {
        "969500FAKE00000000A1": ("FR-SIREN", "552120222"),  # RA000189, "552 120 222"
        "213800FAKE00000000B2": ("GB-CH", "01234567"),  # RA000585, zero-padded
        "213800FAKE00000000C3": ("GB-CH", "SC035033"),  # RA000587 (Scotland)
        "5967007FAKE0000000D4": ("NO-BRREG", "923609016"),  # RA000473
        "549300FAKE00000000E5": ("AU-ABN", "37160626102"),  # RA000013, ABN check digits ok
        "549300FAKE00000000F6": ("AU-ABN", "50001621129"),  # RA000014: ACN 001 621 129 -> ABN
        "549300FAKE00000000G7": ("AU-ACN", "001000009"),  # ambiguous ABN prefix: stays an ACN
        "549300FAKE00000000H8": ("CA-CORP", "7280076"),  # RA000072, "728007-6"
        "724500FAKE0000000Q15": ("NL-KVK", "33014286"),  # RA000463
        "549300FAKE0000000R16": ("DK-CVR", "11679471"),  # RA000170
        "213800FAKE0000000S17": ("GB-CH", "09876543"),  # via the validation authority
        "549300FAKE00000000J9": (None, None),  # Delaware register: not one we link
        "529900FAKE0000000T18": (None, None),  # a German local court register
    }
    assert {lei: (r["registry"], r["company_number"]) for lei, r in by_lei.items()} == expected
    assert by_lei["969500FAKE00000000A1"]["siren"] == "552120222"
    assert all(r["siren"] is None for lei, r in by_lei.items() if lei != "969500FAKE00000000A1")
    fr = by_lei["969500FAKE00000000A1"]
    assert fr["id"] == make_id("co", "reg", "FR-SIREN:552120222")
    assert by_lei["529900FAKE0000000T18"]["id"] == make_id("co", "lei", "529900FAKE0000000T18")


def test_gleif_ra_codes_table() -> None:
    reg = {code: r for code, (r, _) in gleif_golden.RA_REGISTRIES.items()}
    assert reg["RA000189"] == "FR-SIREN"
    assert reg["RA000585"] == reg["RA000586"] == reg["RA000587"] == "GB-CH"
    assert reg["RA000472"] == reg["RA000473"] == "NO-BRREG"
    assert reg["RA000013"] == reg["RA000014"] == "AU-ABN"
    assert reg["RA000072"] == "CA-CORP"


def test_gleif_abn_checks() -> None:
    con = con_with_macros()
    for stmt in gleif_golden.macro_sql():
        con.execute(stmt)
    assert con.execute("SELECT lc_abn('37160626102'), lc_abn('37160626103')").fetchone() == (
        "37160626102",
        None,
    )
    assert con.execute("SELECT lc_acn_abn('160626102'), lc_acn_abn('001000009')").fetchone() == (
        "37160626102",
        None,
    )


def test_gleif_addresses(gleif_rows: list[dict]) -> None:
    by_lei = {r["lei"]: r for r in gleif_rows}
    us = by_lei["549300FAKE00000000J9"]  # Delaware registered agent -> headquarters used
    assert (us["city"], us["region"], us["postal_code"], us["address"]) == (
        "San Francisco",
        "CA",
        "94105",
        "500 Market Street",
    )
    fr = by_lei["969500FAKE00000000A1"]
    assert (fr["country"], fr["city"], fr["postal_code"], fr["founded_year"]) == (
        "FR",
        "Paris",
        "75002",
        2001,
    )
    assert fr["legal_form"] == "SA à conseil d'administration"


def test_gleif_country_filter_and_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(gleif_golden.URL_ENV, str(FIX / "gleif-golden-copy.csv"))
    stats = bulk.run_source("gleif_golden", opts(tmp_path, countries=frozenset({"FR", "DE"})))
    assert {r["country"] for r in rows(stats.files[0])} == {"FR", "DE"}
    assert (FIX / "gleif-golden-copy.csv").exists()


def test_gleif_finds_the_csv_url_in_the_api_answer() -> None:
    answer = {
        "data": {
            "publish_date": "2026-11-15 00:00:00",
            "lei2": {
                "full_file": {
                    "csv": {
                        "url": "https://x/20261115-0000-gleif-goldencopy-lei2-golden-copy.csv.zip"
                    },
                    "xml": {
                        "url": "https://x/20261115-0000-gleif-goldencopy-lei2-golden-copy.xml.zip"
                    },
                }
            },
        }
    }
    assert gleif_golden._find_csv_url(answer).endswith("lei2-golden-copy.csv.zip")
    other = {
        "files": [{"link": "https://y/20261115-0000-gleif-goldencopy-lei2-golden-copy.csv.zip"}]
    }
    assert gleif_golden._find_csv_url(other).startswith("https://y/")
    assert gleif_golden._find_csv_url({"data": {}}) is None


# --------------------------------------------------------------------------- SIRENE


def sirene_env(monkeypatch, ul: Path, etab: Path) -> None:
    monkeypatch.setenv(sirene_stock.URL_ENVS["ul"], str(ul))
    monkeypatch.setenv(sirene_stock.URL_ENVS["etab"], str(etab))


def to_parquet(src: Path, dest: Path, casts: str = "") -> Path:
    con = duckdb.connect()
    con.execute(
        f"COPY (SELECT * {casts} FROM read_csv('{src}', all_varchar=true)) "
        f"TO '{dest}' (FORMAT PARQUET)"
    )
    return dest


@pytest.fixture(params=["csv", "parquet", "zip"])
def sirene_rows(request, tmp_path: Path, monkeypatch) -> list[dict]:
    ul, etab = FIX / "StockUniteLegale_utf8.csv", FIX / "StockEtablissement_utf8.csv"
    if request.param == "parquet":  # INSEE's Parquet types booleans and dates
        created = "dateCreationUniteLegale"
        ul = to_parquet(
            ul, tmp_path / "ul.parquet", f"REPLACE (CAST({created} AS DATE) AS {created})"
        )
        siege = "etablissementSiege"
        etab = to_parquet(
            etab, tmp_path / "etab.parquet", f"REPLACE (CAST({siege} AS BOOLEAN) AS {siege})"
        )
    elif request.param == "zip":
        zips = []
        for path in (ul, etab):
            z = tmp_path / (path.stem + ".zip")
            with zipfile.ZipFile(z, "w") as zf:
                zf.write(path, path.name)
            zips.append(z)
        ul, etab = zips
    sirene_env(monkeypatch, ul, etab)
    stats = bulk.run_source("sirene_stock", opts(tmp_path))
    assert columns(stats.files[0]) == list(PACK_COLUMNS)
    assert ul.exists() and etab.exists()
    return rows(stats.files[0])


def test_sirene_keeps_active_diffusible_companies(sirene_rows: list[dict]) -> None:
    sirens = {r["siren"] for r in sirene_rows}
    assert sirens == {"552120222", "123456782", "222222226", "666666668", "777777776",
                      "888888884", "999999992"}  # fmt: skip
    assert all(
        r["registry"] == "FR-SIREN" and r["company_number"] == r["siren"] for r in sirene_rows
    )
    assert all(r["country"] == "FR" and r["status"] == "active" for r in sirene_rows)
    assert json.loads(sirene_rows[0]["sources_json"])[0]["licence"] == "etalab-2.0"


def test_sirene_never_writes_a_natural_persons_name(sirene_rows: list[dict]) -> None:
    text = " ".join(str(v) for r in sirene_rows for v in r.values() if isinstance(v, str)).upper()
    for word in ["DUPONT", "JEAN", "MARTIN", "MARIE", "DURAND", "PAUL"]:
        assert word not in text
    baker = next(r for r in sirene_rows if r["siren"] == "222222226")
    assert baker["name"] == "BOULANGERIE DU CENTRE"
    assert baker["legal_form"] == "Entrepreneur individuel"


def test_sirene_fields(sirene_rows: list[dict]) -> None:
    by = {r["siren"]: r for r in sirene_rows}
    big = by["552120222"]
    assert (big["name"], big["size_range"], big["founded_year"], big["industry"]) == (
        "SOCIETE EXEMPLE",
        "10001+",
        1955,
        "K",
    )
    assert (big["address"], big["postal_code"], big["city"], big["region"]) == (
        "12 RUE DE LA PAIX",
        "75002",
        "Paris 2",
        "Île-de-France",
    )
    assert big["legal_form"] == "SA à conseil d'administration"
    assert big["id"] == make_id("co", "reg", "FR-SIREN:552120222")
    tech = by["123456782"]
    assert tech["address"] == "5 B AVENUE DE LA REPUBLIQUE" and tech["industry_label"].startswith(
        "Information"
    )
    assert by["666666668"]["region"] == "Corse" and by["666666668"]["size_range"] is None
    assert (
        by["777777776"]["address"] is None and by["777777776"]["city"] is None
    )  # siège not diffusible
    assert by["888888884"]["industry"] is None and by["888888884"]["region"] == "La Réunion"
    assert by["999999992"]["address"] == "7 QUAI DE LA LOIRE"  # the head office, not the old site
    assert by["999999992"]["name"] == "EXEMPLE CONSEIL"
    assert by["222222226"]["postal_code"] == "01000"


def sample_keys(path: Path, n: int | None = None) -> list[str]:
    """Register keys of a shard in SAMPLE_ORDER (the order a limited run keeps)."""
    con = duckdb.connect()
    limit = f"LIMIT {n}" if n else ""
    return [
        r[0]
        for r in con.execute(
            f"SELECT coalesce(registry || ':' || company_number, lei, cik, name) "
            f"FROM read_parquet('{path}') ORDER BY {bulk.SAMPLE_ORDER} {limit}"
        ).fetchall()
    ]


def test_limited_shards_are_one_shared_sample(tmp_path: Path, monkeypatch) -> None:
    sirene_env(monkeypatch, FIX / "StockUniteLegale_utf8.csv", FIX / "StockEtablissement_utf8.csv")
    monkeypatch.setenv(gleif_golden.URL_ENV, str(FIX / "gleif-golden-copy.csv"))
    monkeypatch.setenv(ch.URL_ENV, str(FIX / "BasicCompanyData.csv"))
    for name, n in [("sirene_stock", 3), ("gleif_golden", 4), ("companies_house", 5)]:
        full = bulk.run_source(name, opts(tmp_path / "full"))
        part = bulk.run_source(name, opts(tmp_path / "part", limit=n))
        assert len(rows(part.files[0])) == n
        # a limited shard keeps the rows with the smallest key hashes: the same rule in every
        # source, so two sources' samples share the companies they have in common
        assert sorted(sample_keys(part.files[0])) == sorted(sample_keys(full.files[0], n))


def test_sirene_picks_parquet_stock_files_from_the_dataset_api() -> None:
    resources = [
        {"title": "Sirene : Fichier StockUniteLegaleHistorique", "format": "zip",
         "url": "https://files.data.gouv.fr/insee-sirene/StockUniteLegaleHistorique_utf8.zip"},
        {"title": "Sirene : Fichier StockUniteLegale du 01 Novembre 2026", "format": "zip",
         "url": "https://files.data.gouv.fr/insee-sirene/StockUniteLegale_utf8.zip",
         "latest": "https://www.data.gouv.fr/fr/datasets/r/ul-zip"},
        {"title": "Sirene : Fichier StockUniteLegale du 01 Novembre 2026", "format": "parquet",
         "url": "https://object.files.data.gouv.fr/x/StockUniteLegale_utf8.parquet",
         "latest": "https://www.data.gouv.fr/fr/datasets/r/ul-parquet"},
        {"title": "Sirene : Fichier StockEtablissementLiensSuccession", "format": "zip",
         "url": "https://files.data.gouv.fr/insee-sirene/StockEtablissementLiensSuccession_utf8.zip"},
        {"title": "Sirene : Fichier StockEtablissement du 01 Novembre 2026", "format": "parquet",
         "url": "https://object.files.data.gouv.fr/x/StockEtablissement_utf8.parquet"},
    ]  # fmt: skip
    got = sirene_stock.pick_resources(resources)
    assert got["ul"] == [
        "https://www.data.gouv.fr/fr/datasets/r/ul-parquet",
        "https://www.data.gouv.fr/fr/datasets/r/ul-zip",
    ]
    assert got["etab"] == ["https://object.files.data.gouv.fr/x/StockEtablissement_utf8.parquet"]


# --------------------------------------------------------------------------- Companies House


@pytest.fixture
def ch_rows(tmp_path: Path, monkeypatch) -> list[dict]:
    zipped = tmp_path / "BasicCompanyDataAsOneFile-2026-11-01.zip"
    with zipfile.ZipFile(zipped, "w") as zf:
        zf.write(FIX / "BasicCompanyData.csv", "BasicCompanyDataAsOneFile-2026-11-01.csv")
    monkeypatch.setenv(ch.URL_ENV, str(zipped))
    stats = bulk.run_source("companies_house", opts(tmp_path))
    assert columns(stats.files[0]) == list(PACK_COLUMNS)
    return rows(stats.files[0])


def test_companies_house_active_uk_companies(ch_rows: list[dict]) -> None:
    numbers = {r["company_number"] for r in ch_rows}
    assert numbers == {"11743353", "00000001", "SC035033", "12345678", "01234567", "04444444",
                       "05555555", "NI012345", "OC301234"}  # fmt: skip
    assert all(r["registry"] == "GB-CH" and r["country"] == "GB" for r in ch_rows)
    assert json.loads(ch_rows[0]["sources_json"])[0]["licence"] == "OGL-UK-3.0"


def test_companies_house_fields(ch_rows: list[dict]) -> None:
    by = {r["company_number"]: r for r in ch_rows}
    sw = by["11743353"]
    assert (sw["city"], sw["region"], sw["postal_code"], sw["founded_year"]) == (
        "Cardiff",
        "Wales",
        "CF5 2EY",
        2019,
    )
    assert (sw["industry"], sw["industry_label"]) == (
        "J",
        "Business and domestic software development",
    )
    assert by["SC035033"]["region"] == "Scotland" and by["NI012345"]["region"] == "Northern Ireland"
    assert by["12345678"]["industry"] is None  # 99999 dormant
    assert by["04444444"]["industry"] == "L"  # 98000 residents property management
    assert by["NI012345"]["industry"] is None  # "None Supplied"
    assert by["00000001"]["legal_form"] == "Public Limited Company"
    assert by["05555555"]["name"] == 'THE "QUOTED" COMPANY LTD'
    assert by["05555555"]["address"] == "UNIT 4, THE PARK, BLETCHLEY"
    assert by["01234567"]["postal_code"] == "EC1A 1AA"
    assert by["01234567"]["id"] == make_id("co", "reg", "GB-CH:01234567")


def test_companies_house_finds_the_newest_snapshot() -> None:
    html = (
        '<a href="BasicCompanyDataAsOneFile-2026-10-01.zip">old</a>'
        '<a href="BasicCompanyDataAsOneFile-2026-11-01.zip">Company data as one file</a>'
        '<a href="BasicCompanyData-2026-11-01-part1_7.zip">part 1</a>'
    )
    assert ch.files_from_index(html) == [ch.BASE_URL + "BasicCompanyDataAsOneFile-2026-11-01.zip"]
    parts = "".join(f'<a href="BasicCompanyData-2026-11-01-part{i}_3.zip">x</a>' for i in (2, 1, 3))
    parts += '<a href="BasicCompanyData-2026-10-01-part1_3.zip">old</a>'
    assert ch.files_from_index(parts) == [
        ch.BASE_URL + f"BasicCompanyData-2026-11-01-part{i}_3.zip" for i in (1, 2, 3)
    ]
    assert ch.files_from_index("<html></html>") == []


# --------------------------------------------------------------------------- EDGAR


def submissions_zip(tmp_path: Path) -> Path:
    path = tmp_path / "submissions.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted((FIX / "edgar").glob("*.json")):
            zf.write(f, f.name)
    return path


def test_edgar_refuses_to_run_without_a_contact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEADCOMMONS_CONTACT_EMAIL", raising=False)
    monkeypatch.setenv(edgar_bulk.URL_ENV, str(submissions_zip(tmp_path)))
    with pytest.raises(RuntimeError, match="LEADCOMMONS_CONTACT_EMAIL"):
        bulk.run_source("edgar_bulk", opts(tmp_path))
    monkeypatch.setenv("LEADCOMMONS_CONTACT_EMAIL", "ops@example.org")
    assert edgar_bulk.contact_user_agent(opts(tmp_path)).endswith("contact:ops@example.org")
    assert "me@x.org" in edgar_bulk.contact_user_agent(opts(tmp_path, contact="me@x.org"))


def test_edgar_keeps_operating_companies(tmp_path: Path) -> None:
    con = bulk.connect(tmp_path / "work")
    total = edgar_bulk.stage_zip(con, submissions_zip(tmp_path), tmp_path / "work", batch_files=3)
    assert total == 8  # the -submissions-001 overflow file is not a filer
    stats = edgar_bulk.write(con, opts(tmp_path), total, today=TODAY)
    got = {r["cik"]: r for r in rows(stats.files[0])}
    # 103: last 10-K in 2019; 104: a person (insider forms); 107: unknown country code
    assert set(got) == {"0000000101", "0000000102", "0000000105", "0000000106", "0000000108"}
    assert stats.skipped == 3
    robo = got["0000000101"]
    assert (robo["registry"], robo["company_number"], robo["country"], robo["region"]) == (
        "US-SEC",
        "0000000101",
        "US",
        "CA",
    )
    assert (robo["phone"], robo["domain"], robo["website"]) == (
        "+14155550100",
        "example-robotics.com",
        "http://www.example-robotics.com",
    )
    assert (robo["industry"], robo["industry_label"], robo["lei"]) == (
        "C",
        "ELECTRONIC COMPUTERS",
        "549300FAKE00000000J9",
    )
    assert robo["address"] == "500 MARKET STREET, SUITE 100" and robo["city"] == "San Francisco"
    uk = got["0000000105"]
    assert (uk["country"], uk["phone"], uk["domain"]) == ("GB", "+442079460000", "example-uk.co.uk")
    ca = got["0000000106"]
    assert (ca["country"], ca["region"], ca["phone"]) == ("CA", "ON", "+14165550199")
    assert got["0000000108"]["city"] == "Chicago"  # mailing address when no business address
    assert got["0000000101"]["id"] == make_id("co", "reg", "US-SEC:0000000101")
    con.close()


def test_edgar_takes_the_cik_from_the_file_name(tmp_path: Path) -> None:
    doc = json.loads((FIX / "edgar" / "CIK0000000101.json").read_text())
    del doc["cik"]
    path = tmp_path / "sub.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("CIK0000000999.json", json.dumps(doc))
    con = bulk.connect(tmp_path / "work")
    assert edgar_bulk.stage_zip(con, path, tmp_path / "work") == 1
    assert con.execute("SELECT cik FROM edgar_sub").fetchall() == [("0000000999",)]
    con.close()


def test_edgar_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_CONTACT_EMAIL", "ops@example.org")
    monkeypatch.setenv(edgar_bulk.URL_ENV, str(submissions_zip(tmp_path)))
    stats = bulk.run_source("edgar_bulk", opts(tmp_path, countries=frozenset({"US"})))
    got = rows(stats.files[0])
    assert columns(stats.files[0]) == list(PACK_COLUMNS)
    ciks = {r["cik"] for r in got}
    assert {"0000000101", "0000000108"} <= ciks and "0000000104" not in ciks
    assert {r["country"] for r in got} == {"US"}
    assert "DOE" not in " ".join(r["name"] for r in got)


# --------------------------------------------------------------------------- CLI


def test_cli_lists_sources_as_json() -> None:
    result = CliRunner().invoke(app, ["pack", "sources", "--json"])
    assert result.exit_code == 0, result.output
    listed = {s["name"]: s for s in json.loads(result.output)}
    assert {"gleif_golden", "sirene_stock", "companies_house", "edgar_bulk"} <= set(listed)
    assert (
        listed["sirene_stock"]["countries"] == ["FR"]
        and listed["gleif_golden"]["countries"] is None
    )
    text = CliRunner().invoke(app, ["pack", "sources"])
    assert "OGL-UK-3.0" in text.output and "worldwide" in text.output


def test_cli_fetch_source(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ch.URL_ENV, str(FIX / "BasicCompanyData.csv"))
    out = tmp_path / "shards"
    result = CliRunner().invoke(
        app, ["pack", "fetch-source", "companies_house", "--out", str(out), "--limit", "2"]
    )
    assert result.exit_code == 0, result.output
    assert "2 rows written" in result.output
    assert sorted(p.name for p in out.iterdir()) == ["companies_house.parquet"]
    result = CliRunner().invoke(
        app, ["pack", "fetch-source", "sirene_stock", "--out", str(out), "--country", "us"]
    )
    assert result.exit_code == 0 and "nothing to do" in result.output
    result = CliRunner().invoke(app, ["pack", "fetch-source", "nope", "--out", str(out)])
    assert result.exit_code == 1


def test_fixtures_copy_the_real_headers() -> None:
    gleif = bulk.csv_header(FIX / "gleif-golden-copy.csv")
    assert gleif[:2] == ["LEI", "Entity.LegalName"] and "Registration.RegistrationStatus" in gleif
    chh = bulk.csv_header(FIX / "BasicCompanyData.csv")
    assert chh[:2] == ["CompanyName", " CompanyNumber"] and "SICCode.SicText_1" in chh
