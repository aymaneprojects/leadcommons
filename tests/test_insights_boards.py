"""ATS board detection and the public job-board APIs (respx, offline)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from leadcommons.connectors._http import PoliteClient
from leadcommons.insights import boards
from leadcommons.insights.model import Jobs

FIXTURES = Path(__file__).parent / "fixtures" / "insights"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def client() -> PoliteClient:
    return PoliteClient(min_interval=0, max_retries=0, sleep=lambda s: None)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/nordwind", ("greenhouse", "nordwind")),
        ("https://job-boards.greenhouse.io/nordwind/jobs/12", ("greenhouse", "nordwind")),
        ("https://boards.greenhouse.io/embed/job_board?for=acme", ("greenhouse", "acme")),
        ("https://boards.greenhouse.io/embed/job_board/js?for=acme", ("greenhouse", "acme")),
        ("https://jobs.lever.co/acme/abc-123", ("lever", "acme")),
        ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
        ("https://apply.workable.com/acme/", ("workable", "acme")),
        ("https://acme.recruitee.com/", ("recruitee", "acme")),
        ("https://careers.smartrecruiters.com/AcmeCorp", ("smartrecruiters", "AcmeCorp")),
        ("https://www.recruitee.com/pricing", None),
        ("https://boards.greenhouse.io/embed/job_board", None),
        ("https://example.com/careers", None),
    ],
)
def test_board_from_url(url: str, expected) -> None:
    b = boards.board_from_url(url)
    assert (b.ats, b.token) == expected if expected else b is None


def test_find_boards_dedupes_in_order() -> None:
    links = [
        "https://example.com/x",
        "https://boards.greenhouse.io/Acme",
        "https://boards.greenhouse.io/acme/jobs/1",
        "https://jobs.lever.co/other",
    ]
    assert [(b.ats, b.token) for b in boards.find_boards(links)] == [
        ("greenhouse", "Acme"),
        ("lever", "other"),
    ]


@respx.mock
def test_greenhouse() -> None:
    route = respx.get("https://boards-api.greenhouse.io/v1/boards/nordwind/jobs").respond(
        200, json=load("greenhouse.json")
    )
    jobs = boards.collect_jobs(["https://boards.greenhouse.io/nordwind"], client())
    assert route.calls.last.request.url.params["content"] == "true"
    assert jobs.ats == "greenhouse" and jobs.board == "nordwind" and jobs.error is None
    first, second, third = jobs.postings
    assert (first.title, first.department, first.location) == (
        "Senior Data Engineer", "Engineering", "Hamburg, Germany",
    )
    assert first.url == "https://boards.greenhouse.io/nordwind/jobs/1"
    assert first.posted_at == "2026-10-01T09:00:00-04:00" and first.remote is None
    assert second.remote is True
    assert third.department == "Sales" and third.source == "greenhouse"
    assert jobs.by_department() == [("Engineering", 2), ("Sales", 1)]


@respx.mock
def test_lever_and_eu_region() -> None:
    respx.get("https://api.eu.lever.co/v0/postings/acme").respond(200, json=load("lever.json"))
    jobs = boards.collect_jobs(["https://jobs.eu.lever.co/acme"], client())
    a, b = jobs.postings
    assert (a.title, a.department, a.location, a.remote) == (
        "Product Designer",
        "Product",
        "Paris",
        True,
    )
    assert a.posted_at == "2025-10-09" and a.url == "https://jobs.lever.co/acme/a1"
    assert b.department == "Support" and b.remote is False


@respx.mock
def test_ashby_skips_unlisted() -> None:
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").respond(
        200, json=load("ashby.json")
    )
    jobs = boards.collect_jobs(["https://jobs.ashbyhq.com/acme"], client())
    assert [p.title for p in jobs.postings] == ["Machine Learning Engineer", "Head of Growth"]
    assert jobs.postings[1].remote is True and jobs.postings[0].remote is False
    assert jobs.postings[0].posted_at.startswith("2026-09-30")


@respx.mock
def test_workable() -> None:
    respx.get("https://apply.workable.com/api/v1/widget/accounts/acme").respond(
        200, json=load("workable.json")
    )
    jobs = boards.collect_jobs(["https://apply.workable.com/acme/"], client())
    a, b = jobs.postings
    assert (a.location, a.department, a.posted_at, a.remote) == (
        "Madrid, Spain", "Customer Success", "2026-10-10", False,
    )
    assert b.location == "Portugal" and b.remote is True


@respx.mock
def test_recruitee_skips_closed() -> None:
    respx.get("https://acme.recruitee.com/api/offers/").respond(200, json=load("recruitee.json"))
    jobs = boards.collect_jobs(["https://acme.recruitee.com/"], client())
    assert [p.title for p in jobs.postings] == [
        "Sales Development Representative",
        "Frontend Developer",
    ]
    assert jobs.postings[1].remote is True and jobs.postings[1].posted_at.startswith("2026-10-03")


@respx.mock
def test_smartrecruiters() -> None:
    route = respx.get("https://api.smartrecruiters.com/v1/companies/AcmeCorp/postings").respond(
        200, json=load("smartrecruiters.json")
    )
    jobs = boards.collect_jobs(["https://careers.smartrecruiters.com/AcmeCorp"], client())
    assert route.called
    a, b = jobs.postings
    assert a.url == "https://jobs.smartrecruiters.com/AcmeCorp/743999"
    assert (a.department, a.location, a.remote) == ("Finance", "Zurich, Switzerland", False)
    assert b.remote is True and b.location == "Geneva, ch"


@respx.mock
def test_unreadable_board_reports_error_and_tries_next() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/gone/jobs").respond(404)
    respx.get("https://api.lever.co/v0/postings/acme").respond(200, json=load("lever.json"))
    jobs = boards.collect_jobs(
        ["https://boards.greenhouse.io/gone", "https://jobs.lever.co/acme"], client()
    )
    assert jobs.ats == "lever" and len(jobs.postings) == 2 and jobs.error is None

    only = boards.collect_jobs(["https://boards.greenhouse.io/gone"], client())
    assert only.ats == "greenhouse" and only.postings == []
    assert "could not be read" in only.error


@respx.mock
def test_network_error_is_reported() -> None:
    respx.get("https://boards-api.greenhouse.io/v1/boards/x/jobs").mock(
        side_effect=httpx.ConnectError("no")
    )
    jobs = boards.collect_jobs(["https://boards.greenhouse.io/x"], client())
    assert jobs.error and jobs.postings == []


def test_no_board_means_no_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        jobs = boards.collect_jobs(["https://example.com/careers"], client())
    assert jobs == Jobs() and not router.calls
