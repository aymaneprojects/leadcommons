"""Calls: calling list, click-to-call links, call log, history filters, follow-up tasks."""
# ruff: noqa: F811  (fixtures imported from test_deals_support)

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from pepite import engage, insights
from pepite.models import utcnow
from pepite.store.pipeline import Pipeline
from pepite.web.pages_pipeline import (
    duration_label,
    insight_phone,
    parse_duration_sec,
    tel,
)
from tests.test_deals_support import HX, app, client, people, pl  # noqa: F401


@pytest.fixture
def company_phones(monkeypatch: pytest.MonkeyPatch, people) -> dict[str, str]:
    phones = {people["atelier"].id: "+33 4 72 00 00 01"}
    monkeypatch.setattr(
        insights,
        "get",
        lambda store, cid: SimpleNamespace(phones=[phones[cid]]) if cid in phones else None,
    )
    return phones


# ---- helpers


def test_phone_helpers() -> None:
    assert tel("+33 (0)4 72-00") == "tel:+330472" + "00"
    assert (
        duration_label(0) == "—"
        and duration_label(45) == "45 s"
        and duration_label(200) == "3 min 20 s"
    )
    assert parse_duration_sec("3", "20") == 200
    assert parse_duration_sec("", "75") == 59  # seconds are capped at 59
    assert parse_duration_sec("x", None) == 0


def test_insight_phone_reads_common_shapes() -> None:
    assert insight_phone(None) is None
    assert insight_phone(SimpleNamespace(phone="+1 555 0100")) == "+1 555 0100"
    assert insight_phone({"phones": [{"number": "+1 555 0101"}]}) == "+1 555 0101"
    assert (
        insight_phone(SimpleNamespace(phones=[SimpleNamespace(number="+1 555 0102")]))
        == "+1 555 0102"
    )
    assert insight_phone(SimpleNamespace(phones=[])) is None


# ---- calling list


def test_calling_list_uses_company_phone_and_click_to_call_link(
    client: TestClient, people, company_phones
) -> None:
    html = client.get("/calls").text
    assert "does not place calls" in html  # no telephony provider, said plainly
    assert "Claire Martin" in html and "+33 4 72 00 00 01" in html
    assert 'href="tel:+33472000001"' in html and "Company" in html
    assert "Jonas Weber" not in html  # no known number
    nophone = client.get("/calls?only=nophone").text
    assert "Jonas Weber" in nophone and "Add a number" in nophone


def test_calling_list_without_insights_does_not_crash(client: TestClient, people) -> None:
    # insights.get still raises NotImplementedError in this build: handled as "no number"
    r = client.get("/calls")
    assert r.status_code == 200 and "Your calling list is empty" in r.text


def test_add_number_by_hand_puts_person_on_the_list(
    client: TestClient, pl: Pipeline, people
) -> None:
    jonas = people["jonas"]
    r = client.post(
        "/calls/phone", headers=HX, data={"person_id": jonas.id, "phone": "+49 40 1234567"}
    )
    assert r.status_code == 200
    html = client.get("/calls").text
    assert "Jonas Weber" in html and "Added by you" in html and "tel:+49401234567" in html
    bad = client.post("/calls/phone", headers=HX, data={"person_id": jonas.id, "phone": "abc"})
    assert bad.status_code == 422
    assert (
        client.post(
            "/calls/phone", headers=HX, data={"person_id": "zzz", "phone": "+1 555 0100"}
        ).status_code
        == 404
    )
    client.post("/calls/phone", data={"person_id": jonas.id, "phone": ""})
    assert pl.phones([jonas.id]) == {}


def test_uncalled_filter_and_last_call_column(
    client: TestClient, pl: Pipeline, people, company_phones
) -> None:
    claire = people["claire"]
    assert "Claire Martin" in client.get("/calls?only=uncalled").text
    pl.log_call(
        "voicemail", person_id=claire.id, person_name="Claire Martin", phone="+33 4 72 00 00 01"
    )
    assert "Claire Martin" not in client.get("/calls?only=uncalled").text
    assert "Left voicemail" in client.get("/calls").text


# ---- call log form


def test_call_log_partial_prefills_number_and_person(
    client: TestClient, people, company_phones
) -> None:
    claire = people["claire"]
    html = client.get(f"/partials/call-log?person_id={claire.id}", headers=HX).text
    assert "<dialog" in html and "data-autoopen" in html
    assert "Claire Martin" in html and 'value="+33 4 72 00 00 01"' in html
    for label in ("Connected", "No answer", "Left voicemail", "Wrong number", "Busy"):
        assert label in html
    assert "/partials/task-new?person_id=" in html  # link to the engage task dialog


def test_log_a_call(client: TestClient, pl: Pipeline, people) -> None:
    claire, atelier = people["claire"], people["atelier"]
    r = client.post(
        "/calls",
        headers=HX,
        data={
            "person_id": claire.id,
            "phone": "+33 4 72 00 00 02",
            "outcome": "connected",
            "minutes": "3",
            "seconds": "20",
            "notes": "Wants a quote\nCall back Friday",
            "remember": "1",
            "next": "/calls?tab=history",
        },
    )
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/calls?tab=history")
    (call,) = pl.list_calls()
    assert call.outcome == "connected" and call.duration_sec == 200
    assert call.person_name == "Claire Martin" and call.company_id == atelier.id
    assert pl.phones([claire.id])[claire.id] == "+33 4 72 00 00 02"
    (a,) = pl.activity.list_for(person_id=claire.id, kinds=["call"])
    assert a.summary.startswith("Call · Connected · 3 min 20 s: Wants a quote")
    assert a.meta["call_id"] == call.id and a.meta["outcome"] == "connected"
    assert pl.activity.list_for(company_id=atelier.id, kinds=["call"])  # company timeline too


def test_log_call_validation(client: TestClient, pl: Pipeline) -> None:
    assert client.post("/calls", headers=HX, data={"phone": "+1 555 0100"}).status_code == 422
    nobody = client.post("/calls", headers=HX, data={"outcome": "busy"})
    assert nobody.status_code == 422 and "Say who you called" in nobody.headers["HX-Trigger"]
    bad = client.post("/calls", headers=HX, data={"outcome": "teleported", "phone": "1"})
    assert bad.status_code == 422
    assert pl.list_calls() == []
    plain = client.post("/calls", data={"outcome": "no_answer", "phone": "+1 555 0100"})
    assert plain.status_code == 303 and pl.list_calls()[0].outcome == "no_answer"


def test_call_log_without_a_person_keeps_typed_names(client: TestClient, pl: Pipeline) -> None:
    client.post(
        "/calls",
        data={
            "outcome": "wrong_number",
            "person_name": "Unknown caller",
            "company_name": "Some Co",
            "phone": "123456",
        },
    )
    (call,) = pl.list_calls()
    assert (
        call.person_name == "Unknown caller"
        and call.person_id is None
        and call.company_name == "Some Co"
    )


def test_follow_up_task_uses_engage(client: TestClient, pl: Pipeline, people, monkeypatch) -> None:
    seen: list[dict] = []

    def create_task(store, title, **kw):
        seen.append({"title": title, **kw})
        return "task_123"

    monkeypatch.setattr(engage, "create_task", create_task)
    claire = people["claire"]
    client.post(
        "/calls",
        headers=HX,
        data={
            "person_id": claire.id,
            "outcome": "voicemail",
            "followup": "1",
            "followup_title": "Call Claire back",
            "followup_due": "2026-12-01",
        },
    )
    assert seen[0]["title"] == "Call Claire back" and seen[0]["person_id"] == claire.id
    assert seen[0]["kind"] == "call" and seen[0]["due"].date().isoformat() == "2026-12-01"
    assert pl.list_calls()[0].followup_task_id == "task_123"
    assert "Follow-up task created" in client.get("/calls?tab=history").text


def test_follow_up_task_failure_still_logs_the_call(
    client: TestClient, pl: Pipeline, people, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(engage, "create_task", unavailable)  # the contract's default state
    r = client.post(
        "/calls",
        headers=HX,
        data={"person_id": people["claire"].id, "outcome": "connected", "followup": "1"},
    )
    assert r.status_code == 200 and "could not be created" in r.headers["HX-Trigger"]
    assert pl.list_calls()[0].followup_task_id is None


# ---- history


def test_call_history_filters(client: TestClient, pl: Pipeline) -> None:
    pl.log_call("connected", person_name="Alice Roy", phone="+33 1 11", notes="Great chat")
    pl.log_call("busy", person_name="Bob Stone", phone="+33 2 22")
    pl.log_call("connected", person_name="Old Timer", created_at=utcnow() - timedelta(days=40))
    everything = client.get("/calls?tab=history").text
    assert all(n in everything for n in ("Alice Roy", "Bob Stone", "Old Timer"))
    only = client.get("/calls?tab=history&outcome=busy").text
    assert "Bob Stone" in only and "Alice Roy" not in only
    recent = client.get("/calls?tab=history&period=30").text
    assert "Old Timer" not in recent and "Alice Roy" in recent
    assert "Alice Roy" in client.get("/calls?tab=history&q=great").text
    assert "Bob Stone" in client.get("/calls?tab=history&q=2 22").text
    assert "No calls match" in client.get("/calls?tab=history&q=nobody").text


def test_empty_history_and_page_tabs(client: TestClient) -> None:
    html = client.get("/calls?tab=history").text
    assert "No calls logged yet" in html and "/partials/call-log" in html
    assert "Calling list" in html and "Call history" in html


# ---- CSRF


@pytest.mark.parametrize("path", ["/calls", "/calls/phone"])
def test_cross_site_call_posts_are_blocked(client: TestClient, pl: Pipeline, path: str) -> None:
    r = client.post(
        path,
        data={"outcome": "busy", "phone": "+1 555 0100", "person_id": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403 and pl.list_calls() == []
