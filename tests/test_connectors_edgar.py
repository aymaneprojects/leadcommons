"""EDGAR connector: mappings, contact header, 403 hint."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pepite.connectors import USER_AGENT, EdgarConnector
from pepite.connectors.edgar import SUBMISSIONS_URL, TICKERS_URL, pad_cik, parse_submission
from pepite.models import make_id
from pepite.packs.isic import sic_section

DATA = Path(__file__).parent / "fixtures" / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def url(cik: str) -> str:
    return SUBMISSIONS_URL.format(cik=cik)


@pytest.fixture(autouse=True)
def _no_contact(monkeypatch):
    monkeypatch.delenv("PEPITE_CONTACT_EMAIL", raising=False)


def conn() -> EdgarConnector:
    return EdgarConnector(min_interval=0, sleep=lambda s: None)


@pytest.mark.parametrize(
    ("sic", "section"),
    [("0100", "A"), ("1311", "B"), ("1531", "F"), ("2834", "C"), ("3571", "C"), ("4011", "H"),
     ("4812", "J"), ("4911", "D"), ("4941", "E"), ("5311", "G"), ("5812", "I"), ("6021", "K"),
     ("6199", "K"), ("6798", "L"), ("7370", "J"), ("7372", "J"), ("7311", "M"), ("7389", "N"),
     ("7990", "R"), ("8062", "Q"), ("8200", "P"), ("8711", "M"), ("9995", None), ("", None),
     (None, None), ("abc", None), (3571, "C")],
)  # fmt: skip
def test_sic_section(sic, section):
    assert sic_section(sic) == section


def test_pad_cik():
    assert pad_cik(320193) == "0000320193"
    assert pad_cik("0000320193") == "0000320193"
    assert pad_cik("x") is None and pad_cik(None) is None and pad_cik(0) is None


def test_parse_submission():
    c = parse_submission(load("edgar_apple.json"))
    assert c.id == make_id("co", "cik", "0000320193")
    assert c.name == "Apple Inc." and c.country == "US" and c.region == "CA"
    assert c.city == "Cupertino" and c.domain == "apple.com"
    assert c.industry == "C" and c.industry_label == "Electronic Computers"
    assert c.registry_ids == {"cik": "0000320193"}
    assert c.links == {"website": "https://www.apple.com"}
    assert (c.sources[0].source, c.sources[0].licence) == ("edgar", "public-domain")
    assert "942404110" not in json.dumps(c.__dict__, default=str)  # EIN is not stored
    msft = parse_submission(load("edgar_microsoft.json"))
    assert msft.domain is None and msft.links == {} and msft.industry == "J"
    assert parse_submission(load("edgar_foreign.json")).country is None
    assert parse_submission({"cik": "1"}) is None


@respx.mock
def test_iter_companies(monkeypatch):
    monkeypatch.setenv("PEPITE_CONTACT_EMAIL", "ops@example.invalid")
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=load("edgar_tickers.json")))
    apple = respx.get(url("0000320193")).mock(
        return_value=httpx.Response(200, json=load("edgar_apple.json"))
    )
    respx.get(url("0000789019")).mock(
        return_value=httpx.Response(200, json=load("edgar_microsoft.json"))
    )
    respx.get(url("0001234567")).mock(
        return_value=httpx.Response(200, json=load("edgar_foreign.json"))
    )
    out = list(conn().iter_companies(country="US"))
    assert [c.name for c in out] == ["Apple Inc.", "MICROSOFT CORP"]  # foreign dropped, deduped
    assert apple.call_count == 1
    ua = apple.calls[0].request.headers["user-agent"]
    assert ua.startswith(USER_AGENT) and "ops@example.invalid" in ua
    assert list(conn().iter_companies(country="FR")) == []


@respx.mock
def test_limit_and_fetch_by_cik_and_404():
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=load("edgar_tickers.json")))
    respx.get(url("0000320193")).mock(
        return_value=httpx.Response(200, json=load("edgar_apple.json"))
    )
    respx.get(url("0000000042")).mock(return_value=httpx.Response(404))
    assert len(list(conn().iter_companies(limit=1))) == 1
    out = list(conn().fetch_by_cik(["320193", "42", "320193"]))
    assert [c.name for c in out] == ["Apple Inc."]


@respx.mock
def test_403_gives_contact_hint():
    respx.get(TICKERS_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(RuntimeError, match="PEPITE_CONTACT_EMAIL"):
        list(conn().iter_companies())
