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
from leadcommons.web.pages_extra import router as extra_router
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
    app.include_router(extra_router)  # same order as create_app
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
    ],
)
def test_pages_render(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "<title>" in r.text


def test_email_health_lives_in_settings(client: TestClient) -> None:
    r = client.get("/email-health", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    assert r.headers["location"].endswith("/settings/email-health")
    assert client.get("/email-health", follow_redirects=True).status_code == 200


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


# ---------------------------------------------------------------- search screens (v2)


def test_pager_math() -> None:
    from leadcommons.web.pages import pager

    pg = pager("/companies", {"country": ["DE"]}, 3, 130, 25)
    assert (pg["pages"], pg["start"], pg["end"]) == (6, 51, 75)
    assert pg["prev"] == "/companies?country=DE&page=2" and pg["next"].endswith("page=4")
    assert [n for n, _ in pg["options"]] == [1, 2, 3, 4, 5, 6]
    first = pager("/people", {}, 1, 0, 25)
    assert (first["start"], first["end"], first["prev"], first["next"]) == (0, 0, None, None)
    last = pager("/people", {"per": 50}, 9, 101, 50)  # page clamped to the last one
    assert last["page"] == 3 and last["end"] == 101 and last["next"] is None
    assert pager("/x", {}, 1, 10, 7)["per"] == 25  # unknown page size falls back


def test_size_range_maps_to_buckets(client: TestClient) -> None:
    from leadcommons.web.pages import buckets_for_range

    assert buckets_for_range(20, 300) == ["11-50", "51-200", "201-500"]
    assert buckets_for_range(None, 10) == ["1-10"]
    assert buckets_for_range(10001, None) == ["10001+"]
    assert buckets_for_range(300, 20) == ["11-50", "51-200", "201-500"]  # swapped ends
    html = client.get("/companies?size_min=20&size_max=60").text
    assert "Atelier Nuage" in html and "Nordwind Data" in html
    assert "Moss and Pine" not in html and "Kestrel Systems" not in html
    assert "Matches sizes" in html


def test_column_chooser_markup(client: TestClient, seeded: FastAPI) -> None:
    html = client.get("/companies").text
    assert 'id="col-chooser"' in html and "Add column" in html
    for key in ("domain", "country", "city", "founded", "lei", "wikidata"):
        assert f'data-col-toggle="{key}"' in html
    assert 'data-col="lei" hidden' in html  # optional columns start hidden
    assert 'data-col="sources"' in html and 'data-col="sources" hidden' not in html
    people = TestClient(seeded).get("/people").text
    assert 'data-key="lc-cols-people"' in people and 'data-col-toggle="seniority"' in people


def test_filter_badges_and_clearing(client: TestClient) -> None:
    html = client.get("/companies?country=DE&country=FR&size=11-50").text
    assert 'data-fgroup="location" data-fcol="company" data-count="2"' in html
    assert 'href="/companies?size=11-50"' in html  # clearing Location keeps the other filters
    assert 'href="/companies?country=DE&amp;country=FR"' in html  # clearing # Employees
    assert 'class="cnt">3</span>' in html  # Clear all shows the total active count
    clear = client.get("/companies").text
    assert 'class="btn ghost off"' in clear


def test_include_exclude_countries_and_keywords(client: TestClient) -> None:
    html = client.get("/companies?xcountry=DE").text
    assert "Nordwind Data" not in html and "Brücke Software" not in html
    assert "Atelier Nuage" in html and "Kestrel Systems" in html
    html = client.get("/companies?country=DE&country=FR&xcountry=FR").text
    assert "Nordwind Data" in html and "Atelier Nuage" not in html
    assert "No companies match" in client.get("/companies?xcountry=DE&xcountry=FR&xcountry=GB").text
    html = client.get("/companies?xkeyword=moss").text
    assert "Moss and Pine" not in html and "Atelier Nuage" in html
    html = client.get("/companies?keyword=nuage&keyword_add=pine").text
    assert "Atelier Nuage" in html and 'name="keyword" value="pine"' in html


def test_count_endpoint(seeded: FastAPI) -> None:
    client = TestClient(seeded)
    html = client.get("/search/count?kind=companies&country=DE").text
    assert ">2</span>" in html and "records found" in html
    assert ">3</span>" in client.get("/search/count?kind=companies&xcountry=DE").text
    assert ">2</span>" in client.get("/search/count?kind=people").text
    assert ">1</span>" in client.get("/search/count?kind=people&xtitle=ceo").text
    assert ">0</span>" in client.get("/search/count?kind=companies&q=zzzz").text


def test_all_filters_modal_renders(client: TestClient, seeded: FastAPI) -> None:
    html = client.get("/companies").text
    assert 'id="allf-dialog"' in html and "Pinned filters" in html
    assert "Person info" in html and "Company info" in html and "Type: All" in html
    assert "Apply filters" in html and "records found" in html
    for label in ("Technologies", "Funding", "Headcount growth", "Buying intent", "Time zone"):
        assert label in html
    assert html.count("soon-badge") >= 10 and 'aria-disabled="true"' in html
    people = TestClient(seeded).get("/people").text
    assert 'data-fgroup="titles"' in people and "include" in people.lower()
    assert "Include people with similar titles" in people


def real_people_app(tmp_path: Path) -> FastAPI:
    from leadcommons.store import Store

    app = make_app(tmp_path)
    app.state.store = Store(f"sqlite:///{tmp_path / 't.db'}")
    search = app.state.search
    rows = [
        ("Ann One", "CEO", 0),
        ("Bob Two", "Chief Executive Officer", 1),
        ("Cy Three", "CTO and Co-Founder", 2),
        ("Di Four", "Director of Engineering", 3),
        ("Ed Five", "Head of Sales", 4),
    ]
    for name, title, i in rows:
        app.state.store.save_person(sample_person(search.companies[i], name, title))
    return app


def test_include_exclude_titles_and_similar(tmp_path: Path) -> None:
    from leadcommons.web.pages import expand_titles

    assert "chief executive officer" in expand_titles(["CEO"])
    assert {"founder", "co-founder"} <= set(expand_titles(["founder"]))
    client = TestClient(real_people_app(tmp_path))

    def names(query: str) -> set[str]:
        html = client.get("/people" + query).text
        return {n for n in ("Ann One", "Bob Two", "Cy Three", "Di Four", "Ed Five") if n in html}

    assert names("?title=ceo") == {"Ann One", "Bob Two"}  # similar titles on by default
    assert names("?title=ceo&similar=0") == {"Ann One"}
    assert names("?title=cto") == {"Cy Three"}  # "cto" must not match "Director"
    assert names("?xtitle=ceo") == {"Cy Three", "Di Four", "Ed Five"}
    assert names("?xtitle=ceo&similar=0") == {"Bob Two", "Cy Three", "Di Four", "Ed Five"}
    assert names("?title=founder&xtitle=cto") == set()
    assert names("?title=ceo&title_add=sales") == {"Ann One", "Bob Two", "Ed Five"}
    html = client.get("/people?title=ceo&xtitle=cto").text
    assert "Include titles" in html and "Exclude titles" in html


def test_people_location_filter_uses_company_data(tmp_path: Path) -> None:
    client = TestClient(real_people_app(tmp_path))
    html = client.get("/people?country=FR").text  # companies 0 and 3 are French
    assert "Ann One" in html and "Di Four" in html and "Bob Two" not in html
    html = client.get("/people?xcountry=FR").text
    assert "Bob Two" in html and "Ann One" not in html


def test_person_page_contact_score_and_optout(seeded: FastAPI) -> None:
    client = TestClient(seeded)
    store = seeded.state.store
    pid = next(p.id for p in store.people.values() if p.full_name == "Claire Martin")
    html = client.get(f"/people/{pid}").text
    assert "<html" in html and 'aria-label="Breadcrumb"' in html
    assert "Contact information" in html and "claire.martin@ateliernuage.example" in html
    assert "90" in html and "/ 100" in html and "Show breakdown" in html
    assert "Opted out" not in html and "No phone data" in html
    assert "Atelier Nuage" in html and "People at this company" in html
    store.add_suppression("claire.martin@ateliernuage.example")
    html = client.get(f"/people/{pid}").text
    assert "Opted out" in html and "claire.martin@ateliernuage.example" not in html
    drawer = client.get(f"/people/{pid}", headers=HX).text
    assert "<html" not in drawer and "Opted out" in drawer


def test_company_panel_tabs_and_sources(app: FastAPI, client: TestClient) -> None:
    cid = ids(app)["Atelier Nuage"]
    html = client.get(f"/companies/{cid}", headers=HX).text
    for tab in ("Overview", "People", "Sources"):
        assert tab in html
    assert 'data-panel="sources"' in html and "Find people" in html


def test_saved_views_and_settings_markup(app: FastAPI, client: TestClient) -> None:
    app.state.store.save_search("German", "companies", json.dumps({"query": "country=DE"}))
    html = client.get("/companies?country=DE").text
    assert 'class="menu-item vitem on"' in html and "Save current view" in html
    assert "Results per page" in html and 'data-pref="compact"' in html
    assert "v0.2" in html and "From a list of URLs" in html


def test_search_css_scrim_matches_drawer_width() -> None:
    import re

    css = (STATIC_DIR / "css" / "search.css").read_text()
    width = re.search(r"#drawer\s*\{\s*width:\s*(min\([^)]*\))", css)
    assert width and f"inset: 0 {width.group(1)} 0 0" in css
