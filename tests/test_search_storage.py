"""Search gives the same answers on every storage: the local index (packs/index.py), packs
loaded in memory, and plain Parquet views; checked against a small Python model of the
CompanyFilters rules, over several pack files (results merged across files)."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from leadcommons.models import SIZE_RANGES, Company
from leadcommons.packs import DuckDBCompanySearch, duck
from leadcommons.packs import index as pack_index
from leadcommons.packs.builder import write_parquet
from leadcommons.packs.domains import normalize_domain
from leadcommons.search import KEYWORD_FIELDS, PACK_COLUMNS, CompanyFilters

WORDS = ["Acme", "Zeta", "Boulangerie", "Martin", "Café", "Élan", "Nord", "Sud", "Tech", "Bio",
         "Garage", "Conseil", "100%_Pure", "Holding", "Smith", "Øst"]  # fmt: skip
CITIES = ["Paris", "Lyon", "lyon", "Berlin", "München", "Oslo", None]
REGIONS = ["Île-de-France", "Bayern", "Oslo", "Auvergne", None]
INDUSTRIES = ["C", "F", "G", "J", "K", "M", None]
LABELS = {"C": "manufacturing", "F": "construction", "G": "retail trade", "J": "software",
          "K": "bank", "M": "consulting"}  # fmt: skip


def _rows(country: str | None, n: int, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        r = dict.fromkeys(PACK_COLUMNS)
        name = " ".join(rnd.sample(WORDS, rnd.choice([1, 2, 2, 3])))
        ind = rnd.choice(INDUSTRIES)
        cc = country or rnd.choice(["FR", "DE", "NO", None])
        r.update(
            id=f"co_{seed}_{i:04d}",
            name=name.upper() if rnd.random() < 0.3 else name,
            legal_name=(name.upper() + " SAS") if rnd.random() < 0.5 else None,
            domain=f"{name.split()[0].lower().replace('%', '')}{seed}{i}.com"
            if rnd.random() < 0.6
            else None,
            country=cc,
            region=rnd.choice(REGIONS),
            city=rnd.choice(CITIES),
            industry=ind,
            industry_label=LABELS.get(ind) if ind else None,
            size_range=rnd.choice([*SIZE_RANGES, None, None]),
            company_number=f"{seed}{i:05d}" if rnd.random() < 0.8 else None,
            siren=f"9{seed}{i:06d}" if cc == "FR" and rnd.random() < 0.5 else None,
            lei=f"LEI{seed}{i:04d}".ljust(20, "0") if rnd.random() < 0.1 else None,
            sources_json=json.dumps([{"source": "test", "licence": "CC0-1.0"}]),
        )
        out.append(r)
    return out


FILES = {
    "companies-FR-1.parquet": _rows("FR", 260, 1),
    "companies-DE-1.parquet": _rows("DE", 180, 2),
    "companies-mixed.parquet": _rows(None, 140, 3),
}
ALL = [r for rows in FILES.values() for r in rows]


@pytest.fixture(scope="module")
def pack_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("packs")
    for name, rows in FILES.items():
        write_parquet(d / name, rows, {"built_at": "2026-12-01", "sources": "test"})
    return d


@pytest.fixture(scope="module", params=["index", "memory", "raw"])
def search(request, pack_dir: Path, tmp_path_factory) -> DuckDBCompanySearch:
    kind = request.param
    d = tmp_path_factory.mktemp(kind)
    for f in pack_dir.iterdir():
        (d / f.name).write_bytes(f.read_bytes())
    if kind == "index":
        pack_index.ensure_index(d)
    mp = pytest.MonkeyPatch()
    if kind == "raw":
        mp.setattr(duck, "MEMORY_ROWS", 0)
    s = DuckDBCompanySearch(d)
    mp.undo()
    assert {x.kind for x in s._sources} == {kind}
    return s


# -- the model ------------------------------------------------------------------------------


def _low(v: str | None) -> str | None:
    return v.lower() if v is not None else None


def _kw(r: dict, cols: tuple[str, ...], term: str) -> bool:
    t = term.lower()
    return any(r[c] is not None and t in r[c].lower() for c in cols)


def matches(r: dict, f: CompanyFilters, skip: str | None = None) -> bool:
    if f.text and f.text.strip() and not _kw(r, ("name", "legal_name", "domain"), f.text.strip()):
        return False
    cols = KEYWORD_FIELDS.get(f.keyword_fields, KEYWORD_FIELDS["all"])
    kws = [k.strip() for k in f.keywords if k and k.strip()]
    if kws:
        hits = [_kw(r, cols, k) for k in kws]
        if not (all(hits) if f.keyword_mode == "all" else any(hits)):
            return False
    if any(_kw(r, cols, k.strip()) for k in f.exclude_keywords if k and k.strip()):
        return False
    if skip not in ("country", "region", "city"):
        places = []
        if f.countries:
            places.append(r["country"] in {c.upper() for c in f.countries})
        if f.regions:
            places.append(_low(r["region"]) in {x.lower() for x in f.regions})
        if f.cities:
            places.append(_low(r["city"]) in {x.lower() for x in f.cities})
        if places and not any(places):
            return False
        if f.exclude_countries and (
            r["country"] is None or r["country"] in {c.upper() for c in f.exclude_countries}
        ):
            return False
        if f.exclude_regions and (_low(r["region"]) or "") in {x.lower() for x in f.exclude_regions}:
            return False
        if f.exclude_cities and (_low(r["city"]) or "") in {x.lower() for x in f.exclude_cities}:
            return False
    if skip != "industry":
        if f.industries and r["industry"] not in {i.upper() for i in f.industries}:
            return False
        if f.exclude_industries and (
            r["industry"] is None or r["industry"] in {i.upper() for i in f.exclude_industries}
        ):
            return False
    if f.size_ranges and skip != "size_range" and r["size_range"] not in f.size_ranges:
        return False
    if f.domains:
        want = {normalize_domain(d) or d.strip().lower() for d in f.domains}
        if r["domain"] not in want:
            return False
    return True


def order_key(r: dict) -> tuple:
    return (r["domain"] is None, r["size_range"] is None, r["name"].lower(), r["id"])


def expect_ids(f: CompanyFilters) -> list[str]:
    return [r["id"] for r in sorted((r for r in ALL if matches(r, f)), key=order_key)]


def expect_facet(col: str, f: CompanyFilters, limit: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in ALL:
        if r[col] is not None and matches(r, f, skip=col):
            counts[r[col]] = counts.get(r[col], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit])


F = CompanyFilters
CASES = [
    F(),
    F(countries=["fr"]),
    F(countries=["DE", "NO"]),
    F(exclude_countries=["FR"]),
    F(countries=["FR"], industries=["j", "K"]),
    F(industries=["C"], size_ranges=["1-10", "11-50"]),
    F(exclude_industries=["G"], countries=["DE"]),
    F(regions=["île-de-france"]),
    F(regions=["bayern"], cities=["oslo"]),
    F(exclude_regions=["auvergne"], countries=["FR"]),
    F(cities=["LYON"]),
    F(exclude_cities=["paris", "berlin"]),
    F(text="martin"),
    F(text="CAFÉ"),
    F(text="%"),
    F(text="_pure"),
    F(text="sas", countries=["FR"]),
    F(text="nothing-like-this"),
    F(keywords=["software", "bank"]),
    F(keywords=["tech", "nord"], keyword_mode="all"),
    F(keywords=["construction"], keyword_fields="industry"),
    F(keywords=["acme"], keyword_fields="name", size_ranges=["51-200"]),
    F(exclude_keywords=["holding"], countries=["DE"]),
    F(exclude_keywords=["retail"], keyword_fields="industry"),
    F(domains=["https://www.acme10.com/x"]),
    F(domains=[r["domain"] for r in ALL if r["domain"]][:5], industries=["C", "F", "G"]),
    F(text="o", regions=["oslo"], exclude_industries=["F"], size_ranges=[*SIZE_RANGES]),
]


@pytest.mark.parametrize("f", CASES, ids=lambda f: json.dumps({k: v for k, v in vars(f).items() if v and v not in ("any", "all")})[:60])  # fmt: skip
def test_results_counts_and_facets_match_the_model(search: DuckDBCompanySearch, f) -> None:
    ids = expect_ids(f)
    p1 = search.search_companies(f, page=1, per_page=7)
    p2 = search.search_companies(f, page=2, per_page=7)
    assert p1.total == len(ids) == p2.total
    assert [c.id for c in p1.items] + [c.id for c in p2.items] == ids[:14]
    big = search.search_companies(f, page=1, per_page=500)  # the scan path (one window)
    assert [c.id for c in big.items] == ids[:500]
    for col, limit in (("country", 300), ("industry", 300), ("size_range", 20), ("region", 5),
                       ("city", 4)):  # fmt: skip
        assert search.facet_counts(col, f, limit) == expect_facet(col, f, limit), col


def test_deep_pages_and_past_the_end(search: DuckDBCompanySearch) -> None:
    ids = expect_ids(F())
    page = search.search_companies(F(), page=9, per_page=50)
    assert [c.id for c in page.items] == ids[400:450]
    assert search.search_companies(F(), page=99, per_page=50).items == []


def test_lookups(search: DuckDBCompanySearch) -> None:
    r = next(x for x in ALL if x["domain"] and x["company_number"] and x["country"] == "DE")
    c = search.get_company(r["id"])
    assert isinstance(c, Company) and c.name == r["name"] and c.company_number == r["company_number"]
    assert c.sources[0].source == "test"
    assert search.get_company("nope") is None and search.get_company("' OR 1=1 --") is None
    got = search.get_companies([r["id"], "nope", ALL[0]["id"], r["id"]])
    assert set(got) == {r["id"], ALL[0]["id"]}
    assert [x.id for x in search.find_by_domain("HTTPS://" + r["domain"].upper())] == [r["id"]]
    assert search.find_by_domain("no-such.example") == []
    num = r["company_number"]
    assert [x.id for x in search.find_by_number(f" {num[:2]}.{num[2:]} ")] == [r["id"]]
    s = next(x for x in ALL if x["siren"])
    assert s["id"] in [x.id for x in search.find_by_number(s["siren"])]
    lei = next(x for x in ALL if x["lei"])
    assert lei["id"] in [x.id for x in search.find_by_number(lei["lei"].lower())]
    assert search.find_by_number("") == []


def test_lookalikes_match_the_model(search: DuckDBCompanySearch) -> None:
    ref = next(x for x in ALL if x["industry"] == "C" and x["size_range"] == "11-50")
    co = search.get_company(ref["id"])
    near = {"1-10", "11-50", "51-200"}
    cands = [x for x in ALL if x["industry"] == "C" and x["size_range"] in near
             and x["id"] != ref["id"]]  # fmt: skip
    cands.sort(key=lambda x: (x["country"] != ref["country"], x["size_range"] != "11-50",
                              x["domain"] is None, x["name"].lower(), x["id"]))  # fmt: skip
    assert [c.id for c in search.lookalike_companies(co, 12)] == [x["id"] for x in cands[:12]]


def test_place_suggestions(search: DuckDBCompanySearch) -> None:
    got = search.suggest_places("ly", 5)
    lyon = sum(1 for r in ALL if r["city"] == "Lyon")
    assert ("city", "Lyon", got[0][2], lyon) in got
    assert any(k == "region" and v == "Île-de-France" for k, v, _cc, _n in search.suggest_places("îl"))
    assert ("country", "FR", "FR", sum(1 for r in ALL if r["country"] == "FR")) in (
        search.suggest_places("fr")
    )
    assert search.suggest_places("  ") == []


def test_threads_share_one_search(search: DuckDBCompanySearch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    flts = [F(text=w.lower()) for w in WORDS] + [F(countries=[c]) for c in ("FR", "DE", "NO")]

    def work(i: int) -> bool:
        f = flts[i % len(flts)]
        return search.search_companies(f, 1, 5).total == len(expect_ids(f))

    with ThreadPoolExecutor(8) as ex:
        assert all(ex.map(work, range(60)))


def test_injection_like_input_is_inert(search: DuckDBCompanySearch) -> None:
    for evil in ["'; DROP TABLE companies; --", "\\", "x\x00y", "a\x1fb", "' OR '1'='1"]:
        f = F(text=evil, keywords=[evil], countries=[evil], regions=[evil], cities=[evil],
              industries=[evil], size_ranges=[evil], domains=[evil])  # fmt: skip
        assert search.search_companies(f).total == 0
        assert search.facet_counts("city", replace(f, cities=[])) == {}
    assert search.search_companies(F()).total == len(ALL)


# -- index lifecycle ------------------------------------------------------------------------


def test_stale_index_is_not_used_then_rebuilt(tmp_path: Path) -> None:
    f = tmp_path / "companies-FR-1.parquet"
    write_parquet(f, FILES["companies-FR-1.parquet"][:20], {"built_at": "x", "sources": "t"})
    built = pack_index.ensure_index(tmp_path)
    assert len(built) == 1 and pack_index.has_fresh_index(f)
    assert DuckDBCompanySearch(tmp_path)._sources[0].kind == "index"
    write_parquet(f, FILES["companies-FR-1.parquet"][:30], {"built_at": "y", "sources": "t"})
    assert not pack_index.has_fresh_index(f)
    s = DuckDBCompanySearch(tmp_path)
    assert s._sources[0].kind == "memory" and s.search_companies(F()).total == 30
    pack_index.ensure_index(tmp_path)
    assert DuckDBCompanySearch(tmp_path).search_companies(F()).total == 30
    assert len(list(pack_index.index_dir(tmp_path).glob("*.duckdb"))) == 1  # old one deleted
    f.unlink()
    pack_index.remove_orphans(tmp_path)
    assert not list(pack_index.index_dir(tmp_path).glob("*.duckdb"))


def test_large_pack_without_index_is_reported(tmp_path: Path, monkeypatch) -> None:
    write_parquet(tmp_path / "companies-DE-1.parquet", FILES["companies-DE-1.parquet"],
                  {"built_at": "x", "sources": "t"})  # fmt: skip
    monkeypatch.setattr(duck, "MEMORY_ROWS", 10)
    s = DuckDBCompanySearch(tmp_path)
    assert [p.name for p in s.needs_index] == ["companies-DE-1.parquet"]
    assert s._sources[0].countries == frozenset({"DE"})  # from Parquet statistics, no scan
    assert s.search_companies(F(countries=["FR"])).total == 0
    info = s.pack_info()[0]
    assert info["indexed"] is False and info["rows"] == 180
    pack_index.ensure_index(tmp_path)
    s2 = DuckDBCompanySearch(tmp_path)
    assert s2.needs_index == [] and s2.pack_info()[0]["indexed"] is True
    assert s2.pack_info()[0]["sources"] == ["t"]


def test_old_pack_schema_is_indexed(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    old_cols = [c for c in PACK_COLUMNS if c not in ("siren", "cik", "registry", "company_number")]
    rows = [{k: v for k, v in r.items() if k in old_cols} for r in FILES["companies-DE-1.parquet"]]
    schema = pa.schema(
        [pa.field(c, pa.int32() if c in ("founded_year", "employees", "revenue_year")
                  else pa.float64() if c == "revenue" else pa.string()) for c in old_cols]
    )  # fmt: skip
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp_path / "old.parquet")
    pack_index.ensure_index(tmp_path)
    s = DuckDBCompanySearch(tmp_path)
    assert s._sources[0].kind == "index"
    c = s.get_company(rows[0]["id"])
    assert c is not None and c.company_number is None and c.registry_ids.get("siren") is None
    assert s.search_companies(F()).total == len(rows)


def test_memory_and_threads_limits(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_DUCKDB_MEMORY", "300MB")
    monkeypatch.setenv("LEADCOMMONS_DUCKDB_THREADS", "2")
    s = DuckDBCompanySearch(None)
    limit, threads = s._query(
        "SELECT current_setting('memory_limit'), current_setting('threads')", []
    )[0]
    assert threads == 2 and limit.replace(" ", "") in ("286.1MiB", "300.0MB", "286MiB")
    monkeypatch.delenv("LEADCOMMONS_DUCKDB_MEMORY")
    mb = int(pack_index.default_memory_limit().removesuffix("MB"))
    assert 256 <= mb <= 1024
