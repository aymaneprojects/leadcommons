"""Workflow engine: conditions, actions (contracts monkeypatched), failure isolation, scheduler,
webhooks. Uses a real Store (SQLite) so the repository and run log are exercised."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leadcommons import engage, workflows
from leadcommons.config import Settings
from leadcommons.models import EmailStatus, Seniority, utcnow
from leadcommons.store import Store
from leadcommons.store.tools import repo_for
from leadcommons.web.app import create_app
from leadcommons.workflows import engine, scheduler, webhooks
from tests.fakes import FakeJobs, FakeSearch, sample_email, sample_person


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/w.db", **kw
    )
    return create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path, sync_jobs=True)


@pytest.fixture
def world(app: FastAPI) -> dict[str, Any]:
    search, store = app.state.search, app.state.store
    atelier, nordwind = search.companies[0], search.companies[1]  # FR 11-50, DE 51-200
    claire = sample_person(atelier, "Claire Martin", "CEO")
    claire.seniority = Seniority.C_SUITE
    jonas = sample_person(nordwind, "Jonas Weber", "Head of Sales")
    for c in (atelier, nordwind):
        store.save_company(c)
    for p in (claire, jonas):
        store.save_person(p)
    store.save_email(claire.id, sample_email("claire.martin@ateliernuage.example"))
    store.save_email(jonas.id, sample_email("jonas@nordwind.example", EmailStatus.CATCH_ALL, 55))
    return {"claire": claire, "jonas": jonas, "atelier": atelier, "nordwind": nordwind}


def add(
    app: FastAPI, name: str, trigger: dict, actions: list, conditions: list | None = None
) -> str:
    return repo_for(app.state.store).create_workflow(name, trigger, conditions or [], actions)


EVENT = {"type": "event", "event": "person_found"}


def last_run(app: FastAPI, wid: str) -> Any:
    runs = repo_for(app.state.store).runs(wid, 5)
    return runs[0] if runs else None


# ------------------------------------------------------------------ conditions


@pytest.mark.parametrize(
    ("conds", "claire_match", "jonas_match"),
    [
        ([{"field": "country", "op": "is", "value": "FR"}], True, False),
        ([{"field": "country", "op": "is", "value": "Germany"}], False, True),
        ([{"field": "country", "op": "is_not", "value": "FR"}], False, True),
        ([{"field": "size", "op": "is", "value": "11-50"}], True, False),
        ([{"field": "title", "op": "contains", "value": "sales"}], False, True),
        ([{"field": "title", "op": "not_contains", "value": "sales"}], True, False),
        ([{"field": "seniority", "op": "is", "value": "c_suite"}], True, False),
        ([{"field": "email_status", "op": "is", "value": "catch_all"}], False, True),
        ([{"field": "industry", "op": "is", "value": "J"}], True, True),
        ([{"field": "domain", "op": "contains", "value": "nordwind"}], False, True),
        (
            [
                {"field": "country", "op": "is", "value": "DE"},
                {"field": "title", "op": "contains", "value": "ceo"},
            ],
            False,
            False,
        ),
    ],
)
def test_conditions(app: FastAPI, world: dict, conds: list, claire_match: bool, jonas_match: bool):
    ctx = engine.make_ctx(app)
    for key, want in (("claire", claire_match), ("jonas", jonas_match)):
        subject = engine.person_subject(ctx, world[key].id, {})
        assert subject is not None
        assert engine.conditions_met(ctx, subject, conds) is want


def test_list_membership_condition(app: FastAPI, world: dict) -> None:
    store = app.state.store
    lid = store.create_list("VIP", "people")
    store.add_to_list(lid, [world["claire"].id])
    ctx = engine.make_ctx(app)
    cs = engine.person_subject(ctx, world["claire"].id, {})
    js = engine.person_subject(ctx, world["jonas"].id, {})
    assert engine.conditions_met(ctx, cs, [{"field": "list", "op": "in_list", "value": lid}])
    assert not engine.conditions_met(ctx, js, [{"field": "list", "op": "in_list", "value": lid}])
    assert engine.conditions_met(ctx, js, [{"field": "list", "op": "not_in_list", "value": lid}])


def test_unknown_condition_field_never_matches(app: FastAPI, world: dict) -> None:
    ctx = engine.make_ctx(app)
    s = engine.person_subject(ctx, world["claire"].id, {})
    assert not engine.conditions_met(ctx, s, [{"field": "bogus", "op": "is", "value": "x"}])


# ------------------------------------------------------------------ actions through emit


def test_emit_adds_matching_people_to_list(app: FastAPI, world: dict) -> None:
    store = app.state.store
    lid = store.create_list("French CEOs", "people")
    wid = add(
        app,
        "FR to list",
        EVENT,
        [{"type": "add_to_list", "list_id": lid}],
        [{"field": "country", "op": "is", "value": "FR"}],
    )
    workflows.emit("person_found", person_id=world["claire"].id, company_id=world["atelier"].id)
    workflows.emit("person_found", person_id=world["jonas"].id, company_id=world["nordwind"].id)
    assert store.list_members(lid) == [world["claire"].id]
    run = last_run(app, wid)
    assert run.status == "success" and run.steps[0]["status"] == "ok"
    assert "Claire Martin" in run.steps[0]["subject"]
    assert len(repo_for(store).runs(wid)) == 1  # the non-matching event leaves no run


def test_disabled_workflow_does_not_run(app: FastAPI, world: dict) -> None:
    store = app.state.store
    lid = store.create_list("L", "people")
    wid = add(app, "off", EVENT, [{"type": "add_to_list", "list_id": lid}])
    repo_for(store).set_enabled(wid, False)
    workflows.emit("person_found", person_id=world["claire"].id)
    assert store.list_members(lid) == [] and last_run(app, wid) is None


def test_add_to_list_kind_mismatch_is_a_failed_step(app: FastAPI, world: dict) -> None:
    lid = app.state.store.create_list("Companies", "companies")
    wid = add(app, "wrong kind", EVENT, [{"type": "add_to_list", "list_id": lid}])
    workflows.emit("person_found", person_id=world["claire"].id)
    run = last_run(app, wid)
    assert run.status == "failed" and "companies list" in run.steps[0]["detail"]


def test_enroll_and_task_use_engage_contracts(
    app: FastAPI, world: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, Any] = {}

    def fake_enroll(store: Any, sequence_id: str, person_ids: list[str]) -> dict[str, int]:
        calls["enroll"] = (sequence_id, person_ids)
        return {"enrolled": 1, "skipped": 0}

    def fake_task(store: Any, title: str, **kw: Any) -> str:
        calls["task"] = (title, kw)
        return "t1"

    monkeypatch.setattr(engage, "enroll", fake_enroll)
    monkeypatch.setattr(engage, "create_task", fake_task)
    wid = add(
        app,
        "outreach",
        {"type": "event", "event": "email_found"},
        [
            {"type": "enroll", "sequence_id": "seq1"},
            {
                "type": "create_task",
                "title": "Call {name} at {company}",
                "due_days": 2,
                "kind": "call",
            },
        ],
    )
    workflows.emit(
        "email_found", person_id=world["claire"].id, address="c@x.example", status="valid"
    )
    assert calls["enroll"] == ("seq1", [world["claire"].id])
    title, kw = calls["task"]
    assert title == "Call Claire Martin at Atelier Nuage" and kw["kind"] == "call"
    assert kw["person_id"] == world["claire"].id and kw["due"] > utcnow()
    assert last_run(app, wid).status == "success"


def test_failures_are_isolated_and_logged(
    app: FastAPI, world: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise NotImplementedError

    monkeypatch.setattr(engage, "enroll", boom)
    monkeypatch.setattr(
        engage, "create_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down"))
    )
    wid = add(
        app,
        "mixed",
        EVENT,
        [
            {"type": "enroll", "sequence_id": "s"},
            {"type": "create_task", "title": "x"},
            {"type": "notify", "message": "Found {name}"},
        ],
    )
    ok = add(app, "second workflow", EVENT, [{"type": "notify", "message": "again"}])
    workflows.emit("person_found", person_id=world["claire"].id)
    run = last_run(app, wid)
    assert run.status == "partial"
    assert [s["status"] for s in run.steps] == ["failed", "failed", "ok"]
    assert "not available" in run.steps[0]["detail"] and "db down" in run.steps[1]["detail"]
    assert last_run(app, ok).status == "success"  # the other workflow still ran
    notes = repo_for(app.state.store).notifications()
    assert any(n.title == "Found Claire Martin" for n in notes)


def test_enroll_skipped_when_nobody_enrolled(
    app: FastAPI, world: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engage, "enroll", lambda *a, **k: {"enrolled": 0, "skipped": 1})
    wid = add(app, "e", EVENT, [{"type": "enroll", "sequence_id": "s"}])
    workflows.emit("person_found", person_id=world["claire"].id)
    assert last_run(app, wid).steps[0]["status"] == "skipped"


def test_jobs_actions(app: FastAPI, world: dict) -> None:
    add(
        app,
        "jobs",
        EVENT,
        [{"type": "find_email"}, {"type": "find_people"}, {"type": "refresh_insights"}],
    )
    workflows.emit("person_found", person_id=world["claire"].id)
    kinds = sorted(j.kind for j in app.state.jobs.jobs.values())
    # find_people and refresh_insights act on the company of the person
    assert kinds == ["company_insights", "find_email", "find_people"]
    payloads = {j.kind: j.payload for j in app.state.jobs.jobs.values()}
    assert payloads["find_email"] == {"person_id": world["claire"].id}
    assert payloads["find_people"]["domain"] == "ateliernuage.example"


def test_demo_mode_skips_jobs_and_webhooks(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo", sync_jobs=True)
    store = app.state.store
    p = sample_person(app.state.search.companies[0])
    store.save_person(p)
    wid = add(
        app,
        "demo",
        EVENT,
        [
            {"type": "find_email"},
            {"type": "webhook", "url": "https://example.test/h"},
            {"type": "notify"},
        ],
    )
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://example.test/h").respond(200)
        workflows.emit("person_found", person_id=p.id)
    run = last_run(app, wid)
    assert [s["status"] for s in run.steps] == ["skipped", "skipped", "ok"]
    assert "demo" in run.steps[0]["detail"].lower()
    assert not route.called and not app.state.jobs.jobs


def test_webhook_action_posts_json(app: FastAPI, world: dict) -> None:
    wid = add(app, "hook", EVENT, [{"type": "webhook", "url": "https://example.test/in"}])
    with respx.mock() as mock:
        route = mock.post("https://example.test/in").respond(204)
        workflows.emit("person_found", person_id=world["claire"].id)
    body = json.loads(route.calls[0].request.content)
    assert body["event"] == "workflow" and body["workflow"] == "hook"
    assert body["data"]["person"]["name"] == "Claire Martin"
    assert last_run(app, wid).steps[0]["status"] == "ok"


def test_webhook_action_failure_is_a_failed_step(app: FastAPI, world: dict) -> None:
    wid = add(app, "hook", EVENT, [{"type": "webhook", "url": "https://example.test/in"}])
    with respx.mock() as mock:
        mock.post("https://example.test/in").respond(500)
        workflows.emit("person_found", person_id=world["claire"].id)
    run = last_run(app, wid)
    assert run.status == "failed" and "500" in run.steps[0]["detail"]


def test_added_to_list_event_runs_per_record(app: FastAPI, world: dict) -> None:
    store = app.state.store
    target = store.create_list("Target", "people")
    wid = add(
        app,
        "copy",
        {"type": "event", "event": "added_to_list"},
        [{"type": "add_to_list", "list_id": target}],
        [{"field": "country", "op": "is", "value": "DE"}],
    )
    workflows.emit(
        "added_to_list",
        list_id="x",
        record_ids=[world["claire"].id, world["jonas"].id],
        kind="people",
    )
    assert store.list_members(target) == [world["jonas"].id]
    assert len(last_run(app, wid).steps) == 1


def test_deal_stage_condition(app: FastAPI) -> None:
    wid = add(
        app,
        "won",
        {"type": "event", "event": "deal_stage_changed"},
        [{"type": "notify", "message": "Deal won"}],
        [{"field": "stage", "op": "is", "value": "won"}],
    )
    workflows.emit("deal_stage_changed", deal_id="d1", stage="lead", previous_stage="")
    assert last_run(app, wid) is None
    workflows.emit("deal_stage_changed", deal_id="d1", stage="won", previous_stage="lead")
    assert last_run(app, wid).status == "success"


def test_activity_is_logged_for_the_person(app: FastAPI, world: dict) -> None:
    from leadcommons.store.pipeline import ActivityRepo

    add(app, "act", EVENT, [{"type": "notify"}])
    workflows.emit("person_found", person_id=world["claire"].id)
    acts = ActivityRepo(app.state.store.engine).list_for(person_id=world["claire"].id)
    assert any(a.kind == "workflow" for a in acts)


# ------------------------------------------------------------------ emit safety


def test_emit_never_raises(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(engine, "dispatch", boom)
    workflows.emit("person_found", person_id="x")  # must not raise
    workflows.emit("not_an_event")
    workflows.emit("person_found")


def test_emit_without_a_bound_app_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(workflows, "_app", None)
    workflows.emit("person_found", person_id="x")


def test_emit_ignores_missing_records(app: FastAPI) -> None:
    wid = add(app, "ghost", EVENT, [{"type": "notify"}])
    workflows.emit("person_found", person_id="does-not-exist")
    assert last_run(app, wid) is not None  # event without a known person still runs bare steps


def test_workflows_do_not_trigger_workflows(
    app: FastAPI, world: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    def fake_enroll(*a: Any, **k: Any) -> dict[str, int]:
        workflows.emit("person_found", person_id=world["claire"].id)  # nested: ignored
        seen.append("x")
        return {"enrolled": 1, "skipped": 0}

    monkeypatch.setattr(engage, "enroll", fake_enroll)
    wid = add(app, "loop", EVENT, [{"type": "enroll", "sequence_id": "s"}])
    workflows.emit("person_found", person_id=world["claire"].id)
    assert seen == ["x"] and len(repo_for(app.state.store).runs(wid)) == 1


# ------------------------------------------------------------------ manual + scheduled


def test_run_now_manual_on_a_list(app: FastAPI, world: dict) -> None:
    store = app.state.store
    src = store.create_list("Src", "people")
    store.add_to_list(src, [world["jonas"].id])
    dst = store.create_list("Dst", "people")
    wid = add(
        app, "manual", {"type": "manual", "list_id": src}, [{"type": "add_to_list", "list_id": dst}]
    )
    res = engine.run_now(app, wid)
    assert res["status"] == "success"
    assert store.list_members(dst) == [world["jonas"].id]
    assert last_run(app, wid).trigger == "manual"


def test_run_now_event_workflow_uses_recent_people(app: FastAPI, world: dict) -> None:
    store = app.state.store
    dst = store.create_list("Dst", "people")
    wid = add(
        app,
        "recent",
        EVENT,
        [{"type": "add_to_list", "list_id": dst}],
        [{"field": "country", "op": "is", "value": "FR"}],
    )
    engine.run_now(app, wid)
    assert store.list_members(dst) == [world["claire"].id]


def test_run_now_with_no_match_is_logged_as_skipped(app: FastAPI, world: dict) -> None:
    wid = add(
        app,
        "none",
        {"type": "manual"},
        [{"type": "notify"}],
        [{"field": "country", "op": "is", "value": "JP"}],
    )
    assert engine.run_now(app, wid)["status"] == "skipped"


def test_next_run_computation() -> None:
    from datetime import datetime

    after = datetime(2026, 11, 15, 10, 30, tzinfo=utcnow().tzinfo)  # a Sunday
    daily = engine.next_run({"type": "schedule", "frequency": "daily", "hour": 8}, after)
    assert (daily.day, daily.hour) == (16, 8)
    earlier = engine.next_run({"type": "schedule", "frequency": "daily", "hour": 12}, after)
    assert (earlier.day, earlier.hour) == (15, 12)
    weekly = engine.next_run(
        {"type": "schedule", "frequency": "weekly", "weekday": 0, "hour": 9}, after
    )
    assert weekly.weekday() == 0 and weekly > after and weekly.day == 16
    assert engine.next_run({"type": "event", "event": "person_found"}, after) is None


def test_scheduled_workflow_acts_on_new_matches_only_once(app: FastAPI, world: dict) -> None:
    store = app.state.store
    dst = store.create_list("FR companies", "companies")
    trig = {
        "type": "schedule",
        "frequency": "daily",
        "hour": 8,
        "source": "companies",
        "query": "country=FR",
    }
    wid = add(app, "daily FR", trig, [{"type": "add_to_list", "list_id": dst}])
    repo = repo_for(store)
    repo.save_state(wid, {}, utcnow() - timedelta(minutes=1))  # due now
    assert engine.run_due(app) == 1
    members = store.list_members(dst)
    assert len(members) == 2  # Atelier Nuage and Moss and Pine are the French companies
    run = last_run(app, wid)
    assert run.status == "success" and run.trigger == "schedule"
    wf = repo.get_workflow(wid)
    assert wf.next_run_at > utcnow()  # rescheduled for tomorrow
    assert engine.run_due(app) == 0  # nothing is due any more
    repo.save_state(wid, wf.state, utcnow() - timedelta(minutes=1))
    engine.run_due(app)
    assert store.list_members(dst) == members  # nothing new, nothing repeated
    assert "0 new" in last_run(app, wid).summary


def test_scheduled_run_is_skipped_in_demo(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo")
    trig = {"type": "schedule", "frequency": "daily", "hour": 8, "source": "companies", "query": ""}
    wid = add(app, "demo daily", trig, [{"type": "notify"}])
    repo_for(app.state.store).save_state(wid, {}, utcnow() - timedelta(minutes=1))
    engine.run_due(app)
    assert last_run(app, wid).status == "skipped" and not repo_for(app.state.store).notifications()


def test_scheduler_thread_rules(tmp_path: Path) -> None:
    local = make_app(tmp_path / "a", mode="local")
    demo = make_app(tmp_path / "b", mode="demo")
    sync = make_app(tmp_path / "c", sync_jobs=True)
    assert scheduler.Scheduler.allowed(local.state.settings)
    assert not scheduler.Scheduler.allowed(demo.state.settings)
    assert not scheduler.Scheduler.allowed(sync.state.settings)
    s = scheduler.Scheduler(local, interval=0.05)
    assert s.start() and s.running
    s.stop()
    assert not scheduler.Scheduler(demo).start()
    assert not scheduler.Scheduler(sync).start()


def test_scheduler_loop_runs_due_workflows(tmp_path: Path) -> None:
    import time

    app = make_app(tmp_path)
    trig = {"type": "schedule", "frequency": "daily", "hour": 8, "source": "companies", "query": ""}
    wid = add(app, "loop", trig, [{"type": "notify", "message": "tick"}])
    repo_for(app.state.store).save_state(wid, {}, utcnow() - timedelta(minutes=1))
    s = scheduler.Scheduler(app, interval=0.05)
    s.start()
    deadline = time.time() + 5
    while time.time() < deadline and last_run(app, wid) is None:
        time.sleep(0.05)
    s.stop()
    assert last_run(app, wid) is not None


def test_server_starts_scheduler_on_startup(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with TestClient(app):
        assert app.state.workflow_scheduler.running
    app2 = make_app(tmp_path / "d", mode="demo")
    with TestClient(app2):
        assert not app2.state.workflow_scheduler.running


# ------------------------------------------------------------------ outgoing webhooks


def test_webhook_signature_and_delivery_log(app: FastAPI, world: dict) -> None:
    repo = repo_for(app.state.store)
    hid = repo.create_webhook("https://hooks.test/lc", ["email_found", "person_found"])
    secret = repo.get_webhook(hid).secret
    with respx.mock() as mock:
        route = mock.post("https://hooks.test/lc").respond(200)
        workflows.emit(
            "email_found", person_id=world["claire"].id, address="c@x.example", status="valid"
        )
    req = route.calls[0].request
    expected = "sha256=" + hmac.new(secret.encode(), req.content, hashlib.sha256).hexdigest()
    assert req.headers[webhooks.SIGNATURE_HEADER] == expected
    assert req.headers[webhooks.EVENT_HEADER] == "email_found"
    body = json.loads(req.content)
    assert body["event"] == "email_found" and body["data"]["email"]["address"] == "c@x.example"
    log = repo.deliveries(hid)
    assert len(log) == 1 and log[0].ok and log[0].status_code == 200


def test_webhook_only_for_subscribed_events_and_enabled(app: FastAPI, world: dict) -> None:
    repo = repo_for(app.state.store)
    a = repo.create_webhook("https://a.test/h", ["person_found"])
    repo.create_webhook("https://b.test/h", ["email_sent"])
    off = repo.create_webhook("https://c.test/h", ["person_found"])
    repo.update_webhook(off, url="https://c.test/h", events=["person_found"], enabled=False)
    with respx.mock(assert_all_called=False) as mock:
        ra = mock.post("https://a.test/h").respond(200)
        rb = mock.post("https://b.test/h").respond(200)
        rc = mock.post("https://c.test/h").respond(200)
        workflows.emit("person_found", person_id=world["claire"].id)
    assert ra.called and not rb.called and not rc.called
    assert repo.deliveries(a)[0].event == "person_found"


def test_webhook_failures_are_logged_not_raised(app: FastAPI, world: dict) -> None:
    repo = repo_for(app.state.store)
    hid = repo.create_webhook("https://down.test/h", ["person_found"])
    with respx.mock() as mock:
        mock.post("https://down.test/h").mock(side_effect=httpx.ConnectError("refused"))
        workflows.emit("person_found", person_id=world["claire"].id)
    d = repo.deliveries(hid)[0]
    assert not d.ok and d.status_code is None and "ConnectError" in (d.error or "")


def test_no_webhooks_in_demo(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo", sync_jobs=True)
    repo = repo_for(app.state.store)
    hid = repo.create_webhook("https://hooks.test/lc", ["person_found"])
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://hooks.test/lc").respond(200)
        workflows.emit("person_found", person_id="p")
    assert not route.called and repo.deliveries(hid) == []


def test_check_url() -> None:
    assert webhooks.check_url("https://example.com/x") is None
    assert webhooks.check_url("http://localhost:5678/hook") is None
    for bad in ("", "ftp://x.test", "javascript:alert(1)", "example.com", "http://"):
        assert webhooks.check_url(bad)


# ------------------------------------------------------------------ builder form parsing


class Form(dict):
    def getlist(self, key: str) -> list[Any]:
        v = self.get(key)
        return v if isinstance(v, list) else ([] if v is None else [v])


def test_clean_workflow_ok_and_errors() -> None:
    form = Form(
        name="N",
        trigger_type="event",
        event="email_found",
        cond_field_0="country",
        cond_op_0="is",
        cond_value_0="FR",
        cond_field_1="size",
        cond_op_1="is",
        cond_value_1="not-a-size",  # dropped
        action_type_0="notify",
        action_arg_0="hi {name}",
        action_type_1="create_task",
        action_arg_1="Task",
        action_extra_1="3",
        action_kind_1="call",
    )
    name, trig, conds, acts, err = engine.clean_workflow(form)
    assert err is None and trig == {"type": "event", "event": "email_found"}
    assert conds == [{"field": "country", "op": "is", "value": "FR"}]
    assert acts[1] == {"type": "create_task", "title": "Task", "due_days": 3, "kind": "call"}
    assert engine.clean_workflow(Form(trigger_type="event", event="nope"))[4]
    assert engine.clean_workflow(Form(trigger_type="manual"))[4] == "Add at least one action."
    assert (
        "list" in engine.clean_workflow(Form(trigger_type="manual", action_type_0="add_to_list"))[4]
    )
    assert (
        "Webhook"
        in engine.clean_workflow(
            Form(trigger_type="manual", action_type_0="webhook", action_arg_0="nope")
        )[4]
    )
    n2, t2, *_ = engine.clean_workflow(
        Form(
            trigger_type="schedule",
            source="people",
            query="?title=ceo",
            frequency="weekly",
            weekday="2",
            hour="9",
            action_type_0="notify",
        )
    )
    assert t2["query"] == "title=ceo" and t2["frequency"] == "weekly" and t2["source"] == "people"
