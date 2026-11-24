from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from starlette.requests import Request

from pepite.ai import ignored_filters, parse_query, to_query_params, to_url
from pepite.ai import llm as llm_mod
from pepite.models import SIZE_RANGES, Seniority
from tests.test_api import build_app, make_settings

SMALL = ["1-10", "11-50"]
SME = ["11-50", "51-200", "201-500"]
MID = ["201-500", "501-1000", "1001-5000"]
LARGE = ["1001-5000", "5001-10000", "10001+"]


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("PEPITE_LLM_URL", "PEPITE_LLM_MODEL", "PEPITE_LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def got(text: str, kind: str = "companies") -> dict[str, Any]:
    p = parse_query(text, kind)  # type: ignore[arg-type]
    return {
        "countries": p.company.countries,
        "sizes": p.company.size_ranges,
        "industries": p.company.industries,
        "keywords": p.company.keywords,
        "titles": p.people.titles,
        "seniorities": [s.value for s in p.people.seniorities],
    }


def expect(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "countries": [], "sizes": [], "industries": [], "keywords": [], "titles": [],
        "seniorities": [],
    }  # fmt: skip
    base.update(kw)
    return base


CASES: list[tuple[str, str, dict[str, Any]]] = [
    # countries: names, demonyms, codes, languages
    ("French companies", "companies", expect(countries=["FR"])),
    ("entreprises en France", "companies", expect(countries=["FR"])),
    ("allemandes", "companies", expect(countries=["DE"])),
    ("companies in the UK", "companies", expect(countries=["GB"])),
    ("US companies", "companies", expect(countries=["US"])),
    ("startups au Maroc", "companies", expect(countries=["MA"], sizes=SMALL)),
    ("sociétés belges et suisses", "companies", expect(countries=["BE", "CH"])),
    ("empresas españolas", "companies", expect(countries=["ES"])),
    ("Unternehmen in Deutschland", "companies", expect(countries=["DE"])),
    ("companies from the United Kingdom", "companies", expect(countries=["GB"])),
    ("entreprises au Royaume-Uni", "companies", expect(countries=["GB"])),
    ("entreprises de Côte d'Ivoire", "companies", expect(countries=["CI"])),
    # cities map to their country; the city is not kept as a keyword
    ("boulangeries à Lyon", "companies", expect(countries=["FR"], keywords=["boulangeries"])),
    ("software companies in Berlin", "companies", expect(countries=["DE"], industries=["J"])),
    # sizes: words
    ("small startups", "companies", expect(sizes=SMALL)),
    ("TPE", "companies", expect(sizes=SMALL)),
    ("PME françaises", "companies", expect(countries=["FR"], sizes=SME)),
    ("SMB software", "companies", expect(sizes=SME, industries=["J"])),
    ("ETI industrielles", "companies", expect(sizes=MID, industries=["C"])),
    ("mid-size companies", "companies", expect(sizes=MID)),
    ("grandes entreprises", "companies", expect(sizes=LARGE)),
    ("large enterprise banks", "companies", expect(sizes=LARGE, industries=["K"])),
    # sizes: numbers
    ("10-50 employees", "companies", expect(sizes=["11-50"])),
    ("plus de 1000 salariés", "companies", expect(sizes=LARGE)),
    ("companies with under 200 employees", "companies", expect(sizes=SMALL + ["51-200"])),
    ("entre 50 et 500 employés", "companies", expect(sizes=["51-200", "201-500"])),
    ("1000+ employees", "companies", expect(sizes=LARGE)),
    ("more than 500 staff", "companies", expect(sizes=["501-1000"] + MID[2:] + LARGE[1:])),
    ("moins de 10 salariés", "companies", expect(sizes=["1-10"])),
    # industries
    ("banks and insurance", "companies", expect(industries=["K"], keywords=[])),
    ("hôpitaux", "companies", expect(industries=["Q"])),
    ("restaurants et hôtels", "companies", expect(industries=["I"])),
    ("e-commerce", "companies", expect(industries=["G"])),
    ("BTP", "companies", expect(industries=["F"])),
    ("logistique et transport", "companies", expect(industries=["H"])),
    ("immobilier", "companies", expect(industries=["L"])),
    ("law firms", "companies", expect(industries=["M"])),
    ("IT services", "companies", expect(industries=["J"])),
    ("energy and software", "companies", expect(industries=["D", "J"])),
    # people
    (
        "CTO fintech Allemagne 50-200",
        "people",
        expect(
            titles=["CTO"],
            seniorities=["c_suite"],
            industries=["K"],
            countries=["DE"],
            sizes=["51-200"],
        ),
    ),
    (
        "heads of marketing in France",
        "people",
        expect(titles=["head of marketing"], seniorities=["head"], countries=["FR"]),
    ),
    (
        "directeur commercial PME",
        "people",
        expect(titles=["directeur commercial"], seniorities=["director"], sizes=SME),
    ),
    (
        "VP sales US software",
        "people",
        expect(titles=["VP sales"], seniorities=["vp"], countries=["US"], industries=["J"]),
    ),
    (
        "founders of French startups",
        "people",
        expect(titles=["founder"], seniorities=["founder"], countries=["FR"], sizes=SMALL),
    ),
    ("fondateurs", "people", expect(titles=["fondateur"], seniorities=["founder"])),
    ("CEO, CFO", "people", expect(titles=["CEO", "CFO"], seniorities=["c_suite"])),
    (
        "software engineers in Paris",
        "people",
        expect(titles=["software engineer"], seniorities=["entry"], countries=["FR"]),
    ),
    (
        "DRH",
        "people",
        expect(titles=["DRH", "directeur des ressources humaines"], seniorities=["director"]),
    ),
    # conservative behaviour
    ("software companies", "companies", expect(industries=["J"])),
    ("wine producers", "companies", expect(keywords=["wine", "producers"])),
    ("", "companies", expect()),
]


@pytest.mark.parametrize("text,kind,expected", CASES, ids=[c[0] or "empty" for c in CASES])
def test_rules_table(text: str, kind: str, expected: dict[str, Any]) -> None:
    result = got(text, kind)
    if kind == "companies":  # company queries also carry people fields; only check the rest
        result.pop("titles")
        result.pop("seniorities")
        expected = {k: v for k, v in expected.items() if k not in ("titles", "seniorities")}
        expected = {**expect(), **expected}
        expected.pop("titles")
        expected.pop("seniorities")
    assert result == expected


def test_companies_kind_does_not_invent_weak_roles() -> None:
    p = parse_query("engineers and consultants", "companies")
    assert p.people.titles == [] and p.company.keywords == ["engineers", "consultants"]
    q = parse_query("CEO of French banks", "companies")
    assert q.people.titles == ["CEO"] and q.company.countries == ["FR"]
    assert ignored_filters(q, "companies") == ["title", "seniority"]


@pytest.mark.parametrize(
    "text",
    [
        "Jordan Smith",
        "us and them",
        "it is nice",
        "Georgia Brown",
        "in the chad",
        "Paris Hilton",
        "no",
    ],
)
def test_no_false_countries(text: str) -> None:
    if text == "Paris Hilton":  # a major city name is accepted: documented limitation
        assert parse_query(text).company.countries == ["FR"]
    else:
        assert parse_query(text).company.countries == []


def test_uppercase_codes_only() -> None:
    assert parse_query("IT companies in the US").company.industries == ["J"]
    assert parse_query("IT companies in the US").company.countries == ["US"]
    assert parse_query("it companies").company.industries == []


def test_negation_and_unsupported_go_to_unparsed() -> None:
    p = parse_query("restaurants not in Paris founded after 2015")
    assert p.company.countries == [] and p.company.industries == ["I"]
    assert "not in Paris" in p.unparsed and "2015" in p.unparsed
    assert parse_query("banques sans Maroc").company.countries == []


def test_years_are_not_sizes() -> None:
    p = parse_query("2019-2023 banks")
    assert p.company.size_ranges == [] and p.company.industries == ["K"]


def test_explanations_are_human_lines() -> None:
    p = parse_query("CTO fintech Allemagne 50-200", "people")
    assert "Country: Germany" in p.explanation
    assert "Title: CTO" in p.explanation
    assert any(line.startswith("Size: 51-200") for line in p.explanation)
    assert p.engine == "rules"


def test_keywords_capped() -> None:
    p = parse_query("alpha beta gamma delta epsilon zeta eta theta")
    assert len(p.company.keywords) == 6 and p.unparsed == ["eta", "theta"]


def test_all_sizes_are_valid_buckets() -> None:
    for _t, _k, exp in CASES:
        assert set(exp["sizes"]) <= set(SIZE_RANGES)


# ------------------------------------------------------------------------- query params


def test_to_query_params_companies_and_people() -> None:
    p = parse_query("PME françaises banques croissance", "companies")
    params = to_query_params(p, "companies")
    assert params == [
        ("country", "FR"), ("size", "11-50"), ("size", "51-200"), ("size", "201-500"),
        ("industry", "K"), ("keyword", "croissance"),
    ]  # fmt: skip
    q = parse_query("CTO and VP sales in Germany banks 50-200", "people")
    assert to_query_params(q, "people") == [
        ("country", "DE"), ("size", "51-200"), ("industry", "K"),
        ("seniority", "c_suite"), ("seniority", "vp"), ("title", "CTO,VP sales"),
    ]  # fmt: skip
    assert ignored_filters(q, "people") == []
    assert ignored_filters(q, "companies") == ["title", "seniority"]
    assert to_url(parse_query("", "companies"), "companies") == ("", "/companies")


def _request(query: str) -> Request:
    return Request({"type": "http", "query_string": query.encode(), "headers": []})


def test_round_trip_with_pages_parsers() -> None:
    pages = pytest.importorskip("pepite.web.pages")
    p = parse_query("PME françaises banques croissance", "companies")
    query, _ = to_url(p, "companies")
    parsed = pages.parse_company_params(_request(query))
    assert parsed["country"] == p.company.countries
    assert parsed["size"] == p.company.size_ranges
    assert parsed["industry"] == p.company.industries
    assert parsed["keyword"] == p.company.keywords

    q = parse_query("directeur commercial, CTO Allemagne banques 50-200", "people")
    query, _ = to_url(q, "people")
    parsed = pages.parse_people_params(_request(query))
    assert parsed["title"] == q.people.titles == ["directeur commercial", "CTO"]
    assert parsed["seniority"] == [s.value for s in q.people.seniorities]
    assert parsed["country"] == ["DE"] and parsed["size"] == ["51-200"]
    assert parsed["industry"] == ["K"]


# --------------------------------------------------------------------------------- LLM

URL = "http://llm.test/v1"


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPITE_LLM_URL", URL + "/")
    monkeypatch.setenv("PEPITE_LLM_MODEL", "tiny")


def chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


@respx.mock
def test_llm_fills_what_rules_missed(llm_env: None) -> None:
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=chat(
            '```json\n{"countries": ["fr", "XX"], "size_ranges": ["11-50", "7-8"],'
            ' "industries": ["k", "Z"], "titles": ["CTO"], "seniorities": ["c_suite", "boss"],'
            ' "keywords": ["Neobank"]}\n```'
        )
    )
    p = parse_query("neo banques de paiement pour freelances", "people")
    assert p.engine == "llm"
    assert p.company.countries == ["FR"]
    assert p.company.size_ranges == ["11-50"]
    assert p.people.titles == ["CTO"] and p.people.seniorities == [Seniority.C_SUITE]
    assert p.company.industries == ["K"]  # rules found "banques" (K), LLM "K" ignored anyway
    assert any(line.startswith("(AI)") for line in p.explanation)
    sent = route.calls.last.request
    body = sent.content.decode()
    assert "neo banques de paiement pour freelances" in body
    assert "Authorization" not in sent.headers
    assert '"model":"tiny"' in body.replace(" ", "")


@respx.mock
def test_rules_win_on_conflict(llm_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPITE_LLM_API_KEY", "sk-test")
    route = respx.post(f"{URL}/chat/completions").mock(
        return_value=chat('{"countries": ["DE"], "size_ranges": ["1-10"], "industries": ["J"]}')
    )
    p = parse_query("banks in France")
    assert p.company.countries == ["FR"] and p.company.industries == ["K"]
    assert p.company.size_ranges == ["1-10"]  # rules found none, so the LLM fills it
    assert p.engine == "llm"
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@respx.mock
def test_llm_adds_nothing_keeps_rules_engine(llm_env: None) -> None:
    respx.post(f"{URL}/chat/completions").mock(return_value=chat("{}"))
    p = parse_query("banks in France")
    assert p.engine == "rules"


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        chat("not json"),
        chat("[1, 2]"),
        chat(""),
        httpx.Response(500, text="boom"),
        httpx.Response(200, json={"unexpected": True}),
    ],
)
def test_llm_bad_replies_fall_back(llm_env: None, response: httpx.Response) -> None:
    respx.post(f"{URL}/chat/completions").mock(return_value=response)
    p = parse_query("banks in France")
    assert p.engine == "rules" and p.company.countries == ["FR"]


@respx.mock
def test_llm_timeout_falls_back(llm_env: None) -> None:
    route = respx.post(f"{URL}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
    p = parse_query("banks in France")
    assert p.engine == "rules" and p.company.industries == ["K"]
    assert route.called
    assert llm_mod.TIMEOUT_SECONDS == 10.0


def test_llm_disabled_without_both_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPITE_LLM_URL", URL)
    assert llm_mod.llm_config() is None  # model missing: never calls out
    assert parse_query("banks").engine == "rules"


def test_validate_drops_unknown_values() -> None:
    v = llm_mod.validate(
        {"countries": ["FR", "ZZ", 3], "size_ranges": ["11-50", "bogus"], "industries": ["K", "?"],
         "seniorities": ["vp", "king"], "titles": ["CTO", "x" * 100], "keywords": "nope"}
    )  # fmt: skip
    assert v == {
        "countries": ["FR"], "size_ranges": ["11-50"], "industries": ["K"],
        "seniorities": [Seniority.VP], "titles": ["CTO"], "keywords": [],
    }  # fmt: skip
    assert llm_mod.validate("x") is None


# --------------------------------------------------------------------------------- API


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(build_app(make_settings(tmp_path)))


def test_api_parse_companies(client: TestClient) -> None:
    r = client.post("/api/ai/parse", json={"text": "PME françaises banques"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "rules"
    assert body["query"] == "country=FR&size=11-50&size=51-200&size=201-500&industry=K"
    assert body["url"] == "/companies?" + body["query"]
    assert "Country: France" in body["explanation"] and body["unparsed"] == []


def test_api_parse_people_and_validation(client: TestClient) -> None:
    r = client.post(
        "/api/ai/parse", json={"text": "CTO fintech Allemagne 50-200", "kind": "people"}
    )
    body = r.json()
    assert body["url"] == ("/people?country=DE&size=51-200&industry=K&seniority=c_suite&title=CTO")
    assert body["ignored"] == []
    assert client.post("/api/ai/parse", json={"text": "x" * 501}).status_code == 422
    assert client.post("/api/ai/parse", json={"text": "x" * 500}).status_code == 200
    assert client.post("/api/ai/parse", json={"text": ""}).status_code == 422
    assert client.post("/api/ai/parse", json={"text": "   "}).status_code == 422
    assert client.post("/api/ai/parse", json={"text": "a", "kind": "x"}).status_code == 422
    empty = client.post("/api/ai/parse", json={"text": "the"}).json()
    assert empty["url"] == "/companies" and empty["query"] == ""
