# ruff: noqa: E501
"""Assistant: intents, actions on the local app, options, feedback, demo refusals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from leadcommons.ai.assistant import detect_intent, extract_domain
from leadcommons.config import Settings
from leadcommons.enrich import crawl
from leadcommons.models import Seniority
from leadcommons.store import Store
from leadcommons.store.tools import repo_for
from leadcommons.web.app import create_app
from tests.fakes import FakeJobs, FakeSearch, sample_email, sample_person

HX = {"HX-Request": "true"}

HOME_HTML = """<html><head><title>Niteo | Incubator</title>
<meta name="description" content="Niteo supports founders from idea to early customers with incubation and professional training.">
</head><body><h1>Incubation and training for entrepreneurs</h1>
<p>Structured coaching, training and an expert ecosystem to accelerate business creation for founders and partner institutions across the francophone world.</p>
</body></html>"""


def make_app(tmp_path: Path, **kw: Any) -> FastAPI:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/a.db", **kw
    )
    return create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )


@pytest.fixture(autouse=True)
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEADCOMMONS_LLM_URL", raising=False)
    monkeypatch.delenv("LEADCOMMONS_LLM_MODEL", raising=False)


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False)


def say(client: TestClient, text: str, cid: str = "") -> tuple[str, str]:
    r = client.post("/assistant/send", data={"message": text, "conversation_id": cid}, headers=HX)
    assert r.status_code == 200, r.text
    return r.headers["x-conversation"], r.text


def last_assistant(app: FastAPI, cid: str) -> Any:
    return [m for m in repo_for(app.state.store).messages(cid) if m.role == "assistant"][-1]


def choose(client: TestClient, cid: str, msg: Any, label_part: str) -> Any:
    choice = next(c for c in msg.data["options"]["choices"] if label_part in c["label"])
    return client.post(
        f"/assistant/{cid}/choose", data={"message_id": msg.id, "choice": choice["key"]}, headers=HX
    )


# ------------------------------------------------------------------ intents


@pytest.mark.parametrize(
    ("text", "name", "kind"),
    [
        ("/find software companies in Lyon", "find", "companies"),
        ("/people CEOs of fintech", "find", "people"),
        ("/list My list", "list", "companies"),
        ("/site example.com", "site", "companies"),
        ("/enrich", "enrich", "companies"),
        ("/help", "help", "companies"),
        ("/nonsense", "help", "companies"),
        ("hello", "greeting", "companies"),
        ("find software companies in Lyon with 11-50 people", "find", "companies"),
        ("Find CEOs of fintech startups in Germany", "find", "people"),
        ("see leads in my market", "market", "people"),
        ("save these in a list called Lyon software", "list", "companies"),
        ("add them to a sequence", "sequence", "companies"),
        ("automate this every week", "workflow", "companies"),
        ("enrich my csv please", "enrich", "companies"),
        ("my website is https://www.niteo.example/about", "site", "companies"),
        ("niteo.example", "site", "companies"),
    ],
)
def test_intents(text: str, name: str, kind: str) -> None:
    i = detect_intent(text)
    assert (i.name, i.kind) == (name, kind)


def test_extract_domain() -> None:
    assert extract_domain("see https://www.Niteo.example/about now") == "niteo.example"
    assert extract_domain("mail me at a@b.example") is None
    assert extract_domain("report.pdf") is None


# ------------------------------------------------------------------ search and results


def test_find_companies_shows_cards_and_option_card(app: FastAPI, client: TestClient) -> None:
    cid, html = say(client, "software companies in Germany with 11-50 people")
    assert "Brücke Software" in html and "Nordwind" not in html  # 11-50 only
    assert 'href="/companies/' in html and "Pick a next step" in html
    assert "Continue" in html and "disabled" in html  # Continue starts disabled
    msg = last_assistant(app, cid)
    labels = [c["label"] for c in msg.data["options"]["choices"]]
    assert any(lb.startswith("Find people at these") for lb in labels)
    assert any("Automate this search" in lb for lb in labels)
    assert msg.data["context"]["kind"] == "companies" and msg.data["chips"]


def test_find_nothing_matches(app: FastAPI, client: TestClient) -> None:
    cid, html = say(client, "software companies in Japan")
    assert "did not find any company" in html


def test_unintelligible_request_asks_for_more(client: TestClient) -> None:
    _cid, html = say(client, "asdf qwer")
    assert "could not turn that into a search" in html or "Keyword" in html


def test_conversation_is_stored_and_listed(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    say(client, "companies in Spain", cid)
    msgs = repo_for(app.state.store).messages(cid)
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    page = client.get(f"/assistant/{cid}").text
    assert "companies in France" in page and "History" in page and "companies in Spain" in page
    assert client.get("/assistant").text.count('class="hist-item') == 1
    assert client.get("/assistant/nope").status_code == 303


def test_empty_message_is_refused(client: TestClient) -> None:
    r = client.post("/assistant/send", data={"message": "  "}, headers=HX)
    assert r.status_code == 200 and "x-conversation" not in r.headers


def test_no_js_post_redirects_to_the_conversation(client: TestClient) -> None:
    r = client.post("/assistant/send", data={"message": "companies in France"})
    assert r.status_code == 303 and r.headers["location"].startswith("/assistant/")
    assert "Atelier Nuage" in client.get(r.headers["location"]).text


# ------------------------------------------------------------------ choices and actions


def test_choose_find_people_starts_jobs(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    msg = last_assistant(app, cid)
    r = choose(client, cid, msg, "Find people at these")
    assert r.status_code == 200 and "Started looking for people" in r.text
    jobs = list(app.state.jobs.jobs.values())
    assert {j.payload["domain"] for j in jobs} == {"ateliernuage.example", "mosspine.example"}
    assert all(j.kind == "find_people" for j in jobs)
    # the answered card cannot be used twice
    again = choose(client, cid, msg, "Find people at these")
    assert again.headers.get("hx-trigger") and len(app.state.jobs.jobs) == 2
    assert repo_for(app.state.store).get_message(msg.id).data["options"]["chosen"]


def test_choose_make_list_creates_a_company_list(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    r = choose(client, cid, last_assistant(app, cid), "in a list")
    assert "I saved" in r.text
    lists = app.state.store.lists("companies")
    assert len(lists) == 1 and lists[0].size == 2
    assert f"/lists/{lists[0].id}" in r.text
    assert app.state.store.saved_company_ids()  # companies were saved locally too


def test_list_command_after_a_search(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in Germany")
    _, html = say(client, "/list German targets", cid)
    assert "German targets" in html
    assert [li.name for li in app.state.store.lists()] == ["German targets"]


def test_list_without_results_explains(app: FastAPI, client: TestClient) -> None:
    _, html = say(client, "/list nothing")
    assert "nothing to save" in html and not app.state.store.lists()


def test_people_search_uses_saved_people_and_offers_sequence(
    app: FastAPI, client: TestClient
) -> None:
    store, search = app.state.store, app.state.search
    company = search.companies[0]
    store.save_company(company)
    p = sample_person(company, "Claire Martin", "CEO")
    p.seniority = Seniority.C_SUITE
    store.save_person(p)
    store.save_email(p.id, sample_email("claire@ateliernuage.example"))
    cid, html = say(client, "/people CEO")
    assert "Claire Martin" in html and "claire@ateliernuage.example" in html
    msg = last_assistant(app, cid)
    r = choose(client, cid, msg, "sequence")
    assert r.headers["hx-trigger"] and "lc-open" in r.headers["hx-trigger"]
    assert f"/partials/sequence-picker?ids={p.id}" in r.headers["hx-trigger"]
    r = choose(client, cid, last_assistant(app, cid) if False else msg, "list")
    assert r.status_code == 200  # already answered: refused with a toast, nothing breaks


def test_people_search_without_saved_people_offers_jobs(app: FastAPI, client: TestClient) -> None:
    cid, html = say(client, "/people CEOs of software companies in Germany")
    assert "not found any person" in html and "Nordwind Data" in html
    msg = last_assistant(app, cid)
    assert any("Find people" in c["label"] for c in msg.data["options"]["choices"])


def test_automate_option_redirects_to_prefilled_builder(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    r = choose(client, cid, last_assistant(app, cid), "Automate")
    assert r.headers["hx-redirect"].startswith("/workflows/new?source=companies&query=country")


def test_sequence_and_workflow_intents(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    _, html = say(client, "email them with a sequence", cid)
    assert "Sequences email people" in html
    _, html = say(client, "automate this", cid)
    assert "/workflows/new?source=companies" in html


def test_help_enrich_greeting(client: TestClient) -> None:
    assert "/find" in say(client, "/help")[1] and "/enrich" in say(client, "/help")[1]
    assert 'href="/enrich"' in say(client, "/enrich")[1]
    assert "Hello" in say(client, "hi")[1]
    assert "do not know the command" in say(client, "/bogus")[1]


# ------------------------------------------------------------------ website summary


def fake_site(monkeypatch: pytest.MonkeyPatch, html: str = HOME_HTML) -> list[str]:
    asked: list[str] = []

    def fake_get_site(domain: str, *, max_pages: int = 10) -> crawl.Site:
        asked.append(domain)
        site = crawl.Site(domain=domain, max_pages=max_pages)
        if html:
            site.pages.append(crawl.Page(f"https://{domain}/", html))
        return site

    monkeypatch.setattr(crawl, "get_site", fake_get_site)
    return asked


def test_site_summary_and_next_steps(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = fake_site(monkeypatch)
    cid, html = say(client, "my website is https://www.niteo.example/")
    assert asked == ["niteo.example"]
    assert (
        "Niteo" in html and "incubation" in html.lower() and "who you should be selling to" in html
    )
    msg = last_assistant(app, cid)
    labels = [c["label"] for c in msg.data["options"]["choices"]]
    assert labels[0] == "See companies I can sell to" and "Set up my company profile" in labels
    assert "See leads in my market" in labels and "Prospect different company" in labels
    r = choose(client, cid, msg, "company profile")
    assert "Saved your company profile" in r.text
    state = json.loads((app.state.settings.home / "ui_state.json").read_text())
    assert state["assistant_profile"]["domain"] == "niteo.example"
    # "my market" now uses the saved profile
    _, html = say(client, "see leads in my market")
    assert "Understood" in html or "compan" in html


def test_site_unreadable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_site(monkeypatch, html="")
    _, html = say(client, "/site down.example")
    assert "could not read a public homepage" in html


def test_site_without_domain_asks(client: TestClient) -> None:
    _, html = say(client, "/site")
    assert "Which website" in html


def test_market_without_profile_asks_for_website(client: TestClient) -> None:
    _, html = say(client, "companies I can sell to for my market")
    assert "what you sell" in html


# ------------------------------------------------------------------ llm


def test_llm_summarises_website_and_suggests_buyers(
    app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_site(monkeypatch)
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "m")
    reply = {
        "summary": "Niteo coaches founders.",
        "buyers": ["universities in France", "business schools"],
    }
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://llm.test/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}
        )
        cid, html = say(client, "/site niteo.example")
    assert route.called and "Niteo coaches founders." in html
    labels = [c["label"] for c in last_assistant(app, cid).data["options"]["choices"]]
    assert "Search: universities in France" in labels


def test_llm_failure_falls_back_to_rules(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_site(monkeypatch)
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "m")
    with respx.mock() as mock:
        mock.post("http://llm.test/v1/chat/completions").mock(side_effect=httpx.ConnectError("x"))
        _, html = say(client, "/site niteo.example")
    assert "Niteo" in html and "incubation" in html.lower()


def test_llm_reads_a_search_when_rules_find_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "m")
    filters = {
        "countries": ["FR"],
        "size_ranges": [],
        "industries": [],
        "titles": [],
        "seniorities": [],
        "keywords": [],
    }
    with respx.mock() as mock:
        mock.post("http://llm.test/v1/chat/completions").respond(
            200, json={"choices": [{"message": {"content": json.dumps(filters)}}]}
        )
        _, html = say(client, "les boites de la région lyonnaise zzz")
    assert "Atelier Nuage" in html and "(AI)" not in html and "Country" in html


# ------------------------------------------------------------------ demo mode


def test_demo_never_crawls_or_calls_an_llm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(tmp_path, mode="demo")
    client = TestClient(app, follow_redirects=False)
    asked = fake_site(monkeypatch)
    monkeypatch.setenv("LEADCOMMONS_LLM_URL", "http://llm.test/v1")
    monkeypatch.setenv("LEADCOMMONS_LLM_MODEL", "m")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("http://llm.test/v1/chat/completions").respond(200, json={})
        _, html = say(client, "/site niteo.example")
        assert "turned off in the public demo" in html and not asked
        cid, html = say(client, "companies in France")
        assert "Atelier Nuage" in html  # searching the open data still works
        msg = last_assistant(app, cid)
        assert not any("Find people" in c["label"] for c in msg.data["options"]["choices"])
        _, html = say(client, "/people CEOs in Germany")
        assert "turned off in the public demo" in html
    assert not route.called and not app.state.jobs.jobs
    r = client.post("/assistant/send", data={"message": "x"}, headers=HX)
    assert r.status_code == 200


def test_demo_refuses_find_people_choice(tmp_path: Path) -> None:
    app = make_app(tmp_path, mode="demo")
    client = TestClient(app, follow_redirects=False)
    cid, _ = say(client, "companies in France")
    msg = last_assistant(app, cid)
    # a forged choice is still refused by the assistant
    from leadcommons.ai.assistant import Assistant

    a = Assistant(
        app.state.store,
        app.state.search,
        app.state.jobs,
        app.state.settings,
        repo_for(app.state.store),
    )
    rep = a.find_people(["x"])
    assert "turned off" in rep.text and not app.state.jobs.jobs and msg


# ------------------------------------------------------------------ feedback, delete, card, banner


def test_feedback_is_stored_and_toggles(app: FastAPI, client: TestClient) -> None:
    cid, html = say(client, "companies in France")
    mid = last_assistant(app, cid).id
    assert f'id="fb-{mid}"' in html
    r = client.post("/assistant/feedback", data={"message_id": mid, "rating": "up"}, headers=HX)
    assert r.status_code == 200 and "on" in r.text
    assert repo_for(app.state.store).get_message(mid).rating == 1
    client.post("/assistant/feedback", data={"message_id": mid, "rating": "down"}, headers=HX)
    assert repo_for(app.state.store).get_message(mid).rating == -1
    client.post("/assistant/feedback", data={"message_id": mid, "rating": "down"}, headers=HX)
    assert repo_for(app.state.store).get_message(mid).rating == 0
    assert (
        client.post("/assistant/feedback", data={"message_id": 9999, "rating": "up"}).status_code
        == 404
    )
    assert client.post("/assistant/feedback", data={"message_id": "x"}).status_code == 400


def test_delete_conversation(app: FastAPI, client: TestClient) -> None:
    cid, _ = say(client, "companies in France")
    r = client.post(f"/assistant/{cid}/delete")
    assert r.status_code == 303
    assert repo_for(app.state.store).conversations() == []


def test_mcp_card_can_be_dismissed(app: FastAPI, client: TestClient) -> None:
    page = client.get("/assistant").text
    assert "bring your own AI" in page and "docs/mcp.md" in page
    assert client.post("/assistant-card/dismiss", headers=HX).status_code == 200
    assert "bring your own AI" not in client.get("/assistant").text


def test_embed_mode_has_no_mcp_card(client: TestClient) -> None:
    html = client.get("/assistant?embed=1").text
    assert "app embed" in html and "Use LeadCommons from Claude" not in html


def test_home_banner_continues_the_conversation(app: FastAPI, client: TestClient) -> None:
    assert "Your last Assistant chat is waiting" not in client.get("/").text
    cid, _ = say(client, "companies in France")
    home = client.get("/").text
    assert "Your last Assistant chat is waiting" in home and f'href="/assistant/{cid}"' in home
    assert client.post("/dismiss-banner", headers=HX).status_code == 200
    assert "Your last Assistant chat is waiting" not in client.get("/").text
    say(client, "companies in Spain", cid)  # new activity brings it back
    assert "Your last Assistant chat is waiting" in client.get("/").text


def test_assistant_page_does_not_leak_apollo(client: TestClient) -> None:
    html = client.get("/assistant").text
    assert "Apollo" not in html and "Explore" not in html
