"""Polite asyncio crawler: read a few public pages of each candidate domain, keep register numbers.

What it does for one domain (`scan_domain`): fetch the home page (https, then www., then http),
check robots.txt for our User-Agent first, read at most `max_pages` pages (home + the best legal /
about / contact links + guessed paths such as /mentions-legales) and stop early once a register
number is found on a legal page. From every page it keeps only company facts: register numbers,
the home page <title> and switchboard phone numbers (see `leadcommons.sitescan.ids` / `pages`).

Politeness rules, all enforced here:

- robots.txt is read for every origin we touch. Fetch failure or 4xx = allowed, 5xx = the site is
  skipped, `Disallow` (for `LeadCommonsBot` or `*`) = skipped. `Crawl-delay` is honoured (max 10 s).
- One request at a time per site (registrable domain), at least `per_host_delay` seconds apart.
- Redirects are followed by hand and only inside the same registrable domain.
- HTML only, 1 MB per page, 10 s timeouts (and a hard ceiling per request and per domain).

`scan_many` runs many domains with bounded concurrency and writes Findings to Parquet batches
(checkpoints) in a directory, so a restart skips what is already there.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from leadcommons import __version__
from leadcommons.packs.domains import normalize_domain
from leadcommons.sitescan import Finding
from leadcommons.sitescan.hosts import country_for_domain
from leadcommons.sitescan.ids import find_ids
from leadcommons.sitescan.pages import (
    guess_paths,
    page_title,
    rank_links,
    switchboard_phones,
    visible_text,
)

log = logging.getLogger(__name__)

BOT_NAME = "LeadCommonsBot"
DOC_URL = "https://github.com/aymaneprojects/leadcommons/blob/main/docs/sitescan.md"
USER_AGENT = f"{BOT_NAME}/{__version__} (+{DOC_URL})"

MAX_BYTES = 1_000_000  # per page
ROBOTS_MAX_BYTES = 500_000
TIMEOUT = httpx.Timeout(10.0)
REQUEST_DEADLINE = 30.0  # seconds, whole request incl. body (guards against slow drips)
DOMAIN_DEADLINE = 150.0  # seconds, everything for one domain
MAX_REDIRECTS = 5
MAX_CRAWL_DELAY = 10.0
MAX_PHONES = 5

# Pages whose path says "legal notice": a register number there is the proof we are after.
_LEGAL_PATH = re.compile(
    r"mention|legal|impressum|imprint|aviso|legali|colofon|juridisk|rechtlich|cgu|cgv|terms",
    re.IGNORECASE,
)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([a-zA-Z0-9_-]+)", re.IGNORECASE)
_HTML_TYPES = {"text/html", "application/xhtml+xml"}


# --------------------------------------------------------------------------- errors


class _NetworkError(Exception):
    """The request did not get an HTTP answer. `kind`: dns, timeout, connect, tls, other."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


class _Skip(Exception):
    """This page cannot be used (message is the reason)."""


class _RobotsStop(_Skip):
    """robots.txt forbids the fetch or could not be trusted (5xx)."""


def _classify(exc: BaseException) -> _NetworkError:
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < 8:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    text = " ".join(str(e) for e in chain).lower()
    if any(isinstance(e, socket.gaierror) for e in chain) or any(
        s in text for s in ("name or service not known", "nodename nor servname", "no address")
    ):
        return _NetworkError("dns", type(exc).__name__)
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        return _NetworkError("timeout", type(exc).__name__)
    if "ssl" in text or "certificate" in text:
        return _NetworkError("tls", type(exc).__name__)
    if isinstance(exc, httpx.ConnectError):
        return _NetworkError("connect", type(exc).__name__)
    return _NetworkError("other", f"{type(exc).__name__}: {exc}"[:200])


# --------------------------------------------------------------------------- helpers


def registrable(host: str | None) -> str | None:
    """Registrable domain of a host name (no network, small public-suffix table)."""
    return normalize_domain(host) if host else None


def _decode(body: bytes, charset: str | None) -> str:
    meta = _META_CHARSET.search(body[:4096])
    for enc in (charset, meta.group(1).decode("ascii", "ignore") if meta else None, "utf-8"):
        if enc:
            try:
                return body.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return body.decode("cp1252", errors="replace")


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path.rstrip('/') or '/'}?{parts.query}"


def _is_legal(url: str) -> bool:
    return bool(_LEGAL_PATH.search(urlsplit(url).path))


class HostPacer:
    """One request at a time per site, with a minimum gap between two requests to it."""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    @contextlib.asynccontextmanager
    async def slot(self, key: str, delay: float | None = None):
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            gap = self.delay if delay is None else max(self.delay, delay)
            last = self._last.get(key)
            if last is not None:
                wait = last + gap - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            try:
                yield
            finally:
                self._last[key] = time.monotonic()

    def forget(self, key: str) -> None:
        self._locks.pop(key, None)
        self._last.pop(key, None)


@dataclass
class _Response:
    url: str
    status: int
    body: bytes
    content_type: str
    charset: str | None


@dataclass
class _Site:
    """State of one domain scan."""

    client: httpx.AsyncClient
    domain: str
    pacer: HostPacer
    robots: dict[str, RobotFileParser | str | None] = field(default_factory=dict)
    crawl_delay: float = 0.0
    requests: int = 0
    pages: int = 0

    def in_scope(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and registrable(parts.hostname) == self.domain

    async def _request(self, url: str, cap: int) -> _Response:
        """One GET, no redirect following, body cut at `cap` bytes. Raises _NetworkError."""
        self.requests += 1
        try:
            async with asyncio.timeout(REQUEST_DEADLINE):
                async with self.client.stream(
                    "GET",
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
                    timeout=TIMEOUT,
                    follow_redirects=False,
                ) as resp:
                    ctype = resp.headers.get("content-type", "")
                    mime = ctype.split(";")[0].strip().lower()
                    status = resp.status_code
                    body = b""
                    wants_body = status == 200 and (not mime or mime in _HTML_TYPES or cap != MAX_BYTES)
                    if wants_body:
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            chunks.append(chunk)
                            size += len(chunk)
                            if size >= cap:
                                break
                        body = b"".join(chunks)[:cap]
                    return _Response(
                        str(resp.url),
                        status,
                        body,
                        mime,
                        resp.charset_encoding,
                    ) if False else _Response(
                        url,
                        status,
                        body,
                        mime,
                        resp.charset_encoding,
                    ) | _Location(resp.headers.get("location"))
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise _classify(exc) from exc
        except httpx.InvalidURL as exc:  # pragma: no cover - defensive
            raise _NetworkError("other", str(exc)) from exc

    async def get(self, url: str, *, cap: int = MAX_BYTES) -> _Response:
        """GET with same-domain redirects, robots checks and per-site pacing."""
        for _ in range(MAX_REDIRECTS + 1):
            if not self.in_scope(url):
                raise _Skip(f"off-domain: {urlsplit(url).hostname}")
            if cap == MAX_BYTES:  # a page, not robots.txt itself
                await self._check_robots(url)
            async with self.pacer.slot(self.domain, self.crawl_delay):
                resp = await self._request(url, cap)
            if resp.status in (301, 302, 303, 307, 308):
                target = getattr(resp, "location", None)
                if not target:
                    raise _Skip(f"redirect without location ({resp.status})")
                url = urljoin(url, target)
                continue
            return resp
        raise _Skip("too many redirects")

    async def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            self.robots[origin] = await self._load_robots(origin)
        rules = self.robots[origin]
        if isinstance(rules, str):
            raise _RobotsStop(rules)
        if rules is not None and not rules.can_fetch(USER_AGENT, url):
            raise _RobotsStop(f"robots.txt disallows {parts.path or '/'}")

    async def _load_robots(self, origin: str) -> RobotFileParser | str | None:
        """Parser, None (allowed: missing, 4xx, unreachable) or a reason string (skip the site)."""
        try:
            resp = await self.get(origin + "/robots.txt", cap=ROBOTS_MAX_BYTES)
        except (_NetworkError, _Skip):
            return None  # robots fetch failure = allowed
        if resp.status >= 500:
            return f"robots.txt answered {resp.status}"
        if resp.status != 200:
            return None  # 4xx (also 401/403) = allowed
        parser = RobotFileParser()
        parser.parse(_decode(resp.body, resp.charset).splitlines())
        delay = parser.crawl_delay(USER_AGENT)
        if delay:
            with contextlib.suppress(TypeError, ValueError):
                self.crawl_delay = max(self.crawl_delay, min(float(delay), MAX_CRAWL_DELAY))
        return parser

    async def get_html(self, url: str) -> tuple[str, str]:
        """(final url, html) of an HTML page, else raises _Skip / _NetworkError / _RobotsStop."""
        resp = await self.get(url)
        if resp.status != 200:
            raise _Skip(f"HTTP {resp.status}")
        if resp.content_type and resp.content_type not in _HTML_TYPES:
            raise _Skip(f"not HTML ({resp.content_type})")
        self.pages += 1
        return resp.final_url, _decode(resp.body, resp.charset)
