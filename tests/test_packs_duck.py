"""DuckDBCompanySearch over a small generated pack."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pepite.packs import SAMPLE_PACK_DIR, DuckDBCompanySearch
from pepite.packs.builder import write_parquet
from pepite.search import PACK_COLUMNS, CompanyFilters


def row(i, name, **kw) -> dict:
    base = dict.fromkeys(PACK_COLUMNS)
    base.update(id=f"co_{i:03d}", name=name, sources_json=json.dumps([
        {"source": "wikidata", "licence": "CC0-1.0", "url": f"http://wd/Q{i}",
         "fetched_at": "2026-01-02T03:04:05+00:00"}]))  # fmt: skip
    base.update(kw)
    return base


ROWS = [
    row(
        1,
        "Acme Software",
        domain="acme.com",
        country="FR",
        industry="J",
        industry_label="software industry",
        size_range="11-50",
        region="Auvergne",
        lei="L1",
        wikidata_id="Q1",
        website="https://acme.com",
        legal_name="ACME SAS",
    ),
    row(
        2,
        "Zeta Software",
        domain="zeta.de",
        country="DE",
        industry="J",
        industry_label="software industry",
        size_range="51-200",
    ),
    row(
        3,
        "Alpha Bank",
        domain=None,
        country="FR",
        industry="K",
        industry_label="bank",
        size_range="10001+",
    ),
    row(
        4,
        "Beta Foods",
        domain="beta.fr",
        country="FR",
        industry="C",
        industry_label="food industry",
        size_range=None,
    ),
    row(5, "Gamma 100%_Pure", domain="gamma.fr", country="FR", industry=None, size_range="1-10"),
    row(
        6,
        "Delta Co",
        domain="delta.com",
        country="DE",
        industry="C",
        size_range="11-50",
        region="Bayern",
        founded_year=1999,
    ),
]


@pytest.fixture()
def search(tmp_path: Path) -> DuckDBCompanySearch:
    meta = {"built_at": "2026-01-01T00:00:00+00:00", "sources": "wikidata,gleif"}
    write_parquet(tmp_path / "companies-a.parquet", ROWS[:3], meta)
    write_parquet(
        tmp_path / "companies-b.parquet", ROWS[3:], {"built_at": "x", "sources": "wikidata"}
    )
    return DuckDBCompanySearch(tmp_path)


def names(page) -> list[str]:
    return [c.name for c in page.items]


def test_all_ordered_domain_then_size_then_name(search):
    page = search.search_companies(CompanyFilters(), per_page=50)
    assert page.total == 6
    # domain+size first (by name), then domain without size, then no domain
    assert names(page) == [
        "Acme Software", "Delta Co", "Gamma 100%_Pure", "Zeta Software", "Beta Foods", "Alpha Bank",
    ]  # fmt: skip


def test_pagination(search):
    p1 = search.search_companies(CompanyFilters(), page=1, per_page=4)
    p2 = search.search_companies(CompanyFilters(), page=2, per_page=4)
    p3 = search.search_companies(CompanyFilters(), page=3, per_page=4)
    assert (p1.total, p2.total) == (6, 6) and len(p1.items) == 4 and len(p2.items) == 2
    assert p3.items == [] and p2.page == 2 and p2.per_page == 4
    assert not {c.id for c in p1.items} & {c.id for c in p2.items}


def test_text_and_keywords(search):
    assert names(search.search_companies(CompanyFilters(text="SOFT"))) == [
        "Acme Software",
        "Zeta Software",
    ]
    assert names(search.search_companies(CompanyFilters(text="acme sas"))) == ["Acme Software"]
    assert names(search.search_companies(CompanyFilters(text="zeta.de"))) == ["Zeta Software"]
    assert names(search.search_companies(CompanyFilters(keywords=["bank"]))) == ["Alpha Bank"]
    kw = search.search_companies(CompanyFilters(keywords=["food", "bank"]))
    assert {c.name for c in kw.items} == {"Beta Foods", "Alpha Bank"}


def test_filters(search):
    f = CompanyFilters
    assert search.search_companies(f(countries=["fr"])).total == 4
    assert search.search_companies(f(countries=["FR"], industries=["J"])).total == 1
    assert search.search_companies(f(size_ranges=["11-50", "1-10"])).total == 3
    assert names(search.search_companies(f(domains=["https://www.Delta.com/x"]))) == ["Delta Co"]
    assert names(search.search_companies(f(regions=["bayern"]))) == ["Delta Co"]
    assert search.search_companies(f(countries=["DE"], size_ranges=["11-50"])).total == 1
    assert search.search_companies(f(countries=["XX"])).total == 0


@pytest.mark.parametrize(
    "evil",
    ["'; DROP VIEW companies; --", "' OR '1'='1", "\\", 'a" OR 1=1 --', "x\x00y"],
)
def test_injection_like_input_is_inert(search, evil):
    f = CompanyFilters(text=evil, keywords=[evil], countries=[evil], industries=[evil],
                       size_ranges=[evil], domains=[evil], regions=[evil])  # fmt: skip
    assert search.search_companies(f).total == 0
    only_text = search.search_companies(CompanyFilters(text=evil))
    assert only_text.total == 0
    assert search.search_companies(CompanyFilters()).total == 6  # view intact


def test_like_wildcards_are_literal(search):
    assert search.search_companies(CompanyFilters(text="%")).total == 1  # "100%_Pure"
    assert search.search_companies(CompanyFilters(text="_")).total == 1


def test_get_company_roundtrip(search):
    c = search.get_company("co_001")
    assert c is not None
    assert c.name == "Acme Software" and c.legal_name == "ACME SAS" and c.country == "FR"
    assert c.registry_ids == {"lei": "L1", "wikidata": "Q1"}
    assert c.links == {"website": "https://acme.com"}
    assert c.sources[0].source == "wikidata" and c.sources[0].licence == "CC0-1.0"
    assert c.sources[0].fetched_at.year == 2026
    d = search.get_company("co_006")
    assert d.founded_year == 1999 and d.registry_ids == {} and d.links == {}
    assert search.get_company("nope") is None
    assert search.get_company("' OR 1=1 --") is None


def test_facets(search):
    f = CompanyFilters
    assert search.facet_counts("country", f()) == {"FR": 4, "DE": 2}
    assert search.facet_counts("industry", f()) == {"C": 2, "J": 2, "K": 1}
    assert search.facet_counts("size_range", f(), limit=2) == {"11-50": 2, "1-10": 1}
    # respects other filters but not its own
    assert search.facet_counts("country", f(countries=["DE"], industries=["C"])) == {
        "DE": 1,
        "FR": 1,
    }
    assert search.facet_counts("industry", f(countries=["DE"], industries=["C"])) == {
        "C": 1,
        "J": 1,
    }
    with pytest.raises(ValueError):
        search.facet_counts("name; drop", f())


def test_pack_info(search):
    info = search.pack_info()
    assert [i["rows"] for i in info] == [3, 3]
    assert info[0]["sources"] == ["wikidata", "gleif"]
    assert info[0]["built_at"] == "2026-01-01T00:00:00+00:00"
    assert info[0]["file"].endswith("companies-a.parquet")


def test_threaded_use(search):
    def work(i):
        return search.search_companies(CompanyFilters(countries=["FR"]), per_page=2).total

    with ThreadPoolExecutor(8) as ex:
        assert set(ex.map(work, range(40))) == {4}


def test_fallback_to_sample_pack(tmp_path: Path):
    empty = DuckDBCompanySearch(tmp_path)  # no parquet here
    sample_files = list(SAMPLE_PACK_DIR.glob("*.parquet"))
    assert [Path(i["file"]) for i in empty.pack_info()] == sorted(sample_files)
    assert DuckDBCompanySearch(None).pack_info() == empty.pack_info()
    if sample_files:
        assert empty.search_companies(CompanyFilters()).total > 0


def test_reads_old_pack_without_new_columns(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    old_cols = [c for c in PACK_COLUMNS if c not in ("siren", "cik")]
    table = pa.Table.from_pylist(
        [{k: v for k, v in ROWS[0].items() if k in old_cols}],
        schema=pa.schema(
            [pa.field(c, pa.int32() if c == "founded_year" else pa.string()) for c in old_cols]
        ),
    )
    pq.write_table(table, tmp_path / "companies-old.parquet")
    s = DuckDBCompanySearch(tmp_path)
    c = s.get_company("co_001")
    assert c.registry_ids == {"lei": "L1", "wikidata": "Q1"}
    assert s.search_companies(CompanyFilters()).total == 1


def test_old_and_new_packs_together_expose_registry_ids(tmp_path: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    old_cols = [c for c in PACK_COLUMNS if c not in ("siren", "cik")]
    pq.write_table(
        pa.Table.from_pylist(
            [{k: v for k, v in ROWS[1].items() if k in old_cols}],
            schema=pa.schema(
                [pa.field(c, pa.int32() if c == "founded_year" else pa.string()) for c in old_cols]
            ),
        ),
        tmp_path / "a-old.parquet",
    )
    new = row(7, "Neu SAS", country="FR", siren="123456789", cik="0000320193")
    write_parquet(tmp_path / "b-new.parquet", [new], {"built_at": "x", "sources": "sirene,edgar"})
    s = DuckDBCompanySearch(tmp_path)
    assert s.search_companies(CompanyFilters()).total == 2
    c = s.get_company("co_007")
    assert c.registry_ids == {"siren": "123456789", "cik": "0000320193"}
    assert s.get_company("co_002").registry_ids == {}


def test_works_without_pyarrow(tmp_path: Path, monkeypatch):
    import sys

    write_parquet(tmp_path / "companies-a.parquet", ROWS[:2],
                  {"built_at": "2026-01-01T00:00:00+00:00", "sources": "wikidata"})  # fmt: skip
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    s = DuckDBCompanySearch(tmp_path)
    assert s.search_companies(CompanyFilters(text="acme")).total == 1
    info = s.pack_info()
    assert info[0]["rows"] == 2 and info[0]["sources"] == ["wikidata"]
    assert info[0]["built_at"] == "2026-01-01T00:00:00+00:00"
    assert DuckDBCompanySearch(None).pack_info()  # bundled sample too
