"""Wikidata trends: employees over time, revenue, listing, ownership; honest empties."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from leadcommons.connectors._http import PoliteClient
from leadcommons.insights import trends
from leadcommons.insights.model import EmployeePoint, Trends

FIXTURES = Path(__file__).parent / "fixtures" / "insights"
ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
API = "https://www.wikidata.org/w/api.php"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def client() -> PoliteClient:
    return PoliteClient(min_interval=0, max_retries=0, sleep=lambda s: None)


def test_parse_entity() -> None:
    entity = load("wikidata_entity.json")["entities"]["Q42042"]
    labels = {
        k: v["labels"]["en"]["value"] for k, v in load("wikidata_labels.json")["entities"].items()
    }
    t = trends.parse_entity(entity, labels)
    assert t.wikidata_id == "Q42042" and t.wikidata_url.endswith("/Q42042")
    assert [(p.date, p.count) for p in t.employees] == [
        ("2022-12-31", 1200),
        ("2023-12-31", 1500),
        ("2024-12-31", 1800),
    ]  # fmt: skip  (deprecated value dropped)
    assert [(r.year, r.amount, r.currency) for r in t.revenue] == [
        (2023, 250_000_000.0, "euro"), (2024, 310_000_000.0, "euro"),
    ]
    assert [(x.exchange, x.ticker) for x in t.listings] == [("Frankfurt Stock Exchange", "NWD")]
    assert t.owned_by == ["Nordwind Holding"]
    assert t.growth(1) == 20.0
    assert t.growth(2) == 50.0


def test_growth_needs_close_enough_points() -> None:
    t = Trends(employees=[EmployeePoint("2015-12-31", 100), EmployeePoint("2024-12-31", 200)])
    assert t.growth(1) is None
    assert Trends(employees=[EmployeePoint("2024", 10)]).growth(1) is None
    assert Trends(employees=[EmployeePoint("", 10), EmployeePoint("", 20)]).growth(1) is None


@respx.mock
def test_fetch_by_known_qid() -> None:
    respx.get(ENTITY.format("Q42042")).respond(200, json=load("wikidata_entity.json"))
    labels = respx.get(API, params__contains={"action": "wbgetentities"}).respond(
        200, json=load("wikidata_labels.json")
    )
    t = trends.fetch_trends("Nordwind Data", "nordwind.example", "Q42042", client())
    assert t.employees[-1].count == 1800 and t.revenue[0].currency == "euro"
    ids = labels.calls.last.request.url.params["ids"].split("|")
    assert set(ids) == {"Q4916", "Q82059", "Q777"}


@respx.mock
def test_fetch_by_name_requires_matching_website() -> None:
    respx.get(API, params__contains={"action": "wbsearchentities"}).respond(
        200, json=load("wikidata_search.json")
    )
    respx.get(ENTITY.format("Q111")).respond(200, json=load("wikidata_other.json"))
    respx.get(ENTITY.format("Q42042")).respond(200, json=load("wikidata_entity.json"))
    respx.get(API, params__contains={"action": "wbgetentities"}).respond(
        200, json=load("wikidata_labels.json")
    )
    t = trends.fetch_trends("Nordwind Data", "www.nordwind.example", None, client())
    assert t is not None and t.wikidata_id == "Q42042"


@respx.mock
def test_no_match_returns_none() -> None:
    respx.get(API, params__contains={"action": "wbsearchentities"}).respond(
        200, json=load("wikidata_search.json")
    )
    respx.get(ENTITY.format("Q111")).respond(200, json=load("wikidata_other.json"))
    respx.get(ENTITY.format("Q42042")).respond(200, json=load("wikidata_other.json"))
    assert trends.fetch_trends("Nordwind Data", "nordwind.example", None, client()) is None


def test_no_domain_and_no_qid_makes_no_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        assert trends.fetch_trends("Nordwind", None, None, client()) is None
    assert not router.calls


@respx.mock
def test_label_failure_keeps_numbers() -> None:
    respx.get(ENTITY.format("Q42042")).respond(200, json=load("wikidata_entity.json"))
    respx.get(API, params__contains={"action": "wbgetentities"}).respond(500)
    t = trends.fetch_trends("x", "nordwind.example", "Q42042", client())
    assert t.revenue[0].currency is None and t.employees[0].count == 1200
    assert t.listings == []  # exchange unnamed without a label: not invented


@respx.mock
def test_http_error_propagates() -> None:
    respx.get(ENTITY.format("Q1")).mock(side_effect=httpx.ConnectError("blocked"))
    try:
        trends.fetch_trends("x", None, "Q1", client())
    except httpx.HTTPError:
        pass
    else:
        raise AssertionError("expected an HTTP error")
