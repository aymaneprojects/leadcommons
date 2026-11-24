"""GLEIF connector against respx fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from pepite.connectors import USER_AGENT, GleifConnector
from pepite.connectors.gleif import ENDPOINT
from pepite.models import make_id

DATA = Path(__file__).parent / "fixtures" / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def conn() -> GleifConnector:
    return GleifConnector(min_interval=0, sleep=lambda s: None)


@respx.mock
def test_paginates_and_normalises():
    route = respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=load("gleif_page1.json")),
            httpx.Response(200, json=load("gleif_page2.json")),
        ]
    )
    out = list(conn().iter_companies(country="gb"))
    assert route.call_count == 2
    q1 = route.calls[0].request.url.params
    assert q1["filter[entity.legalAddress.country]"] == "GB"
    assert q1["filter[entity.status]"] == "ACTIVE"
    assert q1["page[size]"] == "200" and q1["page[number]"] == "1"
    assert route.calls[1].request.url.params["page[number]"] == "2"
    assert route.calls[0].request.headers["user-agent"] == USER_AGENT
    assert [c.legal_name for c in out] == [
        "ACME INDUSTRIES LIMITED",
        "Gamma Holdings plc",
        "Delta Ltd",
    ]
    first = out[0]
    assert first.id == make_id("co", "lei", "5493001KJTIIGC8Y1R12")
    assert first.region == "WYK" and first.city == "Leeds" and first.country == "GB"
    assert first.domain is None and first.links == {}
    assert out[1].region is None
    assert first.sources[0].source == "gleif" and first.sources[0].licence == "CC0-1.0"


@respx.mock
def test_limit_stops_early():
    route = respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=load("gleif_page1.json"))
    )
    assert len(list(conn().iter_companies(country="GB", limit=1))) == 1
    assert route.call_count == 1


@respx.mock
def test_fetch_by_lei_batches():
    route = respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=load("gleif_page1.json"))
    )
    out = list(conn().fetch_by_lei(["5493001kjtiigc8y1r12", "529900ZZZZZZZZZZZZ99"]))
    assert len(out) == 2
    assert route.calls[0].request.url.params["filter[lei]"].startswith("5493001KJTIIGC8Y1R12,")
