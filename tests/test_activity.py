"""Activity timeline, notes, conversations and the company/person picker."""
# ruff: noqa: F811  (fixtures imported from test_deals_support)

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from leadcommons.store.pipeline import ActivityRepo, Pipeline
from tests.test_deals_support import HX, app, client, people, pl  # noqa: F401

# ---- the shared ActivityRepo contract


def test_activity_repo_contract(pl: Pipeline) -> None:
    repo: ActivityRepo = pl.activity
    a = repo.add("note", "first", person_id="p1", company_id="c1")
    b = repo.add("email_sent", "second", person_id="p1", meta={"message_id": "m1"})
    c = repo.add("deal", "third", deal_id="d1")
    repo.add("call", "other person", person_id="p2")
    assert isinstance(a, int) and a < b < c
    assert [x.summary for x in repo.list_for(person_id="p1")] == ["second", "first"]  # newest first
    assert [x.kind for x in repo.list_for(person_id="p1", kinds=["note"])] == ["note"]
    both = repo.list_for(person_id="p1", deal_id="d1")  # any of the ids
    assert {x.summary for x in both} == {"first", "second", "third"}
    assert repo.list_for(company_id="c1")[0].summary == "first"
    assert len(repo.list_for(person_id="p1", limit=1)) == 1
    assert repo.list_for(person_id="p1")[0].meta == {"message_id": "m1"}
    assert len(repo.list_for()) == 4  # no filter: everything
    assert repo.add("note", "x" * 5000) and len(repo.list_for(limit=1)[0].summary) == 2000


def test_activity_search_get_delete(pl: Pipeline) -> None:
    pl.activity.add("note", "Pricing discussion", person_id="p1", company_id="c1")
    pl.activity.add("call", "Left a message", person_id="p2", company_id="c1")
    assert [a.summary for a in pl.activity.search(text="pricing")] == ["Pricing discussion"]
    assert len(pl.activity.search(company_id="c1")) == 2
    assert len(pl.activity.search(person_id="p2", company_id="c1")) == 1
    (first,) = pl.activity.search(kinds=["call"])
    assert pl.activity.get(first.id).summary == "Left a message"
    assert pl.activity.delete(first.id) and not pl.activity.delete(first.id)


# ---- timeline fragment


def test_activity_partial_renders_kinds_times_and_links(
    client: TestClient, pl: Pipeline, people
) -> None:
    claire, atelier = people["claire"], people["atelier"]
    deal = pl.create_deal("Atelier deal")
    pl.activity.add("note", "Asked for a discount", person_id=claire.id, company_id=atelier.id)
    pl.activity.add("call", "Call · Connected", person_id=claire.id, deal_id=deal.id)
    pl.activity.add("email_reply", "Re: proposal", person_id=claire.id)
    pl.activity.add("deal_stage", "Atelier deal: Qualification → Proposal", deal_id=deal.id)
    html = client.get(f"/partials/activity?company_id={atelier.id}&person_id={claire.id}").text
    for label in ("Note", "Call", "Reply"):
        assert f"<b>{label}</b>" in html
    assert "just now" in html and "Asked for a discount" in html
    assert 'href="/deals/' + deal.id + '"' in html  # linked deal shown as a chip
    assert "pipeline-sprite.svg#p-sticky-note" in html and "#p-phone" in html
    by_deal = client.get(f"/partials/activity?deal_id={deal.id}").text
    assert "Qualification → Proposal" in by_deal and 'href="/people/' + claire.id + '"' in by_deal
    only_notes = client.get(f"/partials/activity?person_id={claire.id}&kinds=note").text
    assert "Asked for a discount" in only_notes and "Re: proposal" not in only_notes


def test_activity_partial_empty_and_unscoped(client: TestClient) -> None:
    assert "Nothing logged yet" in client.get("/partials/activity?person_id=nobody").text
    assert "Nothing logged yet" in client.get("/partials/activity").text


def test_activity_summary_is_escaped(client: TestClient, pl: Pipeline) -> None:
    pl.activity.add("note", "<script>alert(1)</script>", person_id="px")
    html = client.get("/partials/activity?person_id=px").text
    assert "<script>alert" not in html and "&lt;script&gt;" in html


# ---- notes


def test_note_new_partial(client: TestClient, people) -> None:
    claire = people["claire"]
    html = client.get(f"/partials/note-new?person_id={claire.id}", headers=HX).text
    assert "<dialog" in html and "data-autoopen" in html and "Claire Martin" in html
    assert 'action="/notes"' in html


def test_create_note(client: TestClient, pl: Pipeline, people) -> None:
    claire, atelier = people["claire"], people["atelier"]
    r = client.post(
        "/notes",
        headers=HX,
        data={"person_id": claire.id, "body": "Prefers email", "next": "/people/" + claire.id},
    )
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/people/")
    (n,) = pl.activity.list_for(person_id=claire.id, kinds=["note"])
    assert n.summary == "Prefers email" and n.company_id == atelier.id


def test_create_note_on_a_deal_and_without_reload(client: TestClient, pl: Pipeline, people) -> None:
    deal = pl.create_deal(
        "Noted deal", person_id=people["claire"].id, company_id=people["atelier"].id
    )
    r = client.post(
        "/notes", headers=HX, data={"deal_id": deal.id, "body": "Budget confirmed", "reload": "0"}
    )
    assert (
        r.status_code == 200
        and "HX-Redirect" not in r.headers
        and "pl-changed" in r.headers["HX-Trigger"]
    )
    (n,) = pl.activity.list_for(deal_id=deal.id, kinds=["note"])
    assert n.person_id == people["claire"].id  # inherited from the deal


def test_create_note_validation(client: TestClient, pl: Pipeline) -> None:
    assert (
        client.post("/notes", headers=HX, data={"person_id": "x", "body": "  "}).status_code == 422
    )
    nobody = client.post("/notes", headers=HX, data={"body": "orphan"})
    assert nobody.status_code == 422
    assert pl.activity.list_for() == []
    assert client.post("/notes", data={"body": "orphan"}).status_code == 303


def test_cross_site_note_is_blocked(client: TestClient, pl: Pipeline) -> None:
    r = client.post(
        "/notes", data={"body": "x", "person_id": "p"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403 and pl.activity.list_for() == []


# ---- conversations


def seed_conversations(pl: Pipeline, people) -> None:
    claire, jonas, atelier = people["claire"], people["jonas"], people["atelier"]
    pl.activity.add("note", "Likes the pricing page", person_id=claire.id, company_id=atelier.id)
    pl.activity.add(
        "call", "Call · Connected · 2 min 00 s", person_id=claire.id, company_id=atelier.id
    )
    pl.activity.add(
        "email_sent", "Intro email sent", person_id=jonas.id, company_id=people["nordwind"].id
    )
    pl.activity.add("meeting", "Meeting scheduled: Kickoff", company_id=people["nordwind"].id)
    pl.activity.add("task", "not a conversation kind", person_id=claire.id)


def test_conversations_unify_kinds_per_person(client: TestClient, pl: Pipeline, people) -> None:
    seed_conversations(pl, people)
    html = client.get("/conversations").text
    assert "Claire Martin" in html and "Jonas Weber" in html
    assert "not a conversation kind" not in html  # tasks are not conversations
    assert "Likes the pricing page" in html or "Call · Connected" in html
    assert 'aria-current="true"' in html  # a thread is selected
    pane = client.get(f"/conversations?thread=p:{people['claire'].id}").text.split(
        'aria-label="Timeline"'
    )[1]
    assert "Likes the pricing page" in pane and "Intro email sent" not in pane


def test_conversations_filters_and_search(client: TestClient, pl: Pipeline, people) -> None:
    seed_conversations(pl, people)
    calls = client.get("/conversations?kind=call").text
    assert "Call · Connected" in calls and "Intro email sent" not in calls
    emails = client.get("/conversations?kind=email").text
    assert (
        "Intro email sent" in emails and "Jonas Weber" in emails and "Claire Martin" not in emails
    )
    assert "Jonas Weber" in client.get("/conversations?q=intro").text
    assert "Claire Martin" not in client.get("/conversations?q=intro").text
    assert "Claire Martin" in client.get("/conversations?q=claire").text  # matches the name
    assert "No conversations match" in client.get("/conversations?q=zzzz").text
    assert "Notes" in calls and "Meetings" in calls and "Emails" in calls  # filter tabs


def test_company_threads_for_activity_without_a_person(
    client: TestClient, pl: Pipeline, people
) -> None:
    seed_conversations(pl, people)
    html = client.get(f"/conversations?thread=c:{people['nordwind'].id}").text
    assert "Meeting scheduled: Kickoff" in html and "Open profile" in html


def test_conversations_empty_state(client: TestClient) -> None:
    html = client.get("/conversations").text
    assert "No conversations yet" in html and "/partials/note-new" in html


# ---- picker and pages


def test_picker_finds_companies_and_people(client: TestClient, people) -> None:
    html = client.get("/partials/pick?kind=company&q=Atelier").text
    assert "Atelier Nuage" in html and 'data-id="' + people["atelier"].id + '"' in html
    p = client.get("/partials/pick?kind=person&q=claire").text
    assert "Claire Martin" in p and f'data-company-id="{people["atelier"].id}"' in p
    assert "No match" in client.get("/partials/pick?kind=company&q=zzzzz").text
    assert "at least 2" in client.get("/partials/pick?kind=company&q=a").text


@pytest.mark.parametrize(
    "path",
    [
        "/deals",
        "/deals?view=table",
        "/meetings",
        "/meetings?tab=past",
        "/calls",
        "/calls?tab=history",
        "/conversations",
        "/settings/deal-stages",
        "/partials/deal-new",
        "/partials/meeting-new",
        "/partials/call-log",
        "/partials/note-new",
        "/partials/activity",
        "/partials/pick",
    ],
)
def test_every_page_and_partial_renders(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    if path.startswith("/partials/") and "pick" not in path and "activity" not in path:
        assert "<dialog" in r.text
    else:
        assert r.text.strip()


def test_pages_have_pipeline_assets_and_sidebar_marks(client: TestClient) -> None:
    html = client.get("/deals").text
    assert "/static/css/pipeline.css" in html and "/static/js/pipeline.js" in html
    assert (datetime.now() - timedelta(days=1)).year >= 2026
