"""Results and records parity (v0.3): saved-view menu, Search settings drawer and field
editor, selection bar, save window, company drawer, person page, lists start screen.

Uses the real app (create_app) with a real SQLite store, fake company search and fake jobs.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.models import EmailStatus, Seniority
from leadcommons.store import Store
from leadcommons.web import results
from leadcommons.web.app import create_app
from leadcommons.web.deps import STATIC_DIR, TEMPLATES_DIR
from leadcommons.web.pages import insight_phone
from tests.fakes import FakeJobs, FakeSearch, sample_email, sample_person
from tests.insights_samples import sample_insights

HX = {"HX-Request": "true"}


def settings(tmp_path: Path, mode: str = "local") -> Settings:
    return Settings(
        home=tmp_path,
        data_dir=tmp_path / "packs",
        database_url="sqlite://",
        mode=mode,
        password=None,
        password_hash=None,
    )


class World:
    def __init__(self, tmp_path: Path, mode: str = "local") -> None:
        self.store = Store(f"sqlite:///{tmp_path / 'lc.db'}")
        self.search = FakeSearch()
        self.jobs = FakeJobs()
        self.app = create_app(
            settings(tmp_path, mode), search=self.search, store=self.store, jobs=self.jobs
        )
        self.client = TestClient(self.app, follow_redirects=False)
        co = {c.name: c for c in self.search.companies}
        self.atelier, self.nordwind = co["Atelier Nuage"], co["Nordwind Data"]
        self.claire = sample_person(self.atelier, "Claire Martin", "CEO")
        self.claire.seniority = Seniority.C_SUITE
        self.jonas = sample_person(self.nordwind, "Jonas Weber", "Head of Sales")
        self.jonas.seniority = Seniority.HEAD
        self.noemail = sample_person(self.nordwind, "Ina Vogel", "Engineer")
        for p in (self.claire, self.jonas, self.noemail):
            self.store.save_person(p)
        self.store.save_email(self.claire.id, sample_email("claire.martin@ateliernuage.example"))
        self.store.save_email(
            self.jonas.id, sample_email("jonas@nordwind.example", EmailStatus.CATCH_ALL, 55)
        )

    def kinds(self) -> list[str]:
        return [j.kind for j in self.jobs.jobs.values()]


@pytest.fixture
def w(tmp_path: Path) -> World:
    return World(tmp_path)


@pytest.fixture
def demo(tmp_path: Path) -> World:
    return World(tmp_path, "demo")


def trigger(r) -> dict:
    return json.loads(r.headers.get("HX-Trigger", "{}"))


# ---------------------------------------------------------------- saved-view menu (11)


def test_view_menu_has_tabs_system_rows_and_create_button(w: World) -> None:
    html = w.client.get("/people?all=1").text
    menu = html[html.index("viewmenu") : html.index("data-toggle-filters")]
    for tab in ('data-vtab="all"', 'data-vtab="yours"', 'data-vtab="starred"'):
        assert tab in menu
    shared = re.search(r'<span class="vtab off"[^>]*>Shared</span>', menu)
    assert shared and 'aria-disabled="true"' in shared.group(0) and "data-tip=" in shared.group(0)
    assert "one person" in menu  # the reason Shared is off
    assert menu.count(">System</span>") == len(results.SYSTEM_VIEWS["people"])
    assert "Create saved search" in menu and 'data-open-dialog="savesearch-dialog"' in menu
    # Default view is the one in use and links to the results table, not the landing
    assert re.search(r'class="vitem on"[^>]*>\s*<a class="vlink" href="/people\?all=1"', menu)


def test_saved_view_rename_duplicate_star_delete(w: World) -> None:
    vid = w.store.save_search("Founders", "people", json.dumps({"query": "title=Founder"}))
    html = w.client.get("/people?title=Founder").text
    assert 'data-view-rename="' + vid + '"' in html and f"/searches/{vid}/star" in html
    assert re.search(r'class="vitem on"[^>]*data-system="0"', html)

    r = w.client.post(
        f"/searches/{vid}/rename", data={"kind": "people", "name": "CEOs"}, headers=HX
    )
    assert r.status_code == 200 and "Renamed" in trigger(r)["toast"]["message"]
    assert w.store.saved_searches("people")[0].name == "CEOs"

    r = w.client.post(f"/searches/{vid}/star", data={"kind": "people", "on": "1"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "ok"
    payload = results.view_payload(w.store.saved_searches("people")[0].filters_json)
    assert payload == {"query": "title=Founder", "starred": True}
    page = w.client.get("/people?all=1").text
    assert 'data-starred="1"' in page and "Remove star" in page

    r = w.client.post(f"/searches/{vid}/duplicate", data={"kind": "people"}, headers=HX)
    names = sorted(s.name for s in w.store.saved_searches("people"))
    assert names == ["CEOs", "CEOs (copy)"] and "copy" in trigger(r)["toast"]["message"]

    r = w.client.post(f"/searches/{vid}/delete", data={"kind": "people"}, headers=HX)
    assert [s.name for s in w.store.saved_searches("people")] == ["CEOs (copy)"]
    r = w.client.post(f"/searches/{vid}/delete", data={"kind": "people"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "error"


def test_system_views_duplicate_but_refuse_rename_star_delete(w: World) -> None:
    r = w.client.post("/searches/system:valid/duplicate", data={"kind": "people"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "ok"
    copy = w.store.saved_searches("people")[0]
    assert copy.name == "People with a valid email (copy)"
    assert results.view_payload(copy.filters_json)["query"] == "status=valid"
    for action in ("rename", "star", "delete"):
        r = w.client.post(
            f"/searches/system:valid/{action}", data={"kind": "people", "name": "x"}, headers=HX
        )
        assert trigger(r)["toast"]["kind"] == "error"
        assert "Duplicate it" in trigger(r)["toast"]["message"]
    html = w.client.get("/people?all=1").text
    assert html.count(f'data-tip="{results.SYSTEM_LOCKED}"') >= 3 * len(
        results.SYSTEM_VIEWS["people"]
    )


def test_view_actions_work_without_javascript(w: World) -> None:
    vid = w.store.save_search("Mine", "companies", json.dumps({"query": "country=DE"}))
    html = w.client.get("/companies?all=1").text
    assert '<form id="viewact" method="post"' in html
    assert f'form="viewact" formaction="/searches/{vid}/delete"' in html
    r = w.client.post(f"/searches/{vid}/delete", data={"kind": "companies", "next": "/companies"})
    assert r.status_code == 303 and r.headers["location"].startswith("/companies?notice=")


# ---------------------------------------------------------------- Search settings (12, 13)


def test_search_settings_is_a_drawer_with_fields_filters_and_enrichment(w: World) -> None:
    html = w.client.get("/people?title=CEO").text
    drawer = html[html.index('id="col-chooser"') :]
    assert 'class="sidepanel ss-drawer"' in html and 'data-ss-view="main"' in drawer
    assert 'data-ss-go="fields"' in drawer and 'data-ss-go="filters"' in drawer
    assert "Find emails as soon as people are saved" in drawer and 'name="auto"' in drawer
    assert 'select name="per" form="f"' in drawer and 'data-pref="compact"' in drawer
    assert 'data-pref="src"' in drawer
    # applied filters with remove buttons
    filters = drawer[drawer.index('data-ss-view="filters"') :]
    assert 'aria-label="Remove CEO"' in filters and "Clear all filters" in filters
    # field editor: back arrow, list, add more fields, locked rows built by search.js
    fields = drawer[drawer.index('data-ss-view="fields"') :]
    assert 'aria-label="Back to Search settings"' in fields and "data-fld-list" in fields
    assert "Add more fields" in fields and "Reset to default" in fields
    data = json.loads(
        re.search(
            r"<script type=\"application/json\" data-fld-data>(.*?)</script>", html, re.S
        ).group(1)
    )
    keys = [c["key"] for c in data["columns"]]
    assert keys[0] == "name" and data["columns"][0]["locked"] and "phone" in keys
    assert data["icons"]["phone"] == "phone"


def test_search_settings_gear_opens_the_drawer(w: World) -> None:
    html = w.client.get("/people?all=1").text
    gear = re.search(r'<button type="button" class="btn icon-only set-btn"[^>]*>', html).group(0)
    assert 'data-open-panel="col-chooser"' in gear and 'data-panel-view="main"' in gear


def test_enrichment_mode_saves_and_queues_lookups_on_save(w: World) -> None:
    lid = w.store.create_list("Team", "people")
    ids = [w.claire.id, w.jonas.id, w.noemail.id]
    w.client.post(
        "/lists/save-to", data={"kind": "people", "list_id": lid, "ids": ids[:1]}, headers=HX
    )
    assert w.jobs.jobs == {}  # off by default
    r = w.client.post("/search-settings", data={"kind": "people", "auto": "1"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "ok"
    assert (
        'id="ss-auto" type="checkbox" name="auto" value="1" role="switch" data-auto-switch checked'
        in w.client.get("/people?all=1").text
    )
    r = w.client.post(
        "/lists/save-to", data={"kind": "people", "list_id": lid, "ids": ids}, headers=HX
    )
    # Claire has a valid email; Jonas (catch-all) and Ina (none) get a look-up
    found = sorted(j.payload["person_id"] for j in w.jobs.jobs.values() if j.kind == "find_email")
    assert found == sorted([w.jonas.id, w.noemail.id])
    assert "enrichment mode" in trigger(r)["toast"]["message"]
    w.client.post("/search-settings", data={"kind": "people", "auto": "0"}, headers=HX)
    w.jobs.jobs.clear()
    w.client.post("/lists/save-to", data={"kind": "people", "list_id": lid, "ids": ids}, headers=HX)
    assert w.jobs.jobs == {}


def test_enrichment_mode_for_companies_finds_people(w: World) -> None:
    w.client.post("/search-settings", data={"kind": "companies", "auto": "1"}, headers=HX)
    r = w.client.post(f"/companies/{w.atelier.id}/save", headers=HX)
    assert w.kinds() == ["find_people"] and "people now" in trigger(r)["toast"]["message"]


def test_enrichment_mode_is_off_in_the_demo(demo: World) -> None:
    r = demo.client.post("/search-settings", data={"kind": "people", "auto": "1"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "error"
    html = demo.client.get("/people?all=1").text
    switch = re.search(r'<input id="ss-auto"[^>]*>', html).group(0)
    assert "disabled" in switch and 'aria-describedby="ss-auto-why"' in switch
    assert 'id="ss-auto-why"' in html


# ------------------------------------------------ table, selection bar (B1, B2, 14, 17, 18)


def test_phone_column_reads_business_numbers_from_insights(w: World) -> None:
    from leadcommons.store.insights import InsightsRepo

    ins = sample_insights(w.nordwind.id, w.nordwind.domain)
    assert insight_phone(ins) == "+49401234567"
    InsightsRepo(w.store.engine).save(ins)
    html = w.client.get("/people?all=1").text
    assert '<th scope="col" data-col="phone">Phone</th>' in html
    assert 'href="tel:+49401234567"' in html  # Jonas and Ina work at Nordwind
    companies = w.client.get("/companies?all=1").text
    assert 'data-col="phone" hidden' in companies  # optional on Find companies


def test_row_actions_show_on_hover_only() -> None:
    css = (STATIC_DIR / "css" / "search.css").read_text()
    assert re.search(r"\.ract-in \{ opacity: 0;", css)
    assert "tr:hover .ract-in, tr:focus-within .ract-in" in css


def test_selection_bar_is_icon_buttons_with_short_tips(w: World) -> None:
    html = w.client.get("/people?all=1").text
    bar = html[html.index('class="bulk selbar"') :].split('<form id="bulk"')[0]
    assert 'data-open-dialog="save-dialog"' in bar and ">Save</span>" in bar
    export = re.search(
        r'<button type="submit" form="bulk" formaction="/bulk/export.csv"[^>]*>', bar
    ).group(0)
    assert 'data-tip="Export CSV"' in export and 'aria-describedby="export-what"' in export
    for tip in (
        'data-tip="Sequence"',
        'data-tip="Workflow"',
        'data-tip="Add to list"',
        'data-tip="More actions"',
    ):
        assert tip in bar
    assert 'data-open-dialog="list-dialog"' in bar and 'id="list-dialog"' in html
    css = (STATIC_DIR / "css" / "search.css").read_text()
    assert ".selbar [data-tip]:hover::after" in css and "bottom: calc(100% + 8px)" in css


def test_ai_menu_order_and_reasons(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEADCOMMONS_LLM_URL", raising=False)
    monkeypatch.delenv("LEADCOMMONS_LLM_MODEL", raising=False)
    html = w.client.get("/people?all=1").text
    sel = html[html.index("ai-dd ai-sel") :]
    sel = sel[: sel.index("</details>\n")]
    order = [
        sel.index(t)
        for t in (
            "Run a custom prompt",
            "Generate a formula column",
            "Ask the Assistant",
            "Start from a template",
        )
    ]
    assert order == sorted(order)
    assert "ai-drop" in sel and "right" in sel  # drops from the bar, aligned to the trigger
    items = re.findall(r'<button type="button" class="menu-item" data-ai-open="[a-z]+"[^>]*>', sel)
    assert items and all("disabled" not in i.replace("aria-disabled", "") for i in items)
    assert all(
        'aria-disabled="true"' in i and 'aria-describedby="ai-people-sel-why"' in i for i in items
    )
    assert 'id="ai-people-sel-why"' in sel and "LEADCOMMONS_LLM_URL" in sel
    toolbar = html[html.index('class="dd ai-dd"') :]
    assert toolbar.index("Ask the Assistant") < toolbar.index("plain words")


# ---------------------------------------------------------------- save window (15)


def summary(w: World, **data) -> str:
    base = {"kind": "people", "ids": [w.claire.id, w.jonas.id, w.noemail.id], "find": "1"}
    return w.client.post("/partials/save-summary", data={**base, **data}).text


def test_save_window_is_two_columns_with_all_choices(w: World) -> None:
    html = w.client.get("/people?all=1").text
    dlg = html[html.index('id="save-dialog"') : html.index('id="list-dialog"')]
    for part in (
        'name="find"',
        'value="emails"',
        'value="emails_phones"',
        'name="dest_list"',
        'name="dest_new"',
        'name="export"',
        "data-save-summary",
        "Stays on this computer",
        "do-not-contact list is skipped",
    ):
        assert part in dlg, part
    assert 'formaction="/bulk/save"' in dlg and "credit" not in dlg.lower()


def test_save_summary_counts_statuses_and_estimates(w: World) -> None:
    html = summary(w)
    assert 'Saving <span class="mono">3</span> of <span class="mono">3</span>' in html
    counts = dict(
        re.findall(
            r'name="keep" value="([a-z_]+)"[^>]*>.*?<span class="n">(\d+)</span>', html, re.S
        )
    )
    assert counts == {"valid": "1", "catch_all": "1", "unknown": "0", "invalid": "0", "none": "1"}
    assert 'name="keep_sent"' in html and "Email look-up for <b>2</b>" in html
    assert "Added to <b>Saved people</b>" in html and "Time estimate" in html
    narrowed = summary(w, keep_sent="1", keep=["valid"])
    assert (
        'Saving <span class="mono">1</span> of' in narrowed
        and "No email look-up needed" in narrowed
    )
    phones = summary(w, find_mode="emails_phones")
    assert "Phone search on <b>2</b> company" in phones


def test_bulk_save_lists_looks_up_and_filters(w: World) -> None:
    ids = [w.claire.id, w.jonas.id, w.noemail.id]
    r = w.client.post(
        "/bulk/save",
        data={"kind": "people", "ids": ids, "find": "1", "find_mode": "emails_phones"},
        headers=HX,
    )
    t = trigger(r)
    assert "Saved 3 people to Saved people" in t["toast"]["message"] and "lc-saved" in t
    lists = {li.name: li for li in w.store.lists("people")}
    assert lists["Saved people"].size == 3
    assert sorted(w.kinds()) == ["company_insights", "company_insights", "find_email", "find_email"]
    w.jobs.jobs.clear()
    r = w.client.post(
        "/bulk/save",
        data={
            "kind": "people",
            "ids": ids,
            "keep_sent": "1",
            "keep": ["valid", "catch_all"],
            "dest_list": "new",
            "dest_new": "Hot",
        },
        headers=HX,
    )
    assert "1 left out" in trigger(r)["toast"]["message"] and w.jobs.jobs == {}
    assert {li.name: li.size for li in w.store.lists("people")}["Hot"] == 2  # Ina has no email
    r = w.client.post(
        "/bulk/save", data={"kind": "people", "ids": ids, "keep_sent": "1"}, headers=HX
    )
    assert trigger(r)["toast"]["kind"] == "error"
    r = w.client.post(
        "/bulk/save", data={"kind": "people", "ids": ids, "dest_list": "new"}, headers=HX
    )
    assert "Name the new list" in trigger(r)["toast"]["message"]


def test_bulk_save_adds_to_a_sequence_with_engage(w: World) -> None:
    from leadcommons.store.engage import repo_for

    sid = repo_for(w.store).create_sequence("Intro", status="active")
    html = w.client.get("/people?all=1").text
    assert f'<option value="{sid}">Intro</option>' in html
    ids = [w.claire.id, w.noemail.id]
    r = w.client.post(
        "/bulk/save", data={"kind": "people", "ids": ids, "sequence_id": sid}, headers=HX
    )
    msg = trigger(r)["toast"]["message"]
    assert "Added 1 contact to Intro" in msg and "no email" in msg
    assert repo_for(w.store).enrolled_person_ids(sid) == {w.claire.id}


def test_bulk_save_exports_csv(w: World) -> None:
    ids = [w.claire.id, w.jonas.id]
    r = w.client.post("/bulk/save", data={"kind": "people", "ids": ids, "export": "1"})
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "name" and len(rows) == 3
    r = w.client.post("/bulk/save", data={"kind": "people", "ids": ids, "export": "1"}, headers=HX)
    assert "lc-export" in trigger(r)
    keep = w.client.post(
        "/bulk/export.csv",
        data={"kind": "people", "ids": ids, "keep_sent": "1", "keep": ["valid"], "apply_keep": "1"},
    )
    assert len(list(csv.reader(io.StringIO(keep.text)))) == 2
    plain = w.client.post(
        "/bulk/export.csv", data={"kind": "people", "ids": ids, "keep_sent": "1", "keep": ["valid"]}
    )
    assert (
        len(list(csv.reader(io.StringIO(plain.text)))) == 3
    )  # the plain Export ignores the window


def test_save_window_for_companies(w: World) -> None:
    ids = [w.atelier.id, w.nordwind.id]
    html = w.client.post(
        "/partials/save-summary", data={"kind": "companies", "ids": ids, "find": "1"}
    ).text
    assert "People search on <b>2</b> websites" in html and 'name="keep"' not in html
    r = w.client.post("/bulk/save", data={"kind": "companies", "ids": ids, "find": "1"}, headers=HX)
    assert "Saved 2 companies." in trigger(r)["toast"]["message"]
    assert w.store.saved_company_ids() >= set(ids) and w.kinds() == ["find_people", "find_people"]
    page = w.client.get("/companies?all=1").text
    tile = re.search(r'<div class="save-tile off"[^>]*>', page).group(0)
    assert 'aria-disabled="true"' in tile and "Sequences send emails to people" in tile


def test_save_window_lookups_are_off_in_the_demo(demo: World) -> None:
    html = demo.client.get("/people?all=1").text
    dlg = html[html.index('id="save-dialog"') : html.index('id="list-dialog"')]
    assert re.search(r'name="find" value="1"[^>]*disabled', dlg) and 'id="sv-demo-why"' in dlg
    ids = [demo.claire.id, demo.noemail.id]
    r = demo.client.post("/bulk/save", data={"kind": "people", "ids": ids, "find": "1"}, headers=HX)
    assert "off in the public demo" in trigger(r)["toast"]["message"] and demo.jobs.jobs == {}
    assert "they are off in the public demo" in summary(demo)


# ---------------------------------------------------------------- company drawer (B4)


def test_company_drawer_two_columns_tabs_and_leads(w: World) -> None:
    html = w.client.get(f"/companies/{w.nordwind.id}", headers=HX).text
    assert 'class="co-panel cx drawer-inner"' in html and 'class="cx-grid"' in html
    for tab in ('data-tab="overview"', 'data-tab="people"', 'data-tab="locations"'):
        assert tab in html
    assert "Company details" in html and f"/partials/insights?company_id={w.nordwind.id}" in html
    assert ">Save company</span>" in html and "Suggested leads" in html
    leads = html[html.index("data-leads") :]
    assert 'data-lead-tab="leaders"' in leads and 'data-lead-tab="directors"' in leads
    jonas = re.search(r'data-lead-groups="([a-z ]+)" data-text="jonas weber', leads)
    assert jonas and jonas.group(1) == "all directors"
    assert f"/companies/{w.nordwind.id}/find-people" in leads and "Find more people" in leads


def test_saved_company_shows_its_lists(w: World) -> None:
    lid = w.store.create_list("Targets", "companies")
    w.client.post(
        "/lists/save-to", data={"kind": "companies", "list_id": lid, "ids": [w.atelier.id]}
    )
    html = w.client.get(f"/companies/{w.atelier.id}", headers=HX).text
    menu = html[html.index("savedc-dd") :]
    assert (
        "Saved · 1 list" in menu
        and f'href="/lists/{lid}"' in menu
        and "Add to another list" in menu
    )
    assert "disabled" not in re.search(r'<summary class="btn saved-btn"[^>]*>', menu).group(0)


def test_company_locations_from_open_data_and_website(w: World) -> None:
    from leadcommons.store.insights import InsightsRepo

    InsightsRepo(w.store.engine).save(sample_insights(w.nordwind.id, w.nordwind.domain))
    html = w.client.get(f"/companies/{w.nordwind.id}", headers=HX).text
    locs = html[html.index('data-panel="locations"') :]
    assert "Head office" in locs and "Address on its website" in locs and "Hafenstrasse 1" in locs
    assert "Hiring here" in locs and "Remote - Europe" in locs
    bare = results.company_locations(type(w.nordwind)(id="x", name="X"), None)
    assert bare == []


def test_company_drawer_actions_disabled_in_demo_with_reason(demo: World) -> None:
    html = demo.client.get(f"/companies/{demo.nordwind.id}", headers=HX).text
    leads = html[html.index("data-leads") :]
    wrap = re.search(r'<span class="tipwrap inline" data-tip="([^"]+)"', leads)
    assert wrap and "demo" in wrap.group(1).lower()
    assert re.search(r"<button class=\"btn sm\" type=\"submit\" disabled aria-describedby=", leads)


# ---------------------------------------------------------------- person page (B3)


def test_person_page_contact_card_banner_scores_and_copy(w: World) -> None:
    html = w.client.get(f"/people/{w.claire.id}").text
    card = html[
        html.index('class="card cinfo"') : html.index(
            "</section>", html.index('class="card cinfo"')
        )
    ]
    assert 'class="dnc-banner"' in card and "Not on your do-not-contact list" in card
    assert "data-score-sel" in card and 'value="profile"' in card
    assert 'data-copy-value="claire.martin@ateliernuage.example"' in card
    assert "data-copy=" not in html  # tools.js owns [data-copy] for Assistant messages
    js = (STATIC_DIR / "js" / "search.js").read_text()
    assert 't.closest("[data-copy-value]")' in js and 't.closest("[data-copy]")' not in js


def test_insights_card_has_inline_disabled_tabs_gear_and_fold(w: World) -> None:
    html = w.client.get(f"/partials/insights?company_id={w.nordwind.id}").text
    order = [
        html.index(t)
        for t in (
            ">Overview<",
            ">News<",
            ">Technologies<",
            ">Funding<",
            ">Job postings<",
            ">Website visitors<",
        )
    ]
    assert order == sorted(order)
    for name in ("News", "Website visitors"):
        tab = re.search(rf'<span class="ins-tab-off"[^>]*>{name}</span>', html).group(0)
        assert 'aria-disabled="true"' in tab and "data-tip=" in tab and "aria-describedby" in tab
    assert "ins-gear" in html and "Refresh insights now" in html and "data-ins-fold" in html


def test_person_page_company_card_has_score_menu_and_fold(w: World) -> None:
    html = w.client.get(f"/people/{w.claire.id}").text
    card = html[html.index('class="card company-card"') :]
    assert "data-foldable" in html and "data-fold " in card and "data-fold-body" in card
    assert "cc-more" in card and "Open the company page" in card
    assert re.search(r'<span class="chip (ok|amber|red)" title="Record completeness', card)


# ---------------------------------------------------------------- import (16)


def test_import_menu_single_contact_and_csv(w: World) -> None:
    html = w.client.get("/people?all=1").text
    menu = html[html.index("imp-menu") :]
    assert 'data-open-dialog="contact-dialog"' in menu and 'href="/enrich"' in menu
    assert 'id="contact-dialog"' in html and 'action="/people/new"' in html
    companies = w.client.get("/companies?all=1").text
    single = re.search(
        r'<span class="menu-item" aria-disabled="true"[^>]*>',
        companies[companies.index("imp-menu") :],
    ).group(0)
    assert "open data packs" in single and 'id="contact-dialog"' not in companies
    r = w.client.post(
        "/people/new",
        data={
            "full_name": " Lena  Hart ",
            "domain": "https://www.Nordwind.example/team",
            "title": "CTO",
            "email": "lena@nordwind.example",
        },
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/people/pe_")
    pid = r.headers["location"].split("?")[0].rsplit("/", 1)[1]
    person = w.store.get_person(pid)
    assert person.full_name == "Lena Hart" and person.company_domain == "nordwind.example"
    assert person.company_id == w.nordwind.id and person.title == "CTO"
    assert w.store.best_email(pid).status == EmailStatus.UNKNOWN
    r = w.client.post("/people/new", data={"full_name": "X", "domain": "not a domain"}, headers=HX)
    assert trigger(r)["toast"]["kind"] == "error"
    r = w.client.post(
        "/people/new", data={"full_name": "Y", "domain": "y.example", "email": "nope"}, headers=HX
    )
    assert "does not look right" in trigger(r)["toast"]["message"]
    r = w.client.post(
        "/people/new", data={"full_name": "Zoe Ray", "domain": "zr.example"}, headers=HX
    )
    assert r.headers["HX-Redirect"].startswith("/people/pe_")


# ---------------------------------------------------------------- lists (20)


def test_lists_start_screen_on_a_fresh_home(w: World) -> None:
    html = w.client.get("/lists").text
    assert "Your lists start here" in html and 'class="lists-art"' in html
    assert 'data-create-list="people"' in html and 'data-create-list="companies"' in html
    assert "How lists work" in html
    assert 'role="tablist"' not in html.split("<main", 1)[-1].split("lists-empty", 1)[0]
    assert "</svg> Create list</button>" not in html  # no header button before the first list


# ---------------------------------------------------------------- wording guard


def test_new_templates_avoid_copied_phrases() -> None:
    text = " ".join(
        (TEMPLATES_DIR / n).read_text().lower()
        for n in (
            "_search_dialogs.html",
            "_search_save_summary.html",
            "_search_columns.html",
            "lists.html",
            "_company_drawer.html",
        )
    )
    for phrase in (
        "export selected records to csv",
        "welcome to your lists",
        "enable enrichment mode",
        "credit estimate",
        "contact ownership",
    ):
        assert phrase not in text
