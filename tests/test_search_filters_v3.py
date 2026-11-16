"""Search filters v0.3: landing, chip rows, job titles (Simple/Advanced, departments, personas),
location, industry and keywords, the All filters window, suggestions and engagement filters."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from markupsafe import escape
from starlette.datastructures import QueryParams

from pepite.models import Company, Person, Seniority
from pepite.packs import DuckDBCompanySearch
from pepite.search import KEYWORD_FIELDS, CompanyFilters
from pepite.web import search_filters as sf
from tests.fakes import FakeSearch, sample_email, sample_person
from tests.test_pages import make_app

SAME = {"origin": "http://testserver"}


def names_in(html: str, *names: str) -> set[str]:
    return {n for n in names if n in html}


# ---------------------------------------------------------------- Advanced title expressions


@pytest.mark.parametrize(
    ("expr", "title", "ok"),
    [
        ("head AND (sales OR growth) NOT intern", "Head of Sales", True),
        ("head AND (sales OR growth) NOT intern", "Head of Growth", True),
        ("head AND (sales OR growth) NOT intern", "Head of Sales Intern", False),
        ("head AND (sales OR growth) NOT intern", "Head of Marketing", False),
        ("head sales", "Head of Sales", True),  # side by side means AND
        ("head sales", "Head of Marketing", False),
        ('"vice president" OR vp', "Vice President, Sales", True),
        ('"vice president" OR vp', "President", False),
        ("cto -founder", "CTO", True),
        ("cto -founder", "CTO and Founder", False),
        ("market", "Marketing Lead", True),  # word start
        ("cto", "Director", False),  # not inside a word
        ("NOT intern", None, True),
        ("sales", None, False),
    ],
)
def test_title_expression_matches(expr: str, title: str | None, ok: bool) -> None:
    assert sf.eval_title_expr(sf.parse_title_expr(expr), title) is ok


@pytest.mark.parametrize(
    ("expr", "message"),
    [
        ("head AND (sales", "not closed"),
        ("head)", "no opening"),
        ("head AND", "stops after an operator"),
        ("OR sales", "needs a title"),
        ('"vice president', "quotation mark"),
        ("", "Type an expression"),
        ("x " * 200, "under 300"),
        ("(" * 13 + "a" + ")" * 13, "nested"),
    ],
)
def test_title_expression_errors_are_readable(expr: str, message: str) -> None:
    with pytest.raises(sf.TitleExprError, match=message):
        sf.parse_title_expr(expr)


def test_title_expression_never_runs_code() -> None:
    node = sf.parse_title_expr("__import__ OR eval OR \"os.system('x')\"")
    assert node[0] == "or" and all(n[0] == "term" for n in node[1])
    assert not sf.eval_title_expr(node, "Head of Sales")
    with pytest.raises(sf.TitleExprError):  # read as words and brackets, never as code
        sf.parse_title_expr("__import__('os').system('x') OR exit()")


def test_titles_to_expression_and_quoted_titles() -> None:
    assert sf.titles_to_expr(["founder", "head of sales"], ["intern"]) == (
        '(founder OR "head of sales") NOT intern'
    )
    assert sf.split_titles(['founder, "sales manager"', "Founder"]) == [
        "founder",
        '"sales manager"',
    ]
    assert sf.simple_title_match("Sales Manager", ['"sales manager"'], [], True)
    assert not sf.simple_title_match("Senior Sales Manager", ['"sales manager"'], [], True)
    assert sf.simple_title_match("Senior Sales Manager", ["sales manager"], [], True)


# ---------------------------------------------------------------- departments


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Head of Sales", {"sales"}),
        ("Chief Executive Officer", {"leadership"}),
        ("Marketing Manager", {"marketing"}),
        ("VP Sales & Marketing", {"sales", "marketing"}),
        ("Senior Software Engineer", {"engineering"}),
        ("Production Manager", {"operations"}),
        ("Product Manager", {"product"}),
        ("IT Manager", {"it"}),
        ("Data Analyst", {"data"}),
        ("Recruiter", {"hr"}),
        ("HR Business Partner", {"hr"}),
        ("Accountant", {"finance"}),
        ("General Counsel", {"legal"}),
        ("Customer Success Manager", {"support"}),
        ("Waiter", set()),
        (None, set()),
    ],
)
def test_departments_from_titles(title: str | None, expected: set[str]) -> None:
    assert sf.departments_for_title(title) == expected


def test_stored_department_wins_over_the_title() -> None:
    p = Person(id="pe_1", full_name="A", title="Head of Sales", department="Finance")
    assert sf.person_departments(p) == {"finance"}
    p2 = Person(id="pe_2", full_name="B", title="Head of Sales", department="legal")
    assert sf.person_departments(p2) == {"legal"}
    assert set(sf.DEPT_LABELS) == {key for key, _, _ in sf.DEPARTMENTS}


# ---------------------------------------------------------------- query parsing


def parse(query: str, people: bool = True) -> dict:
    g = QueryParams(query)
    p = sf.parse_common(g)
    if people:
        p.update(sf.parse_people_extra(g))
    return p


def test_place_and_industry_text_is_resolved() -> None:
    p = parse("place_add=Germany,Lyon&xplace_add=uk&industry_add=finance&industry_add=nonsense")
    assert p["country"] == ["DE"] and p["place"] == ["Lyon"] and p["xcountry"] == ["GB"]
    assert p["industry"] == ["K"]
    assert sf.resolve_country("United States") == "US" and sf.resolve_country("Atlantis") is None
    assert sf.resolve_industry("62") == "62" and sf.resolve_industry("j") == "J"


def test_advanced_mode_takes_over_the_simple_titles() -> None:
    p = parse("tmode=advanced&title=founder&title=head of sales&xtitle=intern")
    assert p["title"] == [] and p["xtitle"] == []
    assert p["texpr"] == '(founder OR "head of sales") NOT intern'
    assert parse("texpr=head")["texpr"] == ""  # only read in Advanced mode
    assert parse("dept=sales&dept=bogus")["dept"] == ["sales"]
    assert parse("emailed=yes&replied=maybe&activity=30")["replied"] == ""


# ---------------------------------------------------------------- pack SQL == Python matching


@pytest.fixture(scope="module")
def pack() -> tuple[DuckDBCompanySearch, list]:
    search = DuckDBCompanySearch(None)  # bundled sample pack
    rows = search.search_companies(CompanyFilters(), page=1, per_page=500)
    everyone, page = list(rows.items), 1
    while len(everyone) < rows.total:
        page += 1
        everyone += search.search_companies(CompanyFilters(), page=page, per_page=500).items
    return search, everyone


@pytest.mark.parametrize(
    "query",
    [
        "city=Paris",
        "country=BE&city=paris",
        "place=London&xcity=London",
        "xcountry=FR",
        "xcity=Paris&xregion=%C3%8Ele-de-France",
        "region=%C3%8Ele-de-France",
        "keyword=bank",
        "keyword=bank&keyword=france&kwall=1",
        "keyword=industry&kwfield=industry",
        "keyword=bank&kwfield=name",
        "xkeyword=bank",
        "industry=K&xindustry=K",
        "xindustry=C&country=DE",
        "size=10001%2B&xcountry=US",
    ],
)
def test_pack_query_and_python_matching_agree(pack: tuple, query: str) -> None:
    search, everyone = pack
    p = parse(query, people=False)
    flt = sf.build_company_filters(p, [], None)
    found = set()
    page = 1
    while True:
        res = search.search_companies(flt, page=page, per_page=500)
        found |= {c.id for c in res.items}
        if len(found) >= res.total or not res.items:
            break
        page += 1
    expected = {c.id for c in everyone if sf.company_matches(c, p, text=False)}
    assert found == expected and len(expected) < len(everyone)


def test_pack_suggests_places(pack: tuple) -> None:
    search, _ = pack
    found = search.suggest_places("par", 5)
    assert found and found[0][:2] == ("city", "Paris")
    assert search.facet_counts("city", CompanyFilters(), 3)


# ---------------------------------------------------------------- landing + chips (pages)


class KeywordSearch(FakeSearch):
    """The shared fake ignores keywords; this one honours them like the pack does."""

    def _match(self, c: Company, f: CompanyFilters) -> bool:
        if not super()._match(c, f):
            return False
        cols = KEYWORD_FIELDS.get(f.keyword_fields, KEYWORD_FIELDS["all"])
        hay = " ".join(v for col in cols if (v := getattr(c, col, None))).lower()
        hits = [k.lower() in hay for k in f.keywords]
        if hits and not (all(hits) if f.keyword_mode == "all" else any(hits)):
            return False
        return not any(k.lower() in hay for k in f.exclude_keywords)


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    from pepite.store import Store

    app = make_app(tmp_path)
    app.state.search = KeywordSearch()
    app.state.store = Store(f"sqlite:///{tmp_path / 't.db'}")
    search, store = app.state.search, app.state.store
    rows = [
        ("Ann One", "CEO", 0, Seniority.C_SUITE),
        ("Bob Two", "Head of Sales", 1, Seniority.HEAD),
        ("Cy Three", "Senior Software Engineer", 2, Seniority.SENIOR),
        ("Di Four", "Marketing Manager", 3, Seniority.MANAGER),
        ("Ed Five", "Head of Sales Intern", 4, Seniority.INTERN),
    ]
    for name, title, i, level in rows:
        person = sample_person(search.companies[i], name, title)
        person.seniority = level
        store.save_person(person)
    store.save_email(
        sample_person(search.companies[0], "Ann One").id, sample_email("ann@x.example")
    )
    return app


PEOPLE = ("Ann One", "Bob Two", "Cy Three", "Di Four", "Ed Five")


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


def listed(client: TestClient, query: str) -> set[str]:
    html = client.get("/people" + query).text
    body = html[html.index('class="results"') :]
    return names_in(body, *PEOPLE)


def test_landing_replaces_the_table_until_something_is_asked(client: TestClient) -> None:
    html = client.get("/people").text
    assert "Quick filters" in html and "data-ai-box" in html and "data-ai-input" in html
    assert "<table" not in html and "Unlock" not in html and "View plans" not in html
    assert 'href="/people?all=1"' in html and "Show all people" in html
    assert 'href="/people?status=valid"' in html and 'href="/people?country=FR"' in html
    assert re.search(r'href="/people\?title=[^"]+"', html)
    assert "PEPITE_LLM_URL" in html  # how to use your own model
    full = client.get("/people?all=1").text
    assert "<table" in full and "Quick filters" not in full and all(n in full for n in PEOPLE)
    assert 'name="all" value="1"' in full  # sorting or paging keeps the results view
    for query in ("?title=ceo", "?q=ann", "?tab=saved", "?sort=name"):
        assert "Quick filters" not in client.get("/people" + query).text, query
    # the Total tab always opens the results
    assert 'href="/people?all=1"' in client.get("/people?tab=saved").text
    companies = client.get("/companies").text
    assert "Quick filters" in companies and "Employee count" in companies
    assert 'href="/companies?size=1-10"' in companies and "<table" not in companies


def test_empty_store_keeps_its_own_empty_state(tmp_path: Path) -> None:
    html = TestClient(make_app(tmp_path)).get("/people").text
    assert "No people yet" in html and "Quick filters" not in html


def test_active_filters_show_a_chip_row_with_remove_links(client: TestClient) -> None:
    html = client.get("/people?title=head&xtitle=intern&seniority=head").text
    start = html.index('class="fpanel')
    panel = html[start : html.index("</aside>", start)]
    group = panel[panel.index('data-fgroup="titles"') :]
    assert 'class="fgroup sf-g active"' in panel and "sf-dot" in group
    assert "Titles:" in group and "Not titles:" in group and "Level:" in group
    assert 'href="/people?xtitle=intern&amp;seniority=head"' in group  # removes "head" only
    assert listed(client, "?title=head&xtitle=intern&seniority=head") == {"Bob Two"}


# ---------------------------------------------------------------- job titles


def test_simple_titles_exact_match_and_similar(client: TestClient) -> None:
    assert listed(client, "?title=head of sales") == {"Bob Two", "Ed Five"}
    assert listed(client, '?title="head of sales"') == {"Bob Two"}  # quotes: exact title
    assert listed(client, "?title=chief executive officer") == {"Ann One"}  # similar: CEO
    assert listed(client, "?title=chief executive officer&similar=0") == set()
    html = client.get("/people?title=head").text
    assert "Simple" in html and "Advanced" in html and "Also match similar titles" in html
    assert 'data-suggest="/search/suggest?kind=people&amp;field=title"' in html
    assert escape(sf.DISABLED_REASONS["past_titles"]) in html  # Past job titles: says why


def test_advanced_expression_filters_and_reports_errors(client: TestClient) -> None:
    q = "?tmode=advanced&texpr=" + "head AND (sales OR growth) NOT intern".replace(" ", "+")
    assert listed(client, q) == {"Bob Two"}
    html = client.get("/people" + q).text
    assert 'name="texpr"' in html and "Expression:" in html
    bad = client.get("/people?tmode=advanced&texpr=head+AND+(sales").text
    assert "A bracket is not closed" in bad and "ignored until it is fixed" in bad
    assert listed(client, "?tmode=advanced&texpr=head+AND+(sales") == set(PEOPLE)


def test_management_level_and_departments(client: TestClient) -> None:
    assert listed(client, "?seniority=head&seniority=c_suite") == {"Bob Two", "Ann One"}
    assert listed(client, "?dept=sales") == {"Bob Two", "Ed Five"}
    assert listed(client, "?dept=engineering&dept=marketing") == {"Cy Three", "Di Four"}
    assert listed(client, "?dept=leadership") == {"Ann One"}
    html = client.get("/people?dept=sales").text
    assert "Management level" in html and "Departments" in html and "Department:" in html


def test_personas_save_apply_and_delete(client: TestClient) -> None:
    html = client.get("/people").text
    assert 'aria-disabled="true"' in html and "Save as new persona" in html  # nothing to save
    html = client.get("/people?title=head&xtitle=intern").text
    assert 'data-open-dialog="persona-dialog"' in html and 'id="persona-dialog"' in html
    form = {"name": "Sales heads", "title": ["head"], "xtitle": ["intern"], "next": "/people"}
    r = client.post("/personas", data=form, headers={**SAME, "HX-Request": "true"})
    assert r.status_code == 200 and "Saved persona Sales heads" in r.headers["HX-Trigger"]
    assert json.loads(r.headers["HX-Location"])["path"] == "/people"
    html = client.get("/people?all=1").text
    m = re.search(r'name="persona" value="(\w+)"', html)
    assert m and "Sales heads" in html and "head · not intern" in html
    pid = m.group(1)
    assert listed(client, f"?persona={pid}") == {"Bob Two"}
    assert "Persona:" in client.get(f"/people?persona={pid}").text
    r = client.post(
        f"/personas/{pid}/delete", data={"next": f"/people?persona={pid}&all=1"}, headers=SAME
    )
    assert r.status_code == 303 and "persona=" not in r.headers["location"]
    assert "Sales heads" not in client.get("/people?all=1").text
    # a deleted persona no longer narrows anything
    assert listed(client, f"?persona={pid}&all=1") == set(PEOPLE)


def test_persona_validation_and_cross_site_posts(client: TestClient) -> None:
    r = client.post("/personas", data={"name": "", "title": ["x"]}, headers=SAME)
    assert r.status_code == 303 and "name" in r.headers["location"].lower()
    r = client.post("/personas", data={"name": "Empty"}, headers=SAME)
    assert "job+title" in r.headers["location"].lower()
    r = client.post(
        "/personas", data={"name": "Bad", "tmode": "advanced", "texpr": "a AND"}, headers=SAME
    )
    assert "problem" in r.headers["location"]
    bad = {"origin": "http://evil.example"}
    assert (
        client.post("/personas", data={"name": "x", "title": ["y"]}, headers=bad).status_code == 403
    )


# ---------------------------------------------------------------- location + industry


def test_location_places_and_exclusions(client: TestClient) -> None:
    # fake companies: 0 Lyon FR, 1 Hamburg DE, 2 Berlin DE, 3 Paris FR, 4 Leeds GB
    assert listed(client, "?city=Lyon&city=leeds") == {"Ann One", "Ed Five"}
    assert listed(client, "?country=DE&city=Paris") == {"Bob Two", "Cy Three", "Di Four"}
    assert listed(client, "?place_add=Germany") == {"Bob Two", "Cy Three"}
    assert listed(client, "?place_add=Lyon") == {"Ann One"}
    assert listed(client, "?country=FR&xcity=Paris") == {"Ann One"}
    html = client.get("/people?city=Lyon&xplace=Paris").text
    assert "Places:" in html and "Not in:" in html
    assert escape(sf.DISABLED_REASONS["contact_location"]) in html  # Contact tab: says why
    assert escape(sf.DISABLED_REASONS["radius"]) in html  # postal-code radius: says why
    companies = client.get("/companies?city=Berlin").text
    assert "Brücke Software" in companies and "Nordwind Data" not in companies


def test_industry_keywords_any_all_type_and_exclude(client: TestClient) -> None:
    def cos(query: str) -> set[str]:
        html = client.get("/companies" + query).text
        body = html[html.index('class="results"') :]
        return names_in(
            body, "Atelier Nuage", "Nordwind Data", "Brücke Software", "Moss and Pine", "Kestrel"
        )

    assert cos("?keyword=data&keyword=software") == {"Nordwind Data", "Brücke Software"}
    assert cos("?keyword=nordwind&keyword=data&kwall=1") == {"Nordwind Data"}
    assert cos("?keyword=manufacturing&kwfield=name") == set()
    assert cos("?keyword=manufacturing&kwfield=industry") == {"Kestrel"}
    assert cos("?industry=J&xkeyword=data") == {"Atelier Nuage", "Brücke Software"}
    assert cos("?industry_add=Manufacturing") == {"Kestrel"}
    html = client.get("/companies?keyword=data&kwall=1&xkeyword=x").text
    for text in ("Advanced settings", "Keyword type", "Match all keywords", "Exclude keywords"):
        assert text in html, text
    assert "All of:" in html and "Not keywords:" in html


# ---------------------------------------------------------------- All filters window


def test_all_filters_window_has_four_columns_and_drag_handles(client: TestClient) -> None:
    html = client.get("/people?all=1").text
    dialog = html[
        html.index('id="allf-dialog"') : html.index("</dialog>", html.index('id="allf-dialog"'))
    ]
    for box in ("pinned", "person", "company", "engage"):
        assert f'data-flist="{box}"' in dialog, box
    assert dialog.count('class="sf-drag"') >= 10 and 'value="engage"' in dialog
    for label in ("Persona", "Email status", "Sequence", "Email sent", "Replied", "Last activity"):
        assert label in dialog, label
    assert "never adds a tracking pixel" in dialog  # opens: disabled with the reason
    size = dialog[dialog.index('data-fgroup="size"') :]
    assert "Predefined ranges" in size and "Custom range" in size and 'name="size"' in size
    start = html.index('class="fpanel')
    panel = html[start : html.index("</aside>", start)]
    assert 'data-pin-defaults="titles,plook,company,location,industry"' in panel
    companies = client.get("/companies?all=1").text
    assert "Engagement is tracked per person" in companies


def test_count_endpoint_wording(client: TestClient) -> None:
    one = client.get("/search/count?kind=people&title=%22head of sales%22").text
    assert re.search(r">1</span> record found", one)
    many = client.get("/search/count?kind=people").text
    assert "records found" in many


# ---------------------------------------------------------------- suggestions


def test_suggest_titles_places_and_industries(client: TestClient) -> None:
    items = client.get("/search/suggest?kind=people&field=title&q=hea").json()["items"]
    values = [i["value"] for i in items]
    assert set(values[:2]) == {"Head of Sales", "Head of Sales Intern"}
    assert "Head of Marketing" in values  # common titles come after your own
    assert items[0]["hint"] == "1 person" and items[0]["name"] == "title"
    ex = client.get("/search/suggest?kind=people&field=xtitle&q=int").json()["items"]
    assert ex and all(i["name"] == "xtitle" for i in ex)
    places = client.get("/search/suggest?kind=companies&field=place&q=ger").json()["items"]
    assert places[0] == {**places[0], "name": "country", "value": "DE", "label": "Germany"}
    cities = client.get("/search/suggest?kind=companies&field=xplace&q=ham").json()["items"]
    assert any(i["name"] == "xcity" and i["value"] == "Hamburg" for i in cities)
    ind = client.get("/search/suggest?kind=companies&field=industry&q=manu").json()["items"]
    assert ind[0]["value"] == "C" and ind[0]["label"] == "Manufacturing"
    assert client.get("/search/suggest?kind=people&field=bogus").status_code == 400


# ---------------------------------------------------------------- engagement (People)


def test_engagement_filters(app: FastAPI, client: TestClient) -> None:
    from pepite.store.engage import repo_for
    from pepite.store.pipeline import ActivityRepo, Pipeline

    store, search = app.state.store, app.state.search
    ann = sample_person(search.companies[0], "Ann One").id
    bob = sample_person(search.companies[1], "Bob Two").id
    repo = repo_for(store)
    seq = repo.create_sequence("Spring outreach")
    repo.add_enrollment(seq, ann, "ann@x.example")
    repo.add_message(direction="out", status="sent", person_id=ann, to_email="ann@x.example")
    repo.add_message(direction="out", status="sent", person_id=bob, replied_at=datetime.now(UTC))
    ActivityRepo(store.engine).add("note", "Met at a fair", person_id=bob)
    Pipeline(store.engine).create_meeting(
        "Intro", datetime.now() + timedelta(days=2), person_id=ann
    )
    assert listed(client, f"?seq={seq}") == {"Ann One"}
    assert listed(client, "?seq=none") == {"Bob Two", "Cy Three", "Di Four", "Ed Five"}
    assert listed(client, "?emailed=yes") == {"Ann One", "Bob Two"}
    assert listed(client, "?emailed=no") == {"Cy Three", "Di Four", "Ed Five"}
    assert listed(client, "?replied=yes") == {"Bob Two"}
    assert listed(client, "?activity=7") == {"Bob Two"}
    assert listed(client, "?activity=none") == {"Ann One", "Cy Three", "Di Four", "Ed Five"}
    assert listed(client, "?meeting=yes") == {"Ann One"}
    html = client.get(f"/people?seq={seq}").text
    assert "Spring outreach" in html and "Sequence:" in html


def test_engagement_context_survives_missing_tables() -> None:
    ctx = sf.PeopleContext()
    sf.load_engagement(SimpleNamespace(engine=None), {"emailed": "yes"}, ctx)
    assert not sf.engagement_matches("pe_x", {"emailed": "yes"}, ctx)
    assert sf.engagement_matches("pe_x", {"emailed": "no"}, ctx)


# ---------------------------------------------------------------- client code is wired


def test_pages_load_the_filter_assets() -> None:
    from pepite.web.deps import STATIC_DIR

    js = (STATIC_DIR / "js" / "search-filters.js").read_text()
    assert '"lc-pins-" + kind()' in js and "/search/suggest" not in js  # urls come from markup
    assert "pointerdown" in js and "ArrowUp" in js  # drag handles and keyboard reorder
    css = (STATIC_DIR / "css" / "search-filters.css").read_text()
    assert ".sf-landing" in css and ".sf-cols" in css
    for name in ("people.html", "companies.html"):
        tpl = (STATIC_DIR.parent / "templates" / name).read_text()
        assert "search-filters.css" in tpl and "search-filters.js" in tpl, name
