"""HTML page tests. The app is assembled by hand (no create_app) so other modules may be stubs."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.models import EmailStatus
from leadcommons.web.deps import STATIC_DIR
from leadcommons.web.pages import router
from tests.fakes import FakeJobs, FakeSearch, FakeStore, sample_email, sample_person

HX = {"HX-Request": "true"}


def make_app(tmp_path: Path, **settings_kw: object) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url="sqlite://", **settings_kw
    )
    app.state.search = FakeSearch()
    app.state.store = FakeStore()
    app.state.jobs = FakeJobs()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)
    return app


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def seeded(app: FastAPI) -> FastAPI:
    search, store = app.state.search, app.state.store
    atelier, nordwind = search.companies[0], search.companies[1]
    claire = sample_person(atelier, "Claire Martin", "CEO")
    jonas = sample_person(nordwind, "Jonas Weber", "Head of Sales")
    for p in (claire, jonas):
        store.save_person(p)
    store.save_email(claire.id, sample_email("claire.martin@ateliernuage.example"))
    store.save_email(jonas.id, sample_email("jonas@nordwind.example", EmailStatus.CATCH_ALL, 55))
    return app


def ids(app: FastAPI) -> dict[str, str]:
    return {c.name: c.id for c in app.state.search.companies}


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/companies",
        "/people",
        "/lists",
        "/enrich",
        "/settings",
        "/assistant",
        "/opt-out",
        "/sequences",
        "/emails",
        "/tasks",
        "/deals",
        "/email-health",
    ],
)
def test_pages_render(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "<title>" in r.text


def test_shell_navigation(client: TestClient) -> None:
    html = client.get("/companies").text
    assert 'aria-current="page"' in html
    assert "LeadCommons" in html and "Ctrl K" in html
    assert "Apollo" not in html
    for label in ("Prospect", "Engage", "Pipeline", "Getting started", "Email health"):
        assert label in html
    assert 'hx-get="/partials/jobs-pill"' in html


def test_logout_only_with_auth(tmp_path: Path) -> None:
    assert "/logout" not in TestClient(make_app(tmp_path)).get("/").text
    authed = TestClient(make_app(tmp_path, password="secret"))
    assert 'action="/logout"' in authed.get("/").text


def test_login_template_renders(app: FastAPI) -> None:
    from leadcommons.web.deps import templates

    @app.get("/login-test")
    def _login(request: Request):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"request": request, "error": "Incorrect password.", "next": "/x"},
        )

    html = TestClient(app).get("/login-test").text
    assert "Incorrect password." in html and 'name="next" value="/x"' in html


def test_companies_lists_all_and_filters(client: TestClient) -> None:
    html = client.get("/companies").text
    assert "Atelier Nuage" in html and "Kestrel Systems" in html
    assert "Top countries" in html
    html = client.get("/companies?country=DE").text
    assert "Nordwind Data" in html and "Atelier Nuage" not in html
    assert "Top countries" not in html
    html = client.get("/companies?size=1-10&country=FR").text
    assert "Moss and Pine" in html and "Atelier Nuage" not in html
    html = client.get("/companies?q=kestrel").text
    assert "Kestrel Systems" in html and "Nordwind" not in html
    html = client.get("/companies?industry=C").text
    assert "Kestrel Systems" in html and "Moss and Pine" not in html
    assert "No companies match" in client.get("/companies?q=zzzz").text


def test_companies_sort_and_tabs_and_pagination(app: FastAPI, client: TestClient) -> None:
    html = client.get("/companies?sort=name").text
    assert html.index("Atelier Nuage") < html.index("Brücke Software") < html.index("Nordwind Data")
    cid = ids(app)["Kestrel Systems"]
    app.state.store.save_company(app.state.search.get_company(cid))
    saved = client.get("/companies?tab=saved").text
    assert "Kestrel Systems" in saved and "Atelier Nuage" not in saved
    new = client.get("/companies?tab=new").text
    assert "Kestrel Systems" not in new and "Atelier Nuage" in new
    assert "?page=2" not in client.get("/companies").text  # 5 rows, one page


def test_companies_facets_in_panel(client: TestClient) -> None:
    html = client.get("/companies").text
    assert "Germany" in html and "France" in html
    assert "Information and communication" in html
    assert 'name="size" value="11-50"' in html


def test_company_drawer_partial_vs_full(app: FastAPI, client: TestClient) -> None:
    cid = ids(app)["Atelier Nuage"]
    partial = client.get(f"/companies/{cid}", headers=HX)
    assert partial.status_code == 200
    assert "<html" not in partial.text and "Atelier Nuage" in partial.text
    assert "fake · " in partial.text  # source chip
    full = client.get(f"/companies/{cid}")
    assert "<html" in full.text and "Atelier Nuage" in full.text and "Find people" in full.text
    assert client.get("/companies/co_missing").status_code == 404


def test_company_drawer_lists_people_with_email(seeded: FastAPI) -> None:
    client = TestClient(seeded)
    cid = ids(seeded)["Atelier Nuage"]
    html = client.get(f"/companies/{cid}", headers=HX).text
    assert "Claire Martin" in html and "claire.martin@ateliernuage.example" in html
    assert "Jonas Weber" not in html


def test_find_people_submits_job(app: FastAPI, client: TestClient) -> None:
    cid = ids(app)["Atelier Nuage"]
    r = client.post(f"/companies/{cid}/find-people")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/companies/{cid}")
    (job,) = app.state.jobs.jobs.values()
    assert job.kind == "find_people"
    assert job.payload == {"domain": "ateliernuage.example", "company_id": cid}
    assert "ateliernuage.example" in job.label
    r = client.post(f"/companies/{cid}/find-people", headers=HX)
    assert r.status_code == 200 and "toast" in json.loads(r.headers["hx-trigger"])
    assert len(app.state.jobs.jobs) == 2


def test_save_company(app: FastAPI, client: TestClient) -> None:
    cid = ids(app)["Moss and Pine"]
    r = client.post(f"/companies/{cid}/save", headers=HX)
    assert r.status_code == 200 and "Saved" in r.text
    assert cid in app.state.store.saved_company_ids()


def test_bulk_save_to_list(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    c = ids(app)
    r = client.post(
        "/lists/save-to",
        data={
            "kind": "companies",
            "ids": [c["Atelier Nuage"], c["Nordwind Data"]],
            "new_name": "Targets",
            "next": "/companies",
        },
    )
    assert r.status_code == 303
    (info,) = store.lists("companies")
    assert info.name == "Targets" and info.size == 2
    assert c["Atelier Nuage"] in store.saved_company_ids()
    r = client.post(
        "/lists/save-to",
        data={"kind": "companies", "ids": [c["Kestrel Systems"]], "list_id": info.id},
        headers=HX,
    )
    assert r.status_code == 200
    assert json.loads(r.headers["hx-trigger"])["toast"]["kind"] == "ok"
    assert store.lists("companies")[0].size == 3
    r = client.post(
        "/lists/save-to", data={"kind": "companies", "ids": [c["Atelier Nuage"]]}, headers=HX
    )
    assert json.loads(r.headers["hx-trigger"])["toast"]["kind"] == "error"


def test_bulk_find_people_skips_missing_domain(app: FastAPI, client: TestClient) -> None:
    app.state.search.companies[3].domain = None
    c = ids(app)
    r = client.post(
        "/bulk/find-people", data={"ids": [c["Atelier Nuage"], c["Moss and Pine"]]}, headers=HX
    )
    assert r.status_code == 200
    assert len(app.state.jobs.jobs) == 1
    assert "1 skipped" in json.loads(r.headers["hx-trigger"])["toast"]["message"]


def test_people_page_empty_and_filters(client: TestClient, seeded: FastAPI) -> None:
    empty = TestClient(make_app(Path("/tmp"))).get("/people").text
    assert "No people yet" in empty and "Companies" in empty
    html = client.get("/people").text
    assert "Claire Martin" in html and "claire.martin@ateliernuage.example" in html
    assert "Jonas Weber" in html
    html = TestClient(seeded).get("/people?q=sales").text
    assert "Jonas Weber" in html and "Claire Martin" not in html
    html = TestClient(seeded).get("/people?status=valid").text
    assert "Claire Martin" in html and "Jonas Weber" not in html
    html = TestClient(seeded).get("/people?status=bogus&seniority=nope").text
    assert "Claire Martin" in html  # invalid values are ignored


def test_people_saved_tab(seeded: FastAPI) -> None:
    client = TestClient(seeded)
    store = seeded.state.store
    pid = next(p.id for p in store.people.values() if p.full_name == "Claire Martin")
    lid = store.create_list("Hot", "people")
    store.add_to_list(lid, [pid])
    html = client.get("/people?tab=saved").text
    assert "Claire Martin" in html and "Jonas Weber" not in html
    assert "Jonas Weber" in client.get("/people?tab=new").text


def test_person_drawer_and_find_email(seeded: FastAPI) -> None:
    client = TestClient(seeded, follow_redirects=False)
    pid = next(iter(seeded.state.store.people))
    partial = client.get(f"/people/{pid}", headers=HX)
    assert "<html" not in partial.text and "Find email" in partial.text
    assert "<html" in client.get(f"/people/{pid}").text
    r = client.post(f"/people/{pid}/find-email", headers=HX)
    assert r.status_code == 200
    (job,) = seeded.state.jobs.jobs.values()
    assert job.kind == "find_email" and job.payload == {"person_id": pid}
    assert client.get("/people/pe_nope").status_code == 404


def test_bulk_find_emails(seeded: FastAPI) -> None:
    client = TestClient(seeded)
    pids = list(seeded.state.store.people)
    r = client.post("/bulk/find-emails", data={"ids": pids}, headers=HX)
    assert r.status_code == 200 and len(seeded.state.jobs.jobs) == 2


def test_lists_crud_and_export(seeded: FastAPI) -> None:
    client = TestClient(seeded, follow_redirects=False)
    store = seeded.state.store
    r = client.post("/lists", data={"name": "Leads", "kind": "people"})
    assert r.status_code == 303
    lid = store.lists("people")[0].id
    assert r.headers["location"].startswith(f"/lists/{lid}")
    pids = list(store.people)
    client.post(f"/lists/{lid}/add", data={"record_ids": pids})
    assert store.list_members(lid) == pids
    assert "Leads" in client.get("/lists").text
    html = client.get(f"/lists/{lid}").text
    assert "Claire Martin" in html and "Jonas Weber" in html
    client.post(f"/lists/{lid}/remove", data={"ids": [pids[1]]})
    assert store.list_members(lid) == [pids[0]]
    assert (
        client.post("/lists", data={"name": " ", "kind": "people"}, headers=HX).status_code == 200
    )


def rows_of(resp) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    return list(csv.DictReader(io.StringIO(resp.text)))


def test_export_csv_people_excludes_suppressed(seeded: FastAPI) -> None:
    client = TestClient(seeded, follow_redirects=False)
    store = seeded.state.store
    lid = store.create_list("Leads", "people")
    store.add_to_list(lid, list(store.people))
    r = client.get(f"/lists/{lid}/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = rows_of(r)
    assert list(rows[0]) == [
        "name",
        "title",
        "company_domain",
        "email",
        "email_status",
        "confidence",
        "source_urls",
    ]
    assert {x["email"] for x in rows} == {
        "claire.martin@ateliernuage.example",
        "jonas@nordwind.example",
    }
    store.add_suppression("jonas@nordwind.example")
    body = client.get(f"/lists/{lid}/export.csv").text
    assert "jonas@nordwind.example" not in body and "Jonas Weber" in body
    store.add_suppression("ateliernuage.example")
    assert "claire.martin@ateliernuage.example" not in client.get(f"/lists/{lid}/export.csv").text


def test_export_csv_companies_and_bulk(app: FastAPI, client: TestClient) -> None:
    store = app.state.store
    c = ids(app)
    lid = store.create_list("Cos", "companies")
    store.add_to_list(lid, [c["Atelier Nuage"], c["Kestrel Systems"]])
    rows = rows_of(client.get(f"/lists/{lid}/export.csv"))
    assert list(rows[0]) == [
        "name",
        "domain",
        "country",
        "city",
        "industry",
        "size_range",
        "lei",
        "wikidata_id",
    ]
    assert [x["name"] for x in rows] == ["Atelier Nuage", "Kestrel Systems"]
    sel = rows_of(
        client.post("/bulk/export.csv", data={"kind": "companies", "ids": [c["Nordwind Data"]]})
    )
    assert [x["name"] for x in sel] == ["Nordwind Data"]
    assert client.get("/lists/none/export.csv").status_code == 404


def test_csv_formula_injection_is_neutralised(app: FastAPI, client: TestClient) -> None:
    app.state.search.companies[0].name = "=HYPERLINK(1)"
    r = client.post(
        "/bulk/export.csv", data={"kind": "companies", "ids": [app.state.search.companies[0].id]}
    )
    assert "'=HYPERLINK(1)" in r.text


def test_saved_search_and_view_menu(app: FastAPI, client: TestClient) -> None:
    r = client.post(
        "/searches", data={"name": "German", "kind": "companies", "query": "country=DE&notice=x"}
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/companies?country=DE")
    (s,) = app.state.store.saved_searches("companies")
    assert s.name == "German" and "country=DE" in s.filters_json and "notice" not in s.filters_json
    html = client.get("/companies").text
    assert "German" in html and "/companies?country=DE" in html


def test_opt_out_flow(app: FastAPI, client: TestClient) -> None:
    assert client.get("/opt-out").status_code == 200
    r = client.post("/opt-out", data={"email": "Bye@Example.com"})
    assert r.status_code == 200 and "bye@example.com" in r.text
    assert app.state.store.is_suppressed("bye@example.com")
    bad = client.post("/opt-out", data={"email": "nope"})
    assert bad.status_code == 400 and "valid" in bad.text


def test_settings_suppression_and_content(app: FastAPI, client: TestClient) -> None:
    html = client.get("/settings").text
    for needle in (
        "fake.parquet",
        "leadcommons pack download latest",
        "LEADCOMMONS_SMTP_PROBE",
        "AGPL-3.0-or-later",
        "Wikidata",
        "GLEIF",
    ):
        assert needle in html
    r = client.post("/settings/suppression", data={"value": "spam.example"})
    assert r.status_code == 303 and app.state.store.is_suppressed("x@spam.example")


def test_cross_site_post_rejected(app: FastAPI, client: TestClient) -> None:
    cid = ids(app)["Atelier Nuage"]
    r = client.post(f"/companies/{cid}/find-people", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and not app.state.jobs.jobs
    r = client.post(
        f"/companies/{cid}/find-people",
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert r.status_code == 303


def test_jobs_partials(app: FastAPI, client: TestClient) -> None:
    assert "0 running" in client.get("/partials/jobs-pill").text
    app.state.jobs.submit("find_people", {"domain": "a.example"}, "Find people at a.example")
    assert "1 running" in client.get("/partials/jobs-pill").text
    html = client.get("/partials/jobs").text
    assert "Find people at a.example" in html and "Running" in html
    assert "No jobs yet" in TestClient(make_app(Path("/tmp"))).get("/partials/jobs").text


def test_home_checklist_progress_and_banner(seeded: FastAPI) -> None:
    client = TestClient(seeded, follow_redirects=False)
    html = client.get("/").text
    assert "Get started with LeadCommons" in html and html.count("btn primary") >= 1
    assert "Continue with the Assistant" in html
    store = seeded.state.store
    c = seeded.state.search.companies[0]
    store.save_company(c)
    store.create_list("x", "people")
    html = client.get("/").text
    assert "4 of 7 steps done" in html  # company, people, email, list
    client.post("/dismiss-banner")
    assert "Continue with the Assistant" not in client.get("/").text
    # exactly one filled orange button on the page
    assert client.get("/").text.count("btn primary") == 1


def test_static_assets_served(client: TestClient) -> None:
    for path in (
        "/static/css/app.css",
        "/static/js/app.js",
        "/static/vendor/htmx.min.js",
        "/static/icons/sprite.svg",
        "/static/fonts/inter-latin-400-normal.woff2",
    ):
        assert client.get(path).status_code == 200
