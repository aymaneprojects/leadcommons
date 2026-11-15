"""Run one bulk register source, and the plumbing every source module shares.

`run_source(name, options)` is what `leadcommons pack fetch-source` calls: it finds the registered
`BulkSource`, gives it a private work directory, runs it, checks the shard, logs bytes / rows /
seconds and deletes the work directory once the shard is written (a failed run keeps it, so the
next run resumes its downloads).

The helpers below are for source modules (`leadcommons.connectors.bulk.*`):

- `download()`: streaming HTTP download to disk with resume (HTTP Range) and progress logs.
- `connect()`: a DuckDB connection backed by a file in the work dir (bounded memory, spills to
  disk) with the `lc_*` SQL macros installed (title case, years, ISIC sections, domains, ids).
- `csv_source()`: a `read_csv(...)` expression with an explicit header (no dialect sniffing) and
  rejected lines counted instead of failing the whole build.
- `write_source_shard()`: materialise `shard_select(...)`, keep the requested countries, apply
  the row limit (best rows first) and COPY the shard with `write_shard`.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

import httpx

from leadcommons.connectors.base import USER_AGENT
from leadcommons.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    load_all,
    shard_select,
    write_shard,
)
from leadcommons.models import SIZE_RANGES, make_id
from leadcommons.packs.domains import TWO_LEVEL_SUFFIXES
from leadcommons.packs.isic import _DIVISION_SECTIONS, _SIC_RANGES, SECTION_NAMES
from leadcommons.search import PACK_COLUMNS

log = logging.getLogger(__name__)

MB = 1 << 20
REUSE_SECONDS = 2 * 86400  # a finished download left by a failed run is reused for 2 days
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
# Environment knobs for big runs (CI runners: 16 GB RAM, ~14 GB free disk).
MEMORY_ENV = "LEADCOMMONS_DUCKDB_MEMORY"  # e.g. "10GB"; DuckDB's default is 80% of RAM
THREADS_ENV = "LEADCOMMONS_DUCKDB_THREADS"


# --------------------------------------------------------------------------- running a source


def run_source(name: str, options: BuildOptions) -> ShardStats:
    """Build the shard(s) of one registered bulk source. Raises when the source yields nothing."""
    sources = load_all()
    if name not in sources:
        known = ", ".join(sorted(sources)) or "none"
        raise ValueError(f"unknown bulk source {name!r} (known: {known})")
    source = sources[name]
    countries = (
        frozenset(c.strip().upper() for c in options.countries if c.strip())
        if options.countries
        else None
    )
    if source.countries is not None and countries is not None and not source.countries & countries:
        note = f"{name} covers {','.join(sorted(source.countries))}; none was requested"
        log.info("%s: skipped, %s", name, note)
        return ShardStats(source=name, notes=note)
    work = Path(options.work_dir) / name
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(options.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_opts = BuildOptions(
        work_dir=work,
        out_dir=out_dir,
        limit=options.limit,
        countries=countries,
        contact=options.contact,
    )
    started = time.monotonic()
    log.info(
        "%s: start (%s, licence %s, limit=%s, countries=%s, work=%s)",
        name,
        source.title,
        source.licence,
        options.limit,
        ",".join(sorted(countries)) if countries else "all",
        work,
    )
    try:
        stats = source.build(run_opts)
    except BaseException:
        log.error("%s: failed after %.0f s; work dir kept for a resumed run: %s", name,
                  time.monotonic() - started, work)  # fmt: skip
        raise
    _check_shards(stats)
    if stats.rows == 0:
        raise RuntimeError(f"{name}: no rows written ({stats.notes or 'empty source'})")
    shutil.rmtree(work, ignore_errors=True)
    total = sum(p.stat().st_size for p in stats.files if p.exists())
    log.info(
        "%s: done, %d rows (%d dropped) in %s, %.1f MB, %.0f s%s",
        name,
        stats.rows,
        stats.skipped,
        ", ".join(p.name for p in stats.files),
        total / MB,
        time.monotonic() - started,
        f"; {stats.notes}" if stats.notes else "",
    )
    return stats


def _check_shards(stats: ShardStats) -> None:
    """Every shard file must have exactly PACK_COLUMNS, in order."""
    import duckdb

    con = duckdb.connect()
    try:
        for path in stats.files:
            cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {parquet(path)}").fetchall()]
            if cols != list(PACK_COLUMNS):
                raise RuntimeError(f"{path.name}: columns differ from PACK_COLUMNS: {cols}")
    finally:
        con.close()


# --------------------------------------------------------------------------- SQL text helpers


def sql_str(value: str) -> str:
    """A SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def ident(name: str) -> str:
    """A quoted SQL identifier (column names with dots or spaces)."""
    return '"' + name.replace('"', '""') + '"'


def parquet(path: Path | str) -> str:
    return f"read_parquet({sql_str(str(path))})"


def sql_in(values: Iterable[str]) -> str:
    """`('a', 'b')` for an IN clause (values sorted for stable SQL)."""
    return "(" + ", ".join(sql_str(v) for v in sorted(set(values))) + ")"


def country_clause(options: BuildOptions, expr: str = "country") -> str:
    """SQL condition keeping the requested countries (TRUE when every country is wanted)."""
    if not options.countries:
        return "TRUE"
    return f"{expr} IN {sql_in(options.countries)}"


def column_key(name: str) -> str:
    """Normalised column name for lookups: case, spaces and BOM do not matter."""
    return re.sub(r"[\s﻿]+", "", name).lower()


class Columns:
    """Columns of a raw input, looked up by normalised name (`cols["RegAddress.PostTown"]`)."""

    def __init__(self, names: Iterable[str]) -> None:
        self.names = list(names)
        self._by_key = {column_key(n): n for n in self.names}

    def has(self, name: str) -> bool:
        return column_key(name) in self._by_key

    def __getitem__(self, name: str) -> str:
        try:
            return ident(self._by_key[column_key(name)])
        except KeyError:
            msg = f"column {name!r} not found in input (have: {self.names[:12]}...)"
            raise KeyError(msg) from None

    def get(self, name: str, default: str = "NULL") -> str:
        """The quoted column, or `default` (a SQL expression) when the input lacks it."""
        return self[name] if self.has(name) else default

    def require(self, *names: str) -> None:
        missing = [n for n in names if not self.has(n)]
        if missing:
            raise RuntimeError(
                f"input is missing columns {missing}; the register may have changed its format"
            )


def relation_columns(con, from_sql: str) -> Columns:
    return Columns(r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {from_sql}").fetchall())


# --------------------------------------------------------------------------- reading CSV files


def csv_header(path: Path, delimiter: str = ",") -> list[str]:
    """Column names from the first line of a CSV file (BOM stripped, names kept verbatim)."""
    with Path(path).open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        line = fh.readline()
    header = next(csv.reader(io.StringIO(line), delimiter=delimiter), [])
    if not header:
        raise RuntimeError(f"{path}: empty file or missing header")
    return header


def csv_source(
    paths: Path | Sequence[Path], rejects: str, *, delimiter: str = ",", encoding: str = "utf-8"
) -> tuple[str, Columns]:
    """`read_csv(...)` over one or more CSV files sharing the header of the first one.

    Every column is read as text with the RFC 4180 dialect given explicitly (no sniffing).
    Malformed lines are skipped and recorded in table `<rejects>` (see `rejected_lines`).
    """
    files = [Path(paths)] if isinstance(paths, (str, Path)) else [Path(p) for p in paths]
    header = csv_header(files[0], delimiter)
    seen: dict[str, int] = {}
    names = []
    for h in header:  # DuckDB needs unique names
        n = seen.get(h, 0)
        seen[h] = n + 1
        names.append(h if n == 0 else f"{h}_{n}")
    columns = "{" + ", ".join(f"{sql_str(n)}: 'VARCHAR'" for n in names) + "}"
    file_list = "[" + ", ".join(sql_str(str(f)) for f in files) + "]"
    sql = (
        f"read_csv({file_list}, header=true, auto_detect=false, columns={columns}, "
        f"delim={sql_str(delimiter)}, quote='\"', escape='\"', max_line_size=16777216, "
        f"encoding={sql_str(encoding)}, "
        f"store_rejects=true, rejects_table={sql_str(rejects)}, "
        f"rejects_scan={sql_str(rejects + '_scan')})"
    )
    return sql, Columns(names)


def rejected_lines(con, rejects: str) -> int:
    try:
        return int(con.execute(f"SELECT count(*) FROM {ident(rejects)}").fetchone()[0])
    except Exception:  # noqa: BLE001 - no reject table means nothing was rejected
        return 0


# --------------------------------------------------------------------------- downloads


class _Progress:
    """Logs a long transfer or copy every `every` seconds."""

    def __init__(self, label: str, total: int | None, done: int = 0, every: float = 30.0) -> None:
        self.label, self.total, self.done, self.every = label, total, done, every
        self.start_done = done
        self.t0 = self.last = time.monotonic()

    def add(self, n: int) -> None:
        self.done += n
        now = time.monotonic()
        if now - self.last >= self.every:
            self.last = now
            self.log("progress")

    def log(self, what: str) -> None:
        secs = max(time.monotonic() - self.t0, 1e-6)
        rate = (self.done - self.start_done) / MB / secs
        of = (
            f" of {self.total / MB:,.0f} MB ({100 * self.done / self.total:.0f}%)"
            if self.total
            else ""
        )
        log.info("%s: %s %s MB%s, %.1f MB/s, %.0f s", self.label, what, f"{self.done / MB:,.0f}",
                 of, rate, secs)  # fmt: skip


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def local_input(value: str) -> Path | None:
    """A local file given instead of a URL (tests, offline runs, `LEADCOMMONS_*_URL` overrides)."""
    if is_url(value):
        return None
    path = Path(value.removeprefix("file://")).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    return path


def download(
    url: str,
    dest: Path,
    *,
    user_agent: str = USER_AGENT,
    headers: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    retries: int = 6,
    sleep: Callable[[float], None] = time.sleep,
    label: str | None = None,
) -> Path:
    """Stream `url` to `dest`, resuming a partial `dest.part` with an HTTP Range request.

    A local path (or file:// URL) is returned as is, without copying. Transient failures
    (network errors, 429/5xx, a transfer cut short) are retried with backoff, continuing from the
    bytes already on disk when the server honours Range (206), from zero otherwise. A resumed
    request carries If-Range with the first response's ETag / Last-Modified (kept next to the
    partial file), so a "latest" URL that now points to a newer file restarts from zero instead
    of mixing two files.
    """
    if (local := local_input(url)) is not None:
        log.info("%s: using local file %s (%.1f MB)", label or dest.name, local,
                 local.stat().st_size / MB)  # fmt: skip
        return local
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    label = label or dest.name
    if dest.exists():
        if time.time() - dest.stat().st_mtime < REUSE_SECONDS:
            log.info("%s: already downloaded (%.1f MB)", label, dest.stat().st_size / MB)
            return dest
        dest.unlink()  # left by a failed run long ago: the register has a newer file by now
    part = dest.with_name(dest.name + ".part")
    own = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True)
    attempt = 0
    try:
        while True:
            have = part.stat().st_size if part.exists() else 0
            hdrs = {"User-Agent": user_agent, "Accept-Encoding": "identity", **(headers or {})}
            if have:
                hdrs["Range"] = f"bytes={have}-"
                if validator := _read_validator(part):
                    hdrs["If-Range"] = validator
            try:
                if _transfer(http, url, part, have, hdrs, label):
                    part.replace(dest)
                    _validator_file(part).unlink(missing_ok=True)
                    return dest
                problem = "transfer ended early"
            except _RetryableStatus as exc:
                problem = str(exc)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                problem = f"{type(exc).__name__}: {exc}"
            attempt += 1
            if attempt > retries:
                raise RuntimeError(
                    f"{label}: giving up on {url} after {attempt} attempts ({problem})"
                )
            pause = min(120.0, 5.0 * 2 ** (attempt - 1))
            log.warning("%s: %s; retrying in %.0f s (attempt %d of %d)", label, problem, pause,
                        attempt, retries)  # fmt: skip
            sleep(pause)
    finally:
        if own:
            http.close()


class _RetryableStatus(Exception):
    pass


def _validator_file(part: Path) -> Path:
    return part.with_name(part.name + ".validator")


def _read_validator(part: Path) -> str | None:
    try:
        return _validator_file(part).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _save_validator(part: Path, resp: httpx.Response) -> None:
    """Remember the strong ETag (else Last-Modified) of a fresh download, for If-Range."""
    etag = resp.headers.get("ETag", "")
    value = etag if etag and not etag.startswith("W/") else resp.headers.get("Last-Modified", "")
    target = _validator_file(part)
    if value:
        target.write_text(value, encoding="utf-8")
    else:
        target.unlink(missing_ok=True)


def _transfer(
    http: httpx.Client, url: str, part: Path, have: int, headers: dict[str, str], label: str
) -> bool:
    """One GET attempt; True when the file is complete on disk."""
    with http.stream("GET", url, headers=headers) as resp:
        if resp.status_code == 416 and have:
            total = _range_total(resp.headers.get("Content-Range"))
            if total is not None and total == have:
                log.info("%s: already complete on disk (%.1f MB)", label, have / MB)
                return True
            part.unlink(missing_ok=True)  # stale partial file: start again
            raise _RetryableStatus("server refused to resume (416), restarting")
        if resp.status_code in RETRY_STATUSES:
            raise _RetryableStatus(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # Say why the server refused (SEC explains missing contacts or blocks in the body).
            snippet = resp.read()[:400].decode("utf-8", "replace").strip()
            log.error(
                "%s: HTTP %s from %s (User-Agent: %s): %s",
                label,
                resp.status_code,
                url,
                http.headers.get("User-Agent", "?"),
                " ".join(snippet.split())[:300],
            )
        resp.raise_for_status()
        if have and resp.status_code == 206:
            mode, start = "ab", have
            total = _range_total(resp.headers.get("Content-Range"))
            log.info("%s: resuming at %.1f MB", label, have / MB)
        else:
            if have:
                log.info("%s: server did not resume (no Range support or the file changed), "
                         "downloading from the start", label)  # fmt: skip
            mode, start = "wb", 0
            _save_validator(part, resp)
            length = resp.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
        log.info("%s: downloading %s%s", label, resp.url,
                 f" ({total / MB:,.0f} MB)" if total else "")  # fmt: skip
        progress = _Progress(label, total, start)
        with part.open(mode) as fh:
            for chunk in resp.iter_raw(MB):
                fh.write(chunk)
                progress.add(len(chunk))
    size = part.stat().st_size
    if total is not None and size < total:
        return False
    progress.log("downloaded")
    return True


def _range_total(value: str | None) -> int | None:
    m = re.search(r"/(\d+)\s*$", value or "")
    return int(m.group(1)) if m else None


def get_text(
    url: str, *, user_agent: str = USER_AGENT, client: httpx.Client | None = None, retries: int = 4
) -> str:
    """GET a small text/JSON document, retrying transient failures."""
    own = client is None
    http = client or httpx.Client(timeout=60.0, follow_redirects=True)
    try:
        for attempt in range(retries + 1):
            try:
                resp = http.get(url, headers={"User-Agent": user_agent})
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp.text
                problem = f"HTTP {resp.status_code}"
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                problem = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                log.warning("GET %s: %s, retrying", url, problem)
                time.sleep(min(60.0, 3.0 * 2**attempt))
        raise RuntimeError(f"GET {url} failed: {problem}")
    finally:
        if own:
            http.close()


def extract(zip_path: Path, dest_dir: Path, pattern: str = r".*") -> list[Path]:
    """Extract members whose base name matches `pattern` (regex, full match) into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        members = [
            m
            for m in zf.infolist()
            if not m.is_dir() and re.fullmatch(pattern, Path(m.filename).name)
        ]
        total = sum(m.file_size for m in members)
        progress = _Progress(f"unzip {zip_path.name}", total)
        for m in members:
            target = dest_dir / Path(m.filename).name  # flatten: never write outside dest_dir
            with zf.open(m) as src, target.open("wb") as dst:
                while chunk := src.read(16 * MB):
                    dst.write(chunk)
                    progress.add(len(chunk))
            out.append(target)
        progress.log("extracted")
    if not out:
        raise RuntimeError(f"{zip_path.name}: no member matches {pattern!r}")
    return out


def discard(path: Path, work_dir: Path) -> None:
    """Delete a raw input as soon as it is no longer needed (only files inside work_dir)."""
    try:
        path.resolve().relative_to(Path(work_dir).resolve())
    except ValueError:
        return  # a local input given by the user: never delete it
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- DuckDB + macros


def connect(work_dir: Path):
    """DuckDB on a database file in work_dir, spilling to work_dir, with the lc_* macros."""
    import duckdb

    work_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(work_dir / "bulk.duckdb"))
    tmp = work_dir / "duckdb-tmp"
    tmp.mkdir(exist_ok=True)
    con.execute(f"SET temp_directory = {sql_str(str(tmp))}")
    con.execute("SET preserve_insertion_order = false")
    if mem := os.environ.get(MEMORY_ENV, "").strip():
        con.execute(f"SET memory_limit = {sql_str(mem)}")
    if (threads := os.environ.get(THREADS_ENV, "").strip()).isdigit():
        con.execute(f"SET threads = {int(threads)}")
    install_macros(con)
    return con


def list_lambda(var: str, body: str) -> str:
    """List lambda in the syntax of the installed DuckDB (`lambda x: ...` since 1.3)."""
    import duckdb

    major, minor = (int(p) for p in duckdb.__version__.split(".")[:2])
    return f"lambda {var}: {body}" if (major, minor) >= (1, 3) else f"{var} -> {body}"


def _range_case(expr: str, ranges: Iterable[tuple[int, int, str]]) -> str:
    whens = " ".join(f"WHEN {expr} BETWEEN {lo} AND {hi} THEN '{sec}'" for lo, hi, sec in ranges)
    return f"CASE {whens} END"


def macro_sql() -> list[str]:
    """CREATE MACRO statements shared by the bulk sources (generated from the Python tables)."""
    cap = "upper(left(p, 1)) || substr(p, 2)"
    apos = f"array_to_string(list_transform(string_split(h, ''''), {list_lambda('p', cap)}), '''')"
    hyph = f"array_to_string(list_transform(string_split(w, '-'), {list_lambda('h', apos)}), '-')"
    words = "string_split(lower(s), ' ')"
    title = f"array_to_string(list_transform({words}, {list_lambda('w', hyph)}), ' ')"
    division = "TRY_CAST(regexp_extract(CAST(code AS VARCHAR), '^[^0-9]*([0-9]{2})', 1) AS INTEGER)"
    sic = "TRY_CAST(trim(CAST(code AS VARCHAR)) AS INTEGER)"
    labels = " ".join(f"WHEN {sql_str(k)} THEN {sql_str(v)}" for k, v in SECTION_NAMES.items())
    year = (
        "TRY_CAST(regexp_extract(CAST(s AS VARCHAR), '(1[5-9][0-9]{2}|20[0-9]{2})', 1) AS INTEGER)"
    )
    host = (
        "regexp_replace(rtrim(lower(regexp_extract(trim(CAST(u AS VARCHAR)), "
        "'^(?:[a-zA-Z][a-zA-Z0-9+.-]*://)?(?:[^@/?#]*@)?([^/:?#\\s]+)', 1)), '.'), '^www\\.', '')"
    )
    parts = "string_split(lc_host(u), '.')"
    sizes = " ".join(f"WHEN {sql_str(s)} THEN {i + 1}" for i, s in enumerate(SIZE_RANGES))

    def sha(kind: str, value: str) -> str:
        return f"'co_' || left(sha1('{kind}|' || lower(trim({value}))), 16)"

    reg_id = sha("reg", "registry || ':' || num")

    return [
        # whitespace squeezed, '' -> NULL
        "CREATE OR REPLACE MACRO lc_clean(s) AS "
        "NULLIF(trim(regexp_replace(CAST(s AS VARCHAR), '\\s+', ' ', 'g')), '')",
        # Title Case for values written in capitals ("SAINT-ETIENNE" -> "Saint-Etienne")
        f"CREATE OR REPLACE MACRO lc_title(s) AS CASE WHEN s IS NULL THEN NULL "
        f"WHEN s = upper(s) THEN {title} ELSE s END",
        f"CREATE OR REPLACE MACRO lc_year(s) AS CASE WHEN {year} <= year(current_date) "
        f"THEN {year} END",
        "CREATE OR REPLACE MACRO lc_digits(s) AS "
        "regexp_replace(CAST(s AS VARCHAR), '[^0-9]', '', 'g')",
        # NACE / NAF / UK SIC 2007 division -> ISIC Rev.4 section (packs.isic.naf_section)
        f"CREATE OR REPLACE MACRO lc_nace_section(code) AS "
        f"{_range_case(division, _DIVISION_SECTIONS)}",
        # US SIC -> ISIC section, first range wins (packs.isic.sic_section)
        f"CREATE OR REPLACE MACRO lc_sic_section(code) AS {_range_case(sic, _SIC_RANGES)}",
        f"CREATE OR REPLACE MACRO lc_section_label(sec) AS CASE sec {labels} END",
        # domains, as packs.domains.normalize_domain
        f"CREATE OR REPLACE MACRO lc_host(u) AS {host}",
        f"CREATE OR REPLACE MACRO lc_domain(u) AS CASE "
        f"WHEN NOT coalesce(regexp_full_match(lc_host(u), '[a-z0-9-]+(\\.[a-z0-9-]+)+'), false) "
        f"THEN NULL "
        f"WHEN len({parts}) >= 3 AND array_to_string({parts}[-2:], '.') IN "
        f"{sql_in(TWO_LEVEL_SUFFIXES)} THEN array_to_string({parts}[-3:], '.') "
        f"ELSE array_to_string({parts}[-2:], '.') END",
        # UK Companies House number: 8 digits, or 2 letters + 6 digits
        "CREATE OR REPLACE MACRO lc_gb_number(s) AS CASE "
        "WHEN regexp_full_match(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), '[0-9]{1,8}') "
        "THEN lpad(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), 8, '0') "
        "WHEN regexp_full_match(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), "
        "'[A-Z]{2}[0-9]{1,6}') "
        "THEN left(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), 2) || "
        "lpad(substr(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), 3), 6, '0') "
        "WHEN regexp_full_match(upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')), '[A-Z0-9]{8}') "
        "THEN upper(replace(trim(CAST(s AS VARCHAR)), ' ', '')) END",
        # North American number -> E.164 (+1 and ten digits)
        "CREATE OR REPLACE MACRO lc_phone_nanp(s) AS CASE "
        "WHEN length(lc_digits(s)) = 10 AND left(lc_digits(s), 1) BETWEEN '2' AND '9' "
        "THEN '+1' || lc_digits(s) "
        "WHEN length(lc_digits(s)) = 11 AND left(lc_digits(s), 1) = '1' THEN '+' || lc_digits(s) "
        "END",
        f"CREATE OR REPLACE MACRO lc_size_rank(s) AS coalesce(CASE s {sizes} END, 0)",
        # Stable company id: registry:number, then LEI, then CIK (docs/dev/data-agents.md)
        "CREATE OR REPLACE MACRO lc_company_id(registry, num, lei, cik) AS CASE "
        "WHEN NULLIF(trim(registry), '') IS NOT NULL AND NULLIF(trim(num), '') IS NOT NULL "
        f"THEN {reg_id} "
        f"WHEN NULLIF(trim(lei), '') IS NOT NULL THEN {sha('lei', 'lei')} "
        f"WHEN NULLIF(trim(cik), '') IS NOT NULL THEN {sha('cik', 'cik')} END",
    ]


def install_macros(con) -> None:
    for stmt in macro_sql():
        con.execute(stmt)


def stable_company_id(
    registry: str | None, number: str | None, lei: str | None = None, cik: str | None = None
) -> str | None:
    """Python twin of the lc_company_id macro (same ids as models.make_id)."""
    if registry and registry.strip() and number and number.strip():
        return make_id("co", "reg", f"{registry}:{number}")
    if lei and lei.strip():
        return make_id("co", "lei", lei)
    if cik and cik.strip():
        return make_id("co", "cik", cik)
    return None


# --------------------------------------------------------------------------- writing the shard

# Order of a limited (test) shard: by the hash of the strongest key, the same in every source.
SAMPLE_ORDER = "hash(coalesce(registry || ':' || company_number, lei, cik, name)), name"


def write_source_shard(
    con,
    source: BulkSource,
    options: BuildOptions,
    exprs: Mapping[str, str],
    from_sql: str,
    *,
    total: int | None = None,
    best_first: str | None = None,
    order: str | None = None,
    notes: str = "",
    file_name: str | None = None,
) -> ShardStats:
    """Write `<source>.parquet` from `shard_select(exprs, from_sql)`.

    `id` defaults to the stable id (lc_company_id over registry, company_number, lei, cik).
    Rows outside `options.countries` are dropped. With `options.limit` (test runs) the shard is a
    deterministic sample, SAMPLE_ORDER: rows with the smallest hash of their register key, so the
    limited shards of two sources hold the same companies wherever they share keys (a GLEIF
    record with a SIREN and the SIRENE row of that SIREN) and the test merge exercises linking;
    `best_first` (SQL over pack columns) replaces that order. Without a limit, rows are written
    in `order` when given. `total` (raw rows read) gives `skipped` = rows dropped by the rules.
    """
    t0 = time.monotonic()
    con.execute("DROP TABLE IF EXISTS lc_shard")
    con.execute(f"CREATE TABLE lc_shard AS {shard_select(exprs, from_sql)}")
    if options.countries:
        con.execute(
            f"DELETE FROM lc_shard WHERE country IS NULL OR NOT ({country_clause(options)})"
        )
    eligible = int(con.execute("SELECT count(*) FROM lc_shard").fetchone()[0])
    log.info("%s: %d rows pass the rules (%.0f s)", source.name, eligible, time.monotonic() - t0)
    select = (
        "SELECT * REPLACE (coalesce(id, lc_company_id(registry, company_number, lei, cik)) AS id) "
        "FROM lc_shard"
    )
    sort = (best_first or SAMPLE_ORDER) if options.limit else order
    if sort:
        select += f" ORDER BY {sort}"
    if options.limit:
        select += f" LIMIT {int(options.limit)}"
    path = Path(options.out_dir) / (file_name or f"{source.name}.parquet")
    rows = write_shard(con, select, path)
    con.execute("DROP TABLE lc_shard")
    log.info("%s: wrote %s, %d rows, %.1f MB (%.0f s)", source.name, path.name, rows,
             path.stat().st_size / MB, time.monotonic() - t0)  # fmt: skip
    skipped = max(0, total - eligible) if total is not None else 0
    return ShardStats(source=source.name, rows=rows, files=[path], skipped=skipped, notes=notes)


def count(con, from_sql: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {from_sql}").fetchone()[0])


__all__ = [
    "Columns",
    "connect",
    "country_clause",
    "csv_source",
    "discard",
    "download",
    "extract",
    "get_text",
    "ident",
    "install_macros",
    "list_lambda",
    "rejected_lines",
    "run_source",
    "sql_in",
    "sql_str",
    "stable_company_id",
    "write_source_shard",
]
