"""Shell parity pass: sidebar footer, palette, settings, email health, assistant, hub, notices."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pepite.config import Settings
from pepite.store import Store
from pepite.store.engage import repo_for as engage_repo
from pepite.web import pages_extra
from pepite.web.app import create_app
from tests.fakes import FakeJobs, FakeSearch


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/s.db", **kw
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


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEPITE_LLM_URL", raising=False)
    monkeypatch.delenv("PEPITE_LLM_MODEL", raising=False)


def add_mailbox(app: FastAPI, email: str = "me@acme.example", status: str = "ok") -> None:
    engage_repo(app.state.store).create_mailbox(from_email=email, smtp_host="smtp.x", status=status)


# ------------------------------------------------------------------ sidebar and palette


def test_sidebar_footer_has_admin_settings_row(client: TestClient) -> None:
    html = client.get("/").text
    foot = html.split('class="side-foot"')[1].split("</aside>")[0]
    assert "Onboarding hub" in foot and "Email setup and health" in foot
    assert "Admin settings" in foot and "data-settings-pop" in foot
    assert html.index('class="side-foot"') > html.index("</nav>")


def test_palette_empty_query_shows_ai_and_suggested_actions(client: TestClient) -> None:
    html = client.get("/").text
    pal = html.split('id="lc-palette"')[1]
    top = re.findall(r'<a class="pal-item"[^>]*data-top[^>]*>.*?pal-label">([^<]+)<', pal, re.S)
    assert "Build your target audience" in top and "Draft an outbound sequence" in top
    assert "Create a workflow" in top and "Enrich a CSV file" in top
    assert "Suggested actions" in pal and "AI suggestions" in pal


# ------------------------------------------------------------------ settings


def test_outbound_group_lists_every_real_outbound_page(client: TestClient) -> None:
    html = client.get("/settings").text
    group = html.split("data-set-group")[2 if False else 1 :]
    text = "".join(group)
    out = text.split("Outbound")[1].split("Data management")[0]
    for href in (
        "/settings/mailboxes",
        "/settings/sending",
        "/settings/email-health",
        "/settings/privacy",
    ):
        assert f'href="{href}"' in out
    # each of them answers
    for href in ("/settings/mailboxes", "/settings/sending", "/settings/privacy"):
        assert client.get(href).status_code == 200


def test_overview_cards_have_header_actions(client: TestClient) -> None:
    html = client.get("/settings").text
    heads = re.findall(r'<div class="set-card-head">.*?</div>', html, re.S)
    assert len(heads) >= 8
    assert sum("btn sm" in h for h in heads) == len(heads)
    assert "Deliverability" not in html and "Not set up" in html


def test_invite_teammates_local_is_disabled_with_reason(client: TestClient) -> None:
    html = client.get("/settings").text
    btn = re.search(r"<button[^>]*>(?:(?!</button>).)*Invite teammates", html, re.S)
    assert btn and 'aria-disabled="true"' in btn.group(0) and "title=" in btn.group(0)
    assert "docs/server.md" in html and "Single user on this computer" in html
    assert 'id="invite-dlg"' not in html


def test_invite_teammates_server_mode_explains_sharing(tmp_path: Path) -> None:
    c = TestClient(make_app(tmp_path, mode="server", password="pw"), follow_redirects=True)
    c.post("/login", data={"password": "pw"})
    html = c.get("/settings").text
    assert 'id="invite-dlg"' in html and 'data-open-dialog="invite-dlg"' in html
    assert "/settings/api" in html and "PEPITE_PASSWORD" in html and "http" in html


# ------------------------------------------------------------------ email setup and health


def test_health_tabs_and_empty_state(client: TestClient) -> None:
    html = client.get("/settings/email-health?tab=domains").text
    for label in ("Overview", "Domains &amp; mailboxes", "Sending policies"):
        assert label in html
    assert "Deliverability: Not set up" in html
    assert "eh-skel" in html and "What happens next" in html
    assert "Add email &amp; domain" in html and 'href="/settings/mailboxes/new"' in html
    assert "Set up with AI" in html
    # no language model: disabled and explained
    assert re.search(r'<button[^>]*eh-ai-btn[^>]*aria-disabled="true"', html)
    assert "PEPITE_LLM_URL" in html


def test_set_up_with_ai_opens_assistant_when_llm_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PEPITE_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("PEPITE_LLM_MODEL", "m")
    html = client.get("/settings/email-health?tab=domains").text
    assert re.search(r'<a class="btn eh-ai-btn" href="/assistant\?prompt=[^"]+set%20up', html)


def test_set_up_with_ai_is_off_in_demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PEPITE_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("PEPITE_LLM_MODEL", "m")
    html = (
        TestClient(make_app(tmp_path, mode="demo")).get("/settings/email-health?tab=domains").text
    )
    assert "turned off in the public demo" in html


def test_run_check_needs_a_mailbox(client: TestClient) -> None:
    r = client.post("/settings/email-health/run")
    assert r.status_code == 303 and "Connect+a+mailbox" in r.headers["location"]
    html = client.get("/settings/email-health").text
    assert re.search(r'<button[^>]*aria-disabled="true"[^>]*>[^<]*<svg.*?Run a check', html, re.S)


def zone_for(monkeypatch: pytest.MonkeyPatch, states: dict[str, str]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def fake(domain: str, selector: str = "") -> list[dict[str, Any]]:
        calls.append((domain, selector))
        return [
            {"key": k, "label": k.upper(), "state": v, "summary": f"{k} is {v}"}
            for k, v in states.items()
        ]

    monkeypatch.setattr(pages_extra, "run_domain_health", fake)
    return calls


def test_run_check_saves_results_and_computes_verdict(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    add_mailbox(app)
    add_mailbox(app, "other@acme.example")
    html = client.get("/settings/email-health?tab=domains").text
    assert "acme.example" in html and "Not checked yet" in html
    assert html.count("Deliverability: Not checked yet") == 1  # two mailboxes, one domain

    calls = zone_for(monkeypatch, {"mx": "ok", "spf": "ok", "dmarc": "warn", "dkim": "unknown"})
    r = client.post("/settings/email-health/run")
    assert r.status_code == 303 and calls == [("acme.example", "")]
    html = client.get("/settings/email-health?tab=domains").text
    assert "Deliverability: Needs work" in html and "DMARC · Review" in html
    assert "DKIM · Not found" in html and "Checked " in html

    zone_for(monkeypatch, {"mx": "ok", "spf": "ok", "dmarc": "ok", "dkim": "unknown"})
    client.post("/settings/email-health/run", data={"domain": "acme.example", "selector": "s1"})
    html = client.get("/settings/email-health?tab=overview").text
    assert "Deliverability: Good" in html and "3 of 3" in html
    assert 'value="s1"' in client.get("/settings/email-health?tab=domains").text


def test_run_check_rejects_a_bad_selector(app: FastAPI, client: TestClient) -> None:
    add_mailbox(app)
    r = client.post(
        "/settings/email-health/run", data={"domain": "acme.example", "selector": "no no"}
    )
    assert r.status_code == 303 and "not+valid" in r.headers["location"]


def test_health_verdict_rules() -> None:
    v = pages_extra.health_verdict
    assert v([], {})["key"] == "none"
    assert v(["a.com"], {})["key"] == "unchecked"
    err = {"a.com": {"checks": [{"state": "error"}]}}
    assert v(["a.com"], err)["key"] == "unchecked"
    bad = {"a.com": {"checks": [{"state": "ok"}, {"state": "fail"}]}}
    assert v(["a.com"], bad)["key"] == "work"
    good = {"a.com": {"checks": [{"state": "ok"}, {"state": "unknown"}]}}
    assert v(["a.com"], good)["key"] == "good"
    mixed = {"a.com": good["a.com"]}
    assert v(["a.com", "b.com"], mixed)["key"] == "good"  # unchecked domains do not count


def test_domain_health_tries_common_dkim_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(name: str, rtype: str) -> Any:
        if name == "google._domainkey.a.com":
            return pages_extra._Lookup(["v=DKIM1; p=ABC"])
        if name == "a.com" and rtype == "MX":
            return pages_extra._Lookup(["10 mx.a.com"])
        return pages_extra._Lookup(missing=True)

    monkeypatch.setattr(pages_extra, "_resolve", fake)
    rows = {r["key"]: r for r in pages_extra.run_domain_health("a.com")}
    assert rows["dkim"]["state"] == "ok" and rows["mx"]["state"] == "ok"
    assert rows["spf"]["state"] == "fail"
    monkeypatch.setattr(pages_extra, "_resolve", lambda n, t: pages_extra._Lookup(missing=True))
    assert {r["key"]: r for r in pages_extra.run_domain_health("a.com")}["dkim"][
        "state"
    ] == "unknown"


def test_policies_tab_reads_the_engage_settings(app: FastAPI, client: TestClient) -> None:
    add_mailbox(app)
    engage_repo(app.state.store).save_settings(gap_minutes=7)
    html = client.get("/settings/email-health?tab=policies").text
    assert "Sending policies" in html and "me@acme.example" in html
    assert '<span class="mono">7</span> minutes' in html and 'href="/settings/sending"' in html


def test_manual_domain_check_still_works(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pages_extra,
        "run_dns_checks",
        lambda d, s="": [
            {
                "key": "mx",
                "label": "MX records",
                "what": "w",
                "state": "ok",
                "summary": "fine",
                "records": [],
            }
        ],
    )
    html = client.get("/settings/email-health?domain=example.com").text
    assert "dns-row ok" in html and "1 passed" in html


# ------------------------------------------------------------------ assistant


def test_assistant_page_is_full_screen_with_mic_and_attach(client: TestClient) -> None:
    html = client.get("/assistant").text
    assert "assistant-full" in html and "Back to the app" in html
    assert "data-welcome-opts" in html and html.count('type="radio"') == 4
    assert re.search(r"<button[^>]*data-mic[^>]*aria-disabled=\"true\"[^>]*title=", html)
    assert "data-attach" in html and 'type="file"' in html and "Drop in a file" in html


def test_assistant_embed_is_the_side_panel_layout(client: TestClient) -> None:
    html = client.get("/assistant?embed=1").text
    assert (
        "embed" in html and "Hi, I'm your sales assistant" in html and "assistant-full" not in html
    )


def test_assistant_setup_intent_links_to_the_mailbox_pages(client: TestClient) -> None:
    r = client.post("/assistant/send", data={"message": "help me set up a mailbox"})
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "/settings/mailboxes/new" in page and "/settings/email-health?tab=domains" in page


def test_assistant_text_attachment_becomes_context(client: TestClient) -> None:
    r = client.post(
        "/assistant/send",
        data={
            "message": "",
            "attachment_name": "notes.txt",
            "attachment_text": "We sell widgets. See https://acme-widgets.example today.",
        },
    )
    assert r.status_code == 303
    page = client.get(r.headers["location"]).text
    assert "notes.txt" in page and "added <strong>notes.txt</strong>" in page
    assert "Read acme-widgets.example" in page


def test_assistant_send_still_needs_something(client: TestClient) -> None:
    r = client.post("/assistant/send", data={"message": "  "})
    assert r.status_code == 303 and "Type" in r.headers["location"]


def test_csv_attach_offers_enrichment_and_is_gated_in_demo(tmp_path: Path) -> None:
    ok = (
        TestClient(make_app(tmp_path / "a")).get("/assistant").text
        if (tmp_path / "a").mkdir() is None
        else ""
    )
    assert "data-att-enrich" in ok and 'aria-disabled="true" title="Enrichment' not in ok
    demo = (
        TestClient(make_app(tmp_path / "b", mode="demo"))
        if (tmp_path / "b").mkdir() is None
        else None
    )
    assert demo is not None
    assert 'aria-disabled="true" title="Enrichment is turned off' in demo.get("/assistant").text


# ------------------------------------------------------------------ hub and notifications


def test_onboarding_hub_has_three_illustrated_guide_cards(client: TestClient) -> None:
    html = client.get("/onboarding").text
    cards = re.findall(r'<a class="res-card".*?</a>', html, re.S)
    assert len(cards) == 3 and all("<svg" in c for c in cards)


def test_notifications_show_the_integrations_notice(client: TestClient) -> None:
    html = client.get("/partials/notifications").text
    assert "sys-integrations" in html and 'href="/settings/integrations"' in html
    assert ">Older<" in html
