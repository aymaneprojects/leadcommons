from __future__ import annotations

import csv
import io
import re

import pytest
from typer.testing import CliRunner

from pepite import enrich
from pepite.cli import app
from pepite.mcp_server import (
    BearerTokenMiddleware,
    build_http_app,
    build_server,
    resolve_http_token,
)
from pepite.models import EmailCandidate, EmailStatus, Person, make_id
from pepite.store import Store
from tests.fakes import FakeSearch


@pytest.fixture
def server(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'mcp.db'}")
    return build_server(search=FakeSearch(), store=store)


async def call(server, tool_name, /, **args):
    res = await server.call_tool(tool_name, args)
    return res.structured_content if res.structured_content is not None else res.content


async def call_text(server, tool_name, /, **args):
    res = await server.call_tool(tool_name, args)
    return res.content[0].text


@pytest.mark.asyncio
async def test_tools_listed_with_annotations(server):
    tools = {t.name: t for t in await server.list_tools()}
    assert {
        "search_companies",
        "get_company",
        "find_people",
        "search_people",
        "find_email",
        "list_lists",
        "create_list",
        "add_to_list",
        "list_members",
        "export_list",
        "opt_out",
    } <= set(tools)
    assert tools["search_companies"].annotations.read_only_hint is True
    assert tools["opt_out"].annotations.read_only_hint is False
    assert server.instructions and "opt-out" in server.instructions.lower()


@pytest.mark.asyncio
async def test_search_and_get_company(server):
    res = await call(server, "search_companies", q="nuage", countries=["fr"], per_page=500)
    assert res["total"] == 1 and res["per_page"] == 50
    cid = res["companies"][0]["id"]
    full = await call(server, "get_company", company_id=cid)
    assert full["domain"] == "ateliernuage.example"
    assert full["sources"][0]["licence"] == "CC0-1.0"


@pytest.mark.asyncio
async def test_unknown_company_is_clean_error(server):
    with pytest.raises(Exception) as exc:
        await server.call_tool("get_company", {"company_id": "nope"})
    assert "No company with id 'nope'" in str(exc.value)
    assert "Traceback" not in str(exc.value)


@pytest.mark.asyncio
async def test_find_people_find_email_and_suppression(server, monkeypatch):
    cid = (await call(server, "search_companies", q="nuage"))["companies"][0]["id"]
    domain = "ateliernuage.example"
    ada = Person(id=make_id("pe", domain, "Ada Lovelace"), full_name="Ada Lovelace", title="CEO")
    monkeypatch.setattr(enrich, "find_people", lambda d: [ada])
    res = await call(server, "find_people", company_id=cid)
    assert res["count"] == 1
    assert res["people"][0]["company_id"] == cid
    pid = res["people"][0]["id"]

    cand = EmailCandidate(f"ada@{domain}", EmailStatus.VALID, 90, "{first}")
    monkeypatch.setattr(enrich, "find_email", lambda p, d, smtp_probe=False: cand)
    res = await call(server, "find_email", person_id=pid)
    assert res["found"] and res["email"]["address"] == f"ada@{domain}"

    found = await call(
        server, "search_people", text="ada", seniorities=[], email_statuses=["valid"]
    )
    assert found["total"] == 1 and found["people"][0]["email"]["status"] == "valid"

    # After opt-out the address disappears from every output.
    await call(server, "opt_out", email_or_domain=f"ada@{domain}")
    found = await call(server, "search_people", text="ada")
    assert found["people"][0]["email"] is None
    again = await call(server, "find_email", person_id=pid)
    assert again["found"] is False


@pytest.mark.asyncio
async def test_find_email_never_stores_suppressed(server, monkeypatch):
    store = server._pepite_backend.store
    p = Person(id="pe_x", full_name="Bob Smith", company_domain="example.org")
    store.save_person(p)
    await call(server, "opt_out", email_or_domain="bob@example.org")
    cand = EmailCandidate("bob@example.org", EmailStatus.VALID, 80)
    monkeypatch.setattr(enrich, "find_email", lambda *a, **k: cand)
    res = await call(server, "find_email", person_id="pe_x")
    assert res["found"] is False
    assert store.best_email("pe_x") is None


@pytest.mark.asyncio
async def test_find_people_refuses_suppressed_domain(server, monkeypatch):
    monkeypatch.setattr(enrich, "find_people", lambda d: pytest.fail("must not crawl"))
    await call(server, "opt_out", email_or_domain="@blocked.example")
    with pytest.raises(Exception) as exc:
        await server.call_tool("find_people", {"domain": "https://www.blocked.example/team"})
    assert "suppression" in str(exc.value)


@pytest.mark.asyncio
async def test_bad_enum_message(server):
    with pytest.raises(Exception) as exc:
        await server.call_tool("search_people", {"seniorities": ["boss"]})
    assert "unknown seniority" in str(exc.value)


@pytest.mark.asyncio
async def test_lists_and_csv_export_is_safe(server, monkeypatch):
    store = server._pepite_backend.store
    evil = Person(id="pe_e", full_name="=HYPERLINK(1)", title="+cmd", company_domain="ok.example")
    sup = Person(id="pe_s", full_name="Sue Hidden", company_domain="ok.example")
    store.save_person(evil)
    store.save_person(sup)
    store.save_email("pe_e", EmailCandidate("e@ok.example", EmailStatus.VALID, 70))
    store.save_email("pe_s", EmailCandidate("sue@ok.example", EmailStatus.VALID, 70))
    store.add_suppression("sue@ok.example")

    lst = await call(server, "create_list", name="Test", kind="people")
    await call(server, "add_to_list", list_id=lst["id"], record_ids=["pe_e", "pe_s"])
    lists = await call(server, "list_lists")
    assert lists["lists"][0]["size"] == 2
    members = await call(server, "list_members", list_id=lst["id"])
    assert members["total"] == 2

    text = await call_text(server, "export_list", list_id=lst["id"])
    rows = list(csv.reader(io.StringIO(text)))
    body = {r[0]: r for r in rows[1:]}
    assert "'=HYPERLINK(1)" in body
    assert body["'=HYPERLINK(1)"][1] == "'+cmd"
    assert body["Sue Hidden"][3] == "" and body["Sue Hidden"][4] == "suppressed"
    assert "sue@ok.example" not in text


@pytest.mark.asyncio
async def test_company_list_export(server):
    cid = (await call(server, "search_companies", q="kestrel"))["companies"][0]["id"]
    lst = await call(server, "create_list", name="Cos", kind="companies")
    await call(server, "add_to_list", list_id=lst["id"], record_ids=[cid])
    text = await call_text(server, "export_list", list_id=lst["id"])
    assert "Kestrel Systems" in text


@pytest.mark.asyncio
async def test_unknown_list(server):
    with pytest.raises(Exception) as exc:
        await server.call_tool("list_members", {"list_id": "zzz"})
    assert "No list with id" in str(exc.value)


# ---- HTTP transport


def test_resolve_token():
    assert resolve_http_token("local", "127.0.0.1", None) is None
    assert resolve_http_token("local", "127.0.0.1", " abc ") == "abc"
    with pytest.raises(ValueError):
        resolve_http_token("server", "127.0.0.1", None)
    with pytest.raises(ValueError):
        resolve_http_token("local", "0.0.0.0", "")


def test_http_requires_bearer_token(server):
    from starlette.testclient import TestClient

    asgi = build_http_app(server, "s3cret")
    assert isinstance(asgi, BearerTokenMiddleware)
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(asgi, base_url="http://127.0.0.1:8001") as client:
        assert client.post("/mcp", json=init, headers=headers).status_code == 401
        bad = client.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer nope"})
        assert bad.status_code == 401
        assert "www-authenticate" in bad.headers
        ok = client.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


# ---- CLI

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """CI forces colours (rich/typer split option names with ANSI codes): compare plain text."""
    return ANSI.sub("", text)


def test_cli_help_shows_mcp():
    assert "mcp" in plain(runner.invoke(app, ["--help"]).output)
    out = plain(runner.invoke(app, ["mcp", "--help"]).output)
    assert "--http" in out


def test_cli_mcp_http_server_mode_needs_token(monkeypatch, tmp_path):
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setenv("PEPITE_MODE", "server")
    monkeypatch.delenv("PEPITE_MCP_TOKEN", raising=False)
    result = runner.invoke(app, ["mcp", "--http"])
    assert result.exit_code == 1
    assert "PEPITE_MCP_TOKEN" in result.output


def test_cli_mcp_http_starts_with_token(monkeypatch, tmp_path):
    import uvicorn

    calls = {}
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setenv("PEPITE_MODE", "server")
    monkeypatch.setenv("PEPITE_MCP_TOKEN", "tok")
    monkeypatch.setattr(uvicorn, "run", lambda asgi, **k: calls.update(asgi=asgi, **k))
    result = runner.invoke(app, ["mcp", "--http", "--port", "9100"])
    assert result.exit_code == 0, result.output
    assert calls["port"] == 9100 and calls["host"] == "0.0.0.0"
    assert isinstance(calls["asgi"], BearerTokenMiddleware)
