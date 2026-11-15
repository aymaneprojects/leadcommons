"""Insights end to end: crawl snapshot, refresh + store, demo refusal, API, job kind, partials."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from leadcommons import insights
from leadcommons.config import Settings
from leadcommons.insights import boards, dnsinfo, fetch, trends
from leadcommons.models import Company, utcnow
from leadcommons.store import Store
from leadcommons.web.app import create_app
from leadcommons.web.jobs import JobRunner
from tests.fakes import FakeJobs, FakeSearch, sample_companies
from tests.insights_samples import sample_insights

FIXTURES = Path(__file__).parent / "fixtures" / "insights"
BASE = "https://nordwind.example"
HX = {"HX-Request": "true"}


def fx(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fetch.crawl, "CRAWL_DELAY", 0.0)
    monkeypatch.setattr(boards, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(trends, "MIN_INTERVAL", 0.0)
    monkeypatch.setattr(
        dnsinfo,
        "lookup",
        lambda domain, **kw: {
            "mx": ["aspmx.l.google.com."],
            "txt": ["v=spf1 include:_spf.google.com ~all", "atlassian-domain-verification=abc"],
        },
    )
    fetch.clear_cache()
    yield
    fetch.clear_cache()


@pytest.fixture
def company() -> Company:
    c = next(c for c in sample_companies() if c.domain == "nordwind.example")
    c.registry_ids = {"wikidata": "Q42042"}
    return c


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(f"sqlite:///{tmp_path / 'insights.db'}")
    yield s
    s.close()


def mock_world(router: respx.MockRouter, *, team_route: bool = True) -> dict[str, respx.Route]:
    routes = {"robots": router.get(f"{BASE}/robots.txt").respond(404)}
    routes["home"] = router.get(f"{BASE}/").respond(
        200,
        html=fx("home.html"),
        headers=[
            ("server", "cloudflare"),
            ("cf-ray", "1"),
            ("set-cookie", "_ga=GA1; Path=/"),
            ("set-cookie", "PHPSESSID=x; Path=/"),
        ],
    )
    routes["careers"] = router.get(f"{BASE}/careers").respond(200, html=fx("careers.html"))
    routes["contact"] = router.get(f"{BASE}/contact").respond(200, html=fx("contact.html"))
    routes["impressum"] = router.get(f"{BASE}/impressum").respond(
        200, html="<html><body>Impressum</body></html>"
    )
    routes["team"] = router.get(f"{BASE}/team").respond(200, html="<html>team</html>")
    router.get("https://boards-api.greenhouse.io/v1/boards/nordwind/jobs").respond(
        200, json=json.loads(fx("greenhouse.json"))
    )
    router.get("https://www.wikidata.org/wiki/Special:EntityData/Q42042.json").respond(
        200, json=json.loads(fx("wikidata_entity.json"))
    )
    router.get("https://www.wikidata.org/w/api.php").respond(
        200, json=json.loads(fx("wikidata_labels.json"))
    )
    return routes


# -- snapshot -------------------------------------------------------------------------------


@respx.mock
def test_snapshot_reads_company_pages_but_not_team_pages() -> None:
    routes = mock_world(respx.mock)
    snap = fetch.snapshot_site("nordwind.example", delay=0.0)
    urls = {p.url for p in snap.pages}
    assert urls == {f"{BASE}/", f"{BASE}/careers", f"{BASE}/contact", f"{BASE}/impressum"}
    assert not routes["team"].called
    assert snap.headers["server"] == "cloudflare"
    assert snap.cookies == ["PHPSESSID", "_ga"]
    assert "https://boards.greenhouse.io/nordwind" in snap.links
    ua = routes["home"].calls.last.request.headers["user-agent"]
    assert ua.startswith("LeadCommons/")


@respx.mock
def test_snapshot_respects_robots_txt() -> None:
    respx.get(f"{BASE}/robots.txt").respond(200, text="User-agent: *\nDisallow: /careers\n")
    respx.get(f"{BASE}/").respond(200, html=fx("home.html"))
    careers = respx.get(f"{BASE}/careers").respond(200, html=fx("careers.html"))
    respx.get(f"{BASE}/contact").respond(200, html=fx("contact.html"))
    respx.get(f"{BASE}/impressum").respond(200, html="<html></html>")
    snap = fetch.snapshot_site("nordwind.example", delay=0.0)
    assert not careers.called
    assert f"{BASE}/careers" not in {p.url for p in snap.pages}


@respx.mock
def test_snapshot_unreachable_site() -> None:
    respx.get(url__regex=r"https?://nordwind\.example/robots\.txt").respond(500)
    snap = fetch.snapshot_site("nordwind.example", delay=0.0)
    assert snap.pages == []


# -- refresh and store ----------------------------------------------------------------------


def test_get_before_refresh_is_none(store: Store) -> None:
    assert insights.get(store, "co_unknown") is None


@respx.mock
def test_refresh_stores_everything(store: Store, company: Company) -> None:
    mock_world(respx.mock)
    result = insights.refresh(store, company)
    names = {t.name: t for t in result.technologies}
    assert {"WordPress", "Google Tag Manager", "HubSpot", "Cloudflare", "Google Workspace"} <= set(
        names
    )
    assert names["Cloudflare"].evidence == "header server: cloudflare"
    assert names["Google Workspace"].source == "dns"
    assert {p.e164 for p in result.contact.phones} == {
        "+49401234567",
        "+49407654321",
        "+49401111111",
    }
    assert result.contact.address["city"] == "Hamburg"
    assert result.jobs.ats == "greenhouse" and len(result.jobs.postings) == 3
    assert result.jobs.by_department() == [("Engineering", 2), ("Sales", 1)]
    assert result.trends.employees[-1].count == 1800
    assert result.trends.listings[0].ticker == "NWD"
    assert result.pages_crawled == 4 and result.notes == []

    stored = insights.get(store, company.id)
    assert stored.to_dict() == result.to_dict()
    assert stored.refreshed_at.tzinfo is not None
    assert stored.phones == stored.contact.phones and stored.job_postings == stored.jobs.postings

    again = insights.refresh(store, company)  # replaces, never duplicates
    assert insights.get(store, company.id).refreshed_at >= stored.refreshed_at
    assert len(again.technologies) == len(stored.technologies)


@respx.mock
def test_refresh_without_domain_only_checks_wikidata(store: Store) -> None:
    router = respx.mock
    router.get("https://www.wikidata.org/wiki/Special:EntityData/Q9.json").respond(
        200, json={"entities": {"Q9": {"id": "Q9", "claims": {}}}}
    )
    router.get("https://www.wikidata.org/w/api.php").respond(200, json={"entities": {}})
    c = Company(id="co_nodomain", name="No Site Ltd", registry_ids={"wikidata": "Q9"})
    result = insights.refresh(store, c)
    assert result.technologies == [] and result.jobs.postings == []
    assert any("no known website" in n for n in result.notes)
    assert result.trends.wikidata_id == "Q9" and result.trends.employees == []
    assert insights.get(store, "co_nodomain") is not None


@respx.mock
def test_refresh_survives_unreachable_site_and_wikidata(store: Store, company: Company) -> None:
    respx.get(url__regex=r"https?://nordwind\.example/.*").mock(
        side_effect=httpx.ConnectError("down")
    )
    respx.get(url__regex=r"https://www\.wikidata\.org/.*").mock(side_effect=httpx.ConnectError("x"))
    result = insights.refresh(store, company)
    assert result.pages_crawled == 0
    assert any("could not be read" in n for n in result.notes)
    assert any("Wikidata could not be reached" in n for n in result.notes)
    assert insights.get(store, company.id) is not None


@respx.mock
def test_no_supported_board_is_noted(store: Store, company: Company) -> None:
    mock_world(respx.mock)
    respx.get(f"{BASE}/careers").respond(200, html="<html><body>Jobs by email</body></html>")
    result = insights.refresh(store, company)
    assert result.jobs.ats is None and result.jobs.postings == []
    assert any("No supported job board" in n for n in result.notes)


# -- demo mode ------------------------------------------------------------------------------


def demo_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url="sqlite://", mode="demo"
    )


def local_settings(tmp_path: Path, **kw) -> Settings:
    return Settings(
        home=tmp_path,
        data_dir=tmp_path / "packs",
        database_url="sqlite://",
        mode="local",
        password=None,
        password_hash=None,
        **kw,
    )


@respx.mock(assert_all_called=False)
def test_demo_refresh_is_refused_without_any_request(
    tmp_path: Path, store: Store, company: Company
) -> None:
    with pytest.raises(insights.RefreshDisabled):
        insights.refresh(store, company, settings=demo_settings(tmp_path))
    assert insights.get(store, company.id) is None
    assert not respx.calls


def make_client(tmp_path: Path, store: Store, settings: Settings, jobs=None) -> TestClient:
    app = create_app(settings, search=FakeSearch(), store=store, jobs=jobs or FakeJobs())
    return TestClient(app)


def test_demo_api_refresh_refused_but_cached_insights_display(
    tmp_path: Path, store: Store, company: Company
) -> None:
    from leadcommons.store.insights import InsightsRepo

    InsightsRepo(store.engine).save(sample_insights(company.id))
    jobs = FakeJobs()
    client = make_client(tmp_path, store, demo_settings(tmp_path), jobs)
    r = client.post(f"/api/companies/{company.id}/insights/refresh")
    assert r.status_code == 403 and "demo" in r.json()["detail"].lower()
    assert jobs.jobs == {}
    page = client.get(f"/partials/insights?company_id={company.id}&tab=technologies")
    assert page.status_code == 200
    assert "WordPress" in page.text and "Disabled in the public demo" in page.text
    assert "The data below was saved earlier" in page.text
    assert (
        '<button class="btn sm "' in page.text
        and "disabled" in page.text.split("<button", 1)[1].split(">", 1)[0]
    )


def test_demo_job_runner_refuses(tmp_path: Path, store: Store, company: Company) -> None:
    runner = JobRunner(store, FakeSearch(), demo_settings(tmp_path))
    jid = runner.submit(
        "company_insights", {"company_id": company.id, "domain": company.domain}, "x"
    )
    job = runner.get(jid)
    assert job.status == "failed" and "demo" in job.error.lower()
    assert insights.get(store, company.id) is None
    runner.shutdown()


# -- API and job kind -----------------------------------------------------------------------


def test_api_get_insights(tmp_path: Path, store: Store, company: Company) -> None:
    from leadcommons.store.insights import InsightsRepo

    client = make_client(tmp_path, store, local_settings(tmp_path))
    r = client.get(f"/api/companies/{company.id}/insights")
    assert r.json() == {"company_id": company.id, "refreshed": False, "insights": None}
    assert client.get("/api/companies/co_nope/insights").status_code == 404
    InsightsRepo(store.engine).save(sample_insights(company.id))
    body = client.get(f"/api/companies/{company.id}/insights").json()
    assert body["refreshed"] is True
    assert body["insights"]["technologies"][0]["name"] == "WordPress"
    assert body["insights"]["jobs"]["ats"] == "greenhouse"
    assert body["insights"]["trends"]["employees"][-1] == {"date": "2024-12-31", "count": 1800}


def test_api_refresh_queues_job(tmp_path: Path, store: Store, company: Company) -> None:
    jobs = FakeJobs()
    client = make_client(tmp_path, store, local_settings(tmp_path), jobs)
    r = client.post(f"/api/companies/{company.id}/insights/refresh")
    assert r.status_code == 200
    job = jobs.jobs[r.json()["job_id"]]
    assert job.kind == "company_insights"
    assert job.payload == {"company_id": company.id, "domain": company.domain}
    assert client.post("/api/companies/co_nope/insights/refresh").status_code == 404
    bad = client.post(
        f"/api/companies/{company.id}/insights/refresh", headers={"Origin": "https://evil.example"}
    )
    assert bad.status_code == 403 and len(jobs.jobs) == 1


def test_api_refresh_needs_a_domain(tmp_path: Path, store: Store) -> None:
    c = Company(id="co_nodom", name="No Domain")
    client = make_client(tmp_path, store, local_settings(tmp_path))
    client.app.state.search = FakeSearch([c])
    assert client.post(f"/api/companies/{c.id}/insights/refresh").status_code == 400


def test_api_refresh_from_htmx_returns_polling_panel(
    tmp_path: Path, store: Store, company: Company
) -> None:
    client = make_client(tmp_path, store, local_settings(tmp_path))
    r = client.post(f"/api/companies/{company.id}/insights/refresh?tab=jobs", headers=HX)
    assert r.status_code == 200
    assert "Refreshing now" in r.text and 'hx-trigger="load delay:2s"' in r.text
    assert "&tab=jobs&job=" in r.text


@respx.mock
def test_job_kind_company_insights_runs_refresh(
    tmp_path: Path, store: Store, company: Company
) -> None:
    mock_world(respx.mock)
    runner = JobRunner(store, FakeSearch(), local_settings(tmp_path, sync_jobs=True))
    jid = runner.submit(
        "company_insights", {"company_id": company.id, "domain": company.domain}, "x"
    )
    job = runner.get(jid)
    assert job.status == "done", job.error
    assert job.result["jobs"] == 3 and job.result["technologies"] > 10 and job.result["phones"] == 3
    assert insights.get(store, company.id).jobs.ats == "greenhouse"
    runner.shutdown()


# -- partials -------------------------------------------------------------------------------


@pytest.fixture
def seeded(tmp_path: Path, store: Store, company: Company):
    from leadcommons.store.insights import InsightsRepo

    InsightsRepo(store.engine).save(sample_insights(company.id))
    return make_client(tmp_path, store, local_settings(tmp_path)), company


def part(client: TestClient, company: Company, tab: str, **extra: str) -> str:
    q = "".join(f"&{k}={v}" for k, v in extra.items())
    r = client.get(f"/partials/insights?company_id={company.id}&tab={tab}{q}")
    assert r.status_code == 200
    return r.text


def test_partial_never_refreshed(tmp_path: Path, store: Store, company: Company) -> None:
    client = make_client(tmp_path, store, local_settings(tmp_path))
    html = part(client, company, "overview")
    assert "Never refreshed" in html and "No insights yet" in html
    assert 'class="btn sm primary"' in html
    assert f'hx-post="/api/companies/{company.id}/insights/refresh?tab=overview"' in html
    assert "<button" in html and "disabled" not in html.split("<button", 1)[1].split(">", 1)[0]


def test_partial_overview(seeded) -> None:
    client, company = seeded
    html = part(client, company, "overview")
    assert "Refreshed 3 h ago" in html and "4 pages read" in html
    assert "4 open positions" in html and "Engineering" in html
    assert "Relevant technology" in html and "WordPress" in html and "+3 more" in html
    assert "Listed on Frankfurt Stock Exchange" in html and "NWD" in html
    assert "+20.0% in 1 year" in html
    assert "+49401234567" in html
    assert "No supported job board is linked from the site." in html


def test_partial_technologies(seeded) -> None:
    client, company = seeded
    html = part(client, company, "technologies")
    for needle in (
        "Marketing automation",
        "Consent management",
        "Stripe",
        "OneTrust",
        "header server: cloudflare",
    ):
        assert needle in html
    assert re.search(r'aria-selected="true"[^>]*>Technologies<span class="n">11</span>', html)


def test_partial_jobs(seeded) -> None:
    client, company = seeded
    html = part(client, company, "jobs")
    assert "Senior Data Engineer" in html and "Hamburg, Germany" in html and "Remote</span>" in html
    assert 'href="https://boards.greenhouse.io/nordwind/jobs/2"' in html
    assert "2026-10-23" in html and "Hiring signal by department" in html
    assert 'class="ins-bars"' in html


def test_partial_jobs_escapes_titles_and_handles_empties(
    tmp_path: Path, store: Store, company: Company
) -> None:
    from leadcommons.store.insights import InsightsRepo

    ins = sample_insights(company.id)
    ins.jobs.postings[0].title = "<script>alert(1)</script>"
    InsightsRepo(store.engine).save(ins)
    client = make_client(tmp_path, store, local_settings(tmp_path))
    html = part(client, company, "jobs")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html

    ins.jobs.postings, ins.jobs.ats = [], None
    InsightsRepo(store.engine).save(ins)
    assert "No job board found" in part(client, company, "jobs")
    ins.jobs.ats, ins.jobs.error = "lever", "The lever board 'x' could not be read."
    InsightsRepo(store.engine).save(ins)
    html = part(client, company, "jobs")
    assert "Job board unreadable" in html and "could not be read" in html


def test_partial_funding(seeded) -> None:
    client, company = seeded
    html = part(client, company, "funding")
    assert "Frankfurt Stock Exchange" in html and "310M euro" in html and "250M euro" in html
    assert "Nordwind Holding" in html and "Funding rounds and investors: no public data" in html
    assert "https://www.wikidata.org/wiki/Q42042" in html


def test_partial_funding_without_data_says_so(
    tmp_path: Path, store: Store, company: Company
) -> None:
    from leadcommons.insights.model import CompanyInsights
    from leadcommons.store.insights import InsightsRepo

    InsightsRepo(store.engine).save(CompanyInsights(company_id=company.id, domain=company.domain))
    client = make_client(tmp_path, store, local_settings(tmp_path))
    assert "No public data" in part(client, company, "funding")
    assert "No matching Wikidata entry" in part(client, company, "trends")
    assert "No technologies detected" in part(client, company, "technologies")
    assert "No contact details found" in part(client, company, "contact")


def test_partial_trends(seeded) -> None:
    client, company = seeded
    html = part(client, company, "trends")
    assert "<svg" in html and "<polyline" in html and html.count("<circle") == 6
    assert "1,800" in html and "+20.0%" in html and "+50.0%" in html
    assert "2024-12-31" in html and "reported figures" in html


def test_partial_contact(seeded) -> None:
    client, company = seeded
    html = part(client, company, "contact")
    assert "+49407654321" in html and "Head office" in html
    assert (
        "contact@nordwind.example" in html and "Hafenstrasse 1" in html and "20457 Hamburg" in html
    )
    assert "linkedin.com/company/nordwind-data" in html
    assert "Personal numbers and mailboxes are never collected" in html


def test_partial_polls_while_job_runs_and_reports_failure(seeded) -> None:
    client, company = seeded
    jobs = client.app.state.jobs
    jid = jobs.submit("company_insights", {"company_id": company.id}, "x")  # status: running
    html = part(client, company, "overview", job=jid)
    assert (
        "Refreshing now" in html and f"&job={jid}" in html and 'hx-trigger="load delay:2s"' in html
    )
    assert "<button" in html and "disabled" in html.split("<button", 1)[1].split(">", 1)[0]
    jobs.jobs[jid].status, jobs.jobs[jid].error = "failed", "ValueError: boom"
    html = part(client, company, "overview", job=jid)
    assert "ValueError: boom" in html and 'hx-trigger="load delay:2s"' not in html
    jobs.jobs[jid].status = "done"
    html = part(client, company, "overview", job=jid)
    assert "boom" not in html and "Refreshing now" not in html


def test_partial_unknown_company_and_tab(seeded) -> None:
    client, company = seeded
    r = client.get("/partials/insights?company_id=co_missing")
    assert r.status_code == 404 and "Company not found" in r.text
    assert client.get("/partials/insights").status_code == 404
    html = part(client, company, "bogus")
    assert re.search(r'aria-selected="true"[^>]*>Overview', html)


def test_partial_without_domain_disables_refresh(tmp_path: Path, store: Store) -> None:
    c = Company(id="co_nodom", name="No Domain")
    client = make_client(tmp_path, store, local_settings(tmp_path))
    client.app.state.search = FakeSearch([c])
    html = part(client, c, "overview")
    assert "No known domain" in html
    assert "disabled" in html.split("<button", 1)[1].split(">", 1)[0]


def test_ago_filter() -> None:
    from leadcommons.web.pages_insights import ins_ago, ins_money

    assert ins_ago(None) == ""
    assert ins_ago(utcnow()) == "just now"
    assert ins_ago(utcnow() - timedelta(minutes=5, seconds=1)) == "5 min ago"
    assert ins_ago(utcnow().replace(tzinfo=None) - timedelta(days=2, seconds=5)) == "2 d ago"
    assert ins_money(310_000_000, "euro") == "310M euro"
    assert ins_money(1_500, None) == "1.5K" and ins_money(12, None) == "12"
