"""Lookalikes: ranking rules, the DuckDB query helper and people lookalikes."""

from __future__ import annotations

import json
from pathlib import Path

from leadcommons import insights
from leadcommons.insights.similar import rank_lookalikes, similar_people
from leadcommons.models import Company, Person, Seniority
from leadcommons.packs import DuckDBCompanySearch
from leadcommons.packs.builder import write_parquet
from leadcommons.search import PACK_COLUMNS
from tests.fakes import FakeSearch


def co(
    i: str,
    name: str,
    country: str = "FR",
    industry: str = "J",
    size: str | None = "11-50",
    domain: str | None = "x.example",
) -> Company:
    return Company(
        id=f"co_{i}", name=name, domain=domain, country=country, industry=industry, size_range=size
    )


SUBJECT = co("0", "Subject", "FR", "J", "11-50")
POOL = [
    co("1", "Zeta FR same", "FR", "J", "11-50"),
    co("2", "Alpha DE same", "DE", "J", "11-50"),
    co("3", "Beta FR neighbour", "FR", "J", "51-200"),
    co("4", "Gamma FR far", "FR", "J", "501-1000"),
    co("5", "Delta FR other industry", "FR", "C", "11-50"),
    co("6", "Eps FR nodomain", "FR", "J", "11-50", domain=None),
    co("7", "Eta FR unknown size", "FR", "J", None),
    co("8", "Theta FR small neighbour", "FR", "J", "1-10"),
    SUBJECT,
]


def test_rank_rules() -> None:
    got = [c.name for c in rank_lookalikes(SUBJECT, POOL)]
    assert got == [
        "Zeta FR same",  # same country, same size, has a domain
        "Eps FR nodomain",  # same country, same size, no domain: after those with one
        "Beta FR neighbour",  # same country, neighbouring size
        "Theta FR small neighbour",
        "Alpha DE same",  # other country comes after every same-country match
    ]
    assert SUBJECT.name not in got


def test_limit_and_missing_industry() -> None:
    assert len(rank_lookalikes(SUBJECT, POOL, limit=2)) == 2
    assert rank_lookalikes(co("9", "No industry", industry=None), POOL) == []  # type: ignore[arg-type]
    assert rank_lookalikes(SUBJECT, POOL, limit=0) == []


def test_unknown_subject_size_accepts_any_size() -> None:
    names = {c.name for c in rank_lookalikes(co("9", "S", size=None), POOL)}
    assert "Gamma FR far" in names and "Eta FR unknown size" in names


def test_generic_search_backend_is_scanned() -> None:
    search = FakeSearch([*POOL])
    got = insights.lookalikes(search, SUBJECT, limit=3)
    assert [c.name for c in got] == ["Zeta FR same", "Eps FR nodomain", "Beta FR neighbour"]


def pack_row(i: int, name: str, **kw) -> dict:
    base = dict.fromkeys(PACK_COLUMNS)
    base.update(id=f"co_{i}", name=name, sources_json=json.dumps([]))
    base.update(kw)
    return base


def test_duckdb_helper(tmp_path: Path) -> None:
    rows = [
        pack_row(1, "Subject", domain="s.example", country="FR", industry="J", size_range="11-50"),
        pack_row(2, "Same FR", domain="a.example", country="FR", industry="J", size_range="11-50"),
        pack_row(
            3, "Neighbour FR", domain="b.example", country="FR", industry="J", size_range="51-200"
        ),
        pack_row(4, "Same DE", domain="c.example", country="DE", industry="J", size_range="11-50"),
        pack_row(
            5, "Far FR", domain="d.example", country="FR", industry="J", size_range="1001-5000"
        ),
        pack_row(
            6, "Other industry", domain="e.example", country="FR", industry="C", size_range="11-50"
        ),
        pack_row(7, "No domain FR", country="FR", industry="J", size_range="11-50"),
    ]
    write_parquet(tmp_path / "p.parquet", rows, {"built_at": "x", "sources": "test"})
    search = DuckDBCompanySearch(tmp_path)
    subject = search.get_company("co_1")
    got = [c.name for c in search.lookalike_companies(subject, 10)]
    assert got == ["Same FR", "No domain FR", "Neighbour FR", "Same DE"]
    assert [c.name for c in insights.lookalikes(search, subject, limit=2)] == [
        "Same FR",
        "No domain FR",
    ]
    assert search.lookalike_companies(Company(id="x", name="x"), 5) == []


def person(i: str, title: str, seniority: Seniority | None = None) -> Person:
    return Person(id=f"pe_{i}", full_name=f"Person {i}", title=title, seniority=seniority)


def test_similar_people() -> None:
    me = person("0", "Head of Sales", Seniority.HEAD)
    pool = [
        person("1", "Sales Director", Seniority.DIRECTOR),
        person("2", "Head of Marketing"),
        person("3", "Head of Sales Europe"),
        person("4", "Software Engineer"),
        me,
    ]
    got = [p.id for p in similar_people(pool, me)]
    assert got[0] == "pe_3"  # same seniority, department and title words
    assert "pe_0" not in got and "pe_4" not in got
    assert set(got) == {"pe_1", "pe_2", "pe_3"}
    assert len(similar_people(pool, me, limit=1)) == 1
