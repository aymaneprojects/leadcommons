# ruff: noqa: E501
"""Workflows pages, analytics, integrations, API tokens and their auth, settings menu, palette."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pepite.config import Settings
from pepite.models import EmailStatus, utcnow
from pepite.store import Store
from pepite.store.tools import hash_token, repo_for
from pepite.web.app import create_app
from pepite.workflows import stats
from tests.fakes import FakeJobs, FakeSearch, sample_email, sample_person

HX = {"HX-Request": "true"}


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/p.db", **kw
    )
    return create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/onboarding",
        "/assistant",
        "/workflows",
        "/workflows?tab=runs",
        "/workflows/new",
        "/workflows/new?template=reply-notify",
        "/workflows/new?source=companies&query=country%3DFR",
        "/analytics",
        "/analytics?range=7",
        "/analytics?range=all",
        "/settings",
        "/settings/integrations",
        "/settings/api",
        "/partials/notifications",
        "/partials/palette?q=ate",
    ],
)
def test_every_page_renders(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, path
    assert "Apollo" not in r.text and "credits" not in r.text.lower()


def test_every_page_renders_in_demo(tmp_path: Path) -> None:
    c = TestClient(make_app(tmp_path, mode="demo"))
    for path in (
        "/",
        "/assistant",
        "/workflows",
        "/analytics",
        "/settings/integrations",
        "/settings/api",
    ):
        assert c.get(path).status_code == 200, path


def test_builder_prefills_saved_search_trigger(client: TestClient) -> None:
    html = client.get("/workflows/new?source=people&query=title%3Dceo%26country%3DDE").text
    assert 'name="trigger_type" value="schedule" data-trig checked' in html
    assert 'value="title=ceo&amp;country=DE"' in html
    assert '<option value="people" selected>' in html


# ------------------------------------------------------------------ workflow CRUD


def form(**over: Any) -> dict[str, str]:
    data = {
        "name": "Notify me",
        "trigger_type": "event",
        "event": "person_found",
        "action_type_0": "notify",
        "action_arg_0": "Found {name}",
    }
    data.update({k: str(v) for k, v in over.items()})
    return data


def test_create_edit_toggle_run_delete(app: FastAPI, client: TestClient) -> None:
    r = client.post("/workflows", data=form())
    assert r.status_code == 303
    wid = r.headers["location"].split("?")[0].rsplit("/", 1)[1]
    repo = repo_for(app.state.store)
    wf = repo.get_workflow(wid)
    assert wf and wf.enabled and wf.actions == [{"type": "notify", "message": "Found {name}"}]
    page = client.get(f"/workflows/{wid}").text
    assert "Notify me" in page and "wf-config" in page and "Run history" in page
    assert "Notify me" in client.get("/workflows").text
    # edit
    r = client.post(
        f"/workflows/{wid}",
        data=form(name="Renamed", cond_field_0="country", cond_op_0="is", cond_value_0="FR"),
    )
    assert r.status_code == 303
    wf = repo.get_workflow(wid)
    assert wf.name == "Renamed" and wf.conditions == [
        {"field": "country", "op": "is", "value": "FR"}
    ]
    # toggle
    client.post(f"/workflows/{wid}/toggle", data={"enabled": "0"})
    assert not repo.get_workflow(wid).enabled
    client.post(f"/workflows/{wid}/toggle", data={})
    assert repo.get_workflow(wid).enabled
    # run now (most recent people: there are none, so it is skipped but logged)
    r = client.post(f"/workflows/{wid}/run")
    assert r.status_code == 303 and "Ran" in r.headers["location"]
    assert len(repo.runs(wid)) == 1
    assert "Run history" in client.get("/workflows?tab=runs").text
    assert "Renamed" in client.get("/workflows?tab=runs").text
    # delete
    assert client.post(f"/workflows/{wid}/delete").status_code == 303
    assert repo.get_workflow(wid) is None and repo.runs(wid) == []


def test_invalid_workflow_rerenders_with_error(client: TestClient) -> None:
    r = client.post(
        "/workflows", data={"name": "x", "trigger_type": "event", "event": "person_found"}
    )
    assert r.status_code == 400 and "Add at least one action" in r.text
    assert 'value="x"' in r.text  # what was typed is kept
    r = client.post("/workflows", data=form(action_type_0="webhook", action_arg_0="not a url"))
    assert r.status_code == 400 and "Webhook step" in r.text


def test_schedule_workflow_gets_a_next_run(app: FastAPI, client: TestClient) -> None:
    r = client.post(
        "/workflows",
        data=form(
            trigger_type="schedule",
            source="companies",
            query="country=FR",
            frequency="daily",
            hour="9",
        ),
    )
    wid = r.headers["location"].split("?")[0].rsplit("/", 1)[1]
    wf = repo_for(app.state.store).get_workflow(wid)
    assert wf.next_run_at and wf.next_run_at > utcnow() and wf.next_run_at.hour == 9
    client.post(f"/workflows/{wid}/toggle", data={"enabled": "0"})
    assert repo_for(app.state.store).get_workflow(wid).next_run_at is None


def test_unknown_workflow_is_handled(client: TestClient) -> None:
    assert client.get("/workflows/nope").status_code == 303
    assert client.post("/workflows/nope/run", headers=HX).status_code == 200
    assert client.post("/workflows/nope/toggle", headers=HX).status_code == 200


def test_post_checks_origin(client: TestClient) -> None:
    r = client.post("/workflows", data=form(), headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert (
        client.post(
            "/settings/api", data={"name": "x"}, headers={"origin": "https://evil.example"}
        ).status_code
        == 403
    )


def test_workflows_page_in_demo_explains(tmp_path: Path) -> None:
    c = TestClient(make_app(tmp_path, mode="demo"))
    assert "public demo" in c.get("/workflows").text


# ------------------------------------------------------------------ analytics


def test_analytics_only_real_numbers(app: FastAPI, client: TestClient) -> None:
    html = client.get("/analytics").text
    assert (
        "Prospecting" in html and "Outreach" in html and "Pipeline" in html and "Automation" in html
    )
    m = re.search(r'>People found</a>\s*<b class="tile-v">(\d+)</b>', html)
    assert m and m.group(1) == "0"
    store, search = app.state.store, app.state.search
    c = search.companies[0]
    store.save_company(c)
    p = sample_person(c)
    store.save_person(p)
    store.save_email(p.id, sample_email("a@ateliernuage.example", EmailStatus.VALID))
    store.create_list("L", "people")
    data = stats.collect(repo_for(store).engine, "30")
    assert data["people_found"] == 1 and data["emails_found"] == 1 and data["lists_total"] == 1
    assert data["email_status"] == {"valid": 1} and data["companies_saved"] == 1
    assert sum(v for _, v in data["people_series"]) == 1 and len(data["people_series"]) == 31
    html = client.get("/analytics?range=7").text
    assert "Last 7 days" in html and "<svg" in html and 'role="img"' in html


def test_analytics_pipeline_and_outreach_numbers(app: FastAPI, client: TestClient) -> None:
    eng = repo_for(app.state.store).engine
    from sqlalchemy.orm import Session

    from pepite.store import engage as e
    from pepite.store import pipeline as pl

    now = utcnow()
    with Session(eng) as db:
        db.add_all(
            [
                pl.DealStageRow(
                    id="s1", name="Lead", position=0, kind="open", probability=10, archived=False
                ),
                pl.DealStageRow(
                    id="s2", name="Won", position=1, kind="won", probability=100, archived=False
                ),
                pl.DealRow(id="d1", name="A", stage_id="s1", amount=1000, currency="EUR"),
                pl.DealRow(id="d2", name="B", stage_id="s2", amount=500, currency="EUR"),
                e.SequenceRow(id="q1", name="S", status="active"),
            ]
        )
        db.commit()
        db.add_all(
            [
                e.EnrollmentRow(
                    sequence_id="q1", person_id="p0", email="a@b.example", status="replied"
                ),
            ]
        )
        for i, st in enumerate(("sent", "sent", "bounced")):
            db.add(e.MessageRow(direction="out", status=st, person_id=f"p{i}", sent_at=now))
        db.commit()
    d = stats.collect(eng, "30")
    assert d["deals_total"] == 2 and d["deals_value"] == 1500 and d["deals_won_value"] == 500
    assert [x["count"] for x in d["deals"]] == [1, 1]
    seq = d["sequences"]
    assert (
        seq["sent"] == 3 and seq["bounced"] == 1 and seq["replied"] == 1 and seq["contacted"] == 3
    )
    assert seq["reply_rate"] == 33.3 and seq["bounce_rate"] == 33.3 and seq["unsub_rate"] == 0.0
    html = client.get("/analytics").text
    assert (
        "Reply rate" in html
        and "33.3%" in html
        and "EUR 1,500" in html
        and "Deals by stage" in html
    )


def test_analytics_missing_tables_show_na(tmp_path: Path) -> None:
    from sqlalchemy import create_engine

    d = stats.collect(create_engine("sqlite://"), "30")
    assert d["people_found"] is None and d["sequences"] is None and d["deals"] is None
    assert stats.parse_range("bogus") == "30" and stats.since_for("all") is None


def test_svg_charts_are_escaped() -> None:
    svg = str(stats.svg_bars([("<script>x</script>", 3, "")], "t"))
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    assert stats.svg_line([], "x") == ""


# ------------------------------------------------------------------ integrations and webhooks


def test_webhook_crud_and_test_button(app: FastAPI, client: TestClient) -> None:
    r = client.post(
        "/settings/integrations/webhooks", data={"url": "nope", "events": "person_found"}
    )
    assert "http" in r.headers["location"]
    r = client.post("/settings/integrations/webhooks", data={"url": "https://hooks.test/x"})
    assert "at least one event" in r.headers["location"].replace("+", " ")
    r = client.post(
        "/settings/integrations/webhooks",
        data={"url": "https://hooks.test/x", "events": ["person_found", "bogus", "email_sent"]},
    )
    assert r.status_code == 303
    repo = repo_for(app.state.store)
    (hook,) = repo.webhooks()
    assert (
        hook.events == ["person_found", "email_sent"]
        and hook.secret.startswith("whsec_")
        and hook.enabled
    )
    html = client.get("/settings/integrations").text
    assert "https://hooks.test/x" in html and hook.secret in html and "Send test" in html
    assert "claude mcp add pepite" in html and "/api/docs" in html and "docs/mcp.md" in html
    with respx.mock() as mock:
        route = mock.post("https://hooks.test/x").respond(200)
        r = client.post(f"/settings/integrations/webhooks/{hook.id}/test")
    assert route.called and "arrived" in r.headers["location"]
    assert route.calls[0].request.headers["X-Pepite-Event"] == "ping"
    assert "X-Pepite-Signature" in route.calls[0].request.headers
    assert repo.deliveries(hook.id)[0].ok
    with respx.mock() as mock:
        mock.post("https://hooks.test/x").respond(500)
        r = client.post(f"/settings/integrations/webhooks/{hook.id}/test")
    assert "failed" in r.headers["location"] and not repo.deliveries(hook.id)[0].ok
    assert "Delivery log" in client.get("/settings/integrations").text
    old = hook.secret
    client.post(f"/settings/integrations/webhooks/{hook.id}/rotate")
    assert repo.get_webhook(hook.id).secret != old
    client.post(
        f"/settings/integrations/webhooks/{hook.id}",
        data={"url": "https://hooks.test/y", "events": "email_found"},
    )
    h2 = repo.get_webhook(hook.id)
    assert h2.url == "https://hooks.test/y" and h2.events == ["email_found"] and not h2.enabled
    client.post(f"/settings/integrations/webhooks/{hook.id}/delete")
    assert repo.webhooks() == [] and repo.deliveries() == []


def test_webhook_test_button_refused_in_demo(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo")
    c = TestClient(app, follow_redirects=False)
    hid = repo_for(app.state.store).create_webhook("https://hooks.test/x", ["person_found"])
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://hooks.test/x").respond(200)
        r = c.post(f"/settings/integrations/webhooks/{hid}/test")
    assert not route.called and "demo" in r.headers["location"]


# ------------------------------------------------------------------ API tokens


def test_token_lifecycle_stores_only_hashes(app: FastAPI, client: TestClient) -> None:
    r = client.post("/settings/api", data={"name": ""}, headers=HX)
    assert r.status_code == 200 and not repo_for(app.state.store).tokens()
    r = client.post("/settings/api", data={"name": "Zapier"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    token = re.search(r'id="new-token">(lc_[A-Za-z0-9_-]+)<', r.text).group(1)
    page = client.get("/settings/api").text
    assert token not in page and "Zapier" in page and token[:8] in page  # shown once, prefix kept
    repo = repo_for(app.state.store)
    (rec,) = repo.tokens()
    assert rec.prefix == token[:8]
    from sqlalchemy import text

    with repo.engine.connect() as conn:
        stored = conn.execute(text("SELECT hash, name, prefix FROM api_tokens")).one()
    assert stored[0] == hash_token(token) and token not in "".join(map(str, stored))
    assert (
        repo.verify_token(token)
        and not repo.verify_token("lc_wrong")
        and not repo.verify_token("x")
    )
    assert repo.tokens()[0].last_used_at is not None
    assert client.post(f"/settings/api/{rec.id}/revoke").status_code == 303
    assert not repo.verify_token(token) and "Revoked" in client.get("/settings/api").text


def test_tokens_open_the_api_and_mcp_in_server_mode(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="server", password="pw")
    c = TestClient(app, follow_redirects=False)
    assert c.get("/api/companies").status_code == 401
    plain, rec = repo_for(app.state.store).create_token("t")
    ok = c.get("/api/companies", headers={"Authorization": f"Bearer {plain}"})
    assert ok.status_code == 200 and ok.json()["total"] >= 1
    assert c.get("/api/companies", headers={"Authorization": "Bearer lc_nope"}).status_code == 401
    assert c.get("/api/companies", headers={"Authorization": f"Basic {plain}"}).status_code == 401
    # tokens do not open the HTML pages
    assert c.get("/settings/api", headers={"Authorization": f"Bearer {plain}"}).status_code == 303
    assert c.get("/people", headers={"Authorization": f"Bearer {plain}"}).status_code == 303
    # /mcp is not mounted here, but it gets past the login with a valid token (404, not 303/401)
    assert c.get("/mcp", headers={"Authorization": f"Bearer {plain}"}).status_code == 404
    assert c.get("/mcp").status_code == 303
    repo_for(app.state.store).revoke_token(rec.id)
    assert c.get("/api/companies", headers={"Authorization": f"Bearer {plain}"}).status_code == 401


def test_public_unsubscribe_prefix_needs_no_login(tmp_path: Path) -> None:
    from pepite.web.auth import PUBLIC_PREFIXES

    assert "/u/" in PUBLIC_PREFIXES
    app = make_app(tmp_path, mode="server", password="pw")
    c = TestClient(app, follow_redirects=False, raise_server_exceptions=False)
    r = c.get("/u/some-token")
    assert r.status_code != 303  # not redirected to /login (404 until engage mounts the page)


# ------------------------------------------------------------------ settings, palette, notifications


def test_settings_menu_has_every_section_grouped(client: TestClient) -> None:
    html = client.get("/settings/integrations").text
    for label in (
        "Workspace overview",
        "Account",
        "Mailboxes",
        "Sending rules",
        "Email setup and health",
        "Data packs",
        "Unsubscribes &amp; opt-out",
        "Deal stages",
        "Integrations",
        "API tokens",
        "About",
    ):
        assert label in html, label
    for group in ("Personal", "Outbound", "Data management", "Integrations"):
        assert f"{group}<svg" in html
    assert "data-settings-find" in html and "Search settings" in html
    assert 'aria-current="page"' in html


def test_settings_overview_links_new_sections(client: TestClient) -> None:
    html = client.get("/settings").text
    for href in (
        "/settings/mailboxes",
        "/settings/sending",
        "/settings/deal-stages",
        "/settings/integrations",
        "/settings/api",
        "/workflows",
    ):
        assert f'href="{href}"' in html, href


def test_palette_lists_new_pages_and_workflows(app: FastAPI, client: TestClient) -> None:
    html = client.get("/").text
    for label in (
        "Workflows",
        "Analytics",
        "Integrations",
        "API tokens",
        "Create a workflow",
        "Build your target audience",
    ):
        assert label in html
    repo_for(app.state.store).create_workflow(
        "Nurture FR", {"type": "manual"}, [], [{"type": "notify"}]
    )
    assert "Nurture FR" in client.get("/partials/palette?q=nurt").text
    assert "Nurture FR" not in client.get("/partials/palette?q=zzz").text


def test_notifications_include_workflow_runs_replies_and_activities(
    app: FastAPI, client: TestClient
) -> None:
    from pepite.store.pipeline import ActivityRepo

    repo = repo_for(app.state.store)
    wid = repo.create_workflow("Notify", {"type": "manual"}, [], [{"type": "notify"}])
    repo.add_run(wid, "manual", "failed", "All 1 steps failed.", [], utcnow())
    repo.add_notification("Replied: Claire", "", "/people/x")
    ActivityRepo(app.state.store.engine).add("email_reply", "Claire Martin replied", person_id="p1")
    ActivityRepo(app.state.store.engine).add("note", "A note", person_id="p1")
    html = client.get("/partials/notifications").text
    assert "Workflow “Notify” failed" in html and "/workflows/" in html
    assert "Replied: Claire" in html and "New reply" in html
    assert 'data-notif-panel="activities"' in html and "A note" in html
    assert "data-notif-dismiss" in html and "Today" in html


def test_fallback_pages_exist_for_sections_nobody_provides(client: TestClient) -> None:
    # sequences is left out: it is engage's page and may still be in progress in this tree
    for slug in ("emails", "calls", "tasks", "meetings", "conversations", "deals"):
        assert client.get(f"/{slug}").status_code in (200, 302, 303), slug
