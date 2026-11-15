"""Engage pages: every page and dialog renders, forms work, demo mode refuses to send,
the public unsubscribe page works with good, bad and expired tokens."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from leadcommons.engage import core, crypto, enroll
from leadcommons.models import EmailStatus
from leadcommons.store import Store
from leadcommons.store.engage import repo_for
from leadcommons.web.app import create_app
from tests.fakes import FakeJobs, FakeSearch, FakeStore
from tests.test_engage import (
    EMAIL_STEPS,
    FakeSMTP,
    add_mailbox,
    add_person,
    add_sequence,
    install_fake_smtp,
    make_settings,
)

HX = {"HX-Request": "true"}


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    return install_fake_smtp(monkeypatch)


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = make_settings(tmp_path, **kw)
    settings.ensure_dirs()
    return create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def seeded(app: FastAPI) -> dict[str, Any]:
    store, settings = app.state.store, app.state.settings
    mb = add_mailbox(store, settings)
    sid = add_sequence(store, mb, EMAIL_STEPS)
    claire = add_person(store, "Claire Martin", "claire@atelier.example")
    jonas = add_person(store, "Jonas Weber", "jonas@nordwind.example", EmailStatus.CATCH_ALL)
    nomail = add_person(store, "No Mail", None)
    enroll(store, sid, [claire])
    return {"mailbox": mb, "sequence": sid, "claire": claire, "jonas": jonas, "nomail": nomail}


def toast(r: Any) -> dict[str, str]:
    return json.loads(r.headers["HX-Trigger"])["toast"]


EMPTY_PAGES = [
    "/sequences",
    "/sequences?tab=analytics",
    "/sequences?tab=health",
    "/emails",
    "/tasks",
    "/tasks?view=done",
    "/settings/mailboxes",
    "/settings/mailboxes/new",
    "/settings/mailboxes/new?provider=gmail",
    "/settings/sending",
    "/partials/task-new",
    "/partials/email-compose",
    "/partials/sequence-picker?ids=",
]


@pytest.mark.parametrize("path", EMPTY_PAGES)
def test_empty_pages_render(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200, path
    assert "Apollo" not in r.text and "credits" not in r.text.lower()


def test_seeded_pages_and_dialogs_render(client: TestClient, seeded: dict[str, Any]) -> None:
    sid, claire = seeded["sequence"], seeded["claire"]
    step = repo_for(client.app.state.store).steps(sid)[0]
    mid = repo_for(client.app.state.store).add_message(
        direction="out", status="failed", to_email="x@y.example", subject="Oops", error="451"
    )
    paths = [
        "/sequences",
        "/sequences?status=all&q=intro",
        "/sequences?tab=analytics",
        "/sequences?tab=health",
        f"/sequences/{sid}",
        f"/sequences/{sid}?tab=contacts",
        f"/sequences/{sid}?tab=contacts&cstatus=active",
        f"/sequences/{sid}?tab=settings",
        f"/sequences/{sid}/preview?step_id={step.id}&person_id={claire}",
        f"/partials/sequence-step?sequence_id={sid}",
        f"/partials/sequence-step?sequence_id={sid}&step_id={step.id}",
        f"/partials/sequence-add?sequence_id={sid}",
        f"/partials/sequence-contacts?sequence_id={sid}&q=jonas",
        f"/partials/sequence-picker?ids={claire},{seeded['jonas']}",
        f"/partials/email-compose?person_id={claire}",
        f"/partials/task-new?person_id={claire}",
        f"/partials/email/{mid}",
        "/emails?tab=scheduled",
        "/emails?tab=sent",
        "/emails?tab=replies",
        "/emails?tab=failed",
        f"/settings/mailboxes/{seeded['mailbox']}",
        "/settings/mailboxes",
    ]
    for path in paths:
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Apollo" not in r.text
    picker = client.get(f"/partials/sequence-picker?ids={claire},{seeded['nomail']}").text
    assert "<dialog" in picker and "New sequence" in picker and "1 of 2 can be emailed" in picker
    preview = client.get(f"/sequences/{sid}/preview?step_id={step.id}&person_id={claire}").text
    assert "Hi Claire" in preview and "Unsubscribe" in preview


def test_pages_render_with_a_store_without_engine(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    app = create_app(settings=settings, search=FakeSearch(), store=FakeStore(), jobs=FakeJobs())
    c = TestClient(app)
    for path in ("/sequences", "/emails", "/tasks", "/settings/mailboxes", "/partials/task-new"):
        assert c.get(path).status_code == 200, path


def test_unknown_records_are_404(client: TestClient) -> None:
    assert client.get("/sequences/nope").status_code == 404
    assert client.get("/settings/mailboxes/nope").status_code == 404
    assert client.get("/partials/sequence-step?sequence_id=nope").status_code == 404
    assert client.get("/partials/email/999").status_code == 404


def test_cross_site_post_is_blocked(client: TestClient) -> None:
    r = client.post("/sequences", data={"name": "x"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------- sequences


def test_create_sequence_from_template_and_edit_steps(client: TestClient) -> None:
    r = client.post("/sequences", data={"name": "Founders", "template": "call"})
    assert r.status_code == 303
    sid = r.headers["location"].split("/sequences/")[1].split("?")[0]
    repo = repo_for(client.app.state.store)
    assert [s.kind for s in repo.steps(sid)] == ["auto_email", "call", "auto_email"]
    # validation answers 422 + a toast so the dialog stays open
    bad = client.post(
        f"/sequences/{sid}/steps",
        data={"kind": "auto_email", "subject": "x", "body": "Hi {{nick}}"},
        headers=HX,
    )
    assert bad.status_code == 422 and "Unknown variable" in toast(bad)["message"]
    ok = client.post(
        f"/sequences/{sid}/steps",
        data={"kind": "linkedin", "note": "Connect", "delay_days": "2"},
        headers=HX,
    )
    assert ok.status_code == 200 and "HX-Redirect" in ok.headers
    steps = repo.steps(sid)
    assert steps[-1].kind == "linkedin" and steps[-1].delay_days == 2
    client.post(f"/sequences/{sid}/steps/{steps[-1].id}/move", data={"dir": "up"})
    assert repo.steps(sid)[2].kind == "linkedin"
    client.post(
        f"/sequences/{sid}/steps/{steps[0].id}",
        data={"kind": "auto_email", "subject": "New {{first_name}}", "body": "B"},
    )
    assert repo.steps(sid)[0].subject == "New {{first_name}}"
    first_no_subject = client.post(
        f"/sequences/{sid}/steps/{steps[0].id}",
        data={"kind": "auto_email", "subject": "", "body": "B"},
        headers=HX,
    )
    assert first_no_subject.status_code == 422
    client.post(f"/sequences/{sid}/steps/{steps[1].id}/delete")
    assert len(repo.steps(sid)) == 3


def test_sequence_lifecycle(client: TestClient) -> None:
    sid = (
        client.post("/sequences", data={"name": "Empty"})
        .headers["location"]
        .split("/")[2]
        .split("?")[0]
    )
    repo = repo_for(client.app.state.store)
    r = client.post(f"/sequences/{sid}/status", data={"action": "activate"}, headers=HX)
    assert r.status_code == 422 and "step" in toast(r)["message"]
    client.post(f"/sequences/{sid}/steps", data={"kind": "task", "subject": "Prepare", "note": "n"})
    client.post(f"/sequences/{sid}/status", data={"action": "activate"})
    assert repo.sequence(sid).status == "active"
    client.post(f"/sequences/{sid}/rename", data={"name": "Renamed"})
    assert repo.sequence(sid).name == "Renamed"
    client.post(f"/sequences/{sid}/status", data={"action": "pause"})
    assert repo.sequence(sid).status == "paused"
    dup = client.post(f"/sequences/{sid}/duplicate")
    new = dup.headers["location"].split("/sequences/")[1].split("?")[0]
    assert repo.sequence(new).name == "Renamed (copy)" and len(repo.steps(new)) == 1
    client.post(f"/sequences/{sid}/status", data={"action": "archive"})
    assert repo.sequence(sid).status == "archived"
    assert (
        "Renamed"
        not in client.get("/sequences").text.split("Renamed (copy)")[0].split("<tbody>")[-1]
    )
    client.post(f"/sequences/{sid}/delete")
    assert repo.sequence(sid) is None


def test_sequence_settings(client: TestClient, seeded: dict[str, Any]) -> None:
    sid = seeded["sequence"]
    r = client.post(
        f"/sequences/{sid}/settings",
        data={
            "mailbox_id": seeded["mailbox"],
            "schedule": "custom",
            "window_start": "9",
            "window_end": "17",
            "days": ["1", "3"],
            "timezone": "Europe/Paris",
        },
    )
    assert r.status_code == 303
    seq = repo_for(client.app.state.store).sequence(sid)
    assert seq.schedule_custom and seq.window_days == "1,3" and seq.timezone == "Europe/Paris"
    assert seq.stop_on_reply is False
    assert (
        client.post(
            f"/sequences/{sid}/settings", data={"schedule": "custom"}, headers=HX
        ).status_code
        == 422
    )


def test_enroll_endpoint_reports_counts(client: TestClient, seeded: dict[str, Any]) -> None:
    sid = seeded["sequence"]
    ids = f"{seeded['claire']},{seeded['jonas']},{seeded['nomail']}"
    r = client.post("/sequences/enroll", data={"sequence_id": sid, "ids_csv": ids}, headers=HX)
    assert r.status_code == 200
    msg = toast(r)["message"]
    assert "Added 1 contact to Intro" in msg and "1 already in it" in msg and "1 no email" in msg
    none = client.post(
        "/sequences/enroll", data={"sequence_id": sid, "ids": seeded["claire"]}, headers=HX
    )
    assert none.status_code == 422
    new = client.post(
        "/sequences/enroll",
        data={"sequence_id": "__new__", "new_name": "Fresh", "ids": seeded["jonas"]},
        headers=HX,
    )
    assert new.status_code == 200 and "HX-Redirect" in new.headers  # no steps yet: go add some
    assert any(s.name == "Fresh" for s in repo_for(client.app.state.store).sequences())
    assert (
        client.post("/sequences/enroll", data={"sequence_id": sid}, headers=HX).status_code == 422
    )


def test_contact_actions(client: TestClient, seeded: dict[str, Any]) -> None:
    sid = seeded["sequence"]
    repo = repo_for(client.app.state.store)
    eid = repo.enrollments(sid)[0].id
    client.post(f"/sequences/{sid}/contacts/{eid}", data={"action": "pause"})
    assert repo.enrollment(eid).status == "paused"
    client.post(f"/sequences/{sid}/contacts/{eid}", data={"action": "resume"})
    client.post(f"/sequences/{sid}/contacts/{eid}", data={"action": "replied"})
    assert repo.enrollment(eid).status == "replied"
    r = client.post(f"/sequences/{sid}/contacts/{eid}", data={"action": "pause"}, headers=HX)
    assert r.status_code == 422


def test_dismiss_tip(client: TestClient) -> None:
    assert "Sequences step aside" in client.get("/sequences").text
    client.post("/sequences/tip")
    assert "Sequences step aside" not in client.get("/sequences").text


# ---------------------------------------------------------------- emails


def test_demo_mode_disables_sending(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo")
    store, settings = app.state.store, app.state.settings
    add_mailbox(store, settings)
    pid = add_person(store, "Claire Martin", "claire@atelier.example")
    c = TestClient(app, follow_redirects=False)
    html = c.get(f"/partials/email-compose?person_id={pid}").text
    assert re.search(
        r'<button class="btn primary" type="button" disabled[^>]*title="Sending is turned off', html
    )
    r = c.post(
        "/emails/send",
        data={"to": "claire@atelier.example", "subject": "s", "body": "b"},
        headers=HX,
    )
    assert r.status_code == 422 and "public demo" in toast(r)["message"]
    assert c.post("/settings/sending/run", headers=HX).status_code == 422
    mid = repo_for(store).mailboxes()[0].id
    assert c.post(f"/settings/mailboxes/{mid}/test", headers=HX).status_code == 422
    assert "public demo" in c.get("/sequences").text
    # passwords typed in the demo are never stored
    c.post(
        "/settings/mailboxes",
        data={
            "from_email": "d@demo.example",
            "smtp_host": "smtp.demo.example",
            "smtp_password": "secret",
            "days": ["0"],
        },
    )
    demo_box = [m for m in repo_for(store).mailboxes() if m.from_email == "d@demo.example"][0]
    assert demo_box.smtp_password == ""


def test_compose_sends_through_the_mailbox(
    client: TestClient, seeded: dict[str, Any], smtp: type[FakeSMTP]
) -> None:
    r = client.post(
        "/emails/send",
        data={
            "to": "claire@atelier.example",
            "person_id": seeded["claire"],
            "subject": "Hello {{first_name}}",
            "body": "Body",
        },
        headers=HX,
    )
    assert r.status_code == 200 and toast(r)["message"] == "Email sent."
    assert smtp.sent[-1]["Subject"] == "Hello Claire"
    assert "/u/" in smtp.sent[-1].get_body(("plain",)).get_content()
    assert "Hello Claire" in client.get("/emails?tab=sent").text
    client.app.state.store.add_suppression("claire@atelier.example")
    r = client.post(
        "/emails/send",
        data={"to": "claire@atelier.example", "subject": "x", "body": "y"},
        headers=HX,
    )
    assert r.status_code == 422 and "do-not-contact" in toast(r)["message"]


def test_compose_schedule_and_cancel(
    client: TestClient, seeded: dict[str, Any], smtp: type[FakeSMTP]
) -> None:
    r = client.post(
        "/emails/send",
        data={
            "to": "z@prospect.example",
            "subject": "Later",
            "body": "b",
            "when": "later",
            "date": "2099-01-02",
            "time": "10:00",
        },
        headers=HX,
    )
    assert r.status_code == 200 and "scheduled" in toast(r)["message"]
    assert not smtp.sent
    repo = repo_for(client.app.state.store)
    m = repo.messages(statuses=["scheduled"])[0]
    assert "Later" in client.get("/emails?tab=scheduled").text
    client.post(f"/emails/{m.id}/cancel")
    assert repo.message(m.id) is None
    past = client.post(
        "/emails/send",
        data={
            "to": "z@prospect.example",
            "subject": "x",
            "body": "b",
            "when": "later",
            "date": "2001-01-01",
        },
        headers=HX,
    )
    assert past.status_code == 422


def test_email_task_completes_when_sent(
    client: TestClient, seeded: dict[str, Any], smtp: type[FakeSMTP]
) -> None:
    store = client.app.state.store
    tid = core.new_task(
        store, "Email Claire", person_id=seeded["claire"], kind="email", subject="S", body="Draft"
    )
    dialog = client.get(f"/partials/email-compose?task_id={tid}").text
    assert "Draft" in dialog and f'value="{tid}"' in dialog
    client.post(
        "/emails/send",
        data={
            "to": "claire@atelier.example",
            "subject": "S",
            "body": "Draft",
            "task_id": tid,
            "person_id": seeded["claire"],
        },
    )
    assert repo_for(store).task(tid).status == "done"


def test_retry_and_mark_replied(client: TestClient, seeded: dict[str, Any]) -> None:
    repo = repo_for(client.app.state.store)
    failed = repo.add_message(
        direction="out",
        status="failed",
        mailbox_id=seeded["mailbox"],
        to_email="a@b.example",
        subject="F",
        error="451",
    )
    client.post(f"/emails/{failed}/retry")
    assert repo.message(failed).status == "scheduled"
    sent = repo.add_message(
        direction="out",
        status="sent",
        to_email="a@b.example",
        subject="S",
        person_id=seeded["claire"],
        sent_at=datetime.now(UTC),
    )
    client.post(f"/emails/{sent}/replied")
    assert repo.message(sent).replied_at is not None


# ---------------------------------------------------------------- tasks


def test_tasks_flow(client: TestClient, seeded: dict[str, Any]) -> None:
    store = client.app.state.store
    repo = repo_for(store)
    r = client.post(
        "/tasks",
        data={
            "title": "Call Claire",
            "kind": "call",
            "priority": "high",
            "person_id": seeded["claire"],
            "due_date": "2020-01-01",
        },
        headers=HX,
    )
    assert r.status_code == 200 and toast(r)["message"] == "Task created."
    assert client.post("/tasks", data={"title": ""}, headers=HX).status_code == 422
    task = repo.tasks()[0]
    assert task.kind == "call" and task.priority == "high" and task.company_id is None
    page = client.get("/tasks?tab=overdue").text
    assert "Call Claire" in page and "eg-overdue" in page
    assert "Call Claire" not in client.get("/tasks?view=upcoming").text
    assert "Call Claire" in client.get("/tasks?tab=call&priority=high").text
    assert "Call Claire" not in client.get("/tasks?tab=email").text
    assert "Call Claire" in client.get("/tasks?q=claire").text
    assert "Complete task" in client.get(f"/partials/task-done/{task.id}?action=complete").text
    client.post(f"/tasks/{task.id}/complete", data={"note": "Left a voicemail"})
    done = repo.task(task.id)
    assert done.status == "done" and done.note == "Left a voicemail"
    assert "Left a voicemail" in client.get("/tasks?view=done").text
    again = client.post(f"/tasks/{task.id}/complete", headers=HX)
    assert again.status_code == 422
    client.post(f"/tasks/{task.id}/reopen")
    client.post(f"/tasks/{task.id}/skip", data={"note": "no"})
    assert repo.task(task.id).status == "skipped"
    client.post(f"/tasks/{task.id}/delete")
    assert repo.task(task.id) is None


def test_task_dialog_prefills_links(client: TestClient, seeded: dict[str, Any]) -> None:
    company = FakeSearch().companies[0]
    html = client.get(
        f"/partials/task-new?person_id={seeded['claire']}&company_id={company.id}&deal_id=dl_1"
    ).text
    assert 'name="person_id"' in html and company.name in html and 'value="dl_1"' in html


def test_task_time_zone(client: TestClient) -> None:
    repo_for(client.app.state.store).save_settings(timezone="Europe/Paris")
    client.post("/tasks", data={"title": "Paris", "due_date": "2030-06-01", "due_time": "09:00"})
    task = repo_for(client.app.state.store).tasks()[0]
    assert task.due_at == datetime(2030, 6, 1, 7, 0, tzinfo=UTC)


# ---------------------------------------------------------------- mailboxes + sending


def test_mailbox_create_encrypts_and_tests(client: TestClient, smtp: type[FakeSMTP]) -> None:
    form = {
        "from_email": "me@sender.example",
        "from_name": "Me",
        "smtp_host": "smtp.sender.example",
        "smtp_port": "587",
        "smtp_security": "starttls",
        "smtp_username": "me@sender.example",
        "smtp_password": "pw",
        "days": ["0", "1", "2"],
        "window_start": "9",
        "window_end": "17",
        "timezone": "UTC",
        "daily_limit": "30",
    }
    r = client.post("/settings/mailboxes", data=form)
    assert r.status_code == 303
    store = client.app.state.store
    with store.engine.connect() as conn:
        raw = conn.execute(text("select smtp_password from engage_mailboxes")).scalar_one()
    assert raw and "pw" != raw and crypto.decrypt(client.app.state.settings, raw) == "pw"
    m = repo_for(store).mailboxes()[0]
    assert m.daily_limit == 30 and m.window_days == "0,1,2" and m.status == "untested"
    r = client.post(f"/settings/mailboxes/{m.id}/test", headers=HX)
    assert r.status_code == 200 and "Nothing was sent" in unquote_plus(r.headers["HX-Redirect"])
    assert repo_for(store).mailbox(m.id).status == "ok" and smtp.sent == []
    # blank password on edit keeps the stored one
    client.post(
        f"/settings/mailboxes/{m.id}", data={**form, "smtp_password": "", "daily_limit": "40"}
    )
    m2 = repo_for(store).mailbox(m.id)
    assert (
        m2.daily_limit == 40 and crypto.decrypt(client.app.state.settings, m2.smtp_password) == "pw"
    )
    smtp.password = "nope"
    r = client.post("/settings/mailboxes/test-all", headers=HX)
    assert r.status_code == 422 and repo_for(store).mailbox(m.id).status == "error"
    assert "cannot send" in client.get("/sequences?tab=health").text
    client.post(f"/settings/mailboxes/{m.id}/delete")
    assert repo_for(store).mailboxes() == []


def test_mailbox_validation(client: TestClient) -> None:
    r = client.post(
        "/settings/mailboxes", data={"from_email": "not-an-email", "smtp_host": "h"}, headers=HX
    )
    assert r.status_code == 422
    r = client.post(
        "/settings/mailboxes", data={"from_email": "a@b.example", "smtp_host": "h"}, headers=HX
    )
    assert r.status_code == 422 and "day" in toast(r)["message"]


def test_sending_settings(client: TestClient) -> None:
    r = client.post(
        "/settings/sending",
        data={
            "footer_text": "Bye",
            "postal_address": "1 Street",
            "public_url": "https://leads.example",
            "gap_minutes": "2",
            "bounce_action": "stop",
            "timezone": "Europe/Paris",
        },
    )
    assert r.status_code == 303
    cfg = repo_for(client.app.state.store).get_settings()
    assert cfg["footer_text"] == "Bye" and cfg["public_url"] == "https://leads.example"
    assert (
        cfg["stop_on_reply"] is False and cfg["bounce_action"] == "stop" and cfg["gap_minutes"] == 2
    )
    assert "https://leads.example/u/" in client.get("/settings/sending").text
    assert client.post("/settings/sending", data={"footer_text": ""}, headers=HX).status_code == 422
    assert (
        client.post(
            "/settings/sending", data={"footer_text": "x", "public_url": "ftp://x"}, headers=HX
        ).status_code
        == 422
    )


def test_run_now_button(client: TestClient, seeded: dict[str, Any], smtp: type[FakeSMTP]) -> None:
    repo_for(client.app.state.store).update_mailbox(
        seeded["mailbox"], window_days="0,1,2,3,4,5,6", window_start=0, window_end=0
    )
    r = client.post("/settings/sending/run", headers=HX)
    assert r.status_code == 200
    assert smtp.sent and smtp.sent[0]["To"] == "claire@atelier.example"


# ---------------------------------------------------------------- unsubscribe


def test_unsubscribe_page_flow(client: TestClient, seeded: dict[str, Any]) -> None:
    settings, store = client.app.state.settings, client.app.state.store
    token = crypto.make_unsubscribe_token(settings, "claire@atelier.example", 1)
    page = client.get(f"/u/{token}")
    assert page.status_code == 200 and "Stop these emails?" in page.text
    assert "claire@atelier.example" not in page.text  # the address is masked
    assert not store.is_suppressed(
        "claire@atelier.example"
    )  # a GET (link scanners) changes nothing
    done = client.post(f"/u/{token}")
    assert done.status_code == 200 and "You are unsubscribed" in done.text
    assert store.is_suppressed("claire@atelier.example")
    assert repo_for(store).enrollments(seeded["sequence"])[0].status == "unsubscribed"
    # RFC 8058 one-click POST from a mail client (no Origin header)
    other = crypto.make_unsubscribe_token(settings, "jonas@nordwind.example")
    assert client.post(f"/u/{other}", data={"List-Unsubscribe": "One-Click"}).status_code == 200
    assert store.is_suppressed("jonas@nordwind.example")


def test_unsubscribe_bad_and_expired_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = client.get("/u/not-a-real-token")
    assert bad.status_code == 404 and "not valid" in bad.text and "/opt-out" in bad.text
    assert client.post("/u/not-a-real-token").status_code == 404
    token = crypto.make_unsubscribe_token(client.app.state.settings, "a@b.example")
    monkeypatch.setattr(crypto, "UNSUBSCRIBE_MAX_AGE", -1)
    monkeypatch.setattr(crypto.read_unsubscribe_token, "__defaults__", (-1,))
    expired = client.get(f"/u/{token}")
    assert expired.status_code == 410 and "expired" in expired.text


def test_unsubscribe_is_public_in_server_mode(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="server", password="secret")
    c = TestClient(app, follow_redirects=False)
    token = crypto.make_unsubscribe_token(app.state.settings, "a@b.example")
    assert c.get(f"/u/{token}").status_code == 200
    assert c.post(f"/u/{token}").status_code == 200


def test_engage_pages_need_login_in_server_mode(tmp_path: Path) -> None:
    c = TestClient(make_app(tmp_path, mode="server", password="secret"), follow_redirects=False)
    assert c.get("/sequences").status_code in (302, 303, 307, 401)


def test_scheduled_column_shows_next_window(client: TestClient, seeded: dict[str, Any]) -> None:
    repo = repo_for(client.app.state.store)
    e = repo.enrollments(seeded["sequence"])[0]
    repo.update_enrollment(e.id, next_run_at=datetime.now(UTC) + timedelta(days=1))
    html = client.get("/emails?tab=scheduled").text
    assert "Hi Claire" in html and "Intro" in html


# ---------------------------------------------------------------- AI draft + task views


class _Resp:
    def __init__(self, content: str) -> None:
        self._content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


def test_ai_draft_is_disabled_without_a_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LEADCOMMONS_LLM_URL", raising=False)
    monkeypatch.delenv("LEADCOMMONS_LLM_MODEL", raising=False)
    html = client.get("/sequences").text
    assert re.search(
        r'<button class="btn" type="button" disabled aria-disabled="true" '
        r'title="To turn this on, set LEADCOMMONS_LLM_URL',
        html,
    )
    r = client.post("/sequences/ai", data={"goal": "Book demos with logistics leaders"}, headers=HX)
    assert r.status_code == 422 and "LEADCOMMONS_LLM_URL" in toast(r)["message"]


def test_ai_draft_creates_a_reviewed_draft(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.local/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "tiny")
    sent: list[dict[str, Any]] = []
    answer = {
        "name": "Logistics demos",
        "steps": [
            {
                "kind": "auto_email",
                "delay_days": 3,
                "subject": "",
                "body": "Hi {{first_name|there}}, {{secret_field}} demo?",
            },
            {"kind": "call", "delay_days": 2, "note": "Call {{first_name}}"},
            {"kind": "rocket", "delay_days": 1},
            {"kind": "auto_email", "delay_days": 99, "subject": "", "body": "Following up"},
        ],
    }

    def fake_post(url: str, json: Any = None, headers: Any = None, timeout: Any = None) -> _Resp:
        sent.append(json)
        return _Resp(__import__("json").dumps(answer))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert 'data-open-dialog="eg-ai-seq"' in client.get("/sequences").text
    r = client.post(
        "/sequences/ai",
        data={"goal": "Book demos with logistics leaders", "steps": "4"},
        headers=HX,
    )
    assert r.status_code == 200 and "HX-Redirect" in r.headers
    sid = r.headers["HX-Redirect"].split("/sequences/")[1].split("?")[0]
    repo = repo_for(client.app.state.store)
    seq = repo.sequence(sid)
    steps = repo.steps(sid)
    assert seq.name == "Logistics demos" and seq.status == "draft"
    assert [s.kind for s in steps] == ["auto_email", "call", "auto_email"]
    assert steps[0].delay_days == 0 and steps[0].subject  # first email always gets a subject
    assert "{{secret_field}}" not in steps[0].body and "{{first_name|there}}" in steps[0].body
    assert steps[2].delay_days == 30
    # only the goal is sent to the model
    assert "Book demos with logistics leaders" in sent[0]["messages"][1]["content"]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp("not json"))
    bad = client.post(
        "/sequences/ai", data={"goal": "Book demos with logistics leaders"}, headers=HX
    )
    assert bad.status_code == 422 and "usable" in toast(bad)["message"]
    short = client.post("/sequences/ai", data={"goal": "hi"}, headers=HX)
    assert short.status_code == 422


def test_ai_draft_is_off_in_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.local/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "tiny")
    c = TestClient(make_app(tmp_path, mode="demo"))
    assert "Turned off in the public demo." in c.get("/sequences").text
    r = c.post("/sequences/ai", data={"goal": "Book demos with logistics leaders"}, headers=HX)
    assert r.status_code == 422


def test_task_tabs_views_and_options(client: TestClient, seeded: dict[str, Any]) -> None:
    store = client.app.state.store
    now = datetime.now(UTC)
    core.new_task(store, "Call A", kind="call", due=now - timedelta(days=3))
    core.new_task(store, "Email B", kind="email", due=now + timedelta(days=3))
    core.new_task(store, "LinkedIn C", kind="linkedin")
    done = core.new_task(store, "Old todo", kind="action")
    core.finish_task(store, done)
    html = client.get("/tasks").text
    for label in (
        "All tasks",
        "Calls",
        "Emails",
        "LinkedIn",
        "To-dos",
        "Overdue",
        "View options",
        "Show filters",
    ):
        assert label in html
    assert "Old todo" not in html and "Call A" in html
    assert "Email B" not in client.get("/tasks?tab=call").text
    assert "Call A" in client.get("/tasks?tab=overdue").text
    assert "Email B" not in client.get("/tasks?tab=overdue").text
    assert "Old todo" in client.get("/tasks?done=1").text
    assert "Old todo" in client.get("/tasks?view=all").text
    assert "Call A" not in client.get("/tasks?view=done").text
    assert "Email B" in client.get("/tasks?view=upcoming").text
    assert "Start call queue" in html
