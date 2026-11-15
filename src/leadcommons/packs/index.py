"""Local search databases built from company packs (docs/dev/search-performance.md).

A pack is a Parquet file (one per country since schema 3). Searching Parquet directly means
decoding and lower-casing every row for every query, which is too slow at tens of millions of
rows. So when a pack is installed we build a DuckDB database next to it, in `<data_dir>/.index/`:

- `companies`: every pack column, sorted in the default result order (rows with a domain, then
  with a size, then by lower-cased name, then id), numbered by `rk`. The first page of any
  search is "the 25 smallest rk that match", which DuckDB answers by reading a few row groups.
  `txt` holds the lower-cased name, legal name and domain for substring search (no lower() at
  query time). Display-only columns use zstd to keep the file small.
- `keys`: (kind, value, rk) sorted by kind and value, for lookups by id, domain and registry
  number. DuckDB's min/max zone maps turn a lookup into reading one row group, with a much
  smaller file than ART indexes on the same columns.
- `cube`: row counts per country, region, industry and size: the result count and the filter
  panel counts for those filters come from this small table instead of a scan.
- `places`: row counts per city and region, for place suggestions and the city list.
- `meta`: what the database was built from.

An index is named after its pack file and a signature of that file (name, size, mtime, index
version), so a new or changed pack never reuses an old index, and a running app can keep
reading the old one while the new one is built. Only DuckDB core features are used: no
extension is downloaded at runtime (the desktop app works offline).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import duckdb

from leadcommons.search import PACK_COLUMNS, PACK_FLOAT_COLUMNS, PACK_INT_COLUMNS

log = logging.getLogger(__name__)

INDEX_VERSION = 1
INDEX_DIR = ".index"
# Kinds in the keys table.
KEY_ID, KEY_DOMAIN, KEY_NUMBER = "i", "d", "n"
# Long, always-filled columns that are only ever shown: zstd keeps them small. (On sparse
# columns zstd costs more than it saves: each row group's segment takes its own blocks.)
ZSTD_COLUMNS = frozenset({"sources_json", "address"})
SEP = "\x1f"  # between the fields of txt; never part of a search term
TYPES = {**{c: "INTEGER" for c in PACK_INT_COLUMNS}, **{c: "DOUBLE" for c in PACK_FLOAT_COLUMNS}}
# Cube dimensions. A region column with more distinct values than this is left out of the cube
# (region filters then scan the table), so the cube stays small whatever the data looks like.
MAX_CUBE_REGIONS = 5000

# 64 KB storage blocks instead of DuckDB's 256 KB: a lookup reads one block per column, so a
# cold get_company reads ~2 MB instead of ~7 MB (3x faster), scans are as fast, files smaller.
BLOCK_SIZE = 65536

Progress = Callable[[str], None]


def _connect_new(path: Path) -> duckdb.DuckDBPyConnection:
    """A new database file with BLOCK_SIZE blocks (DuckDB's default where unsupported)."""
    try:
        return duckdb.connect(str(path), config={"default_block_size": str(BLOCK_SIZE)})
    except (duckdb.Error, TypeError, ValueError):
        Path(path).unlink(missing_ok=True)
        return duckdb.connect(str(path))


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def sql_type(col: str) -> str:
    return TYPES.get(col, "VARCHAR")


def index_dir(data_dir: Path) -> Path:
    return Path(data_dir) / INDEX_DIR


def signature(parquet: Path) -> str:
    st = Path(parquet).stat()
    raw = f"{Path(parquet).name}|{st.st_size}|{st.st_mtime_ns}|{INDEX_VERSION}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def index_path(parquet: Path, data_dir: Path | None = None) -> Path:
    """Where the index of `parquet` lives (whether or not it exists yet)."""
    parquet = Path(parquet)
    base = index_dir(data_dir if data_dir is not None else parquet.parent)
    return base / f"{parquet.stem}-{signature(parquet)}.duckdb"


def _info_path(db: Path) -> Path:
    return db.with_suffix(".json")


def read_info(parquet: Path, data_dir: Path | None = None) -> dict[str, Any] | None:
    """What the fresh index of `parquet` holds ({"rows", "countries", "sources", ...}), or None
    when there is no up-to-date index."""
    try:
        db = index_path(parquet, data_dir)
    except OSError:
        return None
    try:
        info = json.loads(_info_path(db).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not db.is_file() or info.get("version") != INDEX_VERSION:
        return None
    info["path"] = str(db)
    return info  # type: ignore[no-any-return]


def has_fresh_index(parquet: Path, data_dir: Path | None = None) -> bool:
    return read_info(parquet, data_dir) is not None


# -- DuckDB settings --------------------------------------------------------------------------


def total_memory_bytes() -> int:
    """Physical memory of this computer (DuckDB's default limit is 80% of it)."""
    try:
        con = duckdb.connect()
        try:
            con.execute("RESET memory_limit")
            text = str(con.execute("SELECT current_setting('memory_limit')").fetchone()[0])
        finally:
            con.close()
        return int(_parse_size(text) / 0.8)
    except Exception:
        return 8 << 30


def _parse_size(text: str) -> float:
    units = {"B": 1, "KIB": 1 << 10, "MIB": 1 << 20, "GIB": 1 << 30, "TIB": 1 << 40,
             "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}  # fmt: skip
    num, _, unit = text.strip().partition(" ")
    return float(num) * units.get(unit.strip().upper() or "B", 1)


def default_memory_limit() -> str:
    """LEADCOMMONS_DUCKDB_MEMORY, else 1/16 of the computer's memory between 256 MB and 1 GB
    (512 MB on an 8 GB laptop): enough to keep the search columns of a large country cached
    (docs/dev/search-performance.md), small enough to live next to a browser."""
    env = os.environ.get("LEADCOMMONS_DUCKDB_MEMORY", "").strip()
    if env:
        return env
    mb = int(min(1024, max(256, total_memory_bytes() / (1 << 20) / 16)))
    return f"{mb}MB"


def default_threads() -> int:
    env = os.environ.get("LEADCOMMONS_DUCKDB_THREADS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, min(8, os.cpu_count() or 2))


def temp_dir(data_dir: Path | None) -> str:
    """A writable directory for DuckDB to spill to (its default, '.tmp' in the current
    directory, is read-only inside an installed app). DuckDB creates it only when it spills."""
    if data_dir is not None and index_dir(data_dir).is_dir():
        if os.access(index_dir(data_dir), os.W_OK):
            return str(index_dir(data_dir) / "tmp")
    return str(Path(tempfile.gettempdir()) / "leadcommons-duckdb")


def configure(
    con: duckdb.DuckDBPyConnection,
    data_dir: Path | None,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> None:
    con.execute(f"SET memory_limit = {_q(memory_limit or default_memory_limit())}")
    con.execute(f"SET threads = {int(threads or default_threads())}")
    con.execute(f"SET temp_directory = {_q(temp_dir(data_dir))}")


# -- building -----------------------------------------------------------------------------------


def parquet_columns(con: duckdb.DuckDBPyConnection, parquet: Path) -> set[str]:
    rows = con.execute("SELECT name FROM parquet_schema(?)", [str(parquet)]).fetchall()
    return {str(r[0]) for r in rows}


def projection(have: set[str]) -> str:
    """PACK_COLUMNS from a pack file of any schema version: missing columns are NULL, every
    column has its pack type."""
    return ", ".join(
        f"CAST({c} AS {sql_type(c)}) AS {c}" if c in have else f"CAST(NULL AS {sql_type(c)}) AS {c}"
        for c in PACK_COLUMNS
    )


TXT_SQL = (
    "lower(coalesce(name, '')) || chr(31) || lower(coalesce(legal_name, '')) || chr(31) || "
    "lower(coalesce(domain, ''))"
)
ORDER_SQL = "(domain IS NULL), (size_range IS NULL), lower(name), id"


def norm_number_sql(col: str) -> str:
    """Registry numbers are looked up without spaces, dots or dashes, in upper case."""
    return f"upper(regexp_replace({col}, '[\\s.\\-]', '', 'g'))"


def norm_number(value: str) -> str:
    """The Python twin of norm_number_sql."""
    return "".join(ch for ch in str(value) if ch not in " \t\n\f\r.-").upper()


def _supports_zstd(con: duckdb.DuckDBPyConnection) -> bool:
    try:
        con.execute("CREATE TEMP TABLE _lc_zstd_probe (a VARCHAR USING COMPRESSION zstd)")
        con.execute("DROP TABLE _lc_zstd_probe")
        return True
    except duckdb.Error:
        return False


def kv_metadata(con: duckdb.DuckDBPyConnection, parquet: Path) -> dict[str, str]:
    def txt(v: object) -> str:
        return v.decode() if isinstance(v, bytes | bytearray) else str(v)

    try:
        rows = con.execute("SELECT key, value FROM parquet_kv_metadata(?)", [str(parquet)])
        return {txt(k): txt(v) for k, v in rows.fetchall()}
    except duckdb.Error:
        return {}


def _rk_in_order(con: duckdb.DuckDBPyConnection, schema: str, sample: int = 4000) -> bool:
    """rk follows the result order (checked on a sample of rows)."""
    rows = con.execute(
        f"SELECT rk, domain IS NULL, size_range IS NULL, lower(name), id "
        f"FROM {schema}.companies USING SAMPLE {int(sample)} ROWS"
    ).fetchall()

    def key(r: tuple) -> tuple:  # DuckDB order: NULLs last, strings by code point
        return (r[1], r[2], r[3] is None, r[3] or "", r[4] is None, r[4] or "")

    ranks = [r[0] for r in sorted(rows, key=key)]
    return ranks == sorted(ranks)


def build_tables(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    source_sql: str,
    zstd: bool | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Create companies / keys / cube / places in `schema` (an attached database) from
    `source_sql`, a query returning PACK_COLUMNS. Returns {"rows", "countries"}."""
    say = progress or (lambda _msg: None)
    if zstd is None:
        zstd = _supports_zstd(con)
    cols = ",\n  ".join(
        f"{c} {sql_type(c)}" + (" USING COMPRESSION zstd" if zstd and c in ZSTD_COLUMNS else "")
        for c in PACK_COLUMNS
    )
    con.execute(f"CREATE TABLE {schema}.companies (\n  rk BIGINT,\n  {cols},\n  txt VARCHAR\n)")
    names = ", ".join(PACK_COLUMNS)
    t0 = time.perf_counter()
    say("sorting companies")
    # preserve_insertion_order keeps the ORDER BY: rows are stored in rk order, so the min/max
    # zone maps of rk (and the first page of results) need only a few row groups. One sort:
    # row_number() OVER () numbers the sorted stream as it goes (a streaming window).
    con.execute("SET preserve_insertion_order = true")
    con.execute(
        f"INSERT INTO {schema}.companies SELECT row_number() OVER () - 1 AS rk, * FROM ("
        f"SELECT {names}, {TXT_SQL} AS txt FROM ({source_sql}) src ORDER BY {ORDER_SQL})"
    )
    if not _rk_in_order(con, schema):  # never seen; costs a second sort if it happens
        log.warning("index %s: rows numbered out of order; sorting again", schema)
        con.execute(f"DELETE FROM {schema}.companies")
        con.execute(
            f"INSERT INTO {schema}.companies "
            f"SELECT row_number() OVER (ORDER BY {ORDER_SQL}) - 1 AS rk, {names}, "
            f"{TXT_SQL} AS txt FROM ({source_sql}) src ORDER BY {ORDER_SQL}"
        )
    rows = int(con.execute(f"SELECT count(*) FROM {schema}.companies").fetchone()[0])
    log.info("index %s: %s rows sorted in %.1f s", schema, rows, time.perf_counter() - t0)
    say("building lookups")
    numbers = " ".join(
        f"UNION ALL SELECT '{KEY_NUMBER}', {norm_number_sql(c)}, rk FROM {schema}.companies "
        f"WHERE {c} IS NOT NULL AND {c} <> ''"
        + (" AND company_number IS DISTINCT FROM " + c if c != "company_number" else "")
        for c in ("company_number", "siren", "cik", "lei")
    )
    con.execute(
        f"CREATE TABLE {schema}.keys AS SELECT k, v, rk FROM ("
        f"SELECT '{KEY_ID}' AS k, id AS v, rk FROM {schema}.companies WHERE id IS NOT NULL "
        f"UNION ALL SELECT '{KEY_DOMAIN}', domain, rk FROM {schema}.companies "
        f"WHERE domain IS NOT NULL {numbers}) ORDER BY k, v, rk"
    )
    say("counting")
    regions = int(
        con.execute(f"SELECT count(DISTINCT region) FROM {schema}.companies").fetchone()[0]
    )
    region = "region" if regions <= MAX_CUBE_REGIONS else "CAST(NULL AS VARCHAR) AS region"
    con.execute(
        f"CREATE TABLE {schema}.cube AS SELECT country, {region}, industry, size_range, "
        f"count(*)::BIGINT AS n FROM {schema}.companies GROUP BY ALL ORDER BY ALL"
    )
    con.execute(
        f"CREATE TABLE {schema}.places AS SELECT * FROM ("
        f"SELECT 'city' AS kind, city AS value, lower(city) AS value_lc, country, "
        f"count(*)::BIGINT AS n FROM {schema}.companies WHERE city IS NOT NULL GROUP BY ALL "
        f"UNION ALL SELECT 'region', region, lower(region), country, count(*)::BIGINT "
        f"FROM {schema}.companies WHERE region IS NOT NULL GROUP BY ALL) ORDER BY kind, value_lc"
    )
    countries = sorted(
        str(r[0])
        for r in con.execute(
            f"SELECT DISTINCT country FROM {schema}.cube WHERE country IS NOT NULL"
        ).fetchall()
    )
    return {"rows": rows, "countries": countries, "cube_regions": regions <= MAX_CUBE_REGIONS}


def build_index(
    parquet: Path,
    data_dir: Path | None = None,
    *,
    memory_limit: str | None = None,
    threads: int | None = None,
    progress: Progress | None = None,
) -> Path:
    """Build the search database of one pack file; returns its path. Written under a temporary
    name and renamed when complete, so readers never see half an index."""
    parquet = Path(parquet)
    data_dir = Path(data_dir) if data_dir is not None else parquet.parent
    dest = index_path(parquet, data_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.stem}.{os.getpid()}-{threading.get_ident()}.building")
    for leftover in (tmp, Path(f"{tmp}.wal")):
        leftover.unlink(missing_ok=True)
    t0 = time.perf_counter()
    con = _connect_new(tmp)
    try:
        configure(con, data_dir, memory_limit or build_memory_limit(), threads)
        have = parquet_columns(con, parquet)
        kv = kv_metadata(con, parquet)
        source = f"SELECT {projection(have)} FROM read_parquet({_q(str(parquet))})"
        stats = build_tables(con, "main", source, progress=progress)
        info = {
            "version": INDEX_VERSION,
            "file": parquet.name,
            "rows": stats["rows"],
            "countries": stats["countries"],
            "cube_regions": stats["cube_regions"],
            "sources": [x for x in kv.get("sources", "").split(",") if x],
            "built_at": kv.get("built_at"),
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "duckdb": duckdb.__version__,
        }
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.execute("INSERT INTO meta VALUES ('info', ?)", [json.dumps(info)])
        con.execute("CHECKPOINT")
    except BaseException:
        con.close()
        for leftover in (tmp, Path(f"{tmp}.wal")):
            leftover.unlink(missing_ok=True)
        raise
    con.close()
    Path(f"{tmp}.wal").unlink(missing_ok=True)
    os.replace(tmp, dest)
    info["bytes"] = dest.stat().st_size
    info_tmp = _info_path(dest).with_name(f".{dest.stem}.json.part")
    info_tmp.write_text(json.dumps(info), encoding="utf-8")
    os.replace(info_tmp, _info_path(dest))
    log.info(
        "indexed %s: %s rows, %.0f MB, %.1f s",
        parquet.name,
        info["rows"],
        info["bytes"] / 1e6,
        time.perf_counter() - t0,
    )
    return dest


def build_memory_limit() -> str:
    """Building sorts the whole pack: give it a bit more room than searching (it spills to
    disk beyond the limit, so this only changes the speed)."""
    env = os.environ.get("LEADCOMMONS_DUCKDB_BUILD_MEMORY", "").strip()
    if env:
        return env
    mb = int(min(2048, max(512, total_memory_bytes() / (1 << 20) / 6)))
    return f"{mb}MB"


def pack_files(data_dir: Path) -> list[Path]:
    d = Path(data_dir)
    return sorted(d.glob("*.parquet")) if d.is_dir() else []


def ensure_index(
    data_dir: Path,
    files: Iterable[Path] | None = None,
    *,
    progress: Callable[[str, int, int], None] | None = None,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> list[Path]:
    """Build the missing or out-of-date indexes of the packs in data_dir (or of `files`) and
    delete indexes whose pack is gone. Returns the indexes built."""
    data_dir = Path(data_dir)
    targets = [Path(f) for f in files] if files is not None else pack_files(data_dir)
    todo = [f for f in targets if f.is_file() and not has_fresh_index(f, data_dir)]
    built: list[Path] = []
    for i, f in enumerate(todo, 1):

        def say(msg: str, i: int = i, f: Path = f) -> None:
            if progress:
                progress(f"Optimising {f.name} for search: {msg}", i, len(todo))

        say("starting")
        built.append(
            build_index(f, data_dir, memory_limit=memory_limit, threads=threads, progress=say)
        )
    current = fresh_indexes(data_dir)
    if len(current) >= 2 and not global_keys_path(data_dir, current).is_file():
        if progress:
            progress("Linking the countries for fast lookups", len(todo), len(todo))
        build_global_keys(data_dir, current, memory_limit=memory_limit, threads=threads)
    remove_orphans(data_dir)
    return built


def fresh_indexes(data_dir: Path) -> list[Path]:
    """The up-to-date index of each pack in data_dir that has one."""
    out = []
    for f in pack_files(data_dir):
        info = read_info(f, data_dir)
        if info is not None:
            out.append(Path(info["path"]))
    return out


def global_keys_path(data_dir: Path, indexes: Iterable[Path]) -> Path:
    """The combined key table of exactly these indexes (named after them)."""
    names = "|".join(sorted(Path(p).name for p in indexes)) + f"|{INDEX_VERSION}"
    return index_dir(data_dir) / f"keys-{hashlib.sha1(names.encode()).hexdigest()[:12]}.duckdb"


def build_global_keys(
    data_dir: Path,
    indexes: list[Path],
    *,
    memory_limit: str | None = None,
    threads: int | None = None,
) -> Path:
    """One key table over several indexes ((kind, value) -> index number, rk), so a lookup by
    id, domain or registry number is one query however many countries are installed."""
    dest = global_keys_path(data_dir, indexes)
    tmp = dest.with_name(f".{dest.stem}.{os.getpid()}-{threading.get_ident()}.building")
    for leftover in (tmp, Path(f"{tmp}.wal")):
        leftover.unlink(missing_ok=True)
    t0 = time.perf_counter()
    con = _connect_new(tmp)
    try:
        configure(con, data_dir, memory_limit or build_memory_limit(), threads)
        parts = []
        for n, path in enumerate(indexes):
            con.execute(f"ATTACH {_q(str(path))} AS s{n} (READ_ONLY)")
            parts.append(f"SELECT k, v, {n}::USMALLINT AS f, rk FROM s{n}.keys")
        con.execute("SET preserve_insertion_order = true")
        con.execute(
            f"CREATE TABLE keys AS SELECT * FROM ({' UNION ALL '.join(parts)}) ORDER BY k, v, f"
        )
        con.execute("CREATE TABLE files (f USMALLINT, name VARCHAR)")
        con.executemany(
            "INSERT INTO files VALUES (?, ?)", [[n, p.name] for n, p in enumerate(indexes)]
        )
        for n in range(len(indexes)):
            con.execute(f"DETACH s{n}")
        con.execute("CHECKPOINT")
    except BaseException:
        con.close()
        for leftover in (tmp, Path(f"{tmp}.wal")):
            leftover.unlink(missing_ok=True)
        raise
    con.close()
    Path(f"{tmp}.wal").unlink(missing_ok=True)
    os.replace(tmp, dest)
    log.info("linked %s indexes for lookups in %.1f s", len(indexes), time.perf_counter() - t0)
    return dest


def remove_orphans(data_dir: Path) -> int:
    """Delete indexes and leftovers that belong to no current pack file. Files still open (a
    running app on Windows) stay until next time."""
    base = index_dir(data_dir)
    if not base.is_dir():
        return 0
    keep: set[str] = set()
    for f in pack_files(data_dir):
        with contextlib.suppress(OSError):
            db = index_path(f, data_dir)
            keep |= {db.name, _info_path(db).name}
    keep.add(global_keys_path(data_dir, fresh_indexes(data_dir)).name)
    removed = 0
    for p in base.iterdir():
        if p.is_dir() or p.name in keep:
            continue
        if p.name.endswith(".building") and time.time() - p.stat().st_mtime < 6 * 3600:
            continue  # another process may be building it right now
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def remove_index(parquet: Path, data_dir: Path | None = None) -> None:
    """Delete every index built from a pack file with this name (any signature)."""
    parquet = Path(parquet)
    base = index_dir(data_dir if data_dir is not None else parquet.parent)
    if not base.is_dir():
        return
    for p in base.glob(f"{parquet.stem}-*"):
        if p.suffix in (".duckdb", ".json", ".wal"):
            with contextlib.suppress(OSError):
                p.unlink()


__all__ = [
    "INDEX_DIR",
    "INDEX_VERSION",
    "build_index",
    "configure",
    "default_memory_limit",
    "default_threads",
    "ensure_index",
    "has_fresh_index",
    "index_path",
    "read_info",
    "remove_index",
    "remove_orphans",
]
