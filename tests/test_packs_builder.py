"""Pack builder: merge/dedupe, parquet schema, metadata, attribution, download."""

from __future__ import annotations

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
        connectors={"wikidata": wdc, "gleif": glc},
    )
    assert path.name.startswith("companies-") and path.suffix == ".parquet"
    assert wdc.seen_countries == ["GB", "FR"]
    pf = pq.ParquetFile(path)
    assert tuple(pf.schema_arrow.names) == PACK_COLUMNS
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"
    meta = {k.decode(): v.decode() for k, v in pf.schema_arrow.metadata.items()}
    assert meta["sources"] == "wikidata,gleif"
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
