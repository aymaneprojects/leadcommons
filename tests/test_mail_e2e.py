"""End to end: a mailbox added in the UI, a sequence sent, an unsubscribe, a reply and a bounce.

SMTP and IMAP servers run on 127.0.0.1 over real sockets (tests/mailserver.py); nothing in
smtplib or imaplib is patched. The self-signed certificate is trusted through SSL_CERT_FILE,
so the client still verifies certificates like it does against a real provider.
"""

from __future__ import annotations

import email
import email.policy
import re
from collections.abc import Iterator
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from pepite.cli import app as cli_app
from pepite.engage import crypto
from pepite.models import utcnow
from pepite.store import Store
from pepite.store.engage import repo_for
from pepite.web.app import create_app
from tests.fakes import FakeJobs, FakeSearch
from tests.mailserver import PASSWORD, USER, IMAPServer, SMTPServer, make_cert
from tests.test_engage import add_person, make_settings

HX = {"HX-Request": "true"}


def notice(r: Any) -> str:
    """The message the user sees after a form post (toast, redirect notice or HX-Redirect)."""
    import json

    if "HX-Trigger" in r.headers:
        return json.loads(r.headers["HX-Trigger"])["toast"]["message"]
    url = r.headers.get("HX-Redirect") or r.headers.get("location") or ""
    return parse_qs(urlparse(url).query).get("notice", [""])[0]


@pytest.fixture(scope="module")
def tls(tmp_path_factory: pytest.TempPathFactory) -> Any:
    import ssl

    path = make_cert(tmp_path_factory.mktemp("cert"))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(path)
    ctx.cert_path = path  # type: ignore[attr-defined]
    return ctx


@pytest.fixture(autouse=True)
def _trust_test_cert(monkeypatch: pytest.MonkeyPatch, tls: Any) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(tls.cert_path))


@pytest.fixture
def servers(tls: Any) -> Iterator[dict[str, Any]]:
    made: list[Any] = []

    def start(kind: type, mode: str) -> Any:
        srv = kind(tls, mode)
        made.append(srv)
        return srv

    yield {"start": start}
    for srv in made:
        srv.stop()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setenv("PEPITE_DATA_DIR", str(tmp_path / "packs"))
    monkeypatch.setenv("PEPITE_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("PEPITE_MODE", "local")
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    app = create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )
    c = TestClient(app, follow_redirects=False)
    c.get("/settings/sending")  # the app learns its own address (used in unsubscribe links)
    return c


def repo_of(client: TestClient) -> Any:
    return repo_for(client.app.state.store)  # type: ignore[attr-defined]


def mailbox_form(smtp: SMTPServer, imap: IMAPServer | None = None, **kw: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "from_name": "Zoé Léger",
        "from_email": USER,
        "name": "Test box",
        "smtp_host": "127.0.0.1",
        "smtp_port": smtp.port,
        "smtp_security": smtp.mode,
        "smtp_username": USER,
        "smtp_password": PASSWORD,
        "daily_limit": 50,
        "window_start": 0,
        "window_end": 0,  # start == end: always open
        "days": [0, 1, 2, 3, 4, 5, 6],
        "timezone": "UTC",
        "signature": "Zoé Léger\nSender Co",
    }
    if imap:
        form.update(imap_host="127.0.0.1", imap_port=imap.port, imap_security=imap.mode)
    form.update(kw)
    return form


def add_mailbox(client: TestClient, form: dict[str, Any]) -> str:
    r = client.post("/settings/mailboxes", data=form, headers=HX)
    assert r.status_code == 200, notice(r)
    return repo_of(client).mailboxes()[-1].id


def received(smtp: SMTPServer) -> list[EmailMessage]:
    return [email.message_from_bytes(raw, policy=email.policy.default) for *_, raw in smtp.messages]  # type: ignore[misc]


def body_of(msg: Any) -> str:
    return msg.get_body(preferencelist=("plain",)).get_content()


def sequence_with_people(
    client: TestClient, people: list[str], steps: list[dict[str, Any]] | None = None
) -> tuple[str, list[str]]:
    """Create, fill, activate a sequence and enrol people, all through the routes."""
    steps = steps or [
        {"delay_days": 0, "subject": "Café pour {{first_name}} ({{company}})",
         "body": "Bonjour {{first_name|there}},\n\nUne idée pour {{company}}.\n\n{{sender_name}}"},
        {"delay_days": 2, "subject": "", "body": "Je reviens vers vous, {{first_name}}."},
    ]  # fmt: skip
    r = client.post("/sequences", data={"name": "E2E"}, headers=HX)
    sid = urlparse(r.headers["HX-Redirect"]).path.rsplit("/", 1)[-1]
    for s in steps:
        r = client.post(f"/sequences/{sid}/steps", data={"kind": "auto_email", **s}, headers=HX)
        assert r.status_code == 200, notice(r)
    pids = []
    for name, address in (p.split("|") for p in people):
        pids.append(add_person(client.app.state.store, name, address))  # type: ignore[attr-defined]
    r = client.post("/sequences/enroll", data={"sequence_id": sid, "ids": pids}, headers=HX)
    assert r.status_code == 200, notice(r)
    r = client.post(f"/sequences/{sid}/status", data={"action": "activate"}, headers=HX)
    assert r.status_code == 200, notice(r)
    return sid, pids


def run_pass(client: TestClient) -> str:
    r = client.post("/settings/sending/run", headers=HX)
    return notice(r)


def unsubscribe_path(msg: Any) -> str:
    m = re.search(r"Unsubscribe: https?://[^/\s]+(/u/\S+)", body_of(msg))
    assert m, body_of(msg)
    return m.group(1)


# ---------------------------------------------------------------- mailbox modes


@pytest.mark.parametrize("mode", ["none", "starttls", "ssl"])
def test_mailbox_connects_in_every_security_mode(
    client: TestClient, servers: dict[str, Any], mode: str
) -> None:
    smtp, imap = servers["start"](SMTPServer, mode), servers["start"](IMAPServer, mode)
    mid = add_mailbox(client, mailbox_form(smtp, imap))
    box = repo_of(client).mailbox(mid)
    assert box.smtp_password and PASSWORD not in box.smtp_password  # encrypted at rest
    assert crypto.decrypt(client.app.state.settings, box.smtp_password) == PASSWORD  # type: ignore[attr-defined]
    r = client.post(f"/settings/mailboxes/{mid}/test", headers=HX)
    assert r.status_code == 200 and "Signed in" in notice(r), notice(r)
    box = repo_of(client).mailbox(mid)
    assert (box.status, box.imap_status) == ("ok", "ok")
    assert imap.selected == ["INBOX"] and not smtp.messages  # a test never sends
    if mode == "starttls":
        assert smtp.starttls_seen == 1


def test_wrong_password_and_dead_host_give_a_clear_message(
    client: TestClient, servers: dict[str, Any]
) -> None:
    smtp = servers["start"](SMTPServer, "starttls")
    imap = servers["start"](IMAPServer, "ssl")
    mid = add_mailbox(client, mailbox_form(smtp, imap, smtp_password="wrong"))
    r = client.post(f"/settings/mailboxes/{mid}/test", headers=HX)
    assert r.status_code == 422 and "Login refused" in notice(r)
    assert repo_of(client).mailbox(mid).status == "error"

    # SMTP fine, IMAP password wrong: sending works, the message says reply checks do not.
    mid = add_mailbox(client, mailbox_form(smtp, imap, imap_password="wrong"))
    r = client.post(f"/settings/mailboxes/{mid}/test", headers=HX)
    assert "sending works, but reply checks fail" in notice(r) and "IMAP login refused" in notice(r)

    dead = SMTPServer()
    port = dead.port
    dead.stop()
    mid = add_mailbox(client, mailbox_form(smtp, smtp_host="127.0.0.1", smtp_port=port))
    r = client.post(f"/settings/mailboxes/{mid}/test", headers=HX)
    assert r.status_code == 422 and f"Nothing is listening on 127.0.0.1:{port}" in notice(r)
    assert "Traceback" not in r.text


def test_security_mismatch_is_explained(client: TestClient, servers: dict[str, Any]) -> None:
    """Typical mistake: STARTTLS selected for an SSL port, or SSL for a STARTTLS port."""
    smtp = servers["start"](SMTPServer, "ssl")
    mid = add_mailbox(client, mailbox_form(smtp, smtp_security="starttls"))
    msg = notice(client.post(f"/settings/mailboxes/{mid}/test", headers=HX))
    assert "SSL/TLS" in msg or "security" in msg.lower(), msg
    smtp2 = servers["start"](SMTPServer, "starttls")
    mid = add_mailbox(client, mailbox_form(smtp2, smtp_security="ssl"))
    msg = notice(client.post(f"/settings/mailboxes/{mid}/test", headers=HX))
    assert "SSL/TLS" in msg or "security" in msg.lower(), msg


def test_blank_username_and_port_get_sensible_defaults(
    client: TestClient, servers: dict[str, Any]
) -> None:
    smtp = servers["start"](SMTPServer, "ssl")
    form = mailbox_form(smtp, smtp_username="", smtp_port="")
    form.update(smtp_host="127.0.0.1", imap_host="127.0.0.1", imap_port="", imap_security="ssl")
    box = repo_of(client).mailbox(add_mailbox(client, form))
    assert (box.smtp_port, box.imap_port, box.smtp_username) == (465, 993, USER)
    server_none = servers["start"](SMTPServer, "none")
    mid = add_mailbox(client, mailbox_form(server_none, smtp_security="starttls"))
    msg = notice(client.post(f"/settings/mailboxes/{mid}/test", headers=HX))
    assert "does not offer STARTTLS" in msg, msg


def test_smtp_without_imap_still_works(client: TestClient, servers: dict[str, Any]) -> None:
    smtp = servers["start"](SMTPServer, "starttls")
    mid = add_mailbox(client, mailbox_form(smtp))
    assert not repo_of(client).mailbox(mid).has_imap
    assert "Signed in" in notice(client.post(f"/settings/mailboxes/{mid}/test", headers=HX))
    r = client.post(f"/settings/mailboxes/{mid}/poll", headers=HX)
    assert r.status_code in (200, 422) and "Traceback" not in r.text


# ---------------------------------------------------------------- the whole journey


def test_sequence_unsubscribe_reply_and_bounce(client: TestClient, servers: dict[str, Any]) -> None:
    smtp = servers["start"](SMTPServer, "starttls")
    imap = servers["start"](IMAPServer, "ssl")
    mid = add_mailbox(client, mailbox_form(smtp, imap))
    repo = repo_of(client)
    repo.save_settings(
        postal_address="1 Example Street, Lyon", public_url="https://pepite.example", gap_minutes=0
    )
    sid, (ana, ben, cleo, dan, eli) = sequence_with_people(
        client,
        [
            "Élodie Müller|elodie@atelier.example",
            "Ben Roe|ben@example.com",
            "Cléo Roy|cleo@example.org",
            "Dan Bounce|dan@gone.example",
            "Eli Roe|eli@example.net",
        ],
    )
    smtp.refuse.add("dan@gone.example")

    assert "4 sent" in run_pass(client)  # Dan is refused by the server: a bounce
    msgs = received(smtp)
    assert [m["To"] for m in msgs] == [
        "elodie@atelier.example", "ben@example.com", "cleo@example.org", "eli@example.net"
    ]  # fmt: skip
    first = msgs[0]
    assert str(first["From"]) == f"Zoé Léger <{USER}>"
    assert first["Subject"] == "Café pour Élodie (Atelier)" or first["Subject"].startswith(
        "Café pour Élodie"
    )
    assert "{{" not in first["Subject"] and "{{" not in body_of(first)
    text = body_of(first)
    assert "Bonjour Élodie," in text and "Zoé Léger" in text and "1 Example Street, Lyon" in text
    assert first["Message-ID"].endswith("@sender.example>")
    path = unsubscribe_path(first)
    assert path in first["List-Unsubscribe"] and "mailto:" in first["List-Unsubscribe"]
    assert first["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"  # public_url is https
    assert first.get_body(preferencelist=("html",)) is not None
    assert not [p for p in first.walk() if p.get_content_type() == "image/gif"]  # no pixel
    dan_row = repo.messages(direction="out", q="dan@gone.example")[0]
    assert dan_row.status == "bounced"
    assert client.app.state.store.is_suppressed("dan@gone.example")  # type: ignore[attr-defined]

    # Unsubscribe from the link in the email: opening it changes nothing, confirming does.
    r = client.get(path)
    assert r.status_code == 200 and not client.app.state.store.is_suppressed("ben@example.com")  # type: ignore[attr-defined]
    ben_path = unsubscribe_path(msgs[1])
    assert client.post(ben_path).status_code == 200
    assert client.app.state.store.is_suppressed("ben@example.com")  # type: ignore[attr-defined]
    assert {e.email: e.status for e in repo.enrollments(sid)}["ben@example.com"] == "unsubscribed"

    # A reply from Ana lands in the inbox: next poll marks her enrolment replied.
    sent_id = msgs[0]["Message-ID"]
    reply = EmailMessage()
    reply["From"], reply["To"], reply["Subject"] = (
        "Élodie Müller <elodie@atelier.example>",
        USER,
        "Re: Café",
    )
    reply["Message-ID"], reply["In-Reply-To"] = "<reply-1@atelier.example>", sent_id
    reply["Date"] = "Tue, 24 Nov 2026 10:00:00 +0000"
    reply.set_content("Avec plaisir, appelez-moi demain.")
    imap.add(reply.as_bytes())
    r = client.post(f"/settings/mailboxes/{mid}/poll", headers=HX)
    assert "1 new repl" in notice(r), notice(r)
    by_email = {e.email: e for e in repo.enrollments(sid)}
    assert by_email["elodie@atelier.example"].status == "replied"
    assert any(m.direction == "in" and "Avec plaisir" in m.body for m in repo.messages(limit=50))

    # A delivery report quoting Cléo's message marks her bounced.
    report = (
        "From: MAILER-DAEMON@sender.example\r\nTo: " + USER + "\r\nSubject: Undelivered\r\n"
        "Message-ID: <dsn-1@sender.example>\r\nDate: Tue, 24 Nov 2026 11:00:00 +0000\r\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/plain\r\n\r\nMailbox full.\r\n--b\r\n"
        "Content-Type: message/rfc822\r\n\r\n"
        f"Message-ID: {msgs[2]['Message-ID']}\r\nTo: cleo@example.org\r\n\r\nhi\r\n--b--\r\n"
    )
    imap.add(report.encode())
    client.post(f"/settings/mailboxes/{mid}/poll", headers=HX)
    by_email = {e.email: e for e in repo.enrollments(sid)}
    assert by_email["cleo@example.org"].status == "bounced"

    # Step two is due for everyone still active: only Eli gets it (Ana replied, Ben left,
    # Cléo bounced, Dan was refused).
    for e in repo.enrollments(sid):
        if e.status == "active":
            repo.update_enrollment(e.id, next_run_at=utcnow() - timedelta(minutes=1))
    before = len(smtp.messages)
    run_pass(client)
    after = received(smtp)[before:]
    assert [str(m["To"]) for m in after] == ["eli@example.net"]
    follow = after[0]
    assert follow["Subject"].startswith("Re: Café pour")  # empty subject: same thread
    assert follow["In-Reply-To"] == msgs[3]["Message-ID"]
    assert "Je reviens vers vous, Eli" in body_of(follow)


def test_worker_once_sends(client: TestClient, servers: dict[str, Any], tmp_path: Path) -> None:
    smtp = servers["start"](SMTPServer, "ssl")
    add_mailbox(client, mailbox_form(smtp))
    repo_of(client).save_settings(gap_minutes=0)
    sequence_with_people(client, ["Ana Roe|ana@example.com"])
    result = CliRunner().invoke(cli_app, ["worker", "--once"])
    assert result.exit_code == 0, result.output
    (msg,) = received(smtp)
    assert msg["To"] == "ana@example.com" and "Unsubscribe: http://testserver/u/" in body_of(msg)
    assert "List-Unsubscribe-Post" not in msg  # plain http link: no one-click


def test_send_window_and_daily_limit(client: TestClient, servers: dict[str, Any]) -> None:
    smtp = servers["start"](SMTPServer, "none")
    mid = add_mailbox(client, mailbox_form(smtp, daily_limit=1))
    repo_of(client).save_settings(gap_minutes=0)
    sequence_with_people(client, ["A One|a@example.com", "B Two|b@example.com"])
    assert "1 sent" in run_pass(client) and "waiting" in run_pass(client)
    assert len(smtp.messages) == 1  # the second waits for tomorrow
    # Outside the sending window (one hour that is not now): nothing goes out.
    repo_of(client).update_mailbox(mid, daily_limit=50)
    hour = (utcnow().hour + 6) % 24
    repo_of(client).update_mailbox(mid, window_start=hour, window_end=(hour + 1) % 24)
    run_pass(client)
    assert len(smtp.messages) == 1


def test_demo_mode_refuses_to_send(tmp_path: Path, servers: dict[str, Any]) -> None:
    settings = make_settings(tmp_path, mode="demo")
    settings.ensure_dirs()
    c = TestClient(
        create_app(
            settings=settings,
            search=FakeSearch(),
            store=Store(settings.database_url),
            jobs=FakeJobs(),
        ),
        follow_redirects=False,
    )
    r = c.post("/settings/sending/run", headers=HX)
    assert r.status_code == 422 and "demo" in notice(r).lower()
