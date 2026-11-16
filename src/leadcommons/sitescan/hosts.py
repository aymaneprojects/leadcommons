"""Candidate domains to scan.

Two sources:

- the packs: domains and websites companies already have (the scan then verifies them and finds the
  register number printed on the site);
- Common Crawl's columnar URL index (public Parquet, https://commoncrawl.org/): every registered
  domain of one TLD that the crawl fetched successfully. This is what finds domains of companies
  that have no website in any register. Only the three columns needed are read, and DuckDB's
  `httpfs` fetches only the Parquet row groups that hold the TLD (the files are sorted by host).

The Common Crawl part runs in GitHub Actions (`.github/workflows/sitescan-hosts.yml`): one DuckDB
query per index file, retried, one log line per file, one small Parquet part per file in a work
directory, so an interrupted run resumes and still writes a partial `domains-<tld>.txt.gz`.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

log = logging.getLogger(__name__)

CC_DATA = "https://data.commoncrawl.org/"
CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
INDEX_SUBSET = "warc"

# Top-level domain (or public suffix) -> country code, for the packs we have.
TLD_COUNTRY: dict[str, str] = {
    "fr": "FR",
    "uk": "GB",
    "co.uk": "GB",
    "de": "DE",
    "no": "NO",
    "au": "AU",
    "com.au": "AU",
    "ca": "CA",
    "nl": "NL",
    "be": "BE",
    "dk": "DK",
    "ch": "CH",
    "it": "IT",
    "es": "ES",
}
# TLDs `sitescan hosts` builds by default (public suffixes like co.uk fall under their TLD).
DEFAULT_TLDS: tuple[str, ...] = (
    "fr",
    "uk",
    "de",
    "no",
    "au",
    "ca",
    "nl",
    "be",
    "dk",
    "ch",
    "it",
    "es",
)


def country_for_domain(domain: str) -> str | None:
    """Country of a domain from its public suffix ("example.co.uk" -> "GB"), else None."""
    labels = domain.lower().strip(".").split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in TLD_COUNTRY:
        return TLD_COUNTRY[".".join(labels[-2:])]
    return TLD_COUNTRY.get(labels[-1])


# --------------------------------------------------------------------------- packs


_HOST_FROM_URL = r"^(?:[a-z][a-z0-9+.-]*://)?(?:www\.)?([^/:?#\s]+)"


def packs_domains(
    packs_dir: Path, countries: Iterable[str] | None = None
) -> Iterator[tuple[str, str]]:
    """(domain, country) of every company in the packs that has a domain or a website.

    Reads `companies-<CC>-*.parquet` with DuckDB, only the columns it needs; the domain column
    first, the host of `website` when the domain is empty. Each domain is yielded once.
    """
    import duckdb

    wanted = {c.upper() for c in countries} if countries else None
    seen: set[str] = set()
    con = duckdb.connect()
    try:
        for path in sorted(Path(packs_dir).glob("companies-*.parquet")):
            m = re.match(r"companies-([A-Za-z]{2})-", path.name)
            code = m.group(1).upper() if m else None
            if wanted is not None and code not in wanted:
                continue
            target = str(path).replace("'", "''")
            cols = {
                r[0]
                for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{target}')").fetchall()
            }
            parts = ["nullif(lower(trim(domain)), '')"] if "domain" in cols else []
            if "website" in cols:
                parts.append(
                    f"nullif(regexp_extract(lower(trim(website)), '{_HOST_FROM_URL}', 1), '')"
                )
            if not parts:
                continue
            host = f"coalesce({', '.join(parts)})"
            sql = (
                f"SELECT DISTINCT {host} AS d FROM read_parquet('{target}') "
                f"WHERE {host} IS NOT NULL"
            )
            for (d,) in con.execute(sql).fetchall():
                if "." in d and d not in seen:
                    seen.add(d)
                    yield d, code or country_for_domain(d) or ""
    finally:
        con.close()


# --------------------------------------------------------------------------- Common Crawl


def _q(path: Path | str) -> str:
    """Escape a path for a single-quoted SQL literal."""
    return str(path).replace("'", "''")


def parse_collinfo(text: str) -> str:
    """Newest crawl id ("CC-MAIN-2026-33") from the body of collinfo.json."""
    ids = [str(c["id"]) for c in json.loads(text) if str(c.get("id", "")).startswith("CC-MAIN-")]
    if not ids:
        raise ValueError("collinfo.json lists no CC-MAIN crawl")
    return max(ids)  # CC-MAIN-YYYY-NN sorts by date


def parse_index_paths(text: str, subset: str = INDEX_SUBSET) -> list[str]:
    """Download URLs of the index Parquet files of one subset, from cc-index-table.paths.gz text."""
    urls: list[str] = []
    for line in text.splitlines():
        path = line.strip()
        if path.endswith(".parquet") and f"/subset={subset}/" in path:
            urls.append(path if path.startswith("http") else CC_DATA + path.lstrip("/"))
    return sorted(urls)


def _http_get(url: str, *, attempts: int = 5, sleep: Callable[[float], None] = time.sleep) -> bytes:
    import httpx

    last: Exception | None = None
    for i in range(attempts):
        try:
            resp = httpx.get(
                url, timeout=60, follow_redirects=True, headers={"User-Agent": "LeadCommons-hosts"}
            )
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            last = exc
            log.warning("GET %s failed (%s), attempt %d/%d", url, exc, i + 1, attempts)
            sleep(min(60, 2**i * 2))
    raise RuntimeError(f"could not fetch {url}: {last}")


def latest_crawl() -> str:
    return parse_collinfo(_http_get(CC_COLLINFO).decode("utf-8"))


def index_files(crawl: str) -> list[str]:
    raw = _http_get(f"{CC_DATA}crawl-data/{crawl}/cc-index-table.paths.gz")
    return parse_index_paths(gzip.decompress(raw).decode("utf-8"))


def extract_file(con, source: str, tld: str, dest: Path) -> int:
    """Distinct registered domains of `tld` fetched with status 200 in one index file -> Parquet.

    Reads three columns only: url_host_tld (row-group statistics let httpfs skip the rest of the
    file, which is sorted by host), fetch_status and url_host_registered_domain.
    """
    tmp = dest.with_name(dest.name + ".tmp")
    src = source.replace("'", "''")
    tld_sql = tld.lower().replace("'", "''")
    con.execute(
        "COPY (SELECT DISTINCT url_host_registered_domain AS domain "
        f"FROM read_parquet('{src}') "
        f"WHERE url_host_tld = '{tld_sql}' AND fetch_status = 200 "
        "AND url_host_registered_domain IS NOT NULL) "
        f"TO '{_q(tmp)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    tmp.replace(dest)
    return int(con.execute(f"SELECT count(*) FROM read_parquet('{_q(dest)}')").fetchone()[0])


def _connect(threads: int = 2):
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs")
    con.execute("LOAD httpfs")
    con.execute(f"SET threads = {int(threads)}")
    con.execute("SET http_retries = 5")
    con.execute("SET http_timeout = 120000")
    return con


def merge_parts(parts_dir: Path, out_file: Path) -> int:
    """All parts -> `out_file` (gzip, one domain per line, sorted, unique). Returns the count."""
    import duckdb

    out_file.parent.mkdir(parents=True, exist_ok=True)
    if not any(parts_dir.glob("part-*.parquet")):
        with gzip.open(out_file, "wt", encoding="utf-8"):
            pass
        return 0
    con = duckdb.connect()
    count = 0
    tmp = out_file.with_name(out_file.name + ".tmp")
    try:
        glob = _q(parts_dir) + "/part-*.parquet"
        cur = con.execute(
            f"SELECT DISTINCT domain FROM read_parquet('{glob}') "
            "WHERE domain LIKE '%.%' ORDER BY domain"
        )
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            while rows := cur.fetchmany(100_000):
                fh.writelines(f"{r[0]}\n" for r in rows)
                count += len(rows)
    finally:
        con.close()
    tmp.replace(out_file)
    return count


def build_domains(
    tld: str,
    out_dir: Path,
    *,
    crawl: str | None = None,
    max_files: int | None = None,
    workers: int = 4,
    attempts: int = 8,
    connect: Callable[[], object] = _connect,
    files: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Write `domains-<tld>.txt.gz` (+ `.json` with the facts) from the newest crawl's index.

    One DuckDB query per index file (`workers` in parallel, each retried `attempts` times with
    backoff); each result is kept as `parts-<tld>/part-NNNNN.parquet`, so a rerun skips finished
    files. If files keep failing or the run is interrupted (Ctrl-C), the file is still written from
    the parts that exist, and `partial` is true in the stats.
    """
    tld = tld.lower().strip(".")
    out_dir.mkdir(parents=True, exist_ok=True)
    crawl = crawl or latest_crawl()
    urls = files if files is not None else index_files(crawl)
    if max_files:
        urls = urls[:max_files]
    parts_dir = out_dir / f"parts-{tld}-{crawl}"
    parts_dir.mkdir(exist_ok=True)
    total = len(urls)
    log.info("crawl %s: %d index files for .%s", crawl, total, tld)
    started = time.monotonic()
    stats = {
        "tld": tld,
        "crawl": crawl,
        "files": total,
        "files_done": 0,
        "failed": [],
        "partial": False,
    }

    def one(i: int, url: str) -> tuple[int, str, int | None]:
        dest = parts_dir / f"part-{i:05d}.parquet"
        if dest.exists():
            return i, url, -1  # finished in an earlier run
        con = connect()
        try:
            for attempt in range(1, attempts + 1):
                t0 = time.monotonic()
                try:
                    n = extract_file(con, url, tld, dest)
                    log.info(
                        "[%d/%d] %s: %d domains in %.1fs",
                        i + 1,
                        total,
                        url.rsplit("/", 1)[-1],
                        n,
                        time.monotonic() - t0,
                    )
                    return i, url, n
                except Exception as exc:
                    log.warning(
                        "[%d/%d] %s failed (attempt %d/%d): %s",
                        i + 1,
                        total,
                        url.rsplit("/", 1)[-1],
                        attempt,
                        attempts,
                        exc,
                    )
                    if attempt < attempts:
                        sleep(min(120, 5 * 2**attempt))
            return i, url, None
        finally:
            with contextlib.suppress(Exception):
                con.close()

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futures = [pool.submit(one, i, u) for i, u in enumerate(urls)]
        for fut in as_completed(futures):
            i, url, n = fut.result()
            if n is None:
                stats["failed"].append(url)
            else:
                stats["files_done"] += 1
    except KeyboardInterrupt:
        log.warning("interrupted: writing what we have")
        for f in futures:
            f.cancel()
        stats["partial"] = True
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        out_file = out_dir / f"domains-{tld}.txt.gz"
        stats["domains"] = merge_parts(parts_dir, out_file)
        stats["partial"] = bool(stats["partial"] or stats["failed"] or stats["files_done"] < total)
        stats["seconds"] = round(time.monotonic() - started, 1)
        (out_dir / f"domains-{tld}.json").write_text(json.dumps(stats, indent=2) + "\n")
        log.info(
            "wrote %s: %d domains from %d/%d files%s",
            out_file,
            stats["domains"],
            stats["files_done"],
            total,
            " (PARTIAL)" if stats["partial"] else "",
        )
    return stats


def read_domains_file(path: Path, country: str | None = None) -> Iterator[tuple[str, str | None]]:
    """(domain, country) from a text file (.txt or .txt.gz), one domain per line."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = line.strip().lower()
            if d and not d.startswith("#"):
                yield d, country or country_for_domain(d)


__all__ = [
    "DEFAULT_TLDS",
    "TLD_COUNTRY",
    "build_domains",
    "country_for_domain",
    "extract_file",
    "index_files",
    "latest_crawl",
    "merge_parts",
    "packs_domains",
    "parse_collinfo",
    "parse_index_paths",
    "read_domains_file",
]
