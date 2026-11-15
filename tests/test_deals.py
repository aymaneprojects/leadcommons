"""Deals: stages, models, stage changes and events, drag and drop endpoint, pages, CSRF."""
# ruff: noqa: F811  (fixtures imported from test_deals_support)

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from leadcommons import workflows
from leadcommons.store.pipeline import Pipeline
from leadcommons.web.pages_pipeline import money, parse_amount, totals_label
from tests.test_deals_support import HX, app, client, people, pl  # noqa: F401

JSON = {"Accept": "application/json"}


def stage_id(pl: Pipeline, name: str) -> str:
    return next(s.id for s in pl.list_stages() if s.name == name)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict]]:
    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(workflows, "emit", lambda event, **payload: seen.append((event, payload)))
    return seen


# ---- models


def test_default_stages_are_seeded_once(pl: Pipeline) -> None:
    names = [s.name for s in pl.list_stages()]
    assert names == ["Qualification", "Discovery", "Proposal", "Negotiation", "Won", "Lost"]
    assert [s.kind for s in pl.list_stages()][-2:] == ["won", "lost"]
    assert len(pl.list_stages()) == 6  # not seeded twice


def test_create_deal_defaults_to_first_stage_and_stage_probability(pl: Pipeline) -> None:
    deal = pl.create_deal("Acme licence", amount=12000.0, currency="eur")
    assert deal.stage_id == pl.list_stages()[0].id
    assert deal.probability == 10
    assert deal.currency == "EUR"
    assert pl.get_deal(deal.id).amount == 12000.0
    with pytest.raises(ValueError):
        pl.create_deal("   ")


def test_stage_crud_order_and_archive(pl: Pipeline) -> None:
    pl.list_stages()
    new = pl.add_stage("Contract sent", probability=90)
    names = [s.name for s in pl.list_stages()]
    assert names.index("Contract sent") < names.index("Won")  # open stages go before closed ones
    pl.update_stage(new.id, name="Contract out", probability=85)
    assert pl.get_stage(new.id).name == "Contract out"
    assert pl.get_stage(new.id).probability == 85
    pl.shift_stage(new.id, -1)
    assert [s.name for s in pl.list_stages()].index("Contract out") == 3
    deal = pl.create_deal("Keeps stage busy", new.id)
    assert "Move its 1 deal" in pl.archive_stage(new.id)
    pl.delete_deal(deal.id)
    assert pl.archive_stage(new.id) is None
    assert new.id not in [s.id for s in pl.list_stages()]
    assert new.id in [s.id for s in pl.list_stages(include_archived=True)]
    pl.restore_stage(new.id)
    assert new.id in [s.id for s in pl.list_stages()]


def test_last_open_stage_cannot_be_archived(pl: Pipeline) -> None:
    open_ids = [s.id for s in pl.list_stages() if s.kind == "open"]
    for sid in open_ids[:-1]:
        assert pl.archive_stage(sid) is None
    assert pl.archive_stage(open_ids[-1]) == "Keep at least one open stage."


def test_move_deal_orders_the_column_and_follows_stage_probability(pl: Pipeline) -> None:
    a, b, c = (pl.create_deal(n) for n in "ABC")
    proposal = stage_id(pl, "Proposal")
    for deal in (a, b, c):
        pl.move_deal(deal.id, proposal)
    pl.move_deal(c.id, proposal, 0)
    order = [d.name for d in pl.list_deals(stage_id=proposal)]
    assert order == ["C", "A", "B"]
    assert pl.get_deal(a.id).probability == 50  # followed Qualification (10) -> Proposal (50)
    pl.update_deal(b.id, probability=33)
    pl.move_deal(b.id, stage_id(pl, "Won"))
    assert pl.get_deal(b.id).probability == 33  # hand-set probability is kept


def test_stage_totals_group_by_currency(pl: Pipeline) -> None:
    q = pl.list_stages()[0].id
    pl.create_deal("One", q, amount=100.0, currency="EUR")
    pl.create_deal("Two", q, amount=50.5, currency="EUR")
    pl.create_deal("Three", q, amount=10.0, currency="USD")
    totals = pl.stage_totals()[q]
    assert totals["count"] == 3
    assert totals["by_currency"] == {"EUR": 150.5, "USD": 10.0}
    assert totals_label(totals["by_currency"]) == "€150.50 · $10"


def test_money_and_amount_parsing() -> None:
    assert money(12000, "EUR") == "€12,000"
    assert money(1234.5, "CHF") == "1,234.50 CHF"
    assert money(None) == "—"
    assert parse_amount("1 250,50") == 1250.5
    assert parse_amount("1,250.50") == 1250.5
    assert parse_amount("") is None
    with pytest.raises(ValueError):
        parse_amount("abc")
    with pytest.raises(ValueError):
        parse_amount("-5")


# ---- create and edit


def test_create_deal_through_the_form(client: TestClient, pl: Pipeline, people) -> None:
    claire, atelier = people["claire"], people["atelier"]
    r = client.post(
        "/deals",
        headers=HX,
        data={
            "name": "Atelier rollout",
            "person_id": claire.id,
            "person_name": "Claire Martin",
            "company_name": "typed name",
            "amount": "8 500",
            "currency": "USD",
            "close_date": "2026-12-31",
            "probability": "40",
            "source": "Referral",
            "notes": "Met at the fair",
            "next": "/people/" + claire.id,
        },
    )
    assert r.status_code == 200
    assert r.headers["HX-Redirect"].startswith("/people/" + claire.id)
    (deal,) = pl.list_deals()
    assert deal.name == "Atelier rollout"
    assert deal.company_id == atelier.id and deal.company_name == atelier.name  # via the person
    assert deal.amount == 8500.0 and deal.currency == "USD"
    assert str(deal.close_date) == "2026-12-31" and deal.probability == 40
    assert deal.source == "Referral" and deal.notes == "Met at the fair"
    logged = pl.activity.list_for(deal_id=deal.id)
    assert [a.kind for a in logged] == ["deal"]
    assert pl.activity.list_for(person_id=claire.id)  # shows on the person's timeline too


def test_create_deal_without_javascript_redirects_with_notice(client: TestClient, pl) -> None:
    r = client.post("/deals", data={"name": "Plain form", "stage_id": ""})
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert pl.list_deals()[0].name == "Plain form"
    bad = client.post("/deals", data={"name": ""})
    assert bad.status_code == 303 and "Give+the+deal+a+name" in bad.headers["location"]


def test_create_deal_validation_errors_are_422_for_htmx(client: TestClient, pl) -> None:
    r = client.post("/deals", headers=HX, data={"name": "X", "amount": "lots"})
    assert r.status_code == 422
    assert "The amount must be a number" in r.headers["HX-Trigger"]
    assert pl.list_deals() == []


def test_update_deal_changes_fields_and_logs_a_stage_move(
    client: TestClient, pl: Pipeline, events
) -> None:
    deal = pl.create_deal("Old name")
    negotiation = stage_id(pl, "Negotiation")
    r = client.post(
        f"/deals/{deal.id}",
        headers=HX,
        data={"name": "New name", "amount": "99", "currency": "GBP", "stage_id": negotiation},
    )
    assert r.status_code == 200 and "HX-Redirect" not in r.headers
    after = pl.get_deal(deal.id)
    assert after.name == "New name" and after.amount == 99 and after.currency == "GBP"
    assert after.stage_id == negotiation
    assert [e[0] for e in events] == ["deal_stage_changed"]
    assert [a.kind for a in pl.activity.list_for(deal_id=deal.id)] == ["deal_stage"]


def test_delete_deal(client: TestClient, pl: Pipeline) -> None:
    deal = pl.create_deal("Gone soon")
    r = client.post(f"/deals/{deal.id}/delete", headers=HX)
    assert r.status_code == 200 and r.headers["HX-Redirect"].startswith("/deals")
    assert pl.get_deal(deal.id) is None
    assert client.post("/deals/nope/delete", headers=HX).status_code == 404


# ---- stage changes, events, drag and drop


def test_drag_and_drop_endpoint_moves_logs_and_emits(
    client: TestClient, pl: Pipeline, events
) -> None:
    deal = pl.create_deal("Dragged", amount=1000.0)
    proposal = stage_id(pl, "Proposal")
    r = client.post(
        f"/deals/{deal.id}/move", data={"stage_id": proposal, "position": "0"}, headers=JSON
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["changed"] and body["stage"] == "Proposal"
    assert body["totals"][proposal] == {"count": 1, "amount": "€1,000"}
    assert body["totals"][body["previous_stage_id"]]["count"] == 0
    assert pl.get_deal(deal.id).stage_id == proposal
    assert events == [
        (
            "deal_stage_changed",
            {"deal_id": deal.id, "stage": "Proposal", "previous_stage": "Qualification"},
        )
    ]
    (entry,) = pl.activity.list_for(deal_id=deal.id, kinds=["deal_stage"])
    assert "Qualification → Proposal" in entry.summary
    assert entry.meta["stage"] == "Proposal"


def test_reordering_inside_a_column_logs_nothing(client: TestClient, pl: Pipeline, events) -> None:
    first = pl.create_deal("First")
    second = pl.create_deal("Second")
    stage = pl.get_deal(first.id).stage_id
    r = client.post(
        f"/deals/{second.id}/move", data={"stage_id": stage, "position": "5"}, headers=JSON
    )
    assert r.json()["changed"] is False
    assert events == [] and pl.activity.list_for(deal_id=second.id) == []
    assert [d.name for d in pl.list_deals(stage_id=stage)][-1] == "Second"


def test_move_rejects_unknown_deal_or_archived_stage(client: TestClient, pl: Pipeline) -> None:
    deal = pl.create_deal("Stays put")
    lost = stage_id(pl, "Lost")
    assert pl.archive_stage(lost) is None
    r = client.post(f"/deals/{deal.id}/move", data={"stage_id": lost}, headers=JSON)
    assert r.status_code == 404 and r.json()["ok"] is False
    assert client.post("/deals/nope/move", data={"stage_id": "x"}, headers=JSON).status_code == 404


def test_move_from_the_card_menu_without_javascript(
    client: TestClient, pl: Pipeline, events
) -> None:
    deal = pl.create_deal("Menu move")
    won = stage_id(pl, "Won")
    r = client.post(f"/deals/{deal.id}/move", data={"stage_id": won, "next": "/deals?view=table"})
    assert r.status_code == 303 and r.headers["location"].startswith("/deals?view=table")
    assert pl.get_deal(deal.id).stage_id == won and events


def test_emit_failure_never_breaks_a_move(
    client: TestClient, pl: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a, **k):
        raise RuntimeError("workflow engine down")

    monkeypatch.setattr(workflows, "emit", boom)
    deal = pl.create_deal("Safe")
    r = client.post(f"/deals/{deal.id}/move", data={"stage_id": stage_id(pl, "Won")}, headers=JSON)
    assert r.status_code == 200 and r.json()["ok"]


# ---- settings page


def test_deal_stages_settings_page_and_actions(client: TestClient, pl: Pipeline) -> None:
    page = client.get("/settings/deal-stages")
    assert page.status_code == 200
    assert "Qualification" in page.text and "Add a stage" in page.text
    assert 'aria-current="page"' in page.text  # the Deal stages entry is in the settings menu
    assert (
        client.post(
            "/settings/deal-stages", data={"name": "Trial", "probability": "60"}
        ).status_code
        == 303
    )
    trial = next(s for s in pl.list_stages() if s.name == "Trial")
    assert trial.probability == 60
    client.post(
        f"/settings/deal-stages/{trial.id}",
        data={"name": "Pilot", "kind": "open", "probability": "55"},
    )
    assert pl.get_stage(trial.id).name == "Pilot"
    client.post(f"/settings/deal-stages/{trial.id}/shift", data={"dir": "up"})
    ids = [s.id for s in pl.list_stages()]
    client.post("/settings/deal-stages/order", data={"ids": list(reversed(ids))})
    assert [s.id for s in pl.list_stages()] == list(reversed(ids))
    pl.create_deal("Blocks archive", trial.id)
    r = client.post(f"/settings/deal-stages/{trial.id}/archive", headers=HX)
    assert r.status_code == 422 and "Move its 1 deal" in r.headers["HX-Trigger"]
    empty = client.post("/settings/deal-stages", headers=HX, data={"name": " "})
    assert empty.status_code == 422
    other = pl.add_stage("Scratch")
    assert client.post(f"/settings/deal-stages/{other.id}/archive").status_code == 303
    assert "Archived stages" in client.get("/settings/deal-stages").text
    client.post(f"/settings/deal-stages/{other.id}/restore")
    assert other.id in [s.id for s in pl.list_stages()]


def test_renamed_stages_show_on_the_board(client: TestClient, pl: Pipeline) -> None:
    pl.update_stage(pl.list_stages()[0].id, name="Cold leads")
    pl.create_deal("Anything")
    assert "Cold leads" in client.get("/deals").text


# ---- pages


def test_deals_page_empty_state(client: TestClient) -> None:
    r = client.get("/deals")
    assert r.status_code == 200
    assert "No deals yet" in r.text and "/partials/deal-new" in r.text


def test_board_and_table_views_render(client: TestClient, pl: Pipeline) -> None:
    pl.create_deal(
        "Big one", stage_id(pl, "Proposal"), amount=25000.0, company_name="Atelier Nuage"
    )
    pl.create_deal("Small one", amount=500.0, currency="USD")
    board = client.get("/deals").text
    assert "data-deal-id" in board and "Big one" in board and "€25,000" in board
    assert board.count('class="pl-col ') == 6 and "Open pipeline" in board
    assert 'draggable="true"' in board and "Move to stage" in board  # menu alternative to dragging
    table = client.get("/deals?view=table&sort=amount&dir=desc").text
    assert table.index("Big one") < table.index("Small one")
    assert "Atelier Nuage" in table and 'aria-sort="descending"' in table
    assert "Big one" not in client.get("/deals?view=table&q=small").text
    filtered = client.get("/deals?view=table&stage=" + stage_id(pl, "Proposal")).text
    assert "Big one" in filtered and "Small one" not in filtered
    assert "No deals match" in client.get("/deals?q=zzz").text


def test_board_column_totals(client: TestClient, pl: Pipeline) -> None:
    q = pl.list_stages()[0].id
    pl.create_deal("A", q, amount=100.0)
    pl.create_deal("B", q, amount=250.0)
    html = client.get("/deals").text
    assert "€350" in html


def test_deal_drawer_partial_and_full_page(client: TestClient, pl: Pipeline) -> None:
    deal = pl.create_deal("Drawer deal", company_name="Atelier Nuage", amount=10.0)
    drawer = client.get(f"/deals/{deal.id}", headers=HX).text
    assert "drawer-scrim" in drawer and "Deal details" in drawer
    assert f"/partials/activity?deal_id={deal.id}" in drawer
    assert f"/partials/task-new?deal_id={deal.id}" in drawer  # linked tasks come from engage
    assert "Linked tasks" in drawer and "Notes and activity" in drawer
    page = client.get(f"/deals/{deal.id}").text
    assert "<html" in page and "Drawer deal" in page and "drawer-scrim" not in page
    assert client.get("/deals/nope").status_code == 404


def test_deal_new_partial_is_prefilled(client: TestClient, people) -> None:
    claire, atelier = people["claire"], people["atelier"]
    html = client.get(f"/partials/deal-new?person_id={claire.id}", headers=HX).text
    assert "<dialog" in html and "data-autoopen" in html
    assert atelier.name in html and "Claire Martin" in html
    assert f"{atelier.name} deal" in html
    by_company = client.get(f"/partials/deal-new?company_id={atelier.id}").text
    assert 'value="' + atelier.id + '"' in by_company


def test_deal_new_partial_preselects_the_stage(client: TestClient, pl: Pipeline) -> None:
    disc = stage_id(pl, "Discovery")
    html = client.get(f"/partials/deal-new?stage_id={disc}").text
    assert f'value="{disc}" data-prob="25" selected' in html


# ---- CSRF


POSTS = [
    ("/deals", {"name": "x"}),
    ("/deals/abc", {"name": "x"}),
    ("/deals/abc/move", {"stage_id": "x"}),
    ("/deals/abc/delete", {}),
    ("/settings/deal-stages", {"name": "x"}),
    ("/settings/deal-stages/abc", {"name": "x"}),
    ("/settings/deal-stages/abc/shift", {"dir": "up"}),
    ("/settings/deal-stages/order", {"ids": "a"}),
    ("/settings/deal-stages/abc/archive", {}),
    ("/settings/deal-stages/abc/restore", {}),
]


@pytest.mark.parametrize(("path", "data"), POSTS)
def test_cross_site_posts_are_blocked(
    client: TestClient, pl: Pipeline, path: str, data: dict
) -> None:
    before = pl.list_deals()
    r = client.post(path, data=data, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert pl.list_deals() == before


def test_everything_works_in_demo_mode(tmp_path) -> None:
    """Demo mode writes to the temporary database and calls nothing external."""
    from tests.test_deals_support import SAME_ORIGIN, make_app

    client = TestClient(
        make_app(tmp_path, mode="demo"), follow_redirects=False, headers=SAME_ORIGIN
    )
    assert client.post("/deals", data={"name": "Demo deal"}).status_code == 303
    assert (
        client.post("/calls", data={"outcome": "busy", "phone": "+1 555 0100"}).status_code == 303
    )
    assert client.post("/notes", data={"body": "demo note", "company_id": "x"}).status_code in (
        303,
    )
    html = client.get("/deals").text
    assert "Demo deal" in html
