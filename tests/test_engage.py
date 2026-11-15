"""Engage core: encryption, unsubscribe tokens, enrollment, the scheduler, replies, tasks.

SMTP and IMAP are replaced by in-memory fakes (monkeypatched smtplib / imaplib): nothing
leaves the machine. Time travel goes through run_due(..., now=...).
"""

from __future__ import annotations

import imaplib
import os
import smtplib
import stat
import threading
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from leadcommons.config import Settings
from leadcommons.engage import core, create_task, crypto, enroll, mailer
from leadcommons.engage.replies import poll_mailbox
from leadcommons.engage.scheduler import run_due
from leadcommons.models import EmailCandidate, EmailStatus, Person, make_id
from leadcommons.store import Store
from leadcommons.store.engage import repo_for
from leadcommons.store.pipeline import ActivityRepo

# A Monday, 10:00 UTC (11:00 in Paris): inside the default window.
MONDAY = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------- fakes


class FakeSMTP:
    """Records messages instead of sending them. Class attributes steer failures."""

    sent: list[EmailMessage] = []
    logins: list[tuple[str, str]] = []
    password = "pw"
    refuse: dict[str, tuple[int, bytes]] = {}
    unreachable = False
    instances = 0

    def __init__(self, host: str, port: int, timeout: float | None = None, context: Any = None):
        if FakeSMTP.unreachable:
            raise OSError("unreachable")
        FakeSMTP.instances += 1
        self.host, self.port = host, port

    def ehlo(self) -> None:
        pass

    def starttls(self, context: Any = None) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        FakeSMTP.logins.append((user, password))
        if password != FakeSMTP.password:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def noop(self) -> None:
        pass

    def send_message(self, msg: EmailMessage, from_addr: str | None = None, to_addrs: Any = None):
        to = (to_addrs or [msg["To"]])[0]
        if to in FakeSMTP.refuse:
            raise smtplib.SMTPRecipientsRefused({to: FakeSMTP.refuse[to]})
        FakeSMTP.sent.append(msg)
        return {}

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass


IMAP_ERROR = imaplib.IMAP4.error


class FakeIMAP:
    """A mailbox folder: {uid: raw message bytes}."""

    error = IMAP_ERROR
    messages: dict[int, bytes] = {}
    password = "pw"
    selected: list[tuple[str, bool]] = []

    def __init__(self, host: str, port: int, ssl_context: Any = None, timeout: float | None = None):
        self.host = host

    def starttls(self, ssl_context: Any = None) -> None:
        pass

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if password != FakeIMAP.password:
            raise IMAP_ERROR("AUTHENTICATIONFAILED")
        return "OK", [b"logged in"]

    def select(self, folder: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        FakeIMAP.selected.append((folder, readonly))
        return "OK", [str(len(FakeIMAP.messages)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        if command == "SEARCH":
            if args[1] == "UID":
                low = int(args[2].split(":")[0])
                uids = [u for u in FakeIMAP.messages if u >= low] or list(FakeIMAP.messages)[-1:]
            else:
                uids = list(FakeIMAP.messages)
            return "OK", [" ".join(str(u) for u in sorted(uids)).encode()]
        if command == "FETCH":
            uid = int(args[0])
            raw = FakeIMAP.messages.get(uid)
            if raw is None:
                return "NO", []
            if "HEADER.FIELDS" in args[1]:
                raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
            return "OK", [(f"{uid} (BODY[] {{{len(raw)}}}".encode(), raw), b")"]
        return "BAD", []

    def logout(self) -> None:
        pass


def install_fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.refuse = [], [], {}
    FakeSMTP.unreachable, FakeSMTP.password, FakeSMTP.instances = False, "pw", 0
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    return install_fake_smtp(monkeypatch)


@pytest.fixture
def imap(monkeypatch: pytest.MonkeyPatch) -> type[FakeIMAP]:
    FakeIMAP.messages, FakeIMAP.password, FakeIMAP.selected = {}, "pw", []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(imaplib, "IMAP4", FakeIMAP)
    return FakeIMAP


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []
    import leadcommons.workflows as wf

    monkeypatch.setattr(wf, "emit", lambda event, **payload: seen.append((event, payload)))
    return seen


# ---------------------------------------------------------------- world


def make_settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/t.db", **kw
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def store(settings: Settings) -> Store:
    return Store(settings.database_url)


def add_person(
    store: Store,
    name: str,
    email: str | None,
    status: EmailStatus = EmailStatus.VALID,
    title: str = "CEO",
) -> str:
    domain = email.split("@")[-1] if email and "@" in email else "nomail.example"
    pid = make_id("pe", domain, name)
    store.save_person(Person(id=pid, full_name=name, company_domain=domain, title=title))
    if email:
        store.save_email(pid, EmailCandidate(address=email, status=status, confidence=90))
    return pid


def add_mailbox(store: Store, settings: Settings, **kw: Any) -> str:
    fields = {
        "from_email": "me@sender.example",
        "from_name": "Alex Sender",
        "smtp_host": "smtp.sender.example",
        "smtp_port": 587,
        "smtp_security": "starttls",
        "smtp_username": "me@sender.example",
        "smtp_password": crypto.encrypt(settings, "pw"),
        "status": "ok",
        "window_start": 8,
        "window_end": 18,
        "window_days": "0,1,2,3,4",
        "timezone": "UTC",
        "daily_limit": 50,
        "signature": "Alex Sender\nYour Company",
        **kw,
    }
    return repo_for(store).create_mailbox(**fields)


def add_sequence(store: Store, mailbox_id: str | None, steps: list[dict[str, Any]], **kw: Any):
    return repo_for(store).create_sequence(
        "Intro", steps, status=kw.pop("status", "active"), mailbox_id=mailbox_id, **kw
    )


EMAIL_STEPS = [
    {
        "kind": "auto_email",
        "delay_days": 0,
        "subject": "Hi {{first_name}}",
        "body": "Hello {{first_name}} at {{company}}, {{title|friend}}",
    },
    {
        "kind": "auto_email",
        "delay_days": 2,
        "subject": "",
        "body": "Following up, {{first_name|there}}",
    },
]


@pytest.fixture
def world(store: Store, settings: Settings, smtp: type[FakeSMTP]) -> dict[str, Any]:
    repo_for(store).save_settings(gap_minutes=0, postal_address="1 Example Street, Lyon")
    mb = add_mailbox(store, settings)
    sid = add_sequence(store, mb, EMAIL_STEPS)
    pid = add_person(store, "Claire Martin", "claire@atelier.example")
    return {"mailbox": mb, "sequence": sid, "person": pid, "repo": repo_for(store)}


# ---------------------------------------------------------------- encryption + tokens


def test_encryption_round_trip_and_key_file(settings: Settings) -> None:
    token = crypto.encrypt(settings, "s3cret pass")
    assert token and "s3cret" not in token
    assert crypto.decrypt(settings, token) == "s3cret pass"
    key = settings.home / "secret.key"
    assert key.exists() and stat.S_IMODE(os.stat(key).st_mode) == 0o600
    assert crypto.encrypt(settings, "") == "" and crypto.decrypt(settings, "") == ""


def test_decrypt_with_another_key_gives_nothing(settings: Settings, tmp_path: Path) -> None:
    token = crypto.encrypt(settings, "pw")
    other = make_settings(tmp_path / "other")
    assert crypto.decrypt(other, token) == ""
    assert crypto.decrypt(settings, "not a token") == ""


def test_unsubscribe_token_round_trip(settings: Settings) -> None:
    token = crypto.make_unsubscribe_token(settings, "Claire@Atelier.example", 7)
    assert crypto.read_unsubscribe_token(settings, token) == {"e": "claire@atelier.example", "m": 7}


@pytest.mark.parametrize("bad", ["", "garbage", "a.b.c", "x" * 400])
def test_unsubscribe_token_rejects_garbage(settings: Settings, bad: str) -> None:
    with pytest.raises(crypto.TokenError) as err:
        crypto.read_unsubscribe_token(settings, bad)
    assert not err.value.expired


def test_unsubscribe_token_tampered_and_foreign(settings: Settings, tmp_path: Path) -> None:
    token = crypto.make_unsubscribe_token(settings, "a@b.example")
    tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
    with pytest.raises(crypto.TokenError):
        crypto.read_unsubscribe_token(settings, tampered)
    with pytest.raises(crypto.TokenError):
        crypto.read_unsubscribe_token(make_settings(tmp_path / "x"), token)


def test_unsubscribe_token_expired(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    token = crypto.make_unsubscribe_token(settings, "a@b.example")
    with pytest.raises(crypto.TokenError) as err:
        crypto.read_unsubscribe_token(settings, token, max_age=-1)
    assert err.value.expired


# ---------------------------------------------------------------- enrollment


def test_enroll_skips_suppressed_missing_invalid_and_duplicates(
    world: dict[str, Any], store: Store
) -> None:
    sid = world["sequence"]
    nomail = add_person(store, "No Mail", None)
    invalid = add_person(store, "Bad Status", "bad@x.example", EmailStatus.INVALID)
    syntax = add_person(store, "Bad Syntax", "not an address")
    supp = add_person(store, "Opted Out", "out@opted.example")
    dom = add_person(store, "Blocked Domain", "anyone@blocked.example")
    risky = add_person(store, "Catch All", "maybe@catch.example", EmailStatus.CATCH_ALL)
    store.add_suppression("out@opted.example")
    store.add_suppression("@blocked.example")
    ids = [
        world["person"],
        world["person"],
        nomail,
        invalid,
        syntax,
        supp,
        dom,
        risky,
        "pe_missing",
    ]
    counts = enroll(store, sid, ids)
    assert counts["enrolled"] == 2  # Claire + the catch-all address
    assert counts["no_email"] == 1 and counts["invalid_email"] == 2
    assert counts["suppressed"] == 2 and counts["missing"] == 1
    assert counts["skipped"] == 6
    again = enroll(store, sid, [world["person"]])
    assert again == {**again, "enrolled": 0, "skipped": 1, "duplicate": 1}
    rows = world["repo"].enrollments(sid)
    assert {r.email for r in rows} == {"claire@atelier.example", "maybe@catch.example"}
    assert rows[0].vars["first_name"] in ("Claire", "Catch")


def test_enroll_refuses_archived_or_missing_sequence(world: dict[str, Any], store: Store) -> None:
    world["repo"].update_sequence(world["sequence"], status="archived")
    with pytest.raises(core.EngageError):
        enroll(store, world["sequence"], [world["person"]])
    with pytest.raises(core.EngageError):
        enroll(store, "nope", [world["person"]])


def test_enroll_summary_mentions_skips() -> None:
    msg = core.enroll_summary("Intro", {"enrolled": 2, "no_email": 1, "suppressed": 1})
    assert msg.startswith("Added 2 contacts to Intro.")
    assert "1 no email" in msg and "do-not-contact" in msg


# ---------------------------------------------------------------- scheduler


def test_first_step_is_sent_with_footer_and_headers(
    world: dict[str, Any], store: Store, settings: Settings, smtp: type[FakeSMTP], events: list
) -> None:
    enroll(store, world["sequence"], [world["person"]])
    stats = run_due(store, settings, MONDAY)
    assert stats["sent"] == 1 and len(smtp.sent) == 1
    msg = smtp.sent[0]
    assert msg["Subject"] == "Hi Claire"
    assert msg["To"] == "claire@atelier.example"
    assert msg["From"] == "Alex Sender <me@sender.example>"
    assert msg["Message-ID"].endswith("@sender.example>")
    text = msg.get_body(("plain",)).get_content()
    assert "Hello Claire at Atelier, CEO" in text
    assert "Alex Sender\nYour Company" in text
    assert "1 Example Street, Lyon" in text
    assert (
        "/u/" in text and "/u/" in msg["List-Unsubscribe"] and "mailto:" in msg["List-Unsubscribe"]
    )
    html = msg.get_body(("html",)).get_content()
    assert "<img" not in html.lower()  # no tracking pixel
    token = text.split("/u/")[1].split()[0]
    assert crypto.read_unsubscribe_token(settings, token)["e"] == "claire@atelier.example"
    sent = world["repo"].messages(direction="out", statuses=["sent"])
    assert sent[0].message_id == msg["Message-ID"]
    kinds = [a.kind for a in ActivityRepo(store.engine).list_for(person_id=world["person"])]
    assert "email_sent" in kinds and "sequence" in kinds
    assert (
        "email_sent",
        {
            "person_id": world["person"],
            "sequence_id": world["sequence"],
            "message_id": msg["Message-ID"],
        },
    ) in events
    e = world["repo"].enrollments(world["sequence"])[0]
    assert e.step_index == 1 and e.next_run_at == MONDAY + timedelta(days=2)


def test_follow_up_waits_then_replies_in_thread(
    world: dict[str, Any], store: Store, settings: Settings, smtp: type[FakeSMTP]
) -> None:
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    assert run_due(store, settings, MONDAY + timedelta(days=1))["sent"] == 0
    stats = run_due(store, settings, MONDAY + timedelta(days=2, minutes=1))
    assert stats["sent"] == 1 and stats["finished"] == 1
    first, second = smtp.sent
    assert second["Subject"] == "Re: Hi Claire"
    assert second["In-Reply-To"] == first["Message-ID"] == second["References"]
    assert "Following up, Claire" in second.get_body(("plain",)).get_content()
    assert world["repo"].enrollments(world["sequence"])[0].status == "finished"


def test_sending_window_and_weekdays(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, settings, MONDAY.replace(hour=6))["sent"] == 0  # before 08:00
    saturday = MONDAY + timedelta(days=5)
    assert run_due(store, settings, saturday)["sent"] == 0
    assert run_due(store, settings, MONDAY.replace(hour=9))["sent"] == 1


def test_mailbox_time_zone_is_used(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    world["repo"].update_mailbox(world["mailbox"], timezone="America/New_York")
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, settings, MONDAY)["sent"] == 0  # 05:00 in New York
    assert run_due(store, settings, MONDAY.replace(hour=14))["sent"] == 1


def test_daily_limit(world: dict[str, Any], store: Store, settings: Settings, smtp) -> None:
    world["repo"].update_mailbox(world["mailbox"], daily_limit=2)
    ids = [add_person(store, f"Person {i}", f"p{i}@co{i}.example") for i in range(3)]
    enroll(store, world["sequence"], ids)
    assert run_due(store, settings, MONDAY)["sent"] == 2
    assert run_due(store, settings, MONDAY + timedelta(hours=2))["sent"] == 0
    assert run_due(store, settings, MONDAY + timedelta(days=1))["sent"] == 1


def test_gap_between_emails(world: dict[str, Any], store: Store, settings: Settings, smtp) -> None:
    world["repo"].save_settings(gap_minutes=5)
    ids = [add_person(store, f"Person {i}", f"p{i}@co{i}.example") for i in range(2)]
    enroll(store, world["sequence"], ids)
    assert run_due(store, settings, MONDAY)["sent"] == 1
    assert run_due(store, settings, MONDAY + timedelta(minutes=2))["sent"] == 0
    assert run_due(store, settings, MONDAY + timedelta(minutes=6))["sent"] == 1


def test_suppression_is_checked_again_at_send_time(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    enroll(store, world["sequence"], [world["person"]])
    store.add_suppression("claire@atelier.example")
    stats = run_due(store, settings, MONDAY)
    assert stats["sent"] == 0 and not smtp.sent
    assert world["repo"].enrollments(world["sequence"])[0].status == "unsubscribed"


def test_demo_mode_never_sends(world: dict[str, Any], store: Store, tmp_path: Path, smtp) -> None:
    demo = make_settings(tmp_path, mode="demo")
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, demo, MONDAY) == {"sent": 0, "disabled": 1}
    with pytest.raises(core.EngageError):
        core.send_email(store, demo, to="a@b.example", subject="s", body="b")
    mb = world["repo"].mailbox(world["mailbox"])
    with pytest.raises(core.EngageError):
        core.deliver(store, demo, mb, to="a@b.example", subject="s", body="b")
    assert not smtp.sent and smtp.instances == 0


def test_bounce_stops_and_suppresses(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    smtp.refuse["claire@atelier.example"] = (550, b"5.1.1 no such user")
    enroll(store, world["sequence"], [world["person"]])
    stats = run_due(store, settings, MONDAY)
    assert stats["bounced"] == 1 and stats["sent"] == 0
    assert world["repo"].enrollments(world["sequence"])[0].status == "bounced"
    assert world["repo"].messages(statuses=["bounced"])[0].error.startswith("550")
    assert store.is_suppressed("claire@atelier.example")


def test_bounce_can_only_stop(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    world["repo"].save_settings(bounce_action="stop")
    smtp.refuse["claire@atelier.example"] = (550, b"5.1.1 no such user")
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    assert not store.is_suppressed("claire@atelier.example")


def test_login_failure_keeps_the_contact_waiting(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    smtp.password = "changed"
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, settings, MONDAY)["sent"] == 0
    mb = world["repo"].mailbox(world["mailbox"])
    assert mb.status == "error" and "Login refused" in mb.last_error
    e = world["repo"].enrollments(world["sequence"])[0]
    assert e.status == "active" and e.step_index == 0
    assert world["repo"].messages(direction="out") == []
    smtp.password = "pw"
    assert run_due(store, settings, MONDAY + timedelta(minutes=1))["sent"] == 1


def test_no_mailbox_waits(store: Store, settings: Settings, smtp) -> None:
    sid = add_sequence(store, None, EMAIL_STEPS)
    pid = add_person(store, "Claire Martin", "claire@atelier.example")
    enroll(store, sid, [pid])
    assert run_due(store, settings, MONDAY)["waiting"] == 1
    assert repo_for(store).enrollments(sid)[0].note == "Waiting for a mailbox"


def test_paused_sequence_sends_nothing(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    world["repo"].update_sequence(world["sequence"], status="paused")
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, settings, MONDAY)["sent"] == 0


def test_task_steps_wait_for_completion(
    store: Store, settings: Settings, smtp, events: list
) -> None:
    repo_for(store).save_settings(gap_minutes=0)
    # every day, all day: completion times below come from the real clock
    mb = add_mailbox(store, settings, window_days="0,1,2,3,4,5,6", window_start=0, window_end=0)
    steps = [
        {"kind": "call", "delay_days": 0, "note": "Call {{first_name}}", "priority": "high"},
        {
            "kind": "manual_email",
            "delay_days": 1,
            "subject": "Notes for {{company}}",
            "body": "Hi {{first_name}}",
        },
        {"kind": "linkedin", "delay_days": 0, "note": "Connect"},
        {"kind": "auto_email", "delay_days": 0, "subject": "Last one", "body": "Bye"},
    ]
    sid = add_sequence(store, mb, steps)
    pid = add_person(store, "Claire Martin", "claire@atelier.example")
    enroll(store, sid, [pid])
    repo = repo_for(store)
    assert run_due(store, settings, MONDAY)["tasks"] == 1
    call = repo.tasks(statuses=["open"])[0]
    assert call.kind == "call" and call.priority == "high" and call.body == "Call Claire"
    assert call.title == "Call Claire Martin" and call.enrollment_id
    assert run_due(store, settings, MONDAY + timedelta(days=5))["tasks"] == 0  # still waiting
    core.finish_task(store, call.id, note="Voicemail")
    assert ("task_completed", {"task_id": call.id, "person_id": pid}) in events
    e = repo.enrollments(sid)[0]
    assert e.step_index == 1 and e.task_id is None
    run_due(store, settings, e.next_run_at + timedelta(minutes=1))
    draft = repo.tasks(statuses=["open"])[0]
    assert (
        draft.kind == "email" and draft.subject == "Notes for Atelier" and draft.body == "Hi Claire"
    )
    core.finish_task(store, draft.id, status="skipped", note="Not needed")
    e = repo.enrollments(sid)[0]
    run_due(store, settings, e.next_run_at + timedelta(minutes=1))
    linkedin = repo.tasks(statuses=["open"])[0]
    assert linkedin.kind == "linkedin"
    core.finish_task(store, linkedin.id)
    e = repo.enrollments(sid)[0]
    run_due(store, settings, e.next_run_at + timedelta(minutes=1))
    assert smtp.sent[-1]["Subject"] == "Last one"
    assert repo.enrollments(sid)[0].status == "finished"
    kinds = [a.kind for a in ActivityRepo(store.engine).list_for(person_id=pid, limit=100)]
    assert kinds.count("task") == 3 and kinds.count("task_done") == 3


def test_scheduled_one_off_goes_out_when_due(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    pid = world["person"]
    row_id, state = core.send_email(
        store,
        settings,
        to="claire@atelier.example",
        subject="Hi {{first_name}}",
        body="Later",
        person_id=pid,
        send_at=MONDAY + timedelta(days=1),
    )
    assert state == "scheduled" and not smtp.sent
    run_due(store, settings, MONDAY)
    assert not smtp.sent
    run_due(store, settings, MONDAY + timedelta(days=1, minutes=1))
    assert smtp.sent[0]["Subject"] == "Hi Claire"
    assert world["repo"].message(row_id).status == "sent"


def test_one_off_send_now_checks(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    row_id, state = core.send_email(
        store, settings, to="x@prospect.example", subject="Hello", body="Body"
    )
    assert state == "sent" and smtp.sent[0]["To"] == "x@prospect.example"
    store.add_suppression("@blocked.example")
    for to in ("y@blocked.example", "not-an-email"):
        with pytest.raises(core.EngageError):
            core.send_email(store, settings, to=to, subject="Hello", body="Body")
    with pytest.raises(core.EngageError):
        core.send_email(store, settings, to="x@prospect.example", subject="", body="Body")
    world["repo"].update_mailbox(world["mailbox"], daily_limit=1)
    with pytest.raises(core.EngageError, match="Daily limit"):
        core.send_email(store, settings, to="z@prospect.example", subject="Hello", body="Body")


def test_scheduler_lease_lets_one_pass_run(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    repo = world["repo"]
    assert repo.acquire_lock("scheduler", "other-host", MONDAY + timedelta(minutes=5), MONDAY)
    enroll(store, world["sequence"], [world["person"]])
    assert run_due(store, settings, MONDAY) == {"sent": 0, "busy": 1}
    assert run_due(store, settings, MONDAY + timedelta(minutes=6))["sent"] == 1  # lease expired


def test_deleting_a_step_keeps_contacts_on_track(world: dict[str, Any], store: Store) -> None:
    repo = world["repo"]
    enroll(store, world["sequence"], [world["person"]])
    e = repo.enrollments(world["sequence"])[0]
    repo.update_enrollment(e.id, step_index=1)
    repo.delete_step(repo.steps(world["sequence"])[0].id)
    assert repo.enrollments(world["sequence"])[0].step_index == 0
    assert [s.position for s in repo.steps(world["sequence"])] == [0]


# ---------------------------------------------------------------- replies


def reply_bytes(in_reply_to: str, body: str = "Sounds good, call me Thursday.", **h: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = h.get("frm", "Claire <claire@atelier.example>")
    msg["To"] = "me@sender.example"
    msg["Subject"] = h.get("subject", "Re: Hi Claire")
    msg["Message-ID"] = h.get("mid", "<reply-1@atelier.example>")
    msg["In-Reply-To"] = in_reply_to
    msg["Date"] = "Mon, 16 Nov 2026 12:00:00 +0000"
    msg.set_content(body + "\n\nOn Mon, Alex wrote:\n> Hello Claire")
    return msg.as_bytes().replace(b"\n", b"\r\n")


def test_reply_stops_the_sequence(
    world: dict[str, Any],
    store: Store,
    settings: Settings,
    smtp,
    imap: type[FakeIMAP],
    events: list,
) -> None:
    repo = world["repo"]
    repo.update_mailbox(
        world["mailbox"], imap_host="imap.sender.example", imap_port=993, imap_security="ssl"
    )
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    sent_id = smtp.sent[0]["Message-ID"]
    unrelated = EmailMessage()
    unrelated["From"], unrelated["Subject"], unrelated["Message-ID"] = (
        "x@y.example",
        "News",
        "<n@y>",
    )
    unrelated.set_content("hello")
    imap.messages = {5: unrelated.as_bytes(), 6: reply_bytes(sent_id)}
    found = poll_mailbox(
        store, settings, repo.mailbox(world["mailbox"]), MONDAY + timedelta(hours=2)
    )
    assert found == {"replies": 1, "bounces": 0}
    assert imap.selected[-1] == ("INBOX", True)  # read-only: nothing marked as read
    e = repo.enrollments(world["sequence"])[0]
    assert e.status == "replied"
    inbound = repo.messages(direction="in")
    assert inbound[0].in_reply_to == sent_id and "Thursday" in inbound[0].body
    assert repo.messages(direction="out")[0].replied_at is not None
    assert (
        "email_replied",
        {"person_id": world["person"], "sequence_id": world["sequence"]},
    ) in events
    kinds = [a.kind for a in ActivityRepo(store.engine).list_for(person_id=world["person"])]
    assert "email_reply" in kinds
    # polling again neither duplicates nor sends the follow-up
    again = poll_mailbox(
        store, settings, repo.mailbox(world["mailbox"]), MONDAY + timedelta(hours=3)
    )
    assert again["replies"] == 0 and len(repo.messages(direction="in")) == 1
    assert repo.mailbox(world["mailbox"]).imap_last_uid == 6
    run_due(store, settings, MONDAY + timedelta(days=3))
    assert len(smtp.sent) == 1


def test_reply_asking_to_stop_suppresses(
    world: dict[str, Any], store: Store, settings: Settings, smtp, imap
) -> None:
    repo = world["repo"]
    repo.update_mailbox(world["mailbox"], imap_host="imap.sender.example")
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    imap.messages = {1: reply_bytes(smtp.sent[0]["Message-ID"], body="Please unsubscribe me.")}
    poll_mailbox(store, settings, repo.mailbox(world["mailbox"]), MONDAY + timedelta(hours=1))
    assert store.is_suppressed("claire@atelier.example")
    assert repo.enrollments(world["sequence"])[0].status == "unsubscribed"


def test_delivery_report_marks_a_bounce(
    world: dict[str, Any], store: Store, settings: Settings, smtp, imap
) -> None:
    repo = world["repo"]
    repo.update_mailbox(world["mailbox"], imap_host="imap.sender.example")
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    sent_id = smtp.sent[0]["Message-ID"]
    dsn = (
        "From: Mail Delivery System <MAILER-DAEMON@mx.example>\r\n"
        "To: me@sender.example\r\nSubject: Undelivered Mail Returned to Sender\r\n"
        "Message-ID: <dsn-1@mx.example>\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/plain\r\n\r\nThe mail could not be delivered.\r\n"
        f"--b\r\nContent-Type: text/rfc822-headers\r\n\r\nMessage-ID: {sent_id}\r\n--b--\r\n"
    ).encode()
    imap.messages = {3: dsn}
    found = poll_mailbox(
        store, settings, repo.mailbox(world["mailbox"]), MONDAY + timedelta(hours=1)
    )
    assert found["bounces"] == 1
    assert repo.messages(direction="out")[0].status == "bounced"
    assert repo.enrollments(world["sequence"])[0].status == "bounced"


def test_imap_login_failure_is_reported(
    world: dict[str, Any], store: Store, settings: Settings, smtp, imap
) -> None:
    repo = world["repo"]
    repo.update_mailbox(world["mailbox"], imap_host="imap.sender.example")
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    imap.password = "other"
    poll_mailbox(store, settings, repo.mailbox(world["mailbox"]), MONDAY + timedelta(hours=1))
    mb = repo.mailbox(world["mailbox"])
    assert mb.imap_status == "error" and "refused" in mb.imap_error


def test_run_due_polls_replies_on_its_own(
    world: dict[str, Any], store: Store, settings: Settings, smtp, imap
) -> None:
    repo = world["repo"]
    repo.update_mailbox(world["mailbox"], imap_host="imap.sender.example")
    enroll(store, world["sequence"], [world["person"]])
    run_due(store, settings, MONDAY)
    imap.messages = {1: reply_bytes(smtp.sent[0]["Message-ID"])}
    assert run_due(store, settings, MONDAY + timedelta(minutes=10))["replies"] == 1


def test_manual_mark_as_replied(world: dict[str, Any], store: Store, events: list) -> None:
    enroll(store, world["sequence"], [world["person"]])
    e = world["repo"].enrollments(world["sequence"])[0]
    assert "replied" in core.set_contact_status(store, e.id, "replied")
    assert world["repo"].enrollment(e.id).status == "replied"
    assert events[-1][0] == "email_replied"


def test_contact_pause_resume_remove(world: dict[str, Any], store: Store) -> None:
    enroll(store, world["sequence"], [world["person"]])
    e = world["repo"].enrollments(world["sequence"])[0]
    core.set_contact_status(store, e.id, "pause")
    with pytest.raises(core.EngageError):
        core.set_contact_status(store, e.id, "pause")
    core.set_contact_status(store, e.id, "resume")
    assert world["repo"].enrollment(e.id).status == "active"
    core.set_contact_status(store, e.id, "remove")
    assert world["repo"].enrollment(e.id).status == "finished"


def test_unsubscribe_stops_everything(
    world: dict[str, Any], store: Store, settings: Settings, smtp
) -> None:
    enroll(store, world["sequence"], [world["person"]])
    core.send_email(
        store,
        settings,
        to="claire@atelier.example",
        subject="Later",
        body="b",
        send_at=MONDAY + timedelta(days=9),
    )
    assert core.unsubscribe(store, "claire@atelier.example") == 1
    assert store.is_suppressed("claire@atelier.example")
    assert world["repo"].enrollments(world["sequence"])[0].status == "unsubscribed"
    assert world["repo"].messages(statuses=["scheduled"]) == []


def test_wants_out() -> None:
    assert core.wants_out("Please unsubscribe me")
    assert core.wants_out("Remove me from your list.\n\nThanks")
    assert not core.wants_out("Thanks, sounds good!\n\nOn Mon wrote:\n> unsubscribe link below")


# ---------------------------------------------------------------- tasks + contracts


def test_create_task_contract(store: Store) -> None:
    pid = add_person(store, "Claire Martin", "claire@atelier.example")
    due = datetime(2026, 11, 20, 9, tzinfo=UTC)
    tid = create_task(store, "Call back", person_id=pid, due=due, kind="call")
    task = repo_for(store).task(tid)
    assert task.title == "Call back" and task.kind == "call" and task.due_at == due
    assert repo_for(store).task(create_task(store, "Other", kind="fax")).kind == "action"
    kinds = [a.kind for a in ActivityRepo(store.engine).list_for(person_id=pid)]
    assert kinds == ["task"]


def test_create_task_works_with_a_store_without_engine() -> None:
    from tests.fakes import FakeStore

    fake = FakeStore()
    tid = create_task(fake, "Follow up", company_id="co_1")
    assert repo_for(fake).task(tid).company_id == "co_1"


def test_finish_task_twice_is_refused(store: Store) -> None:
    tid = create_task(store, "Once")
    core.finish_task(store, tid)
    with pytest.raises(core.EngageError):
        core.finish_task(store, tid)
    core.reopen_task(store, tid)
    assert repo_for(store).task(tid).status == "open"


# ---------------------------------------------------------------- mailer + render


def test_test_connection_never_sends(store: Store, settings: Settings, smtp) -> None:
    mb = repo_for(store).mailbox(add_mailbox(store, settings))
    assert "Signed in" in mailer.test_smtp(settings, mb)
    assert smtp.logins == [("me@sender.example", "pw")] and smtp.sent == []
    smtp.unreachable = True
    with pytest.raises(mailer.MailboxError):
        mailer.test_smtp(settings, mb)


def test_render_variables_and_fallbacks() -> None:
    values = {"first_name": "Claire", "company": ""}
    assert (
        core.render("Hi {{ first_name }} at {{company|your team}}", values)
        == "Hi Claire at your team"
    )
    from leadcommons.engage.render import missing_vars, unknown_vars

    assert missing_vars("{{company}} {{title|x}}", values) == ["company"]
    assert unknown_vars("{{first_name}} {{nickname}}") == ["nickname"]


def test_message_parses_back(settings: Settings) -> None:
    out = mailer.Outgoing(
        to="a@b.example", subject="S", text="T", html="<p>T</p>", from_email="me@x.example"
    )
    msg, mid = mailer.build_message(out)
    parsed = message_from_bytes(msg.as_bytes())
    assert parsed["Message-ID"] == mid and parsed["Subject"] == "S"


# ---------------------------------------------------------------- background thread


def test_background_scheduler_starts_only_where_allowed(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from leadcommons.web.app import create_app
    from tests.fakes import FakeJobs, FakeSearch

    def running() -> bool:
        return any(t.name == "engage-scheduler" and t.is_alive() for t in threading.enumerate())

    def app_for(**kw: Any) -> Any:
        s = make_settings(tmp_path / str(len(kw)) / "_".join(kw), **kw)
        s.ensure_dirs()
        return create_app(
            settings=s, search=FakeSearch(), store=Store(s.database_url), jobs=FakeJobs()
        )

    for kw in ({"mode": "demo"}, {"sync_jobs": True}):
        with TestClient(app_for(**kw)) as c:
            assert c.get("/healthz").status_code == 200
            assert not running()
    with TestClient(app_for()) as c:
        assert c.get("/healthz").status_code == 200
        assert running()
