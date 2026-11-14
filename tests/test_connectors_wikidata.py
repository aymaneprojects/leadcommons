"""Wikidata connector: normalisation helpers and SPARQL paging against respx fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from leadcommons.connectors import USER_AGENT, WikidataConnector
from leadcommons.connectors.wikidata import (
    ENDPOINT,
    build_details_query,
    build_ids_query,
)
from leadcommons.models import SIZE_RANGES, make_id
from leadcommons.packs.domains import normalize_domain
from leadcommons.packs.isic import isic_section
from leadcommons.packs.normalize import size_bucket

DATA = Path(__file__).parent / "fixtures" / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.Example.com/path?q=1", "example.com"),
        ("http://shop.example.com", "example.com"),
        ("example.org", "example.org"),
        ("https://www.acme.co.uk/about", "acme.co.uk"),
        ("https://portal.acme.co.uk", "acme.co.uk"),
        ("https://www.foo.com.au", "foo.com.au"),
        ("http://x.co.jp/", "x.co.jp"),
        ("https://bar.com.br", "bar.com.br"),
        ("https://www.baz.co.za", "baz.co.za"),
        ("https://www.maroc.co.ma", "maroc.co.ma"),
        ("", None),
        (None, None),
        ("localhost", None),
        ("http://", None),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("n", "bucket"),
    [
        (None, None), (0, None), (-4, None), (1, "1-10"), (10, "1-10"), (11, "11-50"),
        (50, "11-50"), (51, "51-200"), (200, "51-200"), (201, "201-500"), (500, "201-500"),
        (501, "501-1000"), (1000, "501-1000"), (1001, "1001-5000"), (5000, "1001-5000"),
        (5001, "5001-10000"), (10000, "5001-10000"), (10001, "10001+"), (900000, "10001+"),
    ],
)  # fmt: skip
def test_size_bucket(n, bucket):
    assert size_bucket(n) == bucket
    assert bucket is None or bucket in SIZE_RANGES


def test_isic_section():
    assert isic_section("retail banking") == "K"
    assert isic_section("software industry") == "J"
    assert isic_section("automotive industry") == "C"
    assert isic_section("something unknowable") is None
    assert isic_section(None) is None
    assert isic_section(None, "Q8148") == "C"


def test_queries_validate_input():
    assert 'wdt:P297 "FR"' in build_ids_query("fr", 10, 0, True)
    assert "wd:Q4830453" in build_ids_query("FR", 10, 0, True)
    with pytest.raises(ValueError):
        build_ids_query('FR" } DROP', 10, 0, True)
    with pytest.raises(ValueError):
        build_details_query(["Q1", "Q2 } injected"])


@respx.mock
def test_iter_companies_pages_and_normalises():
    route = respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=load("wikidata_ids.json")),
            httpx.Response(200, json=load("wikidata_details.json")),
        ]
    )
    conn = WikidataConnector(
        page_size=5, min_interval=0, sleep=lambda s: None, prefer_website=False
    )
    out = list(conn.iter_companies(country="GB"))
    assert route.call_count == 2
    assert route.calls[0].request.headers["user-agent"] == USER_AGENT
    assert "query.wikidata.org" in str(route.calls[0].request.url)
    assert [c.name for c in out] == ["Acme Industries", "Beta Bank"]  # unlabeled Q300 dropped
    acme, beta = out
    assert acme.domain == "acme-industries.co.uk"
    assert acme.country == "GB" and acme.city == "Leeds"
    assert acme.industry == "C" and acme.size_range == "201-500" and acme.founded_year == 1957
    assert acme.registry_ids == {"wikidata": "Q100", "lei": "5493001KJTIIGC8Y1R12"}
    assert acme.id == make_id("co", "lei", "5493001KJTIIGC8Y1R12")
    assert acme.links["website"].startswith("https://www.acme")
    assert acme.sources[0].source == "wikidata" and acme.sources[0].licence == "CC0-1.0"
    assert acme.sources[0].url == "http://www.wikidata.org/entity/Q100"
    assert beta.id == make_id("co", "wd", "Q200")
    assert beta.domain == "beta-bank.com" and beta.industry == "K" and beta.size_range is None


@respx.mock
def test_limit_and_retry_after():
    sleeps: list[float] = []
    respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json=load("wikidata_ids.json")),
            httpx.Response(200, json=load("wikidata_details.json")),
        ]
    )
    conn = WikidataConnector(page_size=5, min_interval=0, sleep=sleeps.append, prefer_website=False)
    out = list(conn.iter_companies(country="GB", limit=1))
    assert len(out) == 1
    assert 7 in sleeps  # Retry-After honoured


@respx.mock
def test_gives_up_after_repeated_errors():
    respx.get(ENDPOINT).mock(return_value=httpx.Response(503))
    conn = WikidataConnector(min_interval=0, sleep=lambda s: None)
    conn._http.max_retries = 2
    with pytest.raises(httpx.HTTPError):
        list(conn.iter_companies(country="GB"))
