"""Polite one-site snapshot for insights: homepage plus careers, contact, legal and about pages.

Built on the crawler's fetcher (robots.txt, User-Agent, delay between requests, size limit,
same-site redirects) and additionally keeps the homepage's response headers and cookie names,
which the technology detector needs. Team pages are deliberately not visited: insights hold
company data only. Snapshots are cached per domain for CACHE_TTL seconds.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from pepite.connectors.base import USER_AGENT
from pepite.enrich import crawl
from pepite.enrich.crawl import Page

CACHE_TTL = 900.0
CACHE_SIZE = 8

_KEYWORDS: tuple[str, ...] = (
    "careers, career, jobs, join us, join our team, work with us, we are hiring, hiring, "
    "recruitment, karriere, stellenangebote, emploi, emplois, carrieres, recrutement, "
    "offres d emploi, rejoignez nous, trabaja con nosotros, empleo, vacatures, werken bij, "
    "lavora con noi, offerte di lavoro",
    "contact, contact us, kontakt, contacto, contatti, contactez nous, nous contacter",
    "mentions legales, impressum, imprint, legal notice, aviso legal, note legali, colofon, "
    "informations legales",
    "about, about us, qui sommes nous, a propos, nosotros, quienes somos, chi siamo, over ons, "
    "uber uns, ueber uns, company",
)
_TOKENS = tuple(tuple(tuple(crawl._tokens(k)) for k in group.split(",")) for group in _KEYWORDS)  # noqa: SLF001


@dataclass
class Snapshot:
    domain: str
    pages: list[Page] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)  # homepage, lower-case names
    cookies: list[str] = field(default_factory=list)  # cookie names set by the homepage
    links: list[str] = field(default_factory=list)  # every absolute URL seen (a, iframe, script)


def link_priority(url: str, text: str) -> int | None:
    """0 careers, 1 contact, 2 legal notice, 3 about; None when not interesting."""
    path = crawl._tokens(unquote(urlsplit(url).path))  # noqa: SLF001
    label = crawl._tokens(text)  # noqa: SLF001
    for prio, group in enumerate(_TOKENS):
        for kw in group:
            n = len(kw)
            for toks in (path, label):
                if any(tuple(toks[i : i + n]) == kw for i in range(len(toks) - n + 1)):
                    return prio
    return None


def _links_of(page: Page) -> list[tuple[str, str]]:
    """(absolute url, anchor text) of anchors, plus iframe and script sources."""
    tree = page.tree()
    out: list[tuple[str, str]] = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
            out.append((crawl._clean_url(urljoin(page.url, href)), a.text(separator=" ")))  # noqa: SLF001
    for tag in ("iframe", "script"):
        for node in tree.css(f"{tag}[src]"):
            src = (node.attributes.get("src") or "").strip()
            if src:
                out.append((crawl._clean_url(urljoin(page.url, src)), ""))  # noqa: SLF001
    return out


def _cookie_names(headers: httpx.Headers) -> list[str]:
    names = []
    for raw in headers.get_list("set-cookie"):
        name = raw.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return names


def snapshot_site(
    domain: str,
    *,
    max_pages: int = 6,
    delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Snapshot:
    domain = crawl.normalise_domain(domain)
    snap = Snapshot(domain=domain)
    if not domain:
        return snap
    captured: list[tuple[str, httpx.Headers]] = []

    def on_response(resp: httpx.Response) -> None:
        captured.append((str(resp.url), resp.headers))

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(
        headers=headers,
        timeout=crawl.TIMEOUT,
        follow_redirects=False,
        event_hooks={"response": [on_response]},
    ) as client:
        fetcher = crawl._Fetcher(  # noqa: SLF001
            domain, client, crawl.CRAWL_DELAY if delay is None else delay, sleep
        )
        home = fetcher.page(f"https://{domain}/") or fetcher.page(f"http://{domain}/")
        if home is None:
            return snap
        seen_resp = [(u, h) for u, h in captured if not urlsplit(u).path.endswith("/robots.txt")]
        if seen_resp:
            snap.headers = {k.lower(): v for k, v in seen_resp[-1][1].items()}
            snap.cookies = sorted({c for _, h in seen_resp for c in _cookie_names(h)})
        snap.pages.append(home)
        fetched = {crawl._url_key(home.url)}  # noqa: SLF001
        seen = set(fetched)
        queue: list[tuple[int, int, str]] = []
        order = itertools.count()
        links: list[str] = []

        def enqueue(page: Page) -> None:
            for url, text in _links_of(page):
                links.append(url)
                parts = urlsplit(url)
                if parts.scheme not in {"http", "https"}:
                    continue
                if not crawl.in_domain(parts.hostname, domain) or crawl._SKIP_EXT.search(  # noqa: SLF001
                    parts.path
                ):
                    continue
                prio = link_priority(url, text)
                if prio is not None and (key := crawl._url_key(url)) not in seen:  # noqa: SLF001
                    seen.add(key)
                    heapq.heappush(queue, (prio, next(order), url))

        enqueue(home)
        careers_followed = 0
        while queue and fetcher.page_requests < max_pages:
            prio, _, url = heapq.heappop(queue)
            page = fetcher.page(url)
            if page is None or crawl._url_key(page.url) in fetched:  # noqa: SLF001
                continue
            fetched.add(crawl._url_key(page.url))  # noqa: SLF001
            snap.pages.append(page)
            if prio == 0 and careers_followed < 1:  # one hop into the careers section
                careers_followed += 1
                enqueue(page)
            else:
                links.extend(url for url, _ in _links_of(page))
        snap.links = list(dict.fromkeys(links))
    return snap


_cache: dict[str, tuple[float, Snapshot]] = {}
_lock = threading.Lock()
_domain_locks: dict[str, threading.Lock] = {}


def get_snapshot(domain: str, *, max_pages: int = 6) -> Snapshot:
    """Cached snapshot; concurrent callers for one domain share a single crawl."""
    domain = crawl.normalise_domain(domain)
    with _lock:
        dlock = _domain_locks.setdefault(domain, threading.Lock())
    with dlock:
        with _lock:
            hit = _cache.get(domain)
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            return hit[1]
        snap = snapshot_site(domain, max_pages=max_pages)
        with _lock:
            _cache[domain] = (time.monotonic(), snap)
            while len(_cache) > CACHE_SIZE:
                del _cache[min(_cache, key=lambda d: _cache[d][0])]
        return snap


def clear_cache() -> None:
    with _lock:
        _cache.clear()
