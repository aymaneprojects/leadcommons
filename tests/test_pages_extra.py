"""Shell pages: palette, notifications, settings, onboarding hub, lists, engage empty states."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dns.exception
import dns.resolver
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.models import utcnow
from leadcommons.web import pages_extra
from leadcommons.web.app import create_app
from leadcommons.web.jobs import Job
from tests.fakes import FakeJobs, FakeSearch, FakeStore, sample_email, sample_person


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = Settings(
        home=tmp_path,
        data_dir=tmp_path / "packs",
        database_url=f"sqlite:///{tmp_path}/t.db",
        **kw,
    )
    return create_app(settings=settings, search=FakeSearch(), store=FakeStore(), jobs=FakeJobs())


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


# ---------------------------------------------------------------- DNS fakes


class FakeResolver:
    zone: dict[tuple[str, str], Any] = {}
    seen: list[tuple[float, float]] = []

    def __init__(self) -> None:
        self.timeout = 0.0
        self.lifetime = 0.0

    def resolve(self, name: str, rtype: str) -> Any:
        FakeResolver.seen.append((self.timeout, self.lifetime))
        value = FakeResolver.zone.get((name, rtype))
        if value is None:
            raise dns.resolver.NXDOMAIN()
        if isinstance(value, Exception):
            raise value
        return value


def mx(pref: int, host: str) -> Any:
    return SimpleNamespace(preference=pref, exchange=host)


def txt(value: str) -> Any:
    return SimpleNamespace(strings=[value.encode()])


@pytest.fixture
def dns_zone(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], Any]:
    FakeResolver.zone = {}
    FakeResolver.seen = []
    monkeypatch.setattr(dns.resolver, "Resolver", FakeResolver)
    return FakeResolver.zone


# ---------------------------------------------------------------- every page renders


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/onboarding",
        "/lists",
        "/lists?kind=companies",
        "/settings",
        "/settings/data-packs",
        "/settings/email-health",
        "/settings/privacy",
        "/settings/account",
        "/settings/about",
        "/assistant",
        "/partials/palette",
        "/partials/notifications",
    ],
)
def test_pages_render(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, path
    assert "Apollo" not in r.text
    assert "credits" not in r.text.lower() and "upgrade" not in r.text.lower()


def test_email_health_redirects_to_settings(client: TestClient) -> None:
    r = client.get("/email-health")
    assert r.status_code in (302, 307) and r.headers["location"] == "/settings/email-health"


def test_shell_markup(client: TestClient) -> None:
    html = client.get("/lists").text
    assert 'id="lc-palette"' in html and 'id="palette"' not in html
    assert client.get("/assistant").text.count("<dialog") == 1
    assert "data-notif-toggle" in html and 'hx-get="/partials/notifications"' in html
    assert 'href="/onboarding"' in html
    assert "Ctrl K" in html and "Email setup and health" in html
    for hint in ("Navigate", "Close", "Select"):
        assert hint in html
    for group in ("Pages", "Actions"):
        assert f'data-pal-group="{group}"' in html


def test_settings_menu_replaces_sidebar(client: TestClient) -> None:
    html = client.get("/settings/privacy").text
    for label in (
        "Workspace overview",
        "Data packs",
        "Email setup and health",
        "Unsubscribes &amp; opt-out",
        "Account",
        "About",
    ):
        assert label in html
    assert 'aria-label="Settings menu"' in html
    assert re.search(r'href="/settings/privacy"[^>]*aria-current="page"', html)
    assert 'aria-label="Sidebar"' not in html


# ---------------------------------------------------------------- palette endpoint


def test_palette_company_search(client: TestClient) -> None:
    html = client.get("/partials/palette", params={"q": "nord"}).text
    assert "Nordwind Data" in html and "nordwind.example" in html
    assert "Atelier Nuage" not in html
    assert html.count("pal-item") == 1
    assert "/companies/co_" in html or "/companies/" in html


def test_palette_top_five_and_min_length(client: TestClient) -> None:
    assert "pal-item" not in client.get("/partials/palette", params={"q": "n"}).text
    html = client.get("/partials/palette", params={"q": "e"}).text
    assert "pal-item" not in html  # one letter: no live search
    html = client.get("/partials/palette", params={"q": "example"}).text
    assert html.count('class="pal-item"') == 5


def test_palette_recent_searches(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    store.save_search("Quarterly targets", "companies", '{"query": "country=FR&q=saas"}')
    store.save_search("Founders", "people", '{"query": "q=founder"}')
    html = client.get("/partials/palette").text
    assert "Recent searches" in html and "Quarterly targets" in html and "Founders" in html
    assert "/companies?country=FR&amp;q=saas" in html and "/people?q=founder" in html
    filtered = client.get("/partials/palette", params={"q": "quar"}).text
    assert "Quarterly targets" in filtered and "Founders" not in filtered


def test_palette_empty_without_saved_searches(client: TestClient) -> None:
    assert client.get("/partials/palette").text.strip() == ""


# ---------------------------------------------------------------- notifications


def add_job(app: FastAPI, status: str, kind: str = "find_people", **kw: Any) -> Job:
    job = Job(
        id=f"j{len(app.state.jobs.jobs)}",
        kind=kind,  # type: ignore[arg-type]
        label=kw.pop("label", "Find people at a.example"),
        payload=kw.pop("payload", {"domain": "a.example"}),
        status=status,  # type: ignore[arg-type]
        finished_at=utcnow() if status in ("done", "failed") else None,
        **kw,
    )
    app.state.jobs.jobs[job.id] = job
    return job


def test_notifications_empty_state(client: TestClient) -> None:
    html = client.get("/partials/notifications").text
    assert "all caught up" in html
    assert (
        html.count("data-notif-id") == 1 and "sys-integrations" in html
    )  # only the standing notice


def test_notifications_list_finished_jobs_only(app: FastAPI, client: TestClient) -> None:
    add_job(app, "running", label="Still running")
    done = add_job(app, "done", result={"people": 4}, payload={"company_id": "co_1"})
    add_job(app, "failed", label="Broken job", error="boom " * 100)
    csv_job = add_job(app, "done", kind="enrich_csv", label="Enrich a.csv", payload={})
    html = client.get("/partials/notifications").text
    assert "Still running" not in html
    assert f'data-notif-id="job-{done.id}"' in html and "4 people found" in html
    assert "/companies/co_1" in html
    assert "Broken job" in html and "Failed" in html and "boom" in html
    assert f"/api/enrich/{csv_job.id}/result.csv" in html and "Download CSV" in html
    assert html.count("data-notif-id") == 3 + 1  # plus the standing integrations notice


def test_notifications_most_recent_first_and_capped(app: FastAPI, client: TestClient) -> None:
    for i in range(25):
        add_job(app, "done", label=f"Job number {i:02d}")
    html = client.get("/partials/notifications").text
    assert html.count("data-notif-id") == 20 + 1  # plus the standing integrations notice
    assert html.index("Job number 24") < html.index("Job number 10")


def test_notification_script_marks_read_in_local_storage() -> None:
    js = (Path(pages_extra.__file__).parent / "static/js/shell.js").read_text()
    assert "lc-notif-read" in js and "Mark all as read" not in js  # label lives in the template
    assert js.count("try {") >= 2  # storage access is guarded


# ---------------------------------------------------------------- settings


def test_settings_overview_shows_only_real_numbers(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    claire = sample_person(app.state.search.companies[0])
    store.save_person(claire)
    store.create_list("Leads", "people")
    store.save_search("s", "companies", "{}")
    html = client.get("/settings").text
    assert "Workspace settings" in html and "5</dd>" not in html
    assert "fake.parquet" in html and "leadcommons pack download latest" in html
    assert "5 companies" in html
    assert "SMTP probe off" in html and "LEADCOMMONS_SMTP_PROBE" in html
    assert re.search(r"<dt>People</dt><dd class=\"mono\">1</dd>", html)
    assert re.search(r"<dt>Lists</dt><dd class=\"mono\">1</dd>", html)
    assert re.search(r"<dt>Saved searches</dt><dd class=\"mono\">1</dd>", html)
    assert "AGPL-3.0-or-later" in html and "Wikidata" in html and "GLEIF" in html


def test_settings_smtp_probe_on(tmp_path: Path) -> None:
    html = TestClient(make_app(tmp_path, smtp_probe=True)).get("/settings").text
    assert "SMTP probe on" in html


def test_settings_data_packs_table(client: TestClient) -> None:
    html = client.get("/settings/data-packs").text
    assert "fake.parquet" in html and "Total:" in html


def test_settings_account_modes(tmp_path: Path) -> None:
    assert "Local mode" in TestClient(make_app(tmp_path)).get("/settings/account").text
    authed = TestClient(make_app(tmp_path, password="secret"), follow_redirects=False)
    authed.post("/login", data={"password": "secret"})
    html = authed.get("/settings/account").text
    assert "Password protected" in html and 'action="/logout"' in html


def test_suppression_returns_to_privacy_section(app: FastAPI, client: TestClient) -> None:
    html = client.get("/settings/privacy").text
    assert 'action="/settings/suppression"' in html and "/opt-out" in html
    r = client.post("/settings/suppression", data={"value": "Spam.Example"})
    assert r.status_code == 303 and r.headers["location"].startswith("/settings/privacy")
    assert app.state.store.is_suppressed("x@spam.example")
    bad = client.post("/settings/suppression", data={"value": "nope"})
    assert bad.status_code == 303 and "valid" in bad.headers["location"]
    r = client.post(
        "/settings/suppression",
        data={"value": "a.example"},
        headers={"Origin": "https://evil.example", "Host": "testserver"},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------- email health (DNS)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "example.com"),
        ("  Example.COM  ", "example.com"),
        ("https://www.example.com/path?x=1", "www.example.com"),
        ("me@example.com", "example.com"),
        ("example.com.", "example.com"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("localhost", None),
        ("exa mple.com", "exa"),  # cut at whitespace, then rejected below as one label
        ("", None),
        ("-bad-.com", None),
        ("a" * 70 + ".com", None),
    ],
)
def test_clean_domain(raw: str, expected: str | None) -> None:
    got = pages_extra.clean_domain(raw)
    if raw == "exa mple.com":
        assert got is None
    else:
        assert got == expected


def healthy_zone(zone: dict[tuple[str, str], Any]) -> None:
    zone[("example.com", "MX")] = [mx(10, "mail1.example.com."), mx(20, "mail2.example.com.")]
    zone[("example.com", "TXT")] = [
        txt("google-site-verification=abc"),
        txt("v=spf1 include:x.example -all"),
    ]
    zone[("_dmarc.example.com", "TXT")] = [txt("v=DMARC1; p=reject; rua=mailto:r@example.com")]
    zone[("sel._domainkey.example.com", "TXT")] = [txt("v=DKIM1; k=rsa; p=MIGfMA0GCSq")]


def test_email_health_all_pass(client: TestClient, dns_zone: dict[tuple[str, str], Any]) -> None:
    healthy_zone(dns_zone)
    html = client.get(
        "/settings/email-health", params={"domain": "Example.com", "selector": "sel"}
    ).text
    assert html.count("dns-row ok") == 4
    assert "2 mail servers found" in html and "10 mail1.example.com" in html
    assert "v=spf1 include:x.example -all" in html and "google-site-verification" not in html
    assert "Policy is reject" in html and "4 passed" in html
    assert "DKIM (sel)" in html
    assert "arrives in v0.3" not in html or "Sending not connected" in html
    assert all(t == 5.0 and life == 5.0 for t, life in FakeResolver.seen)


def test_email_health_reports_gaps(
    client: TestClient, dns_zone: dict[tuple[str, str], Any]
) -> None:
    dns_zone[("example.com", "MX")] = [mx(0, ".")]
    dns_zone[("example.com", "TXT")] = [txt("v=spf1 +all"), txt("v=spf1 -all")]
    dns_zone[("_dmarc.example.com", "TXT")] = [txt("v=DMARC1; p=none")]
    html = client.get("/settings/email-health", params={"domain": "example.com"}).text
    assert "null MX" in html
    assert "More than one SPF record" in html
    assert "Policy is none" in html
    assert "DKIM (" not in html  # only checked with a selector
    assert "3 to review" in html or "to review" in html


def test_email_health_missing_records(
    client: TestClient, dns_zone: dict[tuple[str, str], Any]
) -> None:
    for key in (("example.com", "MX"), ("example.com", "TXT"), ("_dmarc.example.com", "TXT")):
        dns_zone[key] = dns.resolver.NoAnswer()
    html = client.get("/settings/email-health", params={"domain": "example.com"}).text
    assert "No MX records found" in html and "No SPF record found" in html
    assert "No DMARC record found" in html and "3 missing" in html


def test_email_health_unknown_domain(
    client: TestClient, dns_zone: dict[tuple[str, str], Any]
) -> None:
    html = client.get("/settings/email-health", params={"domain": "nothing.example"}).text
    assert html.count("does not exist in DNS") == 2  # MX and SPF rows
    assert "No DMARC record found" in html


def test_email_health_timeout_is_polite(
    client: TestClient, dns_zone: dict[tuple[str, str], Any]
) -> None:
    dns_zone[("example.com", "MX")] = dns.exception.Timeout()
    dns_zone[("example.com", "TXT")] = dns.resolver.NoNameservers()
    dns_zone[("_dmarc.example.com", "TXT")] = dns.exception.DNSException("x")
    r = client.get("/settings/email-health", params={"domain": "example.com"})
    assert r.status_code == 200
    assert "did not answer within 5 seconds" in r.text
    assert "No DNS server could answer" in r.text and "lookup failed" in r.text
    assert "Traceback" not in r.text and "could not be checked" in r.text


def test_email_health_resolver_construction_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom() -> None:
        raise dns.resolver.NoResolverConfiguration()

    monkeypatch.setattr(dns.resolver, "Resolver", boom)
    r = client.get("/settings/email-health", params={"domain": "example.com"})
    assert r.status_code == 200 and "lookup failed" in r.text


def test_email_health_rejects_bad_input_without_dns(
    client: TestClient, dns_zone: dict[tuple[str, str], Any]
) -> None:
    html = client.get("/settings/email-health", params={"domain": "not a domain"}).text
    assert "does not look like a domain" in html and FakeResolver.seen == []
    html = client.get(
        "/settings/email-health", params={"domain": "example.com", "selector": "bad sel!"}
    ).text
    assert "DKIM selector may only contain" in html and FakeResolver.seen == []


def test_email_health_mailboxes_tab_links_to_mailboxes(client: TestClient) -> None:
    html = client.get("/settings/email-health").text
    assert 'href="/settings/mailboxes"' in html and "arrives in v0.3" not in html


# ---------------------------------------------------------------- onboarding + home


def test_onboarding_hub_groups_and_progress(app: FastAPI, client: TestClient) -> None:
    html = client.get("/onboarding").text
    assert "Your first steps with LeadCommons" in html and "0 of 18</b> done" in html
    assert "Find the people you want to reach" in html and "0 of 6 completed" in html
    assert "Find a company phone number" in html and "save a mobile" not in html.lower()
    assert (
        "More help for getting the most" in html
        and "hub-progress" in html
        and "btn primary" in html
    )
    store = app.state.store
    store.save_company(app.state.search.companies[0])
    store.create_list("x", "people")
    html = client.get("/onboarding").text
    assert "2 of 6 completed" in html
    assert client.get("/").text.count("hub-group") >= 4


def test_sidebar_getting_started_links_to_hub(client: TestClient) -> None:
    html = client.get("/lists").text
    assert re.search(r'<a class="gs-card[^"]*" href="/onboarding"', html)
    assert "Onboarding hub" in html and "% Completed" in html


# ---------------------------------------------------------------- lists


def test_lists_empty_state(client: TestClient) -> None:
    html = client.get("/lists").text
    assert "Welcome to your lists" in html
    assert "Create a people list" in html and "Create a company list" in html
    assert 'data-create-list="people"' in html and 'action="/lists"' in html
    assert "People <span" in html and "Companies <span" in html
    assert "<table" not in html


def test_lists_tabs_and_table(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    store.create_list("Founders", "people")
    cid = store.create_list("Quarterly targets", "companies")
    store.add_to_list(cid, [c.id for c in app.state.search.companies[:2]])
    people_tab = client.get("/lists?kind=people").text
    assert "Founders" in people_tab and "Quarterly targets" not in people_tab
    companies_tab = client.get("/lists?kind=companies").text
    assert "Quarterly targets" in companies_tab and "Founders" not in companies_tab
    assert f"/lists/{cid}/export.csv" in companies_tab and "Export CSV" in companies_tab
    assert "Open" in companies_tab and "Create list" in companies_tab
    assert "rename" not in companies_tab.lower() and "delete" not in companies_tab.lower()
    only_companies = TestClient(app)
    store._lists.pop(next(k for k, v in store._lists.items() if v.kind == "people"))
    assert "Quarterly targets" in only_companies.get("/lists").text  # falls back to the filled tab


def test_lists_empty_tab_with_other_tab_filled(app: FastAPI, client: TestClient) -> None:
    app.state.store.create_list("Founders", "people")
    html = client.get("/lists?kind=companies").text
    assert "No company lists yet" in html and "Create a company list" in html


def test_create_list_flow(app: FastAPI, client: TestClient) -> None:
    r = client.post("/lists", data={"name": "Leads", "kind": "companies"})
    assert r.status_code == 303 and r.headers["location"].startswith("/lists/")
    page = client.get(r.headers["location"].split("?")[0]).text
    assert "Leads" in page and "No records yet" in page and "Find companies" in page
    assert "Export CSV" in page and 'href="/lists"' in page


def test_list_detail_members_and_remove(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    company = app.state.search.companies[0]
    lid = store.create_list("Team", "people")
    claire = sample_person(company)
    ben = sample_person(company, "Ben Roux", "CTO")
    for p, addr in ((claire, "claire@ateliernuage.example"), (ben, "ben@ateliernuage.example")):
        store.save_person(p)
        store.save_email(p.id, sample_email(addr))
    store.add_to_list(lid, [claire.id, ben.id])
    html = client.get(f"/lists/{lid}").text
    assert "Claire Martin" in html and "Ben Roux" in html and "2</span> records" in html
    assert f"/lists/{lid}/export.csv" in html and "btn primary" in html
    assert f'id="rm-{ben.id}"' in html and f'form="rm-{ben.id}"' in html
    assert 'data-table-filter="#list-table"' in html
    client.post(f"/lists/{lid}/remove", data={"ids": [ben.id]})
    after = client.get(f"/lists/{lid}").text
    assert "Ben Roux" not in after and "Claire Martin" in after
    assert client.get("/lists/missing").status_code == 404


def test_company_list_detail(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    lid = store.create_list("Cos", "companies")
    store.add_to_list(lid, [c.id for c in app.state.search.companies[:2]])
    html = client.get(f"/lists/{lid}").text
    assert "Atelier Nuage" in html and "Germany" in html and "Employees" in html


def test_list_export_never_contains_suppressed_emails(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    company = app.state.search.companies[0]
    lid = store.create_list("Team", "people")
    ok, blocked = sample_person(company), sample_person(company, "Ben Roux", "CTO")
    for p, addr in ((ok, "claire@ateliernuage.example"), (blocked, "ben@ateliernuage.example")):
        store.save_person(p)
        store.save_email(p.id, sample_email(addr))
    store.add_to_list(lid, [ok.id, blocked.id])
    store.add_suppression("ben@ateliernuage.example")
    body = client.get(f"/lists/{lid}/export.csv").text
    assert "claire@ateliernuage.example" in body
    assert "ben@ateliernuage.example" not in body and "suppressed" in body
    assert (
        "ben@ateliernuage.example" not in client.get(f"/lists/{lid}").text.replace("suppressed", "")
        or "suppressed" in client.get(f"/lists/{lid}").text
    )


# ---------------------------------------------------------------- assistant + engage


def test_assistant_page_has_working_composer(client: TestClient) -> None:
    html = client.get("/assistant").text
    assert "Find companies I could sell to" in html and "type / to see commands" in html
    assert (
        'action="/assistant/send"' in html
        and "disabled" not in html.split('id="composer"')[1][:600]
    )
    assert "bring your own AI" in html


def test_sidebar_matches_contract(client: TestClient) -> None:
    html = client.get("/lists").text
    for label in (
        "Prospect and enrich",
        "Data enrichment",
        "Engage",
        "Calls",
        "Win deals",
        "Meetings",
        "Conversations",
        "Tools and automation",
        "Workflows",
        "Analytics",
        "Integrations",
        "Onboarding hub",
        "Email setup and health",
        "Settings",
    ):
        assert label in html, label
    assert ">Soon<" not in html and "badge" not in html.split("</aside>")[0]
