"""Official people sources (Wikidata, SEC insiders, Companies House, Brreg) and their merge with
the website results. Wikidata fixtures are real responses; the other three copy the real formats."""

from __future__ import annotations

import base64
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from pepite import enrich
from pepite.connectors.people import (
    CompanyRefs,
    OfficialPerson,
    SourceSkipped,
    official_people,
    source_label,
)
from pepite.connectors.people import edgar_insiders as edgar
from pepite.connectors.people.brreg_roles import BrregRoles, parse_roles
from pepite.connectors.people.companies_house_officers import (
    CompaniesHouseOfficers,
    parse_officers,
)
from pepite.connectors.people.wikidata_people import (
    ENDPOINT,
    WikidataPeople,
    build_people_query,
    build_resolve_query,
    parse_people,
)
from pepite.enrich.people import gather_people, merge_people
from pepite.enrich.titles import guess_department, map_seniority
from pepite.models import Company, Person, Seniority
from pepite.web.jobs import JobRunner
from tests.fakes import FakeSearch, FakeStore
from tests.test_api import make_settings

FIX = Path(__file__).parent / "fixtures" / "people"
TODAY = date(2026, 11, 15)


def load(name: str):
    text = (FIX / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


# ------------------------------------------------------------------------------- Wikidata


def wikidata_router(request: httpx.Request) -> httpx.Response:
    query = request.url.params["query"]
    return httpx.Response(
        200,
        json=load(
            "wikidata_resolve_microsoft.json"
            if "SELECT DISTINCT" in query
            else "wikidata_people_microsoft.json"
        ),
    )


@respx.mock
def test_wikidata_people_by_website_and_ids() -> None:
    respx.get(ENDPOINT).mock(side_effect=wikidata_router)
    source = WikidataPeople(min_interval=0)
    refs = CompanyRefs(domain="microsoft.com", cik="789019", lei="INR2EJN1ERAN0W5ZP974")
    people = source.fetch(refs)
    by_name = {p.name: p for p in people if p.title == "Chief executive officer"}
    assert set(by_name) == {"Satya Nadella"}
    ceo = by_name["Satya Nadella"]
    assert ceo.since == "2014-02-04"
    assert ceo.source == "wikidata"
    assert ceo.source_url == "https://www.wikidata.org/wiki/Q2283#P169"
    assert any(p.name == "Bill Gates" and p.title == "Founder" for p in people)
    assert any(p.title == "Board member" for p in people)
    assert "Steve Ballmer" not in {p.name for p in people}  # ended in 2014
    # one resolve query, one people query, both polite
    assert len(respx.calls) == 2
    assert "microsoft" not in respx.calls[0].request.headers["User-Agent"].lower()
    assert "Pepite" in respx.calls[0].request.headers["User-Agent"]


@respx.mock
def test_wikidata_known_id_skips_the_lookup() -> None:
    route = respx.get(ENDPOINT).mock(side_effect=wikidata_router)
    WikidataPeople(min_interval=0).fetch(CompanyRefs(wikidata_id="Q2283"))
    assert route.call_count == 1
    assert "wd:Q2283" in route.calls[0].request.url.params["query"]


def test_wikidata_ended_roles_and_junk_rows_are_dropped() -> None:
    row = {
        "prop": {"value": "http://www.wikidata.org/prop/P169"},
        "person": {"value": "http://www.wikidata.org/entity/Q1"},
        "personLabel": {"value": "Jane Doe"},
    }
    ended = row | {"end": {"value": "2020-01-01T00:00:00Z"}}
    future = row | {"end": {"value": "2030-01-01T00:00:00Z"}}
    unlabeled = row | {"personLabel": {"value": "Q123456"}}
    other_prop = row | {"prop": {"value": "http://www.wikidata.org/prop/P31"}}
    out = parse_people([ended, unlabeled, other_prop, future, row], "Q9", today=TODAY)
    assert [p.name for p in out] == ["Jane Doe"]  # `future` and `row` are the same role


def test_wikidata_queries_are_built_safely() -> None:
    q = build_resolve_query(CompanyRefs(cik="789019", domain="example.com", gb_number="445790"))
    assert '"0000789019"' in q and '"00445790"' in q and "<https://www.example.com/>" in q
    with pytest.raises(ValueError):
        build_resolve_query(CompanyRefs(lei='X" } DROP'))
    with pytest.raises(ValueError):
        build_people_query("Q1 } UNION {")
    assert "DeprecatedRank" in build_people_query("Q2283")  # current, living, not deprecated


@respx.mock
def test_wikidata_ambiguous_website_gives_nothing() -> None:
    rows = [
        {"item": {"value": f"http://www.wikidata.org/entity/Q{n}"}, "kind": {"value": "website"}}
        for n in (1, 2)
    ]
    route = respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json={"results": {"bindings": rows}})
    )
    assert WikidataPeople(min_interval=0).fetch(CompanyRefs(domain="shared.example")) == []
    assert route.call_count == 1  # no people query for an ambiguous match


# ---------------------------------------------------------------------------- SEC insiders

ACME = CompanyRefs(cik="1234567")
SUBMISSIONS = "https://data.sec.gov/submissions/CIK0001234567.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1234567/{acc}/{doc}"
FORM_FILES = {
    "doc-a.xml": "edgar_form4_a.xml",
    "doc-b.xml": "edgar_form4_b.xml",
    "doc-cfo.xml": "edgar_form3_cfo.xml",
    "doc-fund.xml": "edgar_form4_fund.xml",
    "doc-left.xml": "edgar_form4_left.xml",
    "doc-stale.xml": "edgar_form4_stale.xml",
}


def mock_edgar() -> None:
    respx.get(SUBMISSIONS).mock(
        return_value=httpx.Response(200, json=load("edgar_submissions_acme.json"))
    )
    sub = load("edgar_submissions_acme.json")["filings"]["recent"]
    for acc, doc in zip(sub["accessionNumber"], sub["primaryDocument"], strict=True):
        name = doc.rsplit("/", 1)[-1]
        if name in FORM_FILES:
            url = ARCHIVE.format(acc=acc.replace("-", ""), doc=name)
            respx.get(url).mock(return_value=httpx.Response(200, text=load(FORM_FILES[name])))


@respx.mock
def test_edgar_insiders_officers_and_directors(monkeypatch) -> None:
    monkeypatch.setenv("PEPITE_CONTACT_EMAIL", "ops@example.org")
    mock_edgar()
    people = edgar.EdgarInsiders(min_interval=0, today=TODAY).fetch(ACME)
    got = {p.name: p.title for p in people}
    assert got == {
        "Jane A. Doe": "Chief Executive Officer, Director",  # newest filing wins over the 2025 one
        "Johan Van Der Berg": "SVP, Chief Financial Officer",
        "Robert Smith": "Director",  # "JR" dropped
    }
    jane = next(p for p in people if p.name == "Jane A. Doe")
    assert jane.source_url == (
        "https://www.sec.gov/Archives/edgar/data/1234567/000111111126000010/0001111111-26-000010-index.htm"
    )
    first = respx.calls[0].request
    assert "contact:ops@example.org" in first.headers["User-Agent"]
    # raw XML, not the styled HTML view; 10-Q and 8-K are never fetched
    fetched = {str(c.request.url) for c in respx.calls}
    assert not any("xslF345" in u or "acmr-2026" in u or "form8k" in u for u in fetched)
    assert len(respx.calls) == 1 + 5  # submissions + the five filings inside the 24 months


@respx.mock
def test_edgar_needs_a_declared_contact(monkeypatch) -> None:
    monkeypatch.delenv("PEPITE_CONTACT_EMAIL", raising=False)
    route = respx.get(SUBMISSIONS)
    with pytest.raises(SourceSkipped, match="PEPITE_CONTACT_EMAIL"):
        edgar.EdgarInsiders(min_interval=0).fetch(ACME)
    assert not route.called
    people, reports = official_people(ACME, [edgar.EdgarInsiders(min_interval=0)])
    assert people == [] and reports[0].status == "skipped"
    assert "PEPITE_CONTACT_EMAIL" in (reports[0].reason or "")


@respx.mock
def test_edgar_403_asks_for_contact_and_404_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("PEPITE_CONTACT_EMAIL", "ops@example.org")
    respx.get(SUBMISSIONS).mock(return_value=httpx.Response(403))
    with pytest.raises(SourceSkipped, match="403"):
        edgar.EdgarInsiders(min_interval=0).fetch(ACME)
    respx.get(SUBMISSIONS).mock(return_value=httpx.Response(404))
    assert edgar.EdgarInsiders(min_interval=0).fetch(ACME) == []


def test_edgar_rate_and_filing_selection() -> None:
    assert edgar.MIN_INTERVAL >= 0.1  # at most 10 requests per second
    rows = edgar.recent_insider_filings(load("edgar_submissions_acme.json"), TODAY)
    assert [r[0] for r in rows][:2] == ["0001111111-26-000010", "0003333333-26-000021"]
    assert all(r[1].endswith(".xml") and "/" not in r[1] for r in rows)
    assert "0006666666-23-000001" not in [r[0] for r in rows]  # older than 24 months
    assert (
        len(edgar.recent_insider_filings(load("edgar_submissions_acme.json"), TODAY, limit=2)) == 2
    )


def test_edgar_parser_ignores_dtds_and_junk() -> None:
    assert (
        edgar.parse_ownership("<!DOCTYPE x [<!ENTITY a 'b'>]><ownershipDocument/>", TODAY, "a")
        == []
    )
    assert edgar.parse_ownership("not xml", TODAY, "a") == []
    owners = edgar.parse_ownership(load("edgar_form4_fund.xml"), TODAY, "0003333333-26-000021")
    assert [(o.name, o.is_director) for o in owners] == [
        ("BIG HOLDINGS FUND LP", False),
        ("SMITH ROBERT JR", True),
    ]


# -------------------------------------------------------------------------- Companies House

GB = CompanyRefs(gb_number="1234567")
CH_URL = "https://api.company-information.service.gov.uk/company/01234567/officers"


def test_companies_house_parser_keeps_active_people_only() -> None:
    people = parse_officers(load("companies_house_officers.json"), "01234567")
    assert [(p.name, p.title, p.since) for p in people] == [
        ("Alice Jane Example", "Director", "2015-03-01"),
        ("Bob Sample", "Company secretary", "2018-07-12"),
        ("Dr Erika Mustermann", "Director", "2021-11-01"),
    ]
    assert people[0].source_url == (
        "https://find-and-update.company-information.service.gov.uk/company/01234567/officers"
    )
    kept = repr(people)
    assert "1975" not in kept and "Fake Street" not in kept and "British" not in kept


def test_companies_house_without_key_is_skipped_with_a_reason(monkeypatch) -> None:
    monkeypatch.delenv("PEPITE_COMPANIES_HOUSE_KEY", raising=False)
    with respx.mock:
        route = respx.get(CH_URL)
        people, reports = official_people(GB, [CompaniesHouseOfficers(min_interval=0)])
        assert not route.called
    assert people == []
    assert reports[0].status == "skipped"
    assert "PEPITE_COMPANIES_HOUSE_KEY" in reports[0].reason
    assert "free API key" in reports[0].reason


@respx.mock
def test_companies_house_fetch_sends_key_as_basic_auth(monkeypatch) -> None:
    monkeypatch.setenv("PEPITE_COMPANIES_HOUSE_KEY", "abc-123")
    route = respx.get(CH_URL).mock(
        return_value=httpx.Response(200, json=load("companies_house_officers.json"))
    )
    people = CompaniesHouseOfficers(min_interval=0).fetch(GB)
    assert len(people) == 3 and route.call_count == 1  # total_results fits one page
    auth = route.calls[0].request.headers["Authorization"]
    assert auth == "Basic " + base64.b64encode(b"abc-123:").decode()
    assert route.calls[0].request.url.params["items_per_page"] == "100"


@respx.mock
def test_companies_house_bad_key_and_unknown_company(monkeypatch) -> None:
    monkeypatch.setenv("PEPITE_COMPANIES_HOUSE_KEY", "bad")
    respx.get(CH_URL).mock(return_value=httpx.Response(401))
    with pytest.raises(SourceSkipped, match="rejected"):
        CompaniesHouseOfficers(min_interval=0).fetch(GB)
    respx.get(CH_URL).mock(return_value=httpx.Response(404))
    assert CompaniesHouseOfficers(min_interval=0).fetch(GB) == []


# ------------------------------------------------------------------------------------ Brreg

NO = CompanyRefs(no_orgnr="923 609 016")
BRREG_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/923609016/roller"


def test_brreg_roles_current_people_only() -> None:
    people = parse_roles(load("brreg_roller.json"), "923609016")
    assert [(p.name, p.title) for p in people] == [
        ("Ola Nordmann", "CEO (daglig leder)"),
        ("Kari Marie Hansen", "Chair of the board (styreleder)"),
        ("Per Olsen", "Board member (styremedlem)"),
    ]  # stepped-down member and the auditor firm are left out
    assert "1980" not in repr(people)  # birth dates are never kept
    assert people[0].source_url.endswith("/923609016")


@respx.mock
def test_brreg_fetch_and_unknown_unit() -> None:
    respx.get(BRREG_URL).mock(return_value=httpx.Response(200, json=load("brreg_roller.json")))
    assert len(BrregRoles(min_interval=0).fetch(NO)) == 3
    respx.get(BRREG_URL).mock(return_value=httpx.Response(404))
    assert BrregRoles(min_interval=0).fetch(NO) == []


def test_sources_apply_only_to_their_registry() -> None:
    assert BrregRoles().applies(NO) and not BrregRoles().applies(GB)
    assert CompaniesHouseOfficers().applies(GB) and not CompaniesHouseOfficers().applies(NO)
    assert edgar.EdgarInsiders().applies(ACME) and not edgar.EdgarInsiders().applies(GB)
    assert not WikidataPeople().applies(CompanyRefs())


def test_company_refs_from_company() -> None:
    uk = Company(
        id="c1", name="Acme Ltd", domain="acme.co.uk", registry="GB-CH", company_number="123"
    )
    assert CompanyRefs.from_company(uk).gb_number == "123"
    no = Company(id="c2", name="Fjord AS", registry="NO-BRREG", company_number="923609016")
    assert CompanyRefs.from_company(no).no_orgnr == "923609016"
    us = Company(
        id="c3", name="Acme", registry_ids={"wikidata": "q2283", "cik": "0000789019", "lei": "abc"}
    )
    refs = CompanyRefs.from_company(us, "acme.com")
    assert (refs.wikidata_id, refs.cik, refs.lei, refs.domain) == (
        "Q2283",
        "0000789019",
        "ABC",
        "acme.com",
    )
    assert CompanyRefs.from_company(None, "x.com") == CompanyRefs(domain="x.com")


# ------------------------------------------------------------------------------ orchestration


class FakeSource:
    def __init__(self, name, people=(), exc=None, applies=True):
        self.name, self.label, self.licence = name, name.title(), "test"
        self._people, self._exc, self._applies = list(people), exc, applies
        self.calls = 0

    def applies(self, refs):
        return self._applies

    def fetch(self, refs):
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._people


def op(name, title, source="wikidata", url="https://www.wikidata.org/wiki/Q1"):
    return OfficialPerson(name=name, title=title, source=source, source_url=url)


def test_one_failing_source_does_not_hide_the_others() -> None:
    sources = [
        FakeSource("a", exc=httpx.ConnectError("down")),
        FakeSource("b", exc=SourceSkipped("needs a key")),
        FakeSource("c", people=[op("Jane Doe", "Director")]),
        FakeSource("d", applies=False),
    ]
    people, reports = official_people(CompanyRefs(domain="x.com"), sources)
    assert len(people) == 1
    assert [(r.source, r.status) for r in reports] == [
        ("a", "error"),
        ("b", "skipped"),
        ("c", "ok"),
    ]
    assert reports[1].reason == "needs a key"
    assert reports[0].reason and "ConnectError" in reports[0].reason


def site_person(name, title, *urls):
    return Person(
        id="x", full_name=name, company_domain="acme.com", title=title, source_urls=list(urls)
    )


def test_merge_duplicates_prefers_official_title_and_keeps_every_url() -> None:
    official = [
        op(
            "Timothy D. Cook",
            "Chief Executive Officer, Director",
            "edgar",
            "https://www.sec.gov/a-index.htm",
        ),
        op(
            "Tim Cook",
            "Chief executive officer",
            "wikidata",
            "https://www.wikidata.org/wiki/Q2283#P169",
        ),
        op("Tim Cook", "Board member", "wikidata", "https://www.wikidata.org/wiki/Q2283#P3320"),
        op(
            "Alice Jane Example",
            "Director",
            "companies-house",
            "https://find-and-update.company-information.service.gov.uk/company/1/officers",
        ),
    ]
    website = [
        site_person("Tim Cook", "Head of Everything", "https://acme.com/team"),
        site_person("Carla Web", "Head of Sales", "https://acme.com/about"),
    ]
    people = merge_people(official, website, "acme.com", company_id="co_1")
    by_name = {p.full_name: p for p in people}
    assert set(by_name) == {"Tim Cook", "Alice Jane Example", "Carla Web"}  # website spelling wins
    cook = by_name["Tim Cook"]
    assert cook.title == "Chief Executive Officer, Director"  # EDGAR beats Wikidata beats website
    assert cook.seniority is Seniority.C_SUITE and cook.department == "executive"
    assert cook.source_urls == [
        "https://www.sec.gov/a-index.htm",
        "https://www.wikidata.org/wiki/Q2283#P169",
        "https://www.wikidata.org/wiki/Q2283#P3320",
        "https://acme.com/team",
    ]
    assert cook.company_id == "co_1" and cook.company_domain == "acme.com"
    assert by_name["Alice Jane Example"].seniority is Seniority.DIRECTOR
    assert (
        by_name["Carla Web"].title == "Head of Sales"
        and by_name["Carla Web"].seniority is Seniority.HEAD
    )
    # ids stay stable for the same name and domain (saved people are updated, not duplicated)
    assert cook.id == merge_people([], [site_person("Tim Cook", None)], "acme.com")[0].id


def test_wikidata_roles_of_one_person_are_joined_best_first() -> None:
    official = [op("Satya Nadella", "Board member"), op("Satya Nadella", "Chief executive officer")]
    (p,) = merge_people(official, [], "x.com")
    assert p.title == "Chief executive officer, Board member"
    assert p.seniority is Seniority.C_SUITE


def test_different_people_with_same_surname_stay_apart() -> None:
    people = merge_people([op("Anna Lee", "Director"), op("Bob Lee", "Director")], [], "x.com")
    assert len(people) == 2
    # honorifics and ALL-CAPS are cleaned like on websites; one-word and numeric names dropped
    cleaned = merge_people(
        [
            op("Dr Erika MUSTERMANN", "Director"),
            op("Madonna", "Director"),
            op("Bot 9000", "Director"),
        ],
        [],
        "x.com",
    )
    assert [p.full_name for p in cleaned] == ["Erika Mustermann"]


def test_gather_people_merges_official_and_website_and_reports() -> None:
    company = Company(id="co_9", name="Acme", domain="acme.com", registry_ids={"wikidata": "Q9"})
    reports: list = []
    people = gather_people(
        "acme.com",
        lambda: [site_person("Jane Doe", "CEO", "https://acme.com/team")],
        company=company,
        sources=[FakeSource("wikidata", people=[op("Jane Doe", "Chief executive officer")])],
        report=reports,
    )
    assert len(people) == 1 and people[0].company_id == "co_9"
    assert people[0].title == "Chief executive officer"
    assert [(r.source, r.status, r.count) for r in reports] == [
        ("wikidata", "ok", 1),
        ("website", "ok", 1),
    ]


def test_registers_are_not_asked_without_a_company() -> None:
    source = FakeSource("wikidata", people=[op("Jane Doe", "Director")])
    people = gather_people("acme.com", lambda: [site_person("Bo Web", "CEO")], sources=[source])
    assert source.calls == 0 and [p.full_name for p in people] == ["Bo Web"]


def test_website_failure_keeps_register_results_or_raises_when_nothing() -> None:
    def broken():
        raise RuntimeError("blocked")

    company = Company(id="c", name="Acme")
    reports: list = []
    ok = gather_people(
        "acme.com",
        broken,
        company=company,
        report=reports,
        sources=[FakeSource("wikidata", people=[op("Jane Doe", "Director")])],
    )
    assert [p.full_name for p in ok] == ["Jane Doe"]
    assert reports[-1].source == "website" and reports[-1].status == "error"
    with pytest.raises(RuntimeError, match="blocked"):
        gather_people("acme.com", broken, company=company, sources=[FakeSource("wikidata")])


def test_suppressed_domain_stops_everything_and_suppressed_people_are_dropped() -> None:
    source = FakeSource("wikidata", people=[op("Jane Doe", "Director"), op("Bob Roe", "Director")])
    company = Company(id="c", name="Acme")
    crawled: list[int] = []

    def website():
        crawled.append(1)
        return []

    reports: list = []
    assert (
        gather_people(
            "acme.com",
            website,
            company=company,
            sources=[source],
            is_suppressed=lambda v: v == "acme.com",
            report=reports,
        )
        == []
    )
    assert source.calls == 0 and not crawled
    assert reports[0].status == "skipped"

    suppressed = {"jane@acme.com"}
    jane_id = merge_people([], [site_person("Jane Doe", None)], "acme.com")[0].id
    kept = gather_people(
        "acme.com",
        website,
        company=company,
        sources=[source],
        is_suppressed=lambda v: v in suppressed,
        known_addresses=lambda pid: ["jane@acme.com"] if pid == jane_id else [],
    )
    assert [p.full_name for p in kept] == ["Bob Roe"]


def test_enrich_find_people_wires_company_website_and_sources(monkeypatch) -> None:
    site = SimpleNamespace(
        domain="acme.com",
        pages=[],
        people=[site_person("Jane Doe", "CEO", "https://acme.com/team")],
    )
    monkeypatch.setattr(enrich, "get_site", lambda domain, max_pages=10: site)
    company = Company(
        id="co_1", name="Acme", domain="acme.com", registry="NO-BRREG", company_number="923609016"
    )
    reports: list = []
    with respx.mock:
        respx.get(BRREG_URL).mock(return_value=httpx.Response(200, json=load("brreg_roller.json")))
        respx.get(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": {"bindings": []}})
        )
        people = enrich.find_people("ACME.com", company=company, report=reports)
    names = {p.full_name: p for p in people}
    assert {"Jane Doe", "Ola Nordmann", "Kari Marie Hansen", "Per Olsen"} <= set(names)
    assert names["Ola Nordmann"].seniority is Seniority.C_SUITE
    assert names["Per Olsen"].seniority is Seniority.DIRECTOR
    assert {r.source for r in reports} == {"brreg", "wikidata", "website"}
    site.people[0].source_urls.append("mutated")  # the crawl cache is never exposed
    assert "mutated" not in names["Jane Doe"].source_urls


# -------------------------------------------------------------------------- titles and labels


@pytest.mark.parametrize(
    ("title", "level", "dept"),
    [
        ("CEO (daglig leder)", Seniority.C_SUITE, "executive"),
        ("Chair of the board (styreleder)", Seniority.C_SUITE, "executive"),
        ("Deputy chair (nestleder)", Seniority.DIRECTOR, "executive"),
        ("Board member (styremedlem)", Seniority.DIRECTOR, "executive"),
        ("Deputy board member (varamedlem)", Seniority.DIRECTOR, "executive"),
        ("Owner (innehaver)", Seniority.OWNER, "executive"),
        ("General partner (komplementar)", Seniority.PARTNER, None),
        ("Director", Seniority.DIRECTOR, None),
        ("Company secretary", Seniority.MANAGER, "legal"),
        ("LLP designated member", Seniority.PARTNER, None),
        ("Président", Seniority.C_SUITE, "executive"),
        ("Directeur général délégué", Seniority.C_SUITE, "executive"),
        ("Gérant", Seniority.C_SUITE, "executive"),
        ("Administrateur", Seniority.DIRECTOR, "executive"),
        ("Chairperson", Seniority.C_SUITE, "executive"),
        ("Founder", Seniority.FOUNDER, "executive"),
        ("Chief Financial Officer, Director", Seniority.C_SUITE, "finance"),
        ("Vice Chairman", Seniority.DIRECTOR, "executive"),
        ("Chairman and CEO", Seniority.C_SUITE, "executive"),
    ],
)
def test_register_roles_map_to_seniority_and_department(title, level, dept) -> None:
    assert map_seniority(title) is level
    assert guess_department(title) == dept


def test_source_labels() -> None:
    assert source_label("https://www.wikidata.org/wiki/Q2283#P169") == "Wikidata"
    assert source_label("https://www.sec.gov/Archives/edgar/data/1/2/3-index.htm") == "SEC EDGAR"
    assert (
        source_label(
            "https://find-and-update.company-information.service.gov.uk/company/1/officers"
        )
        == "Companies House"
    )
    assert (
        source_label("https://data.brreg.no/enhetsregisteret/oppslag/enheter/923609016")
        == "Brønnøysund register"
    )
    assert source_label("https://www.acme.com/team") == "acme.com"


# ------------------------------------------------------------------------------------ job


def test_job_passes_company_and_stores_the_source_report(tmp_path, monkeypatch) -> None:
    store, search = FakeStore(), FakeSearch()
    company = Company(id="co_7", name="Acme", domain="acme.com", registry_ids={"wikidata": "Q7"})
    search.get_company = lambda cid: company if cid == "co_7" else None
    seen: dict = {}

    def fake_find(
        domain, *, company=None, report=None, is_suppressed=None, known_addresses=None, **kw
    ):
        seen.update(domain=domain, company=company, suppress=is_suppressed("x@y.z"))
        report.append(FakeReport("companies-house", "skipped", "needs a free API key"))
        return [Person(id="p1", full_name="Jane Doe", company_domain=domain)]

    class FakeReport:
        def __init__(self, source, status, reason):
            self.d = {
                "source": source,
                "label": "Companies House",
                "status": status,
                "reason": reason,
                "count": 0,
            }

        def as_dict(self):
            return self.d

    monkeypatch.setattr(enrich, "find_people", fake_find)
    runner = JobRunner(store, search, make_settings(tmp_path, max_workers=1, sync_jobs=True))
    try:
        jid = runner.submit("find_people", {"domain": "acme.com", "company_id": "co_7"}, "Find")
        job = runner.get(jid)
    finally:
        runner.shutdown()
    assert job.status == "done", job.error
    assert seen["company"] is company and seen["domain"] == "acme.com" and seen["suppress"] is False
    assert job.result["people"] == 1
    assert job.result["sources"][0]["reason"] == "needs a free API key"
    assert store.people["p1"].company_id == "co_7"
