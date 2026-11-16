"""Meetings: creation, upcoming/past, ICS and calendar links, outcomes, booking link."""
# ruff: noqa: F811  (fixtures imported from test_deals_support)

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from pepite.store.pipeline import Meeting, Pipeline
from pepite.web.pages_pipeline import (
    build_ics,
    google_calendar_url,
    outlook_calendar_url,
    parse_local_dt,
)
from tests.test_deals_support import HX, app, client, people, pl  # noqa: F401


def when(days: float) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")


def sample() -> Meeting:
    return Meeting(
        id="mt_abc123",
        title="Intro, kickoff; budget",
        start=datetime(2026, 12, 3, 14, 30),
        duration_min=45,
        person_name="Claire Martin",
        company_name="Atelier Nuage",
        location="Rue de la Paix 1, Lyon",
        video_url="https://meet.example.com/room-1",
        agenda="Line one\nLine two",
    )


# ---- ICS and calendar links


def test_ics_content() -> None:
    ics = build_ics(sample())
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert "\r\n" in ics and "\n" not in ics.replace("\r\n", "")  # CRLF line ends only
    assert "UID:mt_abc123@pepite.local" in ics
    assert "DTSTART:20261203T143000" in ics and "DTEND:20261203T151500" in ics  # 45 minutes
    assert "SUMMARY:Intro\\, kickoff\\; budget" in ics  # commas and semicolons escaped
    assert "LOCATION:Rue de la Paix 1\\, Lyon" in ics
    assert "URL:https://meet.example.com/room-1" in ics
    assert "Line one\\nLine two" in ics.replace("\r\n ", "")  # newline escaped, lines unfolded
    assert "STATUS:CONFIRMED" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))


def test_ics_cancelled_status_and_location_falls_back_to_video() -> None:
    m = sample()
    m.status, m.location = "cancelled", None
    ics = build_ics(m)
    assert "STATUS:CANCELLED" in ics and "LOCATION:https://meet.example.com/room-1" in ics


def test_calendar_links() -> None:
    m = sample()
    g = urlparse(google_calendar_url(m))
    q = parse_qs(g.query)
    assert g.netloc == "calendar.google.com" and q["action"] == ["TEMPLATE"]
    assert q["text"] == ["Intro, kickoff; budget"]
    assert q["dates"] == ["20261203T143000/20261203T151500"]
    assert "Video call: https://meet.example.com/room-1" in q["details"][0]
    assert q["location"] == ["Rue de la Paix 1, Lyon"]
    o = urlparse(outlook_calendar_url(m))
    oq = parse_qs(o.query)
    assert o.netloc == "outlook.live.com" and oq["rru"] == ["addevent"]
    assert oq["startdt"] == ["2026-12-03T14:30:00"] and oq["enddt"] == ["2026-12-03T15:15:00"]
    assert oq["subject"] == ["Intro, kickoff; budget"]


def test_ics_download_endpoint(client: TestClient, pl: Pipeline) -> None:
    m = pl.create_meeting("Demo call", datetime(2027, 1, 5, 9, 0), duration_min=30)
    r = client.get(f"/meetings/{m.id}.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert 'filename="demo-call.ics"' in r.headers["content-disposition"]
    assert "DTSTART:20270105T090000" in r.text
    assert client.get("/meetings/none.ics").status_code == 404


# ---- create and lists


def test_parse_local_datetime() -> None:
    assert parse_local_dt("2026-12-03T14:30") == datetime(2026, 12, 3, 14, 30)
    assert parse_local_dt("2026-12-03 14:30") == datetime(2026, 12, 3, 14, 30)
    with pytest.raises(ValueError):
        parse_local_dt("tomorrow")


def test_create_meeting_via_form_logs_activity(client: TestClient, pl: Pipeline, people) -> None:
    claire, atelier = people["claire"], people["atelier"]
    r = client.post(
        "/meetings",
        headers=HX,
        data={
            "title": "Intro call",
            "person_id": claire.id,
            "start": when(2),
            "duration": "45",
            "video_url": "https://meet.example.com/x",
            "agenda": "Discuss needs",
            "next": "/people/" + claire.id,
        },
    )
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/people/")
    (m,) = pl.list_meetings("all")
    assert m.duration_min == 45 and m.company_id == atelier.id and m.person_name == "Claire Martin"
    assert m.video_url == "https://meet.example.com/x" and m.status == "scheduled"
    (a,) = pl.activity.list_for(person_id=claire.id, kinds=["meeting"])
    assert "Intro call" in a.summary and a.meta["meeting_id"] == m.id


def test_create_meeting_validation(client: TestClient, pl: Pipeline) -> None:
    bad_time = client.post("/meetings", headers=HX, data={"title": "x", "start": "soon"})
    assert bad_time.status_code == 422
    no_title = client.post("/meetings", headers=HX, data={"title": "", "start": when(1)})
    assert no_title.status_code == 422
    bad_link = client.post(
        "/meetings",
        headers=HX,
        data={"title": "x", "start": when(1), "video_url": "javascript:alert(1)"},
    )
    assert bad_link.status_code == 422 and "https://" in bad_link.headers["HX-Trigger"]
    assert pl.list_meetings("all") == []


def test_upcoming_and_past_lists(client: TestClient, pl: Pipeline) -> None:
    now = datetime.now()
    pl.create_meeting("Future call", now + timedelta(days=3))
    pl.create_meeting("Earlier call", now - timedelta(days=3))
    done = pl.create_meeting("Held in future slot", now + timedelta(days=1))
    pl.update_meeting(done.id, status="done")
    assert [m.title for m in pl.list_meetings("upcoming")] == ["Future call"]
    assert {m.title for m in pl.list_meetings("past")} == {"Earlier call", "Held in future slot"}
    up = client.get("/meetings").text
    assert "Future call" in up and "Earlier call" not in up
    past = client.get("/meetings?tab=past").text
    assert "Earlier call" in past and "Future call" not in past
    assert (
        "Download .ics file" in up
        and "Add to Google Calendar" in up
        and "Add to Outlook calendar" in up
    )
    assert "Earlier call" in client.get("/meetings?tab=past&q=earlier").text
    assert "No meetings match" in client.get("/meetings?q=zzz").text


def test_empty_meeting_pages(client: TestClient) -> None:
    assert "No upcoming meetings" in client.get("/meetings").text
    assert "No past meetings" in client.get("/meetings?tab=past").text


def test_meeting_new_partial_prefills(client: TestClient, people) -> None:
    claire = people["claire"]
    html = client.get(f"/partials/meeting-new?person_id={claire.id}", headers=HX).text
    assert "<dialog" in html and "data-autoopen" in html
    assert "Meeting with Claire Martin" in html and 'type="datetime-local"' in html


# ---- outcome, dialog, delete


def test_outcome_notes_and_dialog(client: TestClient, pl: Pipeline) -> None:
    m = pl.create_meeting("Review", datetime.now() - timedelta(hours=2), person_name="Jonas Weber")
    dlg = client.get(f"/partials/meeting/{m.id}", headers=HX).text
    assert "<dialog" in dlg and "Outcome" in dlg and f"/meetings/{m.id}.ics" in dlg
    r = client.post(
        f"/meetings/{m.id}/outcome",
        headers=HX,
        data={"outcome": "Agreed on a pilot", "status": "done"},
    )
    assert r.status_code == 200
    after = pl.get_meeting(m.id)
    assert after.status == "done" and after.outcome == "Agreed on a pilot"
    assert "Agreed on a pilot" in client.get("/meetings?tab=past").text
    kinds = [a.summary for a in pl.activity.list_for(kinds=["meeting"])]
    assert any(s.startswith("Held: Review. Agreed on a pilot") for s in kinds)
    assert "Agreed on a pilot" in client.get(f"/partials/meeting/{m.id}").text  # notes shown again


def test_outcome_no_show_and_unknown_meeting(client: TestClient, pl: Pipeline) -> None:
    m = pl.create_meeting("Missed", datetime.now() - timedelta(days=1))
    client.post(f"/meetings/{m.id}/outcome", data={"status": "no_show", "outcome": ""})
    assert pl.get_meeting(m.id).status == "no_show"
    assert client.post("/meetings/zzz/outcome", headers=HX, data={}).status_code == 404
    assert client.get("/partials/meeting/zzz").status_code == 404


def test_delete_meeting(client: TestClient, pl: Pipeline) -> None:
    m = pl.create_meeting("Temporary", datetime.now() + timedelta(days=1))
    assert client.post(f"/meetings/{m.id}/delete", headers=HX).status_code == 200
    assert pl.get_meeting(m.id) is None
    assert client.post(f"/meetings/{m.id}/delete", headers=HX).status_code == 404


# ---- booking link


def test_booking_link_setting(client: TestClient, pl: Pipeline) -> None:
    assert "data-copy-from" in client.get("/meetings").text
    r = client.post(
        "/meetings/booking-link", headers=HX, data={"booking_url": "https://cal.example.com/me"}
    )
    assert r.status_code == 200
    assert pl.get_setting("booking_url") == "https://cal.example.com/me"
    page = client.get("/meetings").text
    assert 'value="https://cal.example.com/me"' in page and "Open</span>" in page
    bad = client.post("/meetings/booking-link", headers=HX, data={"booking_url": "ftp://nope"})
    assert bad.status_code == 422
    assert pl.get_setting("booking_url") == "https://cal.example.com/me"  # unchanged
    client.post("/meetings/booking-link", data={"booking_url": ""})
    assert pl.get_setting("booking_url") == ""


# ---- CSRF


@pytest.mark.parametrize(
    "path",
    ["/meetings", "/meetings/booking-link", "/meetings/abc/outcome", "/meetings/abc/delete"],
)
def test_cross_site_meeting_posts_are_blocked(client: TestClient, pl: Pipeline, path: str) -> None:
    r = client.post(
        path,
        data={"title": "x", "start": when(1), "booking_url": "https://x.example"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert pl.list_meetings("all") == [] and pl.get_setting("booking_url") == ""
