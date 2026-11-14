"""Polite crawler for a company's own public pages.

Fetches the homepage (https, then http), follows links whose path or text looks like a team,
about, legal-notice or contact page (EN/FR/DE/ES/NL/IT), stays on the domain and its
subdomains, obeys robots.txt, waits CRAWL_DELAY seconds between requests, never logs in and
only reads text/html. Crawls are cached per domain for CACHE_TTL seconds so find_people,
find_public_emails and repeated find_email calls share one crawl.
"""

from __future__ import annotations

import heapq
import itertools
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cached_property
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from selectolax.lexbor import LexborHTMLParser

from leadcommons.connectors.base import USER_AGENT
from leadcommons.enrich.names import fold
from leadcommons.models import Person

CRAWL_DELAY = 1.0  # seconds between two requests to the same site
TIMEOUT = 10.0
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_DEPTH = 2  # homepage is depth 0; links found on depth-1 pages are still followed
CACHE_TTL = 900.0
CACHE_SIZE = 8

# Link keywords by priority: people pages, about pages, legal notices, contact pages.
_KEYWORDS: tuple[str, ...] = (
    "team, our team, leadership, management, people, founders, equipe, l equipe, notre equipe, "
    "fondateurs, equipo, nuestro equipo, unser team, ons team, het team, il team, la squadra, "
    "direction, gouvernance",
    "about, about us, qui sommes nous, a propos, nosotros, quienes somos, chi siamo, over ons, "
    "uber uns, ueber uns",
    "mentions legales, impressum, imprint, legal notice, aviso legal, note legali, colofon, "
    "informations legales",
    "contact, kontakt, contacto, contatti, contactez nous",
)
_SKIP_EXT = re.compile(
    r"\.(?:pdf|jpe?g|png|gif|svg|webp|ico|zip|gz|docx?|xlsx?|pptx?|mp[34]|mov|css|js|json|xml"
    r"|ics|vcf)$",
    re.IGNORECASE,
)
_BLOCK_TAGS = (
    "p,div,br,li,ul,ol,h1,h2,h3,h4,h5,h6,tr,td,th,table,section,article,header,footer,aside,"
    "nav,dt,dd,dl,address,blockquote,figure,figcaption,main,form,hr,pre"
)
_BREAK = "\x1e"  # record separator: never present in page text
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([a-zA-Z0-9_-]+)", re.IGNORECASE)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(text).lower())


_KEYWORD_TOKENS = tuple(tuple(tuple(_tokens(k)) for k in g.split(",")) for g in _KEYWORDS)


def normalise_domain(domain: str) -> str:
    """Bare lowercase host: "https://www.Example.com/x" -> "example.com"."""
    d = domain.strip().lower()
    if "://" in d:
        d = urlsplit(d).hostname or ""
    d = d.split("/")[0].split(":")[0].strip(".")
    return d.removeprefix("www.")


def in_domain(host: str | None, domain: str) -> bool:
    """True for `domain` itself and any of its subdomains."""
    host = (host or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


@dataclass
class Page:
    """One fetched HTML page."""

    url: str
    html: str

    def tree(self) -> LexborHTMLParser:
        return LexborHTMLParser(self.html)

    @cached_property
    def text(self) -> str:
        """Visible text with one line per block element (scripts and styles removed)."""
        tree = self.tree()
        tree.strip_tags(["script", "style", "noscript", "template", "svg"])
        root = tree.body
        if root is None:
            return ""
        for node in root.css(_BLOCK_TAGS):  # mark block boundaries; source newlines are spaces
            node.insert_before(_BREAK)
            node.insert_after(_BREAK)
        lines = (" ".join(line.split()) for line in root.text(separator="").split(_BREAK))
        return "\n".join(line for line in lines if line)


@dataclass
class Site:
    """Result of one crawl; `people` and `emails` memoise extraction results."""

    domain: str
    max_pages: int
    pages: list[Page] = field(default_factory=list)
    people: list[Person] | None = None
    emails: list[str] | None = None


@dataclass
class _Fetched:
    url: str
    status: int
    text: str


def _clean_url(url: str) -> str:
    return urldefrag(url)[0]


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path.rstrip('/') or '/'}?{parts.query}"


def _is_html(content_type: str) -> bool:
    return content_type.split(";")[0].strip().lower() in {"text/html", "application/xhtml+xml"}


def _read_limited(resp: httpx.Response) -> bytes:
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BYTES:
            break
    return b"".join(chunks)[:MAX_BYTES]


def _decode(body: bytes, charset: str | None) -> str:
    meta = _META_CHARSET.search(body[:4096])
    for enc in (charset, meta.group(1).decode() if meta else None, "utf-8", "cp1252"):
        if enc:
            try:
                return body.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("utf-8", errors="replace")


def link_priority(url: str, text: str) -> int | None:
    """0 people, 1 about, 2 legal notice, 3 contact; None when the link is not interesting."""
    path = _tokens(unquote(urlsplit(url).path))
    label = _tokens(text)
    for prio, group in enumerate(_KEYWORD_TOKENS):
        for kw in group:
            n = len(kw)
            for toks in (path, label):
                if any(tuple(toks[i : i + n]) == kw for i in range(len(toks) - n + 1)):
                    return prio
    return None


def _candidate_links(page: Page, domain: str) -> Iterator[tuple[int, str]]:
    for a in page.tree().css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = _clean_url(urljoin(page.url, href))
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not in_domain(parts.hostname, domain):
            continue
        if _SKIP_EXT.search(parts.path):
            continue
        prio = link_priority(url, a.text(separator=" "))
        if prio is not None:
            yield prio, url


class _Fetcher:
    """Single-site HTTP access: robots.txt, rate limit, same-site redirects, size limit."""

    def __init__(
        self, domain: str, client: httpx.Client, delay: float, sleep: Callable[[float], None]
    ) -> None:
        self.domain = domain
        self.client = client
        self.delay = delay
        self.sleep = sleep
        self.page_requests = 0
        self._robots: dict[str, RobotFileParser] = {}
        self._last: float | None = None

    def _wait(self) -> None:
        if self._last is not None:
            remaining = self.delay - (time.monotonic() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = time.monotonic()

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        return self._robots[origin].can_fetch(USER_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser:
        rp = RobotFileParser(origin + "/robots.txt")
        got = self._get(origin + "/robots.txt", page=False)
        if got is None or got.status in {401, 403} or got.status >= 500:
            rp.disallow_all = True  # unreachable or forbidden: assume we may not crawl
        elif got.status >= 400:
            rp.allow_all = True
        else:
            rp.parse(got.text.splitlines())
        return rp

    def _get(self, url: str, *, page: bool) -> _Fetched | None:
        for _ in range(MAX_REDIRECTS + 1):
            if not in_domain(urlsplit(url).hostname, self.domain):
                return None
            if page and not self.allowed(url):
                return None
            self._wait()
            if page:
                self.page_requests += 1
            try:
                with self.client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        url = _clean_url(urljoin(url, resp.headers["location"]))
                        continue
                    ctype = resp.headers.get("content-type", "")
                    if page and (resp.status_code != 200 or not _is_html(ctype)):
                        return None
                    body = _read_limited(resp)
                    return _Fetched(url, resp.status_code, _decode(body, resp.charset_encoding))
            except httpx.HTTPError:
                return None
        return None

    def page(self, url: str) -> Page | None:
        got = self._get(url, page=True)
        return Page(got.url, got.text) if got else None


def crawl_site(
    domain: str,
    *,
    max_pages: int = 10,
    delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Site:
    """Crawl up to `max_pages` pages of `domain` (homepage first, then candidates by priority)."""
    domain = normalise_domain(domain)
    site = Site(domain=domain, max_pages=max_pages)
    if not domain or max_pages < 1:
        return site
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(headers=headers, timeout=TIMEOUT, follow_redirects=False) as client:
        fetcher = _Fetcher(domain, client, CRAWL_DELAY if delay is None else delay, sleep)
        home = fetcher.page(f"https://{domain}/") or fetcher.page(f"http://{domain}/")
        if home is None:
            return site
        site.pages.append(home)
        fetched = {_url_key(home.url)}
        seen = set(fetched)
        queue: list[tuple[int, int, int, str]] = []
        order = itertools.count()

        def enqueue(page: Page, depth: int) -> None:
            for prio, url in _candidate_links(page, domain):
                if (key := _url_key(url)) not in seen:
                    seen.add(key)
                    heapq.heappush(queue, (prio, depth, next(order), url))

        enqueue(home, 1)
        while queue and fetcher.page_requests < max_pages:
            _, depth, _, url = heapq.heappop(queue)
            page = fetcher.page(url)
            if page is None or _url_key(page.url) in fetched:
                continue
            fetched.add(_url_key(page.url))
            site.pages.append(page)
            if depth < MAX_DEPTH:
                enqueue(page, depth + 1)
    return site


_cache: dict[str, tuple[float, Site]] = {}
_cache_lock = threading.Lock()
_domain_locks: dict[str, threading.Lock] = {}


def get_site(domain: str, *, max_pages: int = 10) -> Site:
    """Cached crawl of `domain`; concurrent callers for one domain wait for a single crawl."""
    domain = normalise_domain(domain)
    with _cache_lock:
        lock = _domain_locks.setdefault(domain, threading.Lock())
    with lock:
        with _cache_lock:
            hit = _cache.get(domain)
        if hit and time.monotonic() - hit[0] < CACHE_TTL and hit[1].max_pages >= max_pages:
            return hit[1]
        site = crawl_site(domain, max_pages=max_pages)
        with _cache_lock:
            _cache[domain] = (time.monotonic(), site)
            while len(_cache) > CACHE_SIZE:
                del _cache[min(_cache, key=lambda d: _cache[d][0])]
        return site


def clear_cache() -> None:
    """Forget cached crawls."""
    with _cache_lock:
        _cache.clear()
