"""Crawler: discovery, scope, robots.txt, redirects, limits, rate limiting and cache."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from pepite.connectors.base import USER_AGENT
from pepite.enrich import crawl

FIXTURES = Path(__file__).parent / "fixtures" / "enrich"
BASE = "https://acme.example"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def simple_page(title: str) -> str:
    return f"<html><body><h1>{title}</h1></body></html>"


@pytest.fixture(autouse=True)
def _fast_and_fresh(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(crawl, "CRAWL_DELAY", 0.0)
    crawl.clear_cache()
    yield
    crawl.clear_cache()


def mock_site(router: respx.MockRouter, robots: str = "") -> dict[str, respx.Route]:
    """Routes for the homepage fixture and its candidate pages."""
    routes = {"robots": router.get(f"{BASE}/robots.txt").respond(200, text=robots)}
    routes["/"] = router.get(f"{BASE}/").respond(200, html=fixture("home.html"))
    for path in ("/team", "/about-us", "/qsn", "/fr/mentions-legales"):
        routes[path] = router.get(BASE + path).respond(200, html=simple_page(path))
    router.get("https://jobs.acme.example/robots.txt").respond(404)
    routes["kontakt"] = router.get("https://jobs.acme.example/kontakt").respond(
        200, html=simple_page("Kontakt")
    )
    for url in (
        f"{BASE}/blog/hello-world",
        f"{BASE}/products/data",
        f"{BASE}/files/impressum.pdf",
        "https://evil.example/team",
        "https://twitter.com/acme",
    ):
        routes[url] = router.get(url).respond(200, html=simple_page("never"))
    return routes


def test_discovers_candidate_pages_by_priority_and_stays_on_domain() -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_site(router)
        site = crawl.crawl_site("https://www.Acme.example/", max_pages=10)
    assert site.domain == "acme.example"
    assert [p.url for p in site.pages] == [
        f"{BASE}/",
        f"{BASE}/team",  # people pages first
        f"{BASE}/about-us",  # then about pages (path or link text keywords)
        f"{BASE}/qsn",  # "Qui sommes-nous ?" link text
        f"{BASE}/fr/mentions-legales",  # legal notices, fragment stripped
        "https://jobs.acme.example/kontakt",  # contact on a subdomain
    ]
    for url in ("blog/hello-world", "products/data", "files/impressum.pdf"):
        assert not routes[f"{BASE}/{url}"].called
    assert not routes["https://evil.example/team"].called
    assert not routes["https://twitter.com/acme"].called
    assert all(call.request.headers["user-agent"] == USER_AGENT for call in router.calls)


def test_robots_disallow_is_obeyed() -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_site(router, robots=fixture("robots_disallow_team.txt"))
        site = crawl.crawl_site("acme.example")
    assert not routes["/team"].called
    urls = [p.url for p in site.pages]
    assert f"{BASE}/team" not in urls
    assert f"{BASE}/about-us" in urls


def test_unreachable_robots_means_no_crawl() -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_site(router)
        routes["robots"].respond(503)
        router.get("http://acme.example/robots.txt").respond(503)
        site = crawl.crawl_site("acme.example")
    assert site.pages == []
    assert not routes["/"].called


def test_max_pages_limits_requests() -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_site(router)
        site = crawl.crawl_site("acme.example", max_pages=2)
    assert [p.url for p in site.pages] == [f"{BASE}/", f"{BASE}/team"]
    assert not routes["/about-us"].called


def test_falls_back_to_http() -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/robots.txt").mock(side_effect=httpx.ConnectError("tls"))
        router.get(f"{BASE}/").mock(side_effect=httpx.ConnectError("tls"))
        router.get("http://acme.example/robots.txt").respond(404)
        router.get("http://acme.example/").respond(200, html=simple_page("plain http"))
        site = crawl.crawl_site("acme.example")
    assert [p.url for p in site.pages] == ["http://acme.example/"]


def test_redirects_followed_only_within_domain_and_html_only() -> None:
    www = "https://www.acme.example"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/robots.txt").respond(404)
        router.get(f"{BASE}/").respond(301, headers={"Location": f"{www}/"})
        router.get(f"{www}/robots.txt").respond(404)
        router.get(f"{www}/").respond(
            200, html='<a href="/about-us">About</a> <a href="/team">Team</a>'
        )
        router.get(f"{www}/about-us").respond(302, headers={"Location": "https://evil.example/"})
        evil = router.get("https://evil.example/").respond(200, html=simple_page("evil"))
        router.get(f"{www}/team").respond(200, text="Team, but plain text")
        site = crawl.crawl_site("acme.example")
    assert [p.url for p in site.pages] == [f"{www}/"]
    assert not evil.called


def test_body_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl, "MAX_BYTES", 64)
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE}/robots.txt").respond(404)
        router.get(f"{BASE}/").respond(200, html="<p>" + "x" * 10_000 + "</p>")
        site = crawl.crawl_site("acme.example", max_pages=1)
    assert len(site.pages[0].html) == 64


def test_waits_between_requests() -> None:
    sleeps: list[float] = []
    with respx.mock(assert_all_called=False) as router:
        mock_site(router)
        crawl.crawl_site("acme.example", max_pages=3, delay=1.0, sleep=sleeps.append)
        requests = len(router.calls)
    assert len(sleeps) == requests - 1
    assert all(0 < s <= 1.0 for s in sleeps)


def test_get_site_caches_the_crawl() -> None:
    with respx.mock(assert_all_called=False) as router:
        routes = mock_site(router)
        first = crawl.get_site("acme.example", max_pages=3)
        assert crawl.get_site("www.acme.example", max_pages=2) is first
        assert routes["/"].call_count == 1
        crawl.get_site("acme.example", max_pages=5)  # wants more pages: crawl again
        assert routes["/"].call_count == 2


@pytest.mark.parametrize(
    ("url", "text", "expected"),
    [
        ("https://x.example/our-team/", "", 0),
        ("https://x.example/es/equipo", "", 0),
        ("https://x.example/page", "L'équipe", 0),
        ("https://x.example/fr/qui-sommes-nous", "", 1),
        ("https://x.example/de/ueber-uns", "", 1),
        ("https://x.example/page", "Über uns", 1),
        ("https://x.example/it/chi-siamo", "", 1),
        ("https://x.example/nl/over-ons", "", 1),
        ("https://x.example/a-propos", "", 1),
        ("https://x.example/impressum", "", 2),
        ("https://x.example/mentions-legales", "", 2),
        ("https://x.example/kontakt", "", 3),
        ("https://x.example/teamwork-tips", "Teamwork tips", None),
        ("https://x.example/pricing", "Pricing", None),
    ],
)
def test_link_priority(url: str, text: str, expected: int | None) -> None:
    assert crawl.link_priority(url, text) == expected


def test_normalise_domain_and_scope() -> None:
    assert crawl.normalise_domain("https://www.Acme.example:443/path") == "acme.example"
    assert crawl.in_domain("jobs.acme.example", "acme.example")
    assert not crawl.in_domain("notacme.example", "acme.example")


def test_visible_text_has_one_line_per_block() -> None:
    page = crawl.Page(
        "u",
        "<p>Directeur de la\n publication : <b>Jean</b>\nDupont</p>"
        "<script>var x = 'hidden';</script><ul><li>a</li><li>b</li></ul>x<br>y",
    )
    assert page.text == "Directeur de la publication : Jean Dupont\na\nb\nx\ny"
