"""Pack builder: merge/dedupe, parquet schema, metadata, attribution, download."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
import respx

from leadcommons.models import Company, Provenance, make_id
from leadcommons.packs import build_pack, download_pack
from leadcommons.packs.builder import Deduper, merge_companies
from leadcommons.packs.builder import build_pack as build
from leadcommons.search import PACK_COLUMNS

LEI = "5493001KJTIIGC8Y1R12"


def wd(name, qid, *, lei=None, domain=None, country="GB") -> Company:
    reg = {"wikidata": qid} | ({"lei": lei} if lei else {})
    return Company(
        id="x", name=name, domain=domain, country=country, city="Leeds", registry_ids=reg,
        links={"website": f"https://{domain}"} if domain else {},
        sources=[Provenance("wikidata", "CC0-1.0", f"http://www.wikidata.org/entity/{qid}")],
    )  # fmt: skip


def gl(lei, legal, *, region="WYK", city="Leeds Central", country="GB") -> Company:
    return Company(
        id="y", name=legal, legal_name=legal, country=country, region=region, city=city,
        registry_ids={"lei": lei},
        sources=[Provenance("gleif", "CC0-1.0", f"https://search.gleif.org/#/record/{lei}")],
    )  # fmt: skip


class FakeConn:
    def __init__(self, name, rows, by_lei=None):
        self.name, self.licence, self.rows = name, "CC0-1.0", rows
        self.by_lei = by_lei or []
        self.seen_countries: list[str | None] = []

    def iter_companies(self, *, country=None, limit=None):
        self.seen_countries.append(country)
        rows = [r for r in self.rows if r.country == country]
        yield from (rows if limit is None else rows[:limit])

    def fetch_by_lei(self, leis):
        yield from (c for c in self.by_lei if c.registry_ids["lei"] in leis)


def test_merge_gleif_fills_legal_data():
    merged = merge_companies(wd("Acme", "Q1", lei=LEI, domain="acme.co.uk"), gl(LEI, "ACME LTD"))
    assert merged.legal_name == "ACME LTD"
    assert merged.region == "WYK"
    assert merged.city == "Leeds"  # wikidata city kept, only gaps are filled
    assert merged.domain == "acme.co.uk"
    assert [p.source for p in merged.sources] == ["wikidata", "gleif"]
    assert merged.id == make_id("co", "lei", LEI)


def test_deduper_keys():
    dd = Deduper()
    dd.add(wd("A", "Q1", lei=LEI))
    dd.add(gl(LEI.lower(), "A LTD"))  # same LEI different case
    dd.add(wd("A again", "Q1"))  # same wikidata id
    dd.add(wd("B", "Q2", domain="b.com"))
    only_domain = Company(id="z", name="B web", domain="b.com")
    dd.add(only_domain)  # registry-less row with a known domain does not merge into Q2...
    assert len(dd.items) == 3
    dd.add(Company(id="z2", name="B web 2", domain="b.com"))  # ...but dedupes with itself
    assert len(dd.items) == 3
    ids = [c.id for c in dd.items]
    assert ids[0] == make_id("co", "lei", LEI)
    assert ids[1] == make_id("co", "wd", "Q2")
    assert ids[2] == make_id("co", "domain", "b.com")


def test_build_pack_writes_schema_metadata_attribution(tmp_path: Path):
    wdc = FakeConn(
        "wikidata",
        [
            wd("Acme", "Q1", lei=LEI, domain="acme.co.uk"),
            wd("Beta", "Q2", domain="beta.com"),
            wd("Frenchie", "Q3", domain="frenchie.fr", country="FR"),
        ],
    )
    glc = FakeConn("gleif", [], by_lei=[gl(LEI, "ACME LTD")])
    path = build(
        tmp_path / "out",
        countries=["gb", "fr"],
        connectors={
            "wikidata": wdc,
            "gleif": glc,
            "sirene": FakeConn("sirene", []),
            "edgar": FakeConn("edgar", []),
        },
    )
    assert path.name.startswith("companies-") and path.suffix == ".parquet"
    assert wdc.seen_countries == ["GB", "FR"]
    pf = pq.ParquetFile(path)
    assert tuple(pf.schema_arrow.names) == PACK_COLUMNS
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"
    meta = {k.decode(): v.decode() for k, v in pf.schema_arrow.metadata.items()}
    assert meta["sources"] == "wikidata,gleif"  # sirene ran for FR but added no rows
    assert "skipped" not in meta
    assert meta["built_at"].endswith("+00:00")
    rows = {r["name"]: r for r in pf.read().to_pylist()}
    assert set(rows) == {"Acme", "Beta", "Frenchie"}
    assert rows["Acme"]["lei"] == LEI and rows["Acme"]["legal_name"] == "ACME LTD"
    assert rows["Acme"]["wikidata_id"] == "Q1" and rows["Acme"]["region"] == "WYK"
    assert rows["Acme"]["website"] == "https://acme.co.uk"
    assert "gleif" in rows["Acme"]["sources_json"]
    attr = (path.parent / "ATTRIBUTION.md").read_text()
    assert "wikidata" in attr and "CC0-1.0" in attr and "**total**: 3" in attr


def test_build_pack_unknown_source(tmp_path: Path):
    with pytest.raises(ValueError):
        build_pack(tmp_path, sources=("nope",))


@respx.mock
def test_download_pack_ok(tmp_path: Path):
    body = b"PAR1" + b"x" * 20 + b"PAR1"
    respx.get("https://example.org/dl/companies-x.parquet").mock(
        return_value=httpx.Response(200, content=body)
    )
    out = download_pack("https://example.org/dl/companies-x.parquet", tmp_path / "packs")
    assert out.name == "companies-x.parquet" and out.read_bytes() == body
    assert not list(out.parent.glob("*.part"))


@respx.mock
def test_download_pack_rejects_non_parquet(tmp_path: Path):
    respx.get("https://example.org/bad.parquet").mock(
        return_value=httpx.Response(200, content=b"<html>not parquet</html>")
    )
    with pytest.raises(ValueError, match="Parquet"):
        download_pack("https://example.org/bad.parquet", tmp_path)
    assert list(tmp_path.iterdir()) == []


@respx.mock
def test_download_pack_http_error(tmp_path: Path):
    respx.get("https://example.org/missing.parquet").mock(return_value=httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        download_pack("https://example.org/missing.parquet", tmp_path)
    assert list(tmp_path.iterdir()) == []


def sirene_row(siren, name, legal, city="Massy") -> Company:
    return Company(
        id="s", name=name, legal_name=legal, country="FR", region="Île-de-France", city=city,
        industry="K", industry_label="Financial and insurance activities", size_range="10001+",
        founded_year=1963, registry_ids={"siren": siren},
        sources=[Provenance("sirene", "etalab-2.0", f"https://x/{siren}")],
    )  # fmt: skip


def test_merge_on_siren_wikidata_keeps_name_domain():
    wdc = wd("Carrefour", "Q1", domain="carrefour.com", country="FR")
    wdc.registry_ids["siren"] = "652014051"
    wdc.city = None
    merged = merge_companies(wdc, sirene_row("652014051", "CARREFOUR", "CARREFOUR SA"))
    assert (merged.name, merged.domain) == ("Carrefour", "carrefour.com")
    assert merged.legal_name == "CARREFOUR SA" and merged.city == "Massy"
    assert merged.industry == "K" and merged.size_range == "10001+" and merged.founded_year == 1963
    assert merged.region == "Île-de-France"
    assert merged.registry_ids == {"wikidata": "Q1", "siren": "652014051"}
    assert [p.source for p in merged.sources] == ["wikidata", "sirene"]
    # registry stored first, Wikidata arrives later: Wikidata still names the company
    dd = Deduper()
    dd.add(sirene_row("652014051", "CARREFOUR", "CARREFOUR SA"))
    wdc2 = wd("Carrefour", "Q1", domain="carrefour.com", country="FR")
    wdc2.registry_ids["siren"] = "652014051"
    stored = dd.add(wdc2)
    assert len(dd.items) == 1 and stored.name == "Carrefour" and stored.domain == "carrefour.com"
    assert stored.id == make_id("co", "wd", "Q1")


def test_merge_on_cik_and_name_fallback():
    dd = Deduper()
    apple = wd("Apple", "Q312", domain="apple.com", country="US")
    apple.registry_ids["cik"] = "0000320193"
    dd.add(apple)
    edgar = Company(
        id="e", name="Apple Inc.", legal_name="Apple Inc.", country="US", region="CA",
        registry_ids={"cik": "0000320193"},
        sources=[Provenance("edgar", "public-domain", "u")],
    )  # fmt: skip
    dd.add(edgar)
    assert len(dd.items) == 1 and dd.items[0].region == "CA"
    # no shared id: exact normalised-name match within the country merges registry rows
    dd.add(wd("Danone", "Q2", domain="danone.com", country="FR"))
    dd.add(sirene_row("552032534", "DANONE", "DANONE SA"))
    assert len(dd.items) == 2
    assert dd.items[1].registry_ids["siren"] == "552032534"
    # different country never merges by name
    dd.add(sirene_row("111111111", "APPLE", "APPLE SAS"))
    assert len(dd.items) == 3


def test_ids_fall_back_to_siren_then_cik():
    from leadcommons.packs.builder import company_id

    s = sirene_row("652014051", "X", "X")
    assert company_id(s) == make_id("co", "siren", "652014051")
    e = Company(id="e", name="E", registry_ids={"cik": "0000000001"})
    assert company_id(e) == make_id("co", "cik", "0000000001")


class Registry(FakeConn):
    """Fake national registry that records enrichment calls."""

    def __init__(self, name, rows, countries, kind, by_id):
        super().__init__(name, rows)
        self.countries, self.kind, self.by_id = countries, kind, by_id
        self.asked: list[str] = []

    def _fetch(self, ids):
        self.asked += list(ids)
        yield from (c for c in self.by_id if c.registry_ids[self.kind] in ids)

    def fetch_by_siren(self, ids):
        return self._fetch(ids)

    def fetch_by_cik(self, ids):
        return self._fetch(ids)


def test_default_sources_add_registry_per_country(tmp_path: Path):
    fr = wd("Carrefour", "Q1", domain="carrefour.com", country="FR")
    fr.registry_ids["siren"] = "652014051"
    us = wd("Apple", "Q312", domain="apple.com", country="US")
    us.registry_ids["cik"] = "0000320193"
    de = wd("SAP", "Q3", domain="sap.com", country="DE")
    extra = sirene_row("300000001", "ZETA", "ZETA SA", city="Nantes")
    sirene = Registry(
        "sirene", [extra], ("FR",), "siren", [sirene_row("652014051", "C", "CARREFOUR SA")]
    )
    edgar_row = Company(
        id="e", name="Apple Inc.", legal_name="Apple Inc.", country="US", region="CA",
        city="Cupertino", industry="C", registry_ids={"cik": "0000320193"},
        sources=[Provenance("edgar", "public-domain", "u")],
    )  # fmt: skip
    edgar = Registry("edgar", [], ("US",), "cik", [edgar_row])
    path = build(
        tmp_path,
        countries=["FR", "US", "DE"],
        connectors={
            "wikidata": FakeConn("wikidata", [fr, us, de]),
            "gleif": FakeConn("gleif", []),
            "sirene": sirene,
            "edgar": edgar,
        },
    )
    assert sirene.seen_countries == ["FR"] and edgar.seen_countries == ["US"]  # not run for DE
    assert sirene.asked == ["652014051"] and edgar.asked == ["0000320193"]
    pf = pq.ParquetFile(path)
    meta = {k.decode(): v.decode() for k, v in pf.schema_arrow.metadata.items()}
    assert meta["sources"] == "wikidata,sirene,edgar"  # gleif had no rows to add
    rows = {r["name"]: r for r in pf.read().to_pylist()}
    assert set(rows) == {"Carrefour", "ZETA", "Apple", "SAP"}  # extra SIRENE row added
    assert (
        rows["Carrefour"]["siren"] == "652014051"
        and rows["Carrefour"]["legal_name"] == "CARREFOUR SA"
    )
    assert rows["Apple"]["cik"] == "0000320193" and rows["Apple"]["region"] == "CA"
    assert rows["ZETA"]["domain"] is None and rows["ZETA"]["siren"] == "300000001"
    assert rows["SAP"]["siren"] is None and rows["SAP"]["cik"] is None
    assert "sirene" in rows["Carrefour"]["sources_json"]
    attr = (path.parent / "ATTRIBUTION.md").read_text()
    assert "etalab-2.0" in attr and "public-domain" in attr


def test_explicit_sources_and_failure_handling(tmp_path: Path):
    class Boom(Registry):
        def iter_companies(self, **kw):
            raise RuntimeError("403")

    base = {
        "wikidata": FakeConn("wikidata", [wd("A", "Q1", country="US")]),
        "gleif": FakeConn("gleif", []),
    }
    boom = Boom("edgar", [], ("US",), "cik", [])
    # implicit registry failing is logged and skipped
    path = build(tmp_path / "a", countries=["US"], connectors={**base, "edgar": boom})
    assert pq.ParquetFile(path).metadata.num_rows == 1
    # explicitly requested registry failing is skipped too, and recorded
    path = build(
        tmp_path / "b", sources=("wikidata", "edgar"), countries=["US"],
        connectors={**base, "edgar": boom},
    )  # fmt: skip
    meta = {k.decode(): v.decode() for k, v in pq.ParquetFile(path).schema_arrow.metadata.items()}
    assert json.loads(meta["skipped"])[0]["source"] == "edgar"
    assert meta["sources"] == "wikidata"
    # explicit sirene only: skipped outside France
    sir = Registry("sirene", [sirene_row("1", "Z", "Z")], ("FR",), "siren", [])
    path = build(
        tmp_path / "c", sources=("sirene",), countries=["FR", "DE"], connectors={"sirene": sir}
    )
    assert pq.ParquetFile(path).metadata.num_rows == 1 and sir.seen_countries == ["FR"]


def test_build_needs_pyarrow_message(tmp_path: Path, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    with pytest.raises(RuntimeError, match=r"leadcommons\[build\]"):
        build(
            tmp_path,
            connectors={"wikidata": FakeConn("wikidata", []), "gleif": FakeConn("gleif", [])},
        )


class Flaky(FakeConn):
    """Source that raises for the listed countries."""

    def __init__(self, name, rows, bad):
        super().__init__(name, rows)
        self.bad = bad

    def iter_companies(self, *, country=None, limit=None):
        if country in self.bad:
            raise httpx.HTTPError("giving up on https://query.wikidata.org/sparql after 5 attempts")
        yield from super().iter_companies(country=country, limit=limit)


def test_one_country_failing_still_builds_pack(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="leadcommons")
    wdc = Flaky("wikidata", [wd("A", "Q1", country="GB"), wd("B", "Q2", country="DE")], {"FR"})
    path = build(
        tmp_path,
        sources=("wikidata",),
        countries=["FR", "GB", "DE"],
        connectors={"wikidata": wdc},
    )
    pf = pq.ParquetFile(path)
    assert pf.metadata.num_rows == 2
    meta = {k.decode(): v.decode() for k, v in pf.schema_arrow.metadata.items()}
    skipped = json.loads(meta["skipped"])
    assert skipped == [{"country": "FR", "source": "wikidata", "reason": skipped[0]["reason"]}]
    assert "giving up" in skipped[0]["reason"]
    attr = (path.parent / "ATTRIBUTION.md").read_text()
    assert "Skipped" in attr and "wikidata / FR" in attr
    assert "wikidata FR skipped" in caplog.text  # warning, not exception


def test_all_failing_raises_and_writes_nothing(tmp_path: Path):
    wdc = Flaky("wikidata", [], {"FR", "GB"})
    with pytest.raises(RuntimeError, match="no companies"):
        build(tmp_path, sources=("wikidata",), countries=["FR", "GB"], connectors={"wikidata": wdc})
    assert not list(tmp_path.glob("companies-*"))
    assert not (tmp_path / "ATTRIBUTION.md").exists()
    assert [p.name for p in tmp_path.iterdir()] == []  # temp work dir cleaned up


def test_sources_metadata_lists_only_contributors(tmp_path: Path):
    wdc = FakeConn("wikidata", [wd("A", "Q1", lei=LEI, country="GB")])
    path = build(
        tmp_path,
        countries=["GB"],
        connectors={"wikidata": wdc, "gleif": FakeConn("gleif", [], by_lei=[gl(LEI, "A LTD")])},
    )
    meta = {k.decode(): v.decode() for k, v in pq.ParquetFile(path).schema_arrow.metadata.items()}
    assert meta["sources"] == "wikidata,gleif"
    path2 = build(
        tmp_path / "2",
        countries=["GB"],
        connectors={"wikidata": FakeConn("wikidata", [wd("A", "Q1", country="GB")]),
                    "gleif": FakeConn("gleif", [])},
    )  # fmt: skip
    meta2 = {k.decode(): v.decode() for k, v in pq.ParquetFile(path2).schema_arrow.metadata.items()}
    assert meta2["sources"] == "wikidata"
    assert "gleif" not in (path2.parent / "ATTRIBUTION.md").read_text().split("## Counts")[0]


def test_build_logs_progress(tmp_path: Path, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="leadcommons")
    sirene = Registry("sirene", [sirene_row("300000001", "ZETA", "ZETA SA")], ("FR",), "siren", [])
    build(
        tmp_path,
        countries=["FR"],
        connectors={
            "wikidata": FakeConn("wikidata", [wd("A", "Q1", country="FR")]),
            "gleif": FakeConn("gleif", []),
            "sirene": sirene,
        },
    )
    text = caplog.text
    assert "wikidata FR: 1 companies" in text
    assert "sirene FR: 1 rows" in text
    assert "country FR done: 2 rows" in text
    assert "pack built:" in text and "2 rows" in text
    assert all(r.name.startswith("leadcommons") for r in caplog.records)
