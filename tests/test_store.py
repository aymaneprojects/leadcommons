"""Tests of the local user database (sqlite in tmp_path; Postgres only when env is set)."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from leadcommons.models import (
    Company,
    EmailCandidate,
    EmailStatus,
    Person,
    Provenance,
    Seniority,
)
from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.store import Store
from leadcommons.store.tables import SuppressionRow

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    # The parent folder "db" does not exist yet: the store must create it.
    s = Store(f"sqlite:///{tmp_path / 'db' / 'lc.db'}")
    yield s
    s.close()


def make_person(
    pid: str,
    full_name: str,
    *,
    title: str | None = None,
    seniority: Seniority | None = None,
    domain: str | None = "acme.example",
    company_id: str | None = None,
    last_seen: datetime = T0,
) -> Person:
    return Person(
        id=pid,
        full_name=full_name,
        company_domain=domain,
        company_id=company_id,
        title=title,
        seniority=seniority,
        first_seen=T0,
        last_seen=last_seen,
    )


def make_email(address: str, status=EmailStatus.VALID, confidence=80, checked=T0):
    return EmailCandidate(
        address=address,
        status=status,
        confidence=confidence,
        pattern="{first}.{last}",
        checks={"syntax": "ok"},
        checked_at=checked,
    )


def ids(page) -> list[str]:
    return [p.id for p in page.items]


def test_creates_parent_folder_and_wal_mode(store):
    with store._sessions() as s:
        assert s.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert s.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_save_person_upsert_merges(store):
    store.save_person(
        make_person(
            "p1",
            "Ada Lovelace",
            title="CTO",
            seniority=Seniority.C_SUITE,
            last_seen=T0,
        )
    )
    first = store.get_person("p1")
    first_seen = first.first_seen
    first.source_urls = ["https://a.example/1"]
    store.save_person(first)

    later = T0 + timedelta(days=5)
    newer = make_person("p1", "Ada King", title=None, seniority=None, last_seen=later)
    newer.first_seen = later
    newer.source_urls = ["https://a.example/1", "https://b.example/2"]
    store.save_person(newer)

    got = store.get_person("p1")
    assert got.full_name == "Ada King"
    assert got.title == "CTO"  # None does not erase an existing value
    assert got.seniority == Seniority.C_SUITE
    assert got.first_seen == first_seen  # kept
    assert got.last_seen == later  # moved forward
    assert got.source_urls == ["https://a.example/1", "https://b.example/2"]  # no duplicates


def test_save_person_does_not_move_last_seen_backwards(store):
    store.save_person(make_person("p1", "Ada", last_seen=T0 + timedelta(days=3)))
    store.save_person(make_person("p1", "Ada", last_seen=T0))
    assert store.get_person("p1").last_seen == T0 + timedelta(days=3)


def test_datetimes_come_back_timezone_aware_utc(store):
    store.save_person(make_person("p1", "Ada"))
    person = store.get_person("p1")
    assert person.last_seen.tzinfo is not None
    assert person.last_seen.utcoffset() == timedelta(0)


def test_save_person_stores_non_utc_aware_datetime_as_utc(store):
    paris = timezone(timedelta(hours=1))
    local = datetime(2026, 3, 1, 12, 0, tzinfo=paris)
    store.save_person(make_person("p1", "Ada", last_seen=local))
    assert store.get_person("p1").last_seen == local.astimezone(UTC)


def test_best_email_highest_confidence_then_newest(store):
    store.save_person(make_person("p1", "Ada"))
    assert store.best_email("p1") is None

    store.save_email("p1", make_email("ada@acme.example", confidence=60, checked=T0))
    store.save_email("p1", make_email("a.lovelace@acme.example", confidence=90, checked=T0))
    store.save_email(
        "p1", make_email("ada.l@acme.example", confidence=90, checked=T0 + timedelta(days=1))
    )
    best = store.best_email("p1")
    assert best.address == "ada.l@acme.example"  # tie on confidence, newer checked_at wins
    assert best.checked_at == T0 + timedelta(days=1)

    store.save_email("p1", make_email("ada@acme.example", confidence=95, checked=T0))
    assert store.best_email("p1").address == "ada@acme.example"  # higher confidence wins


def test_save_email_upsert_on_person_and_address(store):
    store.save_person(make_person("p1", "Ada"))
    store.save_email(
        "p1", make_email("Ada@Acme.example", status=EmailStatus.UNKNOWN, confidence=10)
    )
    store.save_email("p1", make_email("ada@acme.example", status=EmailStatus.VALID, confidence=85))
    with store._sessions() as s:
        from leadcommons.store.tables import EmailRow

        rows = list(s.scalars(select(EmailRow)))
    assert len(rows) == 1
    assert rows[0].status == "valid"
    assert rows[0].address == "ada@acme.example"


def test_company_round_trip_and_upsert(store):
    fetched = datetime(2025, 6, 1, 8, 30, tzinfo=UTC)
    company = Company(
        id="co_1",
        name="Acme",
        domain="acme.example",
        legal_name="Acme SAS",
        country="FR",
        region="IDF",
        city="Paris",
        industry="J",
        industry_label="Information and communication",
        size_range="11-50",
        founded_year=2010,
        registry_ids={"lei": "969500ABC", "wikidata": "Q42"},
        links={"website": "https://acme.example"},
        sources=[
            Provenance(
                source="gleif", licence="CC0-1.0", url="https://gleif.example/x", fetched_at=fetched
            ),
            Provenance(source="website", licence="n/a"),
        ],
    )
    store.save_company(company)
    assert store.get_company("co_1") == company
    assert store.saved_company_ids() == {"co_1"}

    company.name = "Acme Renamed"
    store.save_company(company)
    assert store.get_company("co_1").name == "Acme Renamed"
    assert store.saved_company_ids() == {"co_1"}
    assert store.get_company("missing") is None


def test_search_text_matches_name_or_title_case_insensitive(store):
    store.save_person(make_person("p1", "Ada Lovelace", title="Chief Technology Officer"))
    store.save_person(make_person("p2", "Grace Hopper", title="Rear Admiral"))
    store.save_person(make_person("p3", "Linus", title="Head of Ops"))

    assert ids(store.search_people(PeopleFilters(text="ADA"))) == ["p1"]
    assert ids(store.search_people(PeopleFilters(text="technology"))) == ["p1"]
    assert sorted(ids(store.search_people(PeopleFilters(text="o")))) == ["p1", "p2", "p3"]
    assert ids(store.search_people(PeopleFilters(text="100%"))) == []  # wildcard is literal


def test_search_titles_any_substring(store):
    store.save_person(make_person("p1", "A", title="Head of Sales"))
    store.save_person(make_person("p2", "B", title="Account Director"))
    store.save_person(make_person("p3", "C", title="Engineer"))
    page = store.search_people(PeopleFilters(titles=["sales", "DIRECTOR"]))
    assert sorted(ids(page)) == ["p1", "p2"]
    assert page.total == 2


def test_search_seniorities(store):
    store.save_person(make_person("p1", "A", seniority=Seniority.FOUNDER))
    store.save_person(make_person("p2", "B", seniority=Seniority.ENTRY))
    store.save_person(make_person("p3", "C"))
    page = store.search_people(PeopleFilters(seniorities=[Seniority.FOUNDER, Seniority.VP]))
    assert ids(page) == ["p1"]


def test_search_email_statuses(store):
    store.save_person(make_person("p1", "A"))
    store.save_person(make_person("p2", "B"))
    store.save_person(make_person("p3", "C"))
    store.save_email("p1", make_email("a@x.example", status=EmailStatus.INVALID))
    store.save_email("p1", make_email("a2@x.example", status=EmailStatus.VALID))
    store.save_email("p2", make_email("b@x.example", status=EmailStatus.CATCH_ALL))
    page = store.search_people(PeopleFilters(email_statuses=[EmailStatus.VALID]))
    assert ids(page) == ["p1"]  # one row per person even with two matching emails
    assert page.total == 1
    page = store.search_people(
        PeopleFilters(email_statuses=[EmailStatus.CATCH_ALL, EmailStatus.INVALID])
    )
    assert sorted(ids(page)) == ["p1", "p2"]


def test_search_company_domains(store):
    store.save_person(make_person("p1", "A", domain="acme.example"))
    store.save_person(make_person("p2", "B", domain="globex.example"))
    filters = PeopleFilters(company=CompanyFilters(domains=["Globex.example"]))
    assert ids(store.search_people(filters)) == ["p2"]


def test_search_company_countries_joins_companies(store):
    def company(cid: str, domain: str, country: str) -> Company:
        return Company(id=cid, name=cid, domain=domain, country=country)

    store.save_company(company("co_fr", "acme.example", "FR"))
    store.save_company(company("co_de", "bruecke.example", "DE"))
    store.save_person(make_person("p1", "A", company_id="co_fr"))
    store.save_person(make_person("p2", "B", company_id="co_de"))
    store.save_person(make_person("p3", "C", company_id=None))  # no company: never matches

    filters = PeopleFilters(company=CompanyFilters(countries=["DE", "FR"]))
    assert sorted(ids(store.search_people(filters))) == ["p1", "p2"]
    filters = PeopleFilters(company=CompanyFilters(countries=["DE"]))
    assert ids(store.search_people(filters)) == ["p2"]


def test_search_combined_filters(store):
    store.save_company(Company(id="co_fr", name="X", country="FR"))
    store.save_person(
        make_person(
            "p1", "Ada", title="Head of Sales", seniority=Seniority.HEAD, company_id="co_fr"
        )
    )
    store.save_person(
        make_person("p2", "Ada Two", title="Intern", seniority=Seniority.INTERN, company_id="co_fr")
    )
    store.save_email("p1", make_email("ada@acme.example", status=EmailStatus.VALID))
    filters = PeopleFilters(
        text="ada",
        titles=["sales"],
        seniorities=[Seniority.HEAD],
        email_statuses=[EmailStatus.VALID],
        company=CompanyFilters(countries=["FR"]),
    )
    assert ids(store.search_people(filters)) == ["p1"]


def test_search_order_and_pagination_totals(store):
    for i in range(7):
        store.save_person(make_person(f"p{i}", f"Person {i}", last_seen=T0 + timedelta(days=i)))
    page1 = store.search_people(PeopleFilters(), page=1, per_page=3)
    assert page1.total == 7
    assert page1.page == 1 and page1.per_page == 3
    assert ids(page1) == ["p6", "p5", "p4"]  # last_seen desc
    page3 = store.search_people(PeopleFilters(), page=3, per_page=3)
    assert page3.total == 7
    assert ids(page3) == ["p0"]
    empty = store.search_people(PeopleFilters(), page=5, per_page=3)
    assert empty.items == [] and empty.total == 7


def test_lists_add_remove_size_and_order(store):
    store.save_person(make_person("p1", "A"))
    people_list = store.create_list("Hot leads", "people")
    companies_list = store.create_list("Targets", "companies")
    assert len(people_list) == 12

    store.add_to_list(people_list, ["p3", "p1", "p2"])
    store.add_to_list(people_list, ["p1", "p9"])  # p1 already there, not re-added
    assert store.list_members(people_list) == ["p3", "p1", "p2", "p9"]

    store.add_to_list(companies_list, ["co_1"])
    infos = {li.id: li for li in store.lists()}
    assert infos[people_list].size == 4
    assert infos[people_list].name == "Hot leads"
    assert infos[people_list].created_at.tzinfo is not None
    assert infos[companies_list].size == 1

    store.remove_from_list(people_list, ["p1", "unknown"])
    assert store.list_members(people_list) == ["p3", "p2", "p9"]
    assert {li.id: li.size for li in store.lists(kind="people")} == {people_list: 3}
    assert [li.id for li in store.lists(kind="companies")] == [companies_list]


def test_saved_person_ids_only_from_people_lists(store):
    people_list = store.create_list("People", "people")
    companies_list = store.create_list("Companies", "companies")
    store.add_to_list(people_list, ["p1", "p2"])
    store.add_to_list(companies_list, ["co_1"])
    assert store.saved_person_ids() == {"p1", "p2"}


def test_saved_searches(store):
    first = store.save_search("CTOs in France", "people", '{"titles": ["CTO"]}')
    second = store.save_search("Companies in DE", "companies", '{"countries": ["DE"]}')
    assert first != second
    all_searches = store.saved_searches()
    assert [s.name for s in all_searches] == ["CTOs in France", "Companies in DE"]
    assert all_searches[0].filters_json == '{"titles": ["CTO"]}'
    assert all_searches[0].created_at.tzinfo is not None
    assert [s.id for s in store.saved_searches(kind="companies")] == [second]


def test_suppression_by_email(store):
    store.add_suppression("Ada@Acme.example", reason="asked to be removed")
    assert store.is_suppressed("ada@acme.example")
    assert store.is_suppressed("  ADA@acme.example ")
    assert not store.is_suppressed("grace@acme.example")


def test_suppression_by_domain(store):
    store.add_suppression("globex.example")
    assert store.is_suppressed("anyone@globex.example")
    assert store.is_suppressed("Someone@GLOBEX.example")
    assert not store.is_suppressed("anyone@acme.example")
    assert not store.is_suppressed("anyone@sub.globex.example")


def test_suppression_stores_only_hash(store):
    store.add_suppression("@Globex.example")  # leading '@' means a domain
    store.add_suppression("ada@acme.example")
    with store._sessions() as s:
        rows = {r.hash: r for r in s.scalars(select(SuppressionRow))}
    expected_domain = hashlib.sha256(b"globex.example").hexdigest()
    expected_email = hashlib.sha256(b"ada@acme.example").hexdigest()
    assert set(rows) == {expected_domain, expected_email}
    assert rows[expected_domain].kind == "domain"
    assert rows[expected_email].kind == "email"
    assert rows[expected_email].reason == "opt-out"
    assert all("acme" not in h and "globex" not in h for h in rows)


def test_suppression_add_is_idempotent(store):
    store.add_suppression("a@acme.example")
    store.add_suppression("A@acme.example", reason="again")
    with store._sessions() as s:
        assert len(list(s.scalars(select(SuppressionRow)))) == 1


PG_URL = os.environ.get("LEADCOMMONS_TEST_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="set LEADCOMMONS_TEST_PG_URL to run the Postgres test")
def test_postgres_round_trip():
    store = Store(PG_URL)
    try:
        tag = uuid.uuid4().hex[:8]
        pid = f"pe_test_{tag}"
        store.save_person(make_person(pid, f"Ada {tag}", title="CTO", last_seen=T0))
        store.save_email(pid, make_email(f"ada.{tag}@acme.example", confidence=70))
        store.save_email(pid, make_email(f"ada2.{tag}@acme.example", confidence=90))
        assert store.best_email(pid).confidence == 90
        page = store.search_people(PeopleFilters(text=tag, email_statuses=[EmailStatus.VALID]))
        assert ids(page) == [pid] and page.total == 1
        assert store.get_person(pid).last_seen == T0
        list_id = store.create_list(f"test {tag}", "people")
        store.add_to_list(list_id, [pid])
        assert store.list_members(list_id) == [pid]
        store.remove_from_list(list_id, [pid])
        assert store.list_members(list_id) == []
    finally:
        store.close()
