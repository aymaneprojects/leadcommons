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
from collections.abc import Iterable, Iterator
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
    location: str | None = None


@dataclass
class _Site:
    """State of one domain scan: client, robots cache, counters."""

    client: httpx.AsyncClient
    domain: str
    pacer: HostPacer
    robots: dict[str, RobotFileParser | str | None] = field(default_factory=dict)
    crawl_delay: float = 0.0
    requests: int = 0

    def in_scope(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in {"http", "https"} and registrable(parts.hostname) == self.domain

    async def _request(self, url: str, cap: int, *, html_only: bool) -> _Response:
        """One GET, redirects not followed, body cut at `cap` bytes. Raises _NetworkError."""
        self.requests += 1
        try:
            async with (
                asyncio.timeout(REQUEST_DEADLINE),
                self.client.stream(
                    "GET",
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.5"},
                    timeout=TIMEOUT,
                    follow_redirects=False,
                ) as resp,
            ):
                mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                body = b""
                if resp.status_code == 200 and (not html_only or not mime or mime in _HTML_TYPES):
                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in resp.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= cap:
                            break
                    body = b"".join(chunks)[:cap]
                return _Response(
                    url,
                    resp.status_code,
                    body,
                    mime,
                    resp.charset_encoding,
                    resp.headers.get("location"),
                )
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            raise _classify(exc) from exc

    async def get(self, url: str, *, robots: bool = False) -> _Response:
        """GET with same-domain redirects, robots checks and per-site pacing.

        `robots=True` fetches a robots.txt itself (no robots check, up to 500 KB, any type).
        """
        for _ in range(MAX_REDIRECTS + 1):
            if not self.in_scope(url):
                raise _Skip(f"off-domain: {urlsplit(url).hostname}")
            if not robots:
                await self._check_robots(url)
            async with self.pacer.slot(self.domain, self.crawl_delay):
                resp = await self._request(
                    url, ROBOTS_MAX_BYTES if robots else MAX_BYTES, html_only=not robots
                )
            if resp.status in (301, 302, 303, 307, 308):
                if not resp.location:
                    raise _Skip(f"redirect without location ({resp.status})")
                url = urljoin(url, resp.location)
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
        """Parser, None (allowed: missing, 4xx, odd failure) or a reason (skip the site)."""
        try:
            resp = await self.get(origin + "/robots.txt", robots=True)
        except _NetworkError as exc:
            if exc.kind in {"dns", "timeout", "connect", "tls"}:
                raise  # the host is unreachable: the page request would fail the same way
            return None
        except _Skip:
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
        """(final url, html) of an HTML page; raises _Skip, _NetworkError or _RobotsStop."""
        resp = await self.get(url)
        if resp.status != 200:
            raise _Skip(f"HTTP {resp.status}")
        if resp.content_type and resp.content_type not in _HTML_TYPES:
            raise _Skip(f"not HTML ({resp.content_type})")
        return resp.url, _decode(resp.body, resp.charset)


async def _open_home(site: _Site) -> tuple[str, str]:
    """Home page: https, www. then http. Raises _NetworkError only if nothing answered at all."""
    dead: set[str] = set()
    errors: list[_NetworkError] = []
    skips: list[str] = []
    for scheme, host in (
        ("https", site.domain),
        ("https", "www." + site.domain),
        ("http", site.domain),
        ("http", "www." + site.domain),
    ):
        if host in dead:
            continue
        try:
            return await site.get_html(f"{scheme}://{host}/")
        except _RobotsStop:
            raise
        except _NetworkError as exc:
            errors.append(exc)
            if exc.kind in {"dns", "timeout"}:
                dead.add(host)  # the other scheme will not do better
        except _Skip as exc:
            skips.append(str(exc))
    if skips:
        raise _Skip(skips[0])
    # No answer anywhere: "no-site" if it is DNS / refused / connect timeout, else an error.
    raise (
        errors[0]
        if all(e.kind in {"dns", "connect", "timeout"} for e in errors)
        else _Skip(errors[-1].args[0])
    )


@dataclass
class ScanResult:
    """A Finding plus what the crawl cost (the contract's Finding has no room for these)."""

    finding: Finding
    pages: int = 0
    requests: int = 0
    home_url: str | None = None  # the URL that answered (scheme and host), for the shard


async def scan_with_stats(
    client: httpx.AsyncClient,
    domain: str,
    country: str | None,
    *,
    max_pages: int = 4,
    pacer: HostPacer | None = None,
) -> ScanResult:
    norm = normalize_domain(domain)
    if not norm:
        return ScanResult(
            Finding(domain=domain.strip().lower(), status="error", error="bad domain")
        )
    pacer = pacer or HostPacer(1.0)
    site = _Site(client, norm, pacer)
    finding = Finding(domain=norm)
    result = ScanResult(finding)
    seen_ids: set[tuple[str, str]] = set()
    phones: list[str] = []
    evidence_legal = False

    def read(url: str, html: str) -> bool:
        """Take the facts of one page. True when a register number sits on a legal page."""
        nonlocal evidence_legal
        text = visible_text(html)
        legal = _is_legal(url)
        found = [i for i in find_ids(text, country) if (i.registry, i.number) not in seen_ids]
        for item in found:
            seen_ids.add((item.registry, item.number))
            finding.ids.append(item)
        if found and (finding.evidence_url is None or (legal and not evidence_legal)):
            finding.evidence_url = url
            evidence_legal = legal
        for phone in switchboard_phones(text, country):
            if phone not in phones and len(phones) < MAX_PHONES:
                phones.append(phone)
        return bool(found) and legal

    try:
        async with asyncio.timeout(DOMAIN_DEADLINE):
            home_url, html = await _open_home(site)
            parts = urlsplit(home_url)
            result.home_url = f"{parts.scheme}://{parts.netloc}/"
            pages = 1
            finding.title = page_title(html)
            done = read(home_url, html)
            seen = {_url_key(home_url)}
            candidates = rank_links(html, home_url, norm, limit=max_pages * 2) + [
                urljoin(home_url, p) for p in guess_paths(country)
            ]
            attempts = 0
            failures = 0
            for url in candidates:
                if done or pages >= max_pages or attempts >= 2 * (max_pages - 1):
                    break
                key = _url_key(url)
                if key in seen or not site.in_scope(url):
                    continue
                seen.add(key)
                attempts += 1
                try:
                    final, page_html = await site.get_html(url)
                except _NetworkError:
                    failures += 1
                    if failures >= 2:
                        break  # the site went away
                    continue
                except _Skip:  # 404, not HTML, off-domain redirect, robots on this page
                    continue
                if _url_key(final) in seen and final != url:
                    continue
                seen.add(_url_key(final))
                pages += 1
                done = read(final, page_html)
            result.pages = pages
    except _RobotsStop as exc:
        finding.status, finding.error = "robots", str(exc)
    except _NetworkError as exc:
        finding.status, finding.error = "no-site", str(exc)
    except _Skip as exc:
        finding.status, finding.error = "error", str(exc)
    except TimeoutError:
        finding.status, finding.error = "error", "domain timeout"
    finding.phones = phones
    result.requests = site.requests
    return result


async def scan_domain(
    client: httpx.AsyncClient,
    domain: str,
    country: str | None,
    *,
    max_pages: int = 4,
    pacer: HostPacer | None = None,
) -> Finding:
    """Scan one domain: see the module doc. Never raises for site problems."""
    return (
        await scan_with_stats(client, domain, country, max_pages=max_pages, pacer=pacer)
    ).finding


# --------------------------------------------------------------------------- many domains

FINDINGS_GLOB = "findings-*.parquet"
_SCHEMA_SQL = (
    "domain VARCHAR, country VARCHAR, status VARCHAR, error VARCHAR, title VARCHAR, "
    "home_url VARCHAR, evidence_url VARCHAR, ids VARCHAR, phones VARCHAR, pages INTEGER, "
    "scanned_at VARCHAR"
)


def _row(res: ScanResult, country: str | None) -> tuple:
    f = res.finding
    return (
        f.domain,
        country,
        f.status,
        f.error,
        f.title,
        res.home_url,
        f.evidence_url,
        json.dumps([asdict(i) for i in f.ids], ensure_ascii=False),
        json.dumps(f.phones),
        res.pages,
        datetime.now(UTC).replace(microsecond=0).isoformat(),
    )


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def done_domains(out_dir: Path) -> set[str]:
    """Domains already in the checkpoint files of `out_dir`."""
    import duckdb

    if not any(out_dir.glob(FINDINGS_GLOB)):
        return set()
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT domain FROM read_parquet('{_sql_path(out_dir)}/{FINDINGS_GLOB}')"
        ).fetchall()
    finally:
        con.close()
    return {r[0] for r in rows}


def _write_batch(rows: list[tuple], path: Path) -> None:
    import duckdb

    con = duckdb.connect()
    try:
        con.execute(f"CREATE TABLE f({_SCHEMA_SQL})")
        con.executemany("INSERT INTO f VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        tmp = path.with_name(path.name + ".tmp")
        con.execute(f"COPY f TO '{_sql_path(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        tmp.replace(path)
    finally:
        con.close()


def _next_batch_number(out_dir: Path) -> int:
    nums = [
        int(m.group(1)) for p in out_dir.glob(FINDINGS_GLOB) if (m := re.search(r"(\d+)", p.stem))
    ]
    return max(nums, default=0) + 1


async def scan_many(
    domains: Iterable[tuple[str, str | None]],
    out_path: Path,
    *,
    concurrency: int = 100,
    per_host_delay: float = 1.0,
    checkpoint_every: int = 500,
    max_pages: int = 4,
    progress_every: float = 30.0,
) -> dict:
    """Scan many domains and write Findings to Parquet batches in the directory `out_path`.

    Bounded concurrency (`concurrency` workers pull domains lazily, so a 10M-line input costs no
    memory), one request at a time per site, `per_host_delay` seconds apart. Every
    `checkpoint_every` finished domains a file `findings-NNNNN.parquet` is written (atomically);
    domains already in such files are skipped, so a killed run resumes where it stopped. Returns
    the stats: domains, skipped, with_ids, by_registry, by_status, errors, pages, requests, seconds.
    """
    out_path.mkdir(parents=True, exist_ok=True)
    done = done_domains(out_path)
    skipped = 0
    started = time.monotonic()
    stats: dict = {
        "domains": 0,
        "skipped": 0,
        "with_ids": 0,
        "by_registry": Counter(),
        "by_status": Counter(),
        "errors": 0,
        "pages": 0,
        "requests": 0,
    }
    if done:
        log.info("resuming: %d domains already scanned in %s", len(done), out_path)

    def feed() -> Iterator[tuple[str, str | None]]:
        nonlocal skipped
        for raw, country in domains:
            norm = normalize_domain(raw) or raw.strip().lower()
            if not norm or norm in done:
                skipped += 1
                continue
            done.add(norm)
            yield norm, (country or country_for_domain(norm))

    pending: list[tuple] = []
    flush_lock = asyncio.Lock()
    batch_no = _next_batch_number(out_path)
    pacer = HostPacer(per_host_delay)
    last_log = time.monotonic()

    async def flush() -> None:
        nonlocal pending, batch_no
        async with flush_lock:
            if not pending:
                return
            rows, pending = pending, []
            path = out_path / f"findings-{batch_no:05d}.parquet"
            batch_no += 1
            await asyncio.to_thread(_write_batch, rows, path)
            log.info("checkpoint: %s (%d rows)", path.name, len(rows))

    def record(res: ScanResult, country: str | None) -> None:
        f = res.finding
        stats["domains"] += 1
        stats["by_status"][f.status] += 1
        stats["pages"] += res.pages
        stats["requests"] += res.requests
        if f.status == "error":
            stats["errors"] += 1
        if f.ids:
            stats["with_ids"] += 1
            for registry in {i.registry for i in f.ids}:
                stats["by_registry"][registry] += 1
        pending.append(_row(res, country))

    async def worker(client: httpx.AsyncClient, source: Iterator[tuple[str, str | None]]) -> None:
        nonlocal last_log
        for domain, country in source:
            try:
                res = await scan_with_stats(
                    client, domain, country, max_pages=max_pages, pacer=pacer
                )
            except Exception as exc:  # one bad site must never stop the run
                log.warning("scan of %s failed: %r", domain, exc)
                res = ScanResult(Finding(domain=domain, status="error", error=repr(exc)[:200]))
            pacer.forget(res.finding.domain)
            record(res, country)
            if len(pending) >= checkpoint_every:
                await flush()
            now = time.monotonic()
            if now - last_log >= progress_every:
                last_log = now
                secs = now - started
                log.info(
                    "scanned %d domains (%.1f/s), %d with register numbers, %d no-site, %d errors",
                    stats["domains"],
                    stats["domains"] / secs,
                    stats["with_ids"],
                    stats["by_status"]["no-site"],
                    stats["errors"],
                )

    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=min(max(concurrency, 8), 128)))
    limits = httpx.Limits(
        max_connections=concurrency + 10, max_keepalive_connections=concurrency, keepalive_expiry=5
    )
    source = feed()
    try:
        async with httpx.AsyncClient(limits=limits) as client:
            await asyncio.gather(*(worker(client, source) for _ in range(max(1, concurrency))))
    finally:
        await flush()  # partial output on interruption
    stats["skipped"] = skipped
    stats["seconds"] = round(time.monotonic() - started, 1)
    stats["by_registry"] = dict(stats["by_registry"])
    stats["by_status"] = dict(stats["by_status"])
    return stats


def run_scan(domains: Iterable[tuple[str, str | None]], out_path: Path, **kwargs: object) -> dict:
    """Blocking wrapper of `scan_many` for the CLI."""
    return asyncio.run(scan_many(domains, out_path, **kwargs))  # type: ignore[arg-type]


__all__ = [
    "USER_AGENT",
    "HostPacer",
    "ScanResult",
    "done_domains",
    "run_scan",
    "scan_domain",
    "scan_many",
    "scan_with_stats",
]
