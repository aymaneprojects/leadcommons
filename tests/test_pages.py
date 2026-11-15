"""HTML page tests. The app is assembled by hand (no create_app) so other modules may be stubs."""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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
    for label in ("Prospect", "Engage", "Win deals", "Onboarding", "Email setup"):
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
    assert "Also match similar titles" in people


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
    assert "Run a custom prompt" in html and "From a list of URLs" in html


def test_search_css_scrim_matches_drawer_width() -> None:
    import re

    css = (STATIC_DIR / "css" / "search.css").read_text()
    width = re.search(r"#drawer\s*\{\s*width:\s*(min\([^)]*\))", css)
    assert width and f"inset: 0 {width.group(1)} 0 0" in css


# ------------------------------------------------------------------ v0.3 search actions

DIALOG_PATHS = (
    "/partials/email-compose",
    "/partials/sequence-picker",
    "/partials/task-new",
    "/partials/call-log",
    "/partials/deal-new",
    "/partials/note-new",
    "/partials/meeting-new",
)


def add_stubs(app: FastAPI) -> None:
    """Tiny stand-ins for the partials other features provide (see docs/dev/v0.3-contracts.md)."""
    from fastapi import APIRouter

    stub = APIRouter()
    for path in DIALOG_PATHS:

        def dialog(request: Request, path: str = path) -> HTMLResponse:
            return HTMLResponse(f"<dialog data-stub={path!r}>{request.url.query}</dialog>")

        stub.get(path, response_class=HTMLResponse)(dialog)

    @stub.get("/partials/activity", response_class=HTMLResponse)
    def activity(request: Request) -> HTMLResponse:
        return HTMLResponse(f"<ul class='timeline'><li>stub {request.url.query}</li></ul>")

    @stub.get("/partials/insights", response_class=HTMLResponse)
    def insights_partial(request: Request) -> HTMLResponse:
        return HTMLResponse(f"<div>stub insights {request.url.query}</div>")

    @stub.get("/workflows/new", response_class=HTMLResponse)
    def workflow_new() -> HTMLResponse:
        return HTMLResponse("<h1>New workflow</h1>")

    app.include_router(stub)


@pytest.fixture(autouse=True)
def no_insights(monkeypatch: pytest.MonkeyPatch) -> None:
    """The data module is another feature: by default nothing was refreshed, no lookalikes."""

    def not_ready(*a: object, **k: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr("leadcommons.insights.get", lambda store, company_id: None)
    monkeypatch.setattr("leadcommons.insights.lookalikes", not_ready)


@pytest.fixture
def stubbed(seeded: FastAPI) -> FastAPI:
    add_stubs(seeded)
    return seeded


def person_id(app: FastAPI, name: str = "Claire Martin") -> str:
    return next(p.id for p in app.state.store.people.values() if p.full_name == name)


def hrefs(html: str, attr: str = "href") -> list[str]:
    """Targets of the elements that open another feature's dialog or lazy-load a fragment."""
    out = re.findall(r'<a [^>]*?\bdata-dlg\b[^>]*?href="([^"]+)"', html)
    out += re.findall(r'<a [^>]*?href="([^"]+)"[^>]*?\bdata-dlg\b', html)
    out += re.findall(r'data-lazy-src="([^"]+)"', html)
    return [o.replace("&amp;", "&") for o in out]


def test_every_dialog_and_fragment_link_hits_a_contract_endpoint(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    pid, cid = person_id(stubbed), ids(stubbed)["Atelier Nuage"]
    pages = [
        client.get("/people").text,
        client.get("/companies").text,
        client.get(f"/people/{pid}").text,
        client.get(f"/people/{pid}", headers=HX).text,
        client.get(f"/companies/{cid}").text,
        client.get(f"/companies/{cid}", headers=HX).text,
    ]
    seen: set[str] = set()
    for html in pages:
        for url in hrefs(html):
            assert client.get(url).status_code == 200, url
            seen.add(urlparse(url).path)
    for path in DIALOG_PATHS + ("/partials/activity", "/partials/insights"):
        assert path in seen, path


def test_row_actions_use_contract_parameters(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    pid = person_id(stubbed)
    html = client.get("/people").text
    assert f"/partials/email-compose?person_id={pid}" in html
    assert f"/partials/sequence-picker?ids={pid}" in html
    assert f"/partials/call-log?person_id={pid}" in html
    for path in ("task-new", "deal-new", "note-new", "meeting-new"):
        assert f"/partials/{path}?person_id={pid}&amp;company_id=" in html
    assert "Find email" in html and f"/people/{pid}/find-email" in html
    cid = ids(stubbed)["Atelier Nuage"]
    html = client.get("/companies").text
    assert f"/partials/task-new?company_id={cid}" in html
    for path in ("deal-new", "note-new", "meeting-new"):
        assert f"/partials/{path}?company_id={cid}" in html
    assert f"/companies/{cid}/refresh-insights" in html and "Refresh company insights" in html


def test_call_is_a_tel_link_only_when_a_phone_is_known(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = TestClient(stubbed)
    pid, cid = person_id(stubbed), ids(stubbed)["Atelier Nuage"]
    html = client.get(f"/people/{pid}").text
    assert 'href="tel:' not in html and "No phone data" in html
    assert "No phone number known" in html
    monkeypatch.setattr(
        "leadcommons.insights.get",
        lambda store, company_id: (
            SimpleNamespace(phones=["+33 4 72 00 11 22"]) if company_id == cid else None
        ),
    )
    html = client.get(f"/people/{pid}").text
    assert 'href="tel:+33472001122"' in html and "+33 4 72 00 11 22" in html
    assert "No phone data" not in html and "Log a call" in html
    assert 'href="tel:+33472001122"' in client.get("/people").text  # row menu
    assert 'href="tel:+33472001122"' in client.get(f"/companies/{cid}", headers=HX).text


def test_selection_bar_actions(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    html = client.get("/people?country=DE&sort=name").text
    bar = html[html.index('class="bulk selbar"') :]
    bar = bar[: bar.index("</form>")] if "</form>" in bar else bar
    assert "/partials/sequence-picker?ids={ids}" in bar
    assert "/partials/email-compose?person_id={id}" in bar
    assert "/partials/call-log?person_id={id}" in bar
    assert 'data-open-dialog="task-dialog"' in bar and 'data-open-dialog="save-dialog"' in bar
    assert "/bulk/find-emails" in bar and "/bulk/export.csv" in bar and "data-tip=" in bar
    assert "Opted-out addresses are never included" in bar
    assert "/bulk/suppress" in bar and "Set up an integration" in bar
    assert "workflows/new?source=people&amp;query=country%3DDE%26sort%3Dname" in bar
    assert (
        'id="task-dialog"' in html
        and 'action="/bulk/create-tasks"' in html
        or "/bulk/create-tasks" in html
    )
    comp = client.get("/companies").text
    assert "/bulk/find-people" in comp and "/bulk/refresh-insights" in comp
    assert (
        "/partials/sequence-picker"
        not in comp[comp.index('class="bulk selbar"') :].split("</form>")[0]
    )


def test_create_workflow_menu_passes_the_current_query(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    html = client.get("/companies?country=DE&size=11-50").text
    m = re.search(r'href="(/workflows/new\?[^"]+)"', html)
    assert m and "Create workflow" in html
    url = m.group(1).replace("&amp;", "&")
    q = parse_qs(urlparse(url).query)
    assert q["source"] == ["companies"] and q["query"] == ["country=DE&size=11-50"]
    assert unquote(urlparse(url).query).count("country=DE") == 1
    assert client.get(url).status_code == 200
    assert "source=people" in client.get("/people").text


# ---- profiles: activity, insights tabs, score, do not contact


def test_person_page_activity_insights_score_and_dnc(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    pid, cid = person_id(stubbed), ids(stubbed)["Atelier Nuage"]
    html = client.get(f"/people/{pid}").text
    assert f"/partials/activity?person_id={pid}&amp;company_id={cid}" in html
    assert f"/partials/insights?company_id={cid}&amp;tab=overview" in html
    assert "Website visitors" in html and "Needs a tracking script" not in html
    assert "need a tracking script on your own website" in html and "News" in html
    assert "Profile completeness" in html and "Email verified" in html
    assert "Do not contact" in html and "Mark this person as do not contact" in html
    assert f'action="/people/{pid}/suppress"' in html


def test_company_panel_has_activity_insights_score_and_dnc(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    cid = ids(stubbed)["Atelier Nuage"]
    html = client.get(f"/companies/{cid}", headers=HX).text
    assert 'data-panel="activity"' in html and f"/partials/activity?company_id={cid}" in html
    assert f"/partials/insights?company_id={cid}&amp;tab=overview" in html
    assert "Insights refreshed" in html and "Company profile completeness" in html
    assert f'action="/companies/{cid}/suppress"' in html and "Mark everyone at this company" in html
    dnc = html[html.index('aria-label="Do not contact"') :]
    assert 'hx-target="#drawer"' in dnc.split("</form>")[0]  # the drawer keeps you on the results
    page = client.get(f"/companies/{cid}").text
    dnc = page[page.index('aria-label="Do not contact"') :]
    assert (
        f'action="/companies/{cid}/suppress"' in dnc
        and 'hx-target="#drawer"' not in dnc.split("</form>")[0]
    )


def test_completeness_scores_reflect_the_data(stubbed: FastAPI, monkeypatch) -> None:
    from leadcommons.web.pages import company_score, profile_score

    store, search = stubbed.state.store, stubbed.state.search
    pid = person_id(stubbed)
    person, co = store.get_person(pid), search.companies[0]
    sc = profile_score(person, store.best_email(pid), co)
    assert 0 < sc["value"] < 100 and ("Job title", "Yes") in sc["rows"]
    assert ("Department", "Missing") in sc["rows"]
    bare = profile_score(person, None, None)
    assert bare["value"] < sc["value"] and ("Email found", "Missing") in bare["rows"]
    c1 = company_score(co, [], None)
    c2 = company_score(co, [person], SimpleNamespace())
    assert c2["value"] > c1["value"] and ("Insights refreshed", "Yes") in c2["rows"]


def test_person_suppress_and_unsuppress(stubbed: FastAPI) -> None:
    store = stubbed.state.store
    store.remove_suppression = lambda v: (
        v in store.suppressed and (store.suppressed.discard(v) or True)
    )
    client = TestClient(stubbed, follow_redirects=False)
    pid = person_id(stubbed)
    addr = "claire.martin@ateliernuage.example"
    r = client.post(f"/people/{pid}/suppress")
    assert r.status_code == 303 and addr in store.suppressed
    html = client.get(f"/people/{pid}").text
    assert "On your do-not-contact list" in html and f'action="/people/{pid}/unsuppress"' in html
    r = client.post(f"/people/{pid}/unsuppress", headers={**HX, "HX-Target": "drawer"})
    assert r.status_code == 200 and "<html" not in r.text and "Mark this person" in r.text
    assert addr not in store.suppressed and "removed" in r.headers["HX-Trigger"].lower()
    r = client.post(f"/people/{pid}/unsuppress", headers=HX)
    assert r.status_code == 200 and "error" in r.headers["HX-Trigger"]


def test_person_suppress_needs_an_address_and_respects_domain_suppression(
    stubbed: FastAPI,
) -> None:
    store = stubbed.state.store
    store.remove_suppression = lambda v: (
        v in store.suppressed and (store.suppressed.discard(v) or True)
    )
    client = TestClient(stubbed, follow_redirects=False)
    jonas = person_id(stubbed, "Jonas Weber")
    store.emails.pop(jonas)
    r = client.post(f"/people/{jonas}/suppress", headers=HX)
    assert "error" in r.headers["HX-Trigger"] and not store.suppressed
    html = client.get(f"/people/{jonas}").text
    assert "No email address on file" in html
    claire = person_id(stubbed)
    store.add_suppression("ateliernuage.example")
    html = client.get(f"/people/{claire}").text
    assert "whole domain" in html and "Remove from the list" not in html


def test_company_suppress_and_unsuppress(stubbed: FastAPI) -> None:
    store = stubbed.state.store
    store.remove_suppression = lambda v: (
        v in store.suppressed and (store.suppressed.discard(v) or True)
    )
    client = TestClient(stubbed, follow_redirects=False)
    cid = ids(stubbed)["Atelier Nuage"]
    assert client.post(f"/companies/{cid}/suppress").status_code == 303
    assert "ateliernuage.example" in store.suppressed
    drawer = client.get(f"/companies/{cid}", headers=HX).text
    assert "On your do-not-contact list" in drawer and f"/companies/{cid}/unsuppress" in drawer
    r = client.post(f"/companies/{cid}/unsuppress", headers={**HX, "HX-Target": "drawer"})
    assert r.status_code == 200 and "Mark everyone at this company" in r.text
    assert "ateliernuage.example" not in store.suppressed
    assert "error" in client.post(f"/companies/{cid}/unsuppress", headers=HX).headers["HX-Trigger"]


def test_suppression_routes_are_csrf_protected(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    pid = person_id(stubbed)
    for path in (
        f"/people/{pid}/suppress",
        "/bulk/create-tasks",
        "/bulk/suppress",
        "/bulk/ai-prompt",
    ):
        assert client.post(path, headers={"Origin": "https://evil.example"}).status_code == 403


def test_bulk_suppress(stubbed: FastAPI) -> None:
    store = stubbed.state.store
    client = TestClient(stubbed, follow_redirects=False)
    r = client.post(
        "/bulk/suppress", data={"kind": "people", "ids": [person_id(stubbed)]}, headers=HX
    )
    assert (
        "claire.martin@ateliernuage.example" in store.suppressed and "ok" in r.headers["HX-Trigger"]
    )
    cid = ids(stubbed)["Nordwind Data"]
    client.post("/bulk/suppress", data={"kind": "companies", "ids": [cid]}, headers=HX)
    assert "nordwind.example" in store.suppressed
    r = client.post("/bulk/suppress", data={"kind": "people"}, headers=HX)
    assert "error" in r.headers["HX-Trigger"]


# ---- bulk tasks, refresh insights, demo mode


def test_bulk_create_tasks_uses_the_engage_contract(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict]] = []

    def fake_create(store, title, **kw):  # noqa: ANN001
        calls.append((title, kw))
        return "t1"

    monkeypatch.setattr("leadcommons.engage.create_task", fake_create)
    client = TestClient(stubbed, follow_redirects=False)
    pids = [person_id(stubbed), person_id(stubbed, "Jonas Weber")]
    r = client.post(
        "/bulk/create-tasks",
        data={
            "kind": "people",
            "ids": pids,
            "title": "Intro email",
            "task_kind": "email",
            "due_days": "3",
        },
        headers=HX,
    )
    assert "Created 2 tasks" in r.headers["HX-Trigger"]
    assert [c[0] for c in calls] == ["Intro email"] * 2
    assert calls[0][1]["person_id"] == pids[0] and calls[0][1]["kind"] == "email"
    assert calls[0][1]["due"] is not None and calls[0][1]["company_id"]
    calls.clear()
    cid = ids(stubbed)["Atelier Nuage"]
    client.post(
        "/bulk/create-tasks",
        data={"kind": "companies", "ids": [cid], "title": "Research"},
        headers=HX,
    )
    assert calls[0][1] == {"company_id": cid, "due": None, "kind": "action"}
    r = client.post(
        "/bulk/create-tasks", data={"kind": "people", "ids": pids, "title": " "}, headers=HX
    )
    assert "error" in r.headers["HX-Trigger"]
    r = client.post("/bulk/create-tasks", data={"kind": "people", "title": "x"}, headers=HX)
    assert "error" in r.headers["HX-Trigger"]


def test_bulk_create_tasks_when_engage_is_not_installed(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*a: object, **k: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr("leadcommons.engage.create_task", missing)
    client = TestClient(stubbed, follow_redirects=False)
    r = client.post(
        "/bulk/create-tasks",
        data={"kind": "people", "ids": [person_id(stubbed)], "title": "x"},
        headers=HX,
    )
    assert r.status_code == 200 and "not available" in r.headers["HX-Trigger"]


def test_refresh_insights_submits_the_contract_job(stubbed: FastAPI) -> None:
    client = TestClient(stubbed, follow_redirects=False)
    jobs = stubbed.state.jobs
    c = stubbed.state.search.companies
    r = client.post(f"/companies/{c[0].id}/refresh-insights", headers=HX)
    assert r.status_code == 200 and "Refreshing insights" in r.headers["HX-Trigger"]
    job = list(jobs.jobs.values())[-1]
    assert job.kind == "company_insights"
    assert job.payload == {"company_id": c[0].id, "domain": c[0].domain}
    r = client.post("/bulk/refresh-insights", data={"ids": [c[1].id, c[2].id]}, headers=HX)
    assert "2 companies" in r.headers["HX-Trigger"] and len(jobs.jobs) == 3
    assert client.post(f"/companies/{c[0].id}/refresh-insights").status_code == 303


def test_demo_mode_disables_crawling_actions(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo")
    add_stubs(app)
    store = app.state.store
    co = app.state.search.companies[0]
    p = sample_person(co)
    store.save_person(p)
    client = TestClient(app, follow_redirects=False)
    for url in ("/people", "/companies", f"/people/{p.id}", f"/companies/{co.id}"):
        html = client.get(url, headers=HX if "/" in url[1:] else {}).text
        assert "Turned off in the public demo" in html, url
    assert 'disabled data-tip="Turned off in the public demo' in client.get("/people").text
    for path, data in (
        (f"/companies/{co.id}/refresh-insights", {}),
        (f"/companies/{co.id}/find-people", {}),
        (f"/people/{p.id}/find-email", {}),
        ("/bulk/refresh-insights", {"ids": [co.id]}),
        ("/bulk/find-people", {"ids": [co.id]}),
        ("/bulk/find-emails", {"ids": [p.id]}),
    ):
        r = client.post(path, data=data, headers=HX)
        assert "error" in r.headers["HX-Trigger"] and "demo" in r.headers["HX-Trigger"], path
    assert not app.state.jobs.jobs
    assert (
        client.post(
            "/bulk/ai-prompt", data={"kind": "people", "ids": [p.id], "prompt": "x"}
        ).status_code
        == 403
    )


# ---- AI research


@pytest.fixture
def llm_on(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "tiny")
    sent: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        sent.append({"url": url, "body": json})
        text = json["messages"][1]["content"]
        reply = "Unknown" if "Nordwind" in text else "Sells clouds"

        class R:
            def raise_for_status(self) -> None: ...
            def json(self) -> dict:
                return {"choices": [{"message": {"content": reply}}]}

        return R()

    monkeypatch.setattr("leadcommons.web.pages.httpx.post", fake_post)
    return sent


def ai_items(html: str) -> list[str]:
    return re.findall(r'<button type="button" class="menu-item" data-ai-open="[a-z]+"[^>]*>', html)


def test_ai_menu_is_disabled_with_a_how_to_without_a_model(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LEADCOMMONS_LLM_URL", raising=False)
    monkeypatch.delenv("LEADCOMMONS_LLM_MODEL", raising=False)
    html = TestClient(stubbed).get("/people").text
    items = ai_items(html)
    assert items and all("disabled" in i and "LEADCOMMONS_LLM_URL" in i for i in items)
    assert "Open the Assistant" in html
    client = TestClient(stubbed)
    r = client.post(
        "/bulk/ai-prompt", data={"kind": "people", "ids": [person_id(stubbed)], "prompt": "x"}
    )
    assert r.status_code == 400 and "LEADCOMMONS_LLM_MODEL" in r.json()["error"]


def test_ai_menu_is_enabled_with_a_model_and_has_templates(
    stubbed: FastAPI, llm_on: list[dict]
) -> None:
    html = TestClient(stubbed).get("/companies").text
    items = ai_items(html)
    assert items and not any("disabled" in i for i in items)
    assert 'data-ai-open="formula"' in html and "Start from a template" in html
    assert 'id="ai-dialog"' in html and "Sent to your model" in html


def test_ai_prompt_answers_each_selected_row(stubbed: FastAPI, llm_on: list[dict]) -> None:
    client = TestClient(stubbed)
    c = stubbed.state.search.companies
    r = client.post(
        "/bulk/ai-prompt",
        data={"kind": "companies", "ids": [c[0].id, c[1].id], "prompt": "What do they sell?"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["answers"] == {c[0].id: "Sells clouds", c[1].id: "Unknown"}
    assert len(llm_on) == 2 and llm_on[0]["url"] == "http://llm.test/v1/chat/completions"
    user = llm_on[0]["body"]["messages"][1]["content"]
    assert (
        "What do they sell?" in user and "Atelier Nuage" in user and "ateliernuage.example" in user
    )
    assert llm_on[0]["body"]["model"] == "tiny"
    r = client.post("/bulk/ai-prompt", data={"kind": "companies", "ids": [c[0].id]})
    assert r.status_code == 400  # no prompt


def test_ai_prompt_sends_only_visible_fields_and_caps_rows(
    stubbed: FastAPI, llm_on: list[dict]
) -> None:
    client = TestClient(stubbed)
    pid = person_id(stubbed)
    r = client.post(
        "/bulk/ai-prompt",
        data={
            "kind": "people",
            "ids": [pid] + [f"missing{i}" for i in range(40)],
            "prompt": "Opener",
            "mode": "formula",
        },
    )
    assert r.json()["answers"] == {pid: "Sells clouds"}
    body = llm_on[0]["body"]
    assert "Reply with only that value" in body["messages"][0]["content"]
    text = body["messages"][1]["content"]
    assert "Claire Martin" in text and "CEO" in text and "@" not in text


def test_ai_prompt_survives_a_broken_model(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "tiny")

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise TimeoutError

    monkeypatch.setattr("leadcommons.web.pages.httpx.post", boom)
    pid = person_id(stubbed)
    r = TestClient(stubbed).post(
        "/bulk/ai-prompt", data={"kind": "people", "ids": [pid], "prompt": "x"}
    )
    assert r.status_code == 200 and "Model error" in r.json()["answers"][pid]


# ---- filters: lookalikes, technologies, job postings, locked filters


def fake_lookalikes(domains: list[str]):
    def inner(search, company, limit=25):  # noqa: ANN001
        return [c for c in search.companies if c.domain in domains]

    return inner


def listed(html: str, *names: str) -> set[str]:
    return {n for n in names if n in html}


def test_company_lookalike_filter(stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "leadcommons.insights.lookalikes", fake_lookalikes(["nordwind.example", "bruecke.example"])
    )
    client = TestClient(stubbed)
    names = ("Atelier Nuage", "Nordwind Data", "Brücke Software", "Kestrel Systems")
    html = client.get("/companies?lookalike=Atelier").text
    assert listed(html, *names) == {"Nordwind Data", "Brücke Software"}
    assert "Similar to" in html  # removable chip
    html = client.get(
        "/companies?lookalike=ateliernuage.example&country=DE&domain=bruecke.example"
    ).text
    assert listed(html, *names) == {"Brücke Software"}
    assert "No company found" in client.get("/companies?lookalike=zzzz").text
    assert listed(client.get("/companies?lookalike=zzzz").text, *names) == set()
    assert "records found" in client.get("/search/count?kind=companies&lookalike=Atelier").text


def test_company_lookalike_soon_when_not_implemented(stubbed: FastAPI) -> None:
    client = TestClient(stubbed)
    html = client.get("/companies?lookalike=Atelier").text
    assert "Soon" in html and "not available in this build yet" in html
    assert "Nordwind Data" in html and "Atelier Nuage" in html  # not narrowed


def test_people_lookalike_filters(stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    store = stubbed.state.store
    search = stubbed.state.search
    kestrel = search.companies[4]
    store.save_person(sample_person(kestrel, "Kim Lee", "CTO"))
    monkeypatch.setattr("leadcommons.insights.lookalikes", fake_lookalikes(["nordwind.example"]))
    client = TestClient(stubbed)
    names = ("Claire Martin", "Jonas Weber", "Kim Lee")
    assert listed(client.get("/people?lookalike=Atelier").text, *names) == {"Jonas Weber"}
    # people like Claire: people at her company and at companies like it
    html = client.get("/people?plook=Claire").text
    assert listed(html, *names) == {"Claire Martin", "Jonas Weber"}
    assert "Like" in html
    assert "No person found" in client.get("/people?plook=Nobody").text


def test_people_lookalike_soon(stubbed: FastAPI) -> None:
    html = TestClient(stubbed).get("/people?plook=Claire").text
    assert "not available in this build yet" in html and "Jonas Weber" in html


def insights_for(mapping: dict[str, dict]):
    def get(store, company_id):  # noqa: ANN001
        data = mapping.get(company_id)
        return SimpleNamespace(**data) if data else None

    return get


def test_technology_and_job_filters_work_over_refreshed_insights(
    stubbed: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = ids(stubbed)
    monkeypatch.setattr(
        "leadcommons.insights.get",
        insights_for(
            {
                c["Atelier Nuage"]: {
                    "technologies": ["Shopify", {"name": "React"}],
                    "job_postings": [{"title": "Sales Manager"}],
                },
                c["Nordwind Data"]: {
                    "technologies": ["PostgreSQL"],
                    "job_postings": [{"title": "Data Engineer"}],
                },
            }
        ),
    )
    client = TestClient(stubbed)
    names = ("Atelier Nuage", "Nordwind Data", "Brücke Software")
    assert listed(client.get("/companies?tech=shopify").text, *names) == {"Atelier Nuage"}
    assert listed(client.get("/companies?tech=react&tech=postgres").text, *names) == {
        "Atelier Nuage",
        "Nordwind Data",
    }
    assert listed(client.get("/companies?job=engineer").text, *names) == {"Nordwind Data"}
    assert listed(client.get("/companies?tech=react&job=engineer").text, *names) == set()
    assert listed(client.get("/companies?tech=react&country=DE").text, *names) == set()
    html = client.get("/companies?tech=react").text
    assert "whose insights were refreshed" in html and "Uses" in html
    # people: only people at matching companies
    pe = ("Claire Martin", "Jonas Weber")
    assert listed(client.get("/people?tech=shopify").text, *pe) == {"Claire Martin"}
    assert listed(client.get("/people?job=engineer").text, *pe) == {"Jonas Weber"}
    # saved tab honours it too
    stubbed.state.store.save_company(stubbed.state.search.companies[2])
    assert listed(client.get("/companies?tab=saved&tech=react").text, *names) == set()


def test_technology_filter_without_insights_matches_nothing(stubbed: FastAPI) -> None:
    html = TestClient(stubbed).get("/companies?tech=shopify").text
    assert "No companies match" in html


def test_unsupported_filters_stay_disabled_with_a_reason(stubbed: FastAPI) -> None:
    html = TestClient(stubbed).get("/companies").text
    for label, reason in (
        ("Buying intent", "paid third-party signals"),
        ("Revenue", "not in the open data packs"),
        ("Funding", "company insights"),
        ("Website visitors", "tracking script"),
    ):
        assert label in html and reason in html, label
    panel = html[html.index('class="fpanel"') :].split("</aside>")[0]
    assert "Website visitors" in panel and "Lookalikes" in panel
    people = TestClient(stubbed).get("/people").text
    assert "People lookalikes" in people[people.index('class="fpanel"') :].split("</aside>")[0]
    assert "Company lookalikes" in people and "Technologies" in people and "Job postings" in people
