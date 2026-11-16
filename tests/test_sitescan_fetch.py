"""Site scan crawler: robots, redirects, page budget, checkpoints, one request per site at once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from leadcommons.sitescan import fetch
from leadcommons.sitescan.fetch import HostPacer, scan_domain, scan_many

SIREN_TEXT = "Acme SAS, SIREN 552 081 317, RCS Nanterre. Standard : 01 40 20 30 40"
HOME = (
    "<html><head><title> Acme  SAS </title></head><body>"
    '<a href="/mentions-legales">Mentions légales</a><a href="/contact">Contact</a>'
    "</body></html>"
)
LEGAL = f"<html><body><p>{SIREN_TEXT}</p></body></html>"
CONTACT = "<html><body><p>Write to us</p></body></html>"


def html(body: str, status: int = 200, ctype: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(status, text=body, headers={"content-type": ctype})


def mount(router: respx.MockRouter, host: str, pages: dict[str, httpx.Response], *, robots=None):
    """Routes for https://<host>: pages by path, robots.txt (text or Response), 404 otherwise."""
    if robots is not None:
        resp = robots if isinstance(robots, httpx.Response) else httpx.Response(200, text=robots)
        router.get(f"https://{host}/robots.txt").mock(return_value=resp)
    for path, resp in pages.items():
        router.get(f"https://{host}{path}").mock(return_value=resp)
    router.route(host=host).mock(return_value=httpx.Response(404))


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as r:
        yield r


def seal(router: respx.MockRouter) -> None:
    """Anything not mounted is "no such host" (added last: respx uses the first match)."""
    router.route().mock(side_effect=httpx.ConnectError("no route"))


def called(router: respx.MockRouter, url: str) -> int:
    return sum(1 for c in router.calls if str(c.request.url) == url)


async def scan(router, domain: str, country: str | None = "FR", **kw):
    seal(router)
    async with httpx.AsyncClient() as client:
        return await scan_domain(client, domain, country, pacer=HostPacer(0), **kw)


@pytest.mark.asyncio
async def test_finds_register_number_on_legal_page_and_stops(router):
    mount(
        router,
        "acme.fr",
        {"/": html(HOME), "/mentions-legales": html(LEGAL), "/contact": html(CONTACT)},
        robots="User-agent: *\nDisallow: /private\n",
    )
    finding = await scan(router, "acme.fr")
    assert finding.status == "ok"
    assert [(i.registry, i.number) for i in finding.ids] == [("FR-SIREN", "552081317")]
    assert finding.evidence_url == "https://acme.fr/mentions-legales"
    assert finding.title == "Acme SAS"
    assert finding.phones  # switchboard number of the legal page
    # Early stop: the legal page had a number, so the contact page was never fetched.
    assert called(router, "https://acme.fr/contact") == 0


@pytest.mark.asyncio
async def test_user_agent_names_the_bot(router):
    mount(router, "acme.fr", {"/": html(HOME), "/mentions-legales": html(LEGAL)})
    await scan(router, "acme.fr")
    agents = {c.request.headers["user-agent"] for c in router.calls}
    assert len(agents) == 1
    (agent,) = agents
    assert agent.startswith("LeadCommonsBot/")
    assert "docs/sitescan.md" in agent


@pytest.mark.asyncio
async def test_page_budget_is_respected(router):
    pages = {"/": html(HOME), "/contact": html(CONTACT), "/mentions-legales": html(CONTACT)}
    mount(router, "acme.fr", pages)
    await scan(router, "acme.fr", max_pages=2)
    fetched = [c.request.url.path for c in router.calls if c.request.url.path != "/robots.txt"]
    assert len(fetched) == 2  # home + one more


@pytest.mark.asyncio
async def test_robots_disallow_for_our_agent(router):
    mount(
        router,
        "acme.fr",
        {"/": html(HOME)},
        robots="User-agent: LeadCommonsBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n",
    )
    finding = await scan(router, "acme.fr")
    assert finding.status == "robots"
    assert called(router, "https://acme.fr/") == 0


@pytest.mark.asyncio
async def test_robots_disallow_everyone_but_other_bot_allowed(router):
    mount(
        router,
        "acme.fr",
        {"/": html(HOME), "/mentions-legales": html(LEGAL)},
        robots="User-agent: Googlebot\nDisallow: /\n",
    )
    assert (await scan(router, "acme.fr")).status == "ok"


@pytest.mark.asyncio
async def test_robots_disallow_one_page_keeps_the_rest(router):
    mount(
        router,
        "acme.fr",
        {"/": html(HOME), "/mentions-legales": html(LEGAL), "/contact": html(LEGAL)},
        robots="User-agent: *\nDisallow: /mentions-legales\n",
    )
    finding = await scan(router, "acme.fr")
    assert finding.status == "ok"
    assert called(router, "https://acme.fr/mentions-legales") == 0
    assert finding.evidence_url == "https://acme.fr/contact"


@pytest.mark.parametrize("status", [404, 403, 401])
@pytest.mark.asyncio
async def test_robots_4xx_means_allowed(router, status):
    mount(router, "acme.fr", {"/": html(HOME)}, robots=httpx.Response(status))
    assert (await scan(router, "acme.fr")).status == "ok"


@pytest.mark.asyncio
async def test_robots_fetch_failure_means_allowed(router):
    router.get("https://acme.fr/robots.txt").mock(side_effect=httpx.ReadError("reset"))
    mount(router, "acme.fr", {"/": html(HOME)})
    assert (await scan(router, "acme.fr")).status == "ok"


@pytest.mark.asyncio
async def test_robots_5xx_skips_the_site(router):
    mount(router, "acme.fr", {"/": html(HOME)}, robots=httpx.Response(503))
    finding = await scan(router, "acme.fr")
    assert finding.status == "robots"
    assert "503" in (finding.error or "")
    assert called(router, "https://acme.fr/") == 0


@pytest.mark.asyncio
async def test_crawl_delay_is_honoured_up_to_a_cap(router):
    mount(router, "acme.fr", {"/": html(HOME)}, robots="User-agent: *\nCrawl-delay: 3\n")
    async with httpx.AsyncClient() as client:
        site = fetch._Site(client, "acme.fr", HostPacer(0))
        await site._check_robots("https://acme.fr/")
    assert site.crawl_delay == 3.0


@pytest.mark.asyncio
async def test_off_domain_redirect_is_refused(router):
    router.get("https://acme.fr/robots.txt").mock(return_value=httpx.Response(404))
    router.get("https://acme.fr/").mock(
        return_value=httpx.Response(301, headers={"location": "https://other.com/"})
    )
    mount(router, "other.com", {"/": html(LEGAL)})
    finding = await scan(router, "acme.fr")
    assert finding.status == "error"
    assert "off-domain" in (finding.error or "")
    assert called(router, "https://other.com/") == 0
    assert finding.ids == []


@pytest.mark.asyncio
async def test_same_domain_redirect_to_www_is_followed(router):
    router.get("https://acme.fr/robots.txt").mock(return_value=httpx.Response(404))
    router.get("https://acme.fr/").mock(
        return_value=httpx.Response(301, headers={"location": "https://www.acme.fr/"})
    )
    mount(router, "www.acme.fr", {"/": html(HOME), "/mentions-legales": html(LEGAL)})
    finding = await scan(router, "acme.fr")
    assert finding.status == "ok"
    assert finding.ids and finding.evidence_url == "https://www.acme.fr/mentions-legales"


@pytest.mark.asyncio
async def test_falls_back_to_www_then_http(router):
    router.get("https://acme.fr/").mock(side_effect=httpx.ConnectError("refused"))
    mount(router, "www.acme.fr", {"/": html(HOME), "/mentions-legales": html(LEGAL)})
    assert (await scan(router, "acme.fr")).status == "ok"
    router.reset()
    router.calls.reset()
    router.get("http://acme.fr/").mock(return_value=html(LEGAL))
    finding = await scan(router, "acme.fr")
    assert finding.status == "ok" and finding.ids


@pytest.mark.asyncio
async def test_no_site_when_nothing_answers(router):
    finding = await scan(router, "ghost.fr")
    assert finding.status == "no-site"
    assert finding.ids == []


@pytest.mark.asyncio
async def test_http_error_on_home_is_an_error_not_no_site(router):
    mount(router, "acme.fr", {"/": httpx.Response(500)})
    assert (await scan(router, "acme.fr")).status == "error"


@pytest.mark.asyncio
async def test_only_html_is_read(router):
    mount(router, "acme.fr", {"/": html("%PDF-1.4 " + SIREN_TEXT, ctype="application/pdf")})
    finding = await scan(router, "acme.fr")
    assert finding.status == "error" and finding.ids == []


@pytest.mark.asyncio
async def test_page_size_cap(router):
    big = "<html><body><p>" + "x " * 600_000 + f"</p><p>{SIREN_TEXT}</p></body></html>"
    mount(router, "acme.fr", {"/": html(big)})
    finding = await scan(router, "acme.fr")
    assert finding.status == "ok"
    assert finding.ids == []  # the number sits beyond the first megabyte


@pytest.mark.asyncio
async def test_requests_to_one_site_never_overlap(router):
    state = {"now": 0, "max": 0}

    async def slow(request: httpx.Request) -> httpx.Response:
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        if request.url.path == "/":
            return html(HOME)
        if request.url.path == "/contact":
            return html(CONTACT)
        return httpx.Response(404)

    router.route(host="acme.fr").mock(side_effect=slow)
    seal(router)
    pacer = HostPacer(0.01)
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(scan_domain(client, "acme.fr", "FR", pacer=pacer) for _ in range(3)))
    assert state["max"] == 1


@pytest.mark.asyncio
async def test_pacer_keeps_the_delay_between_requests():
    pacer = HostPacer(0.05)
    stamps: list[float] = []
    loop = asyncio.get_running_loop()

    async def hit():
        async with pacer.slot("acme.fr"):
            stamps.append(loop.time())

    await asyncio.gather(hit(), hit(), hit())
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert all(g >= 0.045 for g in gaps)


def _mount_many(router, hosts: list[str]):
    for h in hosts:
        mount(router, h, {"/": html(HOME), "/mentions-legales": html(LEGAL)})


def _rows(out: Path):
    con = duckdb.connect()
    return con.execute(
        f"SELECT domain, status, ids, pages FROM read_parquet('{out}/findings-*.parquet') "
        "ORDER BY domain"
    ).fetchall()


@pytest.mark.asyncio
async def test_scan_many_writes_parquet_and_stats(router, tmp_path):
    _mount_many(router, ["a.fr", "b.fr"])
    seal(router)
    stats = await scan_many(
        [("a.fr", "FR"), ("b.fr", None), ("ghost.fr", "FR")],
        tmp_path,
        concurrency=3,
        per_host_delay=0,
        checkpoint_every=2,
    )
    assert stats["domains"] == 3
    assert stats["with_ids"] == 2
    assert stats["by_registry"] == {"FR-SIREN": 2}
    assert stats["by_status"] == {"ok": 2, "no-site": 1}
    assert stats["pages"] == 4
    assert len(list(tmp_path.glob("findings-*.parquet"))) == 2  # 2 + 1 rows
    rows = _rows(tmp_path)
    assert [r[0] for r in rows] == ["a.fr", "b.fr", "ghost.fr"]
    ids = json.loads(rows[0][2])
    assert ids[0]["registry"] == "FR-SIREN" and ids[0]["number"] == "552081317"


@pytest.mark.asyncio
async def test_scan_many_resumes_from_checkpoints(router, tmp_path):
    _mount_many(router, ["a.fr", "b.fr", "c.fr"])
    seal(router)
    first = await scan_many(
        [("a.fr", "FR"), ("b.fr", "FR")], tmp_path, per_host_delay=0, checkpoint_every=1
    )
    assert first["domains"] == 2
    before = len(router.calls)
    second = await scan_many(
        [("a.fr", "FR"), ("b.fr", "FR"), ("c.fr", "FR"), ("c.fr", "FR")],
        tmp_path,
        per_host_delay=0,
    )
    assert second["domains"] == 1 and second["skipped"] == 3  # a, b done; c listed twice
    assert {c.request.url.host for c in list(router.calls)[before:]} == {"c.fr"}
    assert [r[0] for r in _rows(tmp_path)] == ["a.fr", "b.fr", "c.fr"]


@pytest.mark.asyncio
async def test_scan_many_keeps_partial_output_when_interrupted(router, tmp_path):
    _mount_many(router, ["a.fr"])
    seal(router)

    def feed():
        yield "a.fr", "FR"
        raise RuntimeError("killed")

    with pytest.raises(RuntimeError):
        await scan_many(feed(), tmp_path, concurrency=1, per_host_delay=0, checkpoint_every=100)
    assert [r[0] for r in _rows(tmp_path)] == ["a.fr"]


@pytest.mark.asyncio
async def test_scan_many_survives_a_crashing_scan(router, tmp_path, monkeypatch):
    _mount_many(router, ["b.fr"])
    seal(router)
    real = fetch.scan_with_stats

    async def boom(client, domain, *a, **kw):
        if domain == "a.fr":
            raise RuntimeError("bug")
        return await real(client, domain, *a, **kw)

    monkeypatch.setattr(fetch, "scan_with_stats", boom)
    stats = await scan_many([("a.fr", "FR"), ("b.fr", "FR")], tmp_path, per_host_delay=0)
    assert stats["by_status"] == {"error": 1, "ok": 1}
    assert stats["errors"] == 1
