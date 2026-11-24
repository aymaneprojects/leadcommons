"""DuckDB-backed company search over company packs.

Each pack file is one *source*, queried in one of three ways:

- ``index``: the local search database built when the pack was installed (packs/index.py),
  attached read-only. Fast at any size: results come from a pre-sorted table, counts from a
  small cube, lookups from a sorted key table.
- ``memory``: a pack without an index but small enough (MEMORY_ROWS) to load into the same
  structure in memory when the search opens (the bundled sample, packs built with
  `pepite pack build`).
- ``raw``: a large pack without an index (yet): plain views over the Parquet file. Correct but
  slow; `needs_index` reports these so the app can build their index in the background.

Every source exposes `companies` (PACK_COLUMNS, `rk`, `txt`), `cube` and `places`; indexed and
in-memory sources also have `keys`. A query runs per source (each one returns at most the rows
of the requested page, in its own rk order) and the results are merged in the default order.
Counts and filter-panel counts are cached per search object: a new pack means a new object.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from pepite.models import SIZE_RANGES, Company, Provenance
from pepite.packs import index as ix
from pepite.packs.domains import normalize_domain
from pepite.search import KEYWORD_FIELDS, PACK_COLUMNS, CompanyFilters, Page

log = logging.getLogger(__name__)

FACETS = {
    "country": "country",
    "industry": "industry",
    "size_range": "size_range",
    "region": "region",
    "city": "city",
}
_COLS = ", ".join(PACK_COLUMNS)
_REG_COLS = (("lei", "lei"), ("wikidata", "wikidata_id"), ("siren", "siren"), ("cik", "cik"))
_ORDER = ix.ORDER_SQL
_PLACE_GROUP = ("country", "region", "city")
MEMORY_ROWS = 300_000  # packs without an index up to this size are loaded in memory
CACHE_SIZE = 2048  # cached counts and filter-panel counts
PREFETCH, PREFETCH_MIN, WINDOWS = 5000, 100, 4  # see _page_rows
MINIS = 16  # mini cubes kept (see _mini)


def _lit(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


def _parse_dt(text: str | None) -> datetime:
    try:
        return datetime.fromisoformat(text) if text else datetime.now(UTC)
    except ValueError:
        return datetime.now(UTC)


def _ints(values: list[int]) -> str:
    return ",".join(str(int(v)) for v in values)


@dataclass
class _Source:
    alias: str
    file: Path
    kind: str  # "index" | "memory" | "raw"
    rows: int
    countries: frozenset[str] | None = None  # None: unknown (never pruned)
    cube_regions: bool = True
    info: dict[str, Any] = field(default_factory=dict)

    @property
    def table(self) -> str:
        return f"{self.alias}.companies"

    @property
    def keys(self) -> str | None:
        return None if self.kind == "raw" else f"{self.alias}.keys"

    @property
    def order(self) -> str:
        return _ORDER if self.kind == "raw" else "rk"


class DuckDBCompanySearch:
    """Implements pepite.search.CompanySearch over every *.parquet in data_dir
    (or SAMPLE_PACK_DIR when data_dir has none)."""

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        memory_limit: str | None = None,
        threads: int | None = None,
        saved: Callable[[str], Company | None] | None = None,
    ) -> None:
        from pepite.packs import SAMPLE_PACK_DIR

        # Companies the user saved stay reachable when a pack update drops or renumbers them.
        self.saved = saved

        files = self._find(data_dir) if data_dir else []
        base = Path(data_dir) if files and data_dir else None
        if not files:
            files = self._find(SAMPLE_PACK_DIR)
            base = Path(SAMPLE_PACK_DIR)
        self.files: list[Path] = files
        self.data_dir = base
        self._con = duckdb.connect(":memory:")
        ix.configure(self._con, base, memory_limit, threads)
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, Any] = OrderedDict()
        self._windows: OrderedDict[tuple, list[tuple]] = OrderedDict()
        self._minis: OrderedDict[tuple, str] = OrderedDict()
        self._mini_seq = 0
        self._con.execute("ATTACH ':memory:' AS lc_cache")
        self._sources: list[_Source] = []
        for i, f in enumerate(files):
            try:
                self._sources.append(self._open_source(i, f, base))
            except duckdb.Error:
                log.exception("could not read pack %s; skipping it", f)
        self._gk: dict[int, str] = {}  # combined key table: index number -> source alias
        self._open_global_keys(base)
        parts = [f"SELECT {_COLS}, txt FROM {s.table}" for s in self._sources]
        self._con.execute(
            "CREATE VIEW companies AS "
            + (
                " UNION ALL ".join(parts)
                if parts
                else "SELECT "
                + ", ".join(f"CAST(NULL AS {ix.sql_type(c)}) AS {c}" for c in PACK_COLUMNS)
                + ", CAST(NULL AS VARCHAR) AS txt WHERE false"
            )
        )

    @staticmethod
    def _find(directory: Path | None) -> list[Path]:
        if directory is None or not Path(directory).is_dir():
            return []
        return sorted(Path(directory).glob("*.parquet"))

    # -- opening the sources -----------------------------------------------------------------

    def _open_source(self, i: int, f: Path, base: Path | None) -> _Source:
        info = ix.read_info(f, base) if base is not None else None
        if info is not None:
            alias = f"p{i}"
            try:
                self._con.execute(f"ATTACH {_lit(info['path'])} AS {alias} (READ_ONLY)")
                return _Source(
                    alias,
                    f,
                    "index",
                    int(info.get("rows") or 0),
                    frozenset(info.get("countries") or []) or None,
                    bool(info.get("cube_regions", True)),
                    info,
                )
            except duckdb.Error:
                log.warning("index of %s cannot be read; using the pack file", f.name)
        rows = int(
            self._con.execute(
                "SELECT coalesce(sum(num_rows), 0) FROM parquet_file_metadata(?)", [str(f)]
            ).fetchone()[0]
        )
        have = ix.parquet_columns(self._con, f)
        source = f"SELECT {ix.projection(have)} FROM read_parquet({_lit(str(f))})"
        if rows <= MEMORY_ROWS:
            alias = f"m{i}"
            self._con.execute(f"ATTACH ':memory:' AS {alias}")
            stats = ix.build_tables(self._con, alias, source, zstd=False)
            return _Source(
                alias,
                f,
                "memory",
                stats["rows"],
                frozenset(stats["countries"]) or None,
                stats["cube_regions"],
            )
        log.warning("%s (%s rows) has no search index yet: searches will be slow", f.name, rows)
        alias = f"r{i}"
        self._con.execute(f"CREATE SCHEMA {alias}")
        self._con.execute(
            f"CREATE VIEW {alias}.companies AS SELECT CAST(NULL AS BIGINT) AS rk, *, "
            f"{ix.TXT_SQL} AS txt FROM ({source})"
        )
        self._con.execute(
            f"CREATE VIEW {alias}.cube AS SELECT country, region, industry, size_range, "
            f"count(*)::BIGINT AS n FROM {alias}.companies GROUP BY ALL"
        )
        self._con.execute(
            f"CREATE VIEW {alias}.places AS "
            f"SELECT 'city' AS kind, city AS value, lower(city) AS value_lc, country, "
            f"count(*)::BIGINT AS n FROM {alias}.companies WHERE city IS NOT NULL GROUP BY ALL "
            f"UNION ALL SELECT 'region', region, lower(region), country, count(*)::BIGINT "
            f"FROM {alias}.companies WHERE region IS NOT NULL GROUP BY ALL"
        )
        return _Source(alias, f, "raw", rows, self._raw_countries(f))

    def _open_global_keys(self, base: Path | None) -> None:
        """Attach the key table shared by every indexed source, when it covers exactly them."""
        indexed = [s for s in self._sources if s.kind == "index"]
        if base is None or len(indexed) < 2:
            return
        path = ix.global_keys_path(base, [Path(s.info["path"]) for s in indexed])
        if not path.is_file():
            return
        try:
            self._con.execute(f"ATTACH {_lit(str(path))} AS gk (READ_ONLY)")
            files = dict(self._con.execute("SELECT name, f FROM gk.files").fetchall())
        except duckdb.Error:
            log.warning("the combined key table cannot be read; looking up per pack")
            return
        by_name = {Path(s.info["path"]).name: s.alias for s in indexed}
        if set(files) != set(by_name):
            return
        self._gk = {int(f): by_name[name] for name, f in files.items()}

    def _raw_countries(self, f: Path) -> frozenset[str] | None:
        """The pack's countries from the Parquet statistics (no scan), when they say."""
        try:
            rows = self._con.execute(
                "SELECT DISTINCT stats_min_value, stats_max_value FROM parquet_metadata(?) "
                "WHERE path_in_schema = 'country'",
                [str(f)],
            ).fetchall()
        except duckdb.Error:
            return None
        if not rows or any(lo is None or lo != hi for lo, hi in rows):
            return None
        return frozenset(str(lo) for lo, _ in rows)

    @property
    def needs_index(self) -> list[Path]:
        """Large pack files searched without an index (slow until ensure_index runs)."""
        return [s.file for s in self._sources if s.kind == "raw"]

    def close(self) -> None:
        try:
            self._con.close()
        except duckdb.Error:
            pass

    # -- query building (all user input is bound as parameters) --------------------------

    @staticmethod
    def _where(
        f: CompanyFilters, skip: str | None = None, part: str = "all"
    ) -> tuple[list[str], list[Any]]:
        """Conditions over the source tables (and, when cube_ok, the cubes). Domains are left to
        the caller: indexed sources answer them from their key table. part: "core" for the
        text conditions only (never skipped by a facet), "rest" for the others."""
        conds: list[str] = []
        params: list[Any] = []

        def contains(col: str, term: str) -> str:
            low = term.lower()
            if ix.SEP in low:
                return "false"
            params.append(low)
            return f"contains({col}, ?)"

        def kw_match(cols: tuple[str, ...], term: str) -> str:
            parts = []
            if any(c in ("name", "legal_name", "domain") for c in cols):
                parts.append(contains("txt", term))
            if "industry_label" in cols:
                parts.append(contains("lower(industry_label)", term))
            return "(" + " OR ".join(parts) + ")"

        def is_in(expr: str, values: list[str]) -> str:
            params.extend(values)
            return f"{expr} IN ({','.join('?' * len(values))})"

        if part != "rest":
            if f.text and f.text.strip():
                conds.append(kw_match(("name",), f.text.strip()))
            cols = KEYWORD_FIELDS.get(f.keyword_fields, KEYWORD_FIELDS["all"])
            kws = [k.strip() for k in f.keywords if k and k.strip()]
            if kws:
                joiner = " AND " if f.keyword_mode == "all" else " OR "
                conds.append("(" + joiner.join(kw_match(cols, k) for k in kws) + ")")
            for k in (k.strip() for k in f.exclude_keywords if k and k.strip()):
                conds.append(f"NOT coalesce({kw_match(cols, k)}, false)")
        if part == "core":
            return conds, params
        if skip not in _PLACE_GROUP:
            places = []
            if f.countries:
                places.append(is_in("country", [c.strip().upper() for c in f.countries]))
            if f.regions:
                places.append(is_in("lower(region)", [r.strip().lower() for r in f.regions]))
            if f.cities:
                places.append(is_in("lower(city)", [c.strip().lower() for c in f.cities]))
            if places:
                conds.append("(" + " OR ".join(places) + ")")
            if f.exclude_countries:
                xs = [c.strip().upper() for c in f.exclude_countries]
                conds.append("country IS NOT NULL AND NOT " + is_in("country", xs))
            if f.exclude_regions:
                xs = [r.strip().lower() for r in f.exclude_regions]
                conds.append("NOT " + is_in("coalesce(lower(region), '')", xs))
            if f.exclude_cities:
                xs = [c.strip().lower() for c in f.exclude_cities]
                conds.append("NOT " + is_in("coalesce(lower(city), '')", xs))
        if skip != "industry":
            if f.industries:
                conds.append(is_in("industry", [i.strip().upper() for i in f.industries]))
            if f.exclude_industries:
                xs = [i.strip().upper() for i in f.exclude_industries]
                conds.append("industry IS NOT NULL AND NOT " + is_in("industry", xs))
        if f.size_ranges and skip != "size_range":
            conds.append(is_in("size_range", list(f.size_ranges)))
        return conds, params

    @staticmethod
    def _domains(f: CompanyFilters) -> list[str]:
        return [normalize_domain(d) or d.strip().lower() for d in f.domains]

    def _cube_ok(self, f: CompanyFilters, skip: str | None, sources: list[_Source]) -> bool:
        """True when the counts can come from the cubes: only country / region / industry /
        size filters (region only where the cube kept it)."""
        if (f.text and f.text.strip()) or f.domains:
            return False
        if any(k and k.strip() for k in [*f.keywords, *f.exclude_keywords]):
            return False
        regions = skip == "region"
        if skip not in _PLACE_GROUP:
            if f.cities or f.exclude_cities:
                return False
            regions = regions or bool(f.regions or f.exclude_regions)
        return not regions or all(s.cube_regions for s in sources)

    def _candidates(self, f: CompanyFilters, skip: str | None = None) -> list[_Source]:
        """Sources that can hold a match (a pack of other countries cannot)."""
        out = []
        places = skip not in _PLACE_GROUP
        want = {c.strip().upper() for c in f.countries} if places else set()
        only_countries = places and want and not f.regions and not f.cities
        drop = {c.strip().upper() for c in f.exclude_countries} if places else set()
        for s in self._sources:
            if s.countries is not None:
                if only_countries and not (s.countries & want):
                    continue
                if drop and s.countries <= drop:
                    continue
            out.append(s)
        return out

    def _plan(
        self, f: CompanyFilters, skip: str | None = None, part: str = "all"
    ) -> list[tuple[_Source, str, list[Any]]]:
        """(source, WHERE clause, params) for each source that can hold matches."""
        conds, params = self._where(f, skip, part)
        sources = self._candidates(f, skip if part == "all" else "country")
        hits: dict[str, list[int]] = {}
        domains = self._domains(f) if f.domains else []
        if domains:
            hits = self._key_hits(ix.KEY_DOMAIN, domains, sources)
        out = []
        for s in sources:
            c, p = list(conds), list(params)
            if domains:
                if s.keys:
                    if not hits.get(s.alias):
                        continue
                    c.append(f"rk IN ({_ints(hits[s.alias])})")
                else:
                    c.append(f"domain IN ({','.join('?' * len(domains))})")
                    p.extend(domains)
            out.append((s, (" WHERE " + " AND ".join(c)) if c else "", p))
        return out

    # -- low level ---------------------------------------------------------------------------

    def _query(self, sql: str, params: list[Any]) -> list[tuple]:
        cur = self._con.cursor()  # one cursor per call: safe across FastAPI worker threads
        try:
            return cur.execute(sql, params).fetchall()
        finally:
            cur.close()

    def _cached(self, key: tuple, compute: Any) -> Any:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        value = compute()
        with self._lock:
            self._cache[key] = value
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return value

    def _key_hits(
        self, kind: str, values: list[str], sources: list[_Source] | None = None
    ) -> dict[str, list[int]]:
        """alias -> rks of the rows whose key `kind` is one of values (keyed sources only)."""
        keyed = [s for s in (self._sources if sources is None else sources) if s.keys]
        if not keyed or not values:
            return {}
        marks = ",".join("?" * len(values))
        parts, params = [], []
        tags: dict[int, str] = {}
        if self._gk:  # one query on the combined key table for every indexed source
            wanted = {s.alias for s in keyed}
            parts.append(f"SELECT f AS s, rk FROM gk.keys WHERE k = ? AND v IN ({marks})")
            params += [kind, *values]
            tags = {f: a for f, a in self._gk.items() if a in wanted}
            keyed = [s for s in keyed if s.alias not in self._gk.values()]
        for n, s in enumerate(keyed, start=1 << 16):
            parts.append(f"SELECT {n} AS s, rk FROM {s.keys} WHERE k = ? AND v IN ({marks})")
            params += [kind, *values]
            tags[n] = s.alias
        out: dict[str, list[int]] = {}
        for n, rk in self._query(" UNION ALL ".join(parts), params) if parts else []:
            if int(n) in tags:
                out.setdefault(tags[int(n)], []).append(int(rk))
        return out

    def _rows_by_key(
        self,
        kind: str,
        values: list[str],
        raw_sql: str,
        limit: int,
        raw_params: list[Any] | None = None,
    ) -> list[tuple]:
        """Rows whose key matches, in source order: key tables first, then a filtered scan
        of raw sources (raw_sql: a condition over raw_params, by default one '?' per value)."""
        hits = self._key_hits(kind, values)
        parts, params = [], []
        for n, s in enumerate(self._sources):
            if s.keys:
                if s.alias in hits:
                    parts.append(
                        f"SELECT {n} AS s, rk, {_COLS} FROM {s.table} "
                        f"WHERE rk IN ({_ints(hits[s.alias])})"
                    )
            else:
                parts.append(f"SELECT {n} AS s, 0 AS rk, {_COLS} FROM {s.table} WHERE {raw_sql}")
                params += values if raw_params is None else raw_params
        if not parts:
            return []
        sql = f"SELECT * FROM ({' UNION ALL '.join(parts)}) ORDER BY s, rk LIMIT {int(limit)}"
        return [r[2:] for r in self._query(sql, params)]

    def _mini_ok(self, f: CompanyFilters) -> bool:
        """Text, keyword or domain filters: the matching rows are grouped once (a mini cube)
        and the count and the filter-panel counts are read from that small table."""
        return bool(self._where(f, part="core")[0] or f.domains)

    def _mini(self, f: CompanyFilters, with_city: bool) -> str | None:
        conds, params = self._where(f, part="core")
        key = (tuple(conds), tuple(params), tuple(self._domains(f)), with_city)
        with self._lock:
            name = self._minis.get(key)
            if name is not None:
                self._minis.move_to_end(key)
                return name
            self._mini_seq += 1
            name = f"lc_cache.m{self._mini_seq}"
        plan = self._plan(f, part="core")
        if not plan:
            return None
        cols = "country, region, industry, size_range" + (", city" if with_city else "")
        union = " UNION ALL ".join(f"SELECT {cols} FROM {s.table}{w}" for s, w, _ in plan)
        cur = self._con.cursor()
        try:
            cur.execute(
                f"CREATE TABLE {name} AS SELECT {cols}, count(*)::BIGINT AS n FROM ({union}) "
                "GROUP BY ALL",
                [p for _, _, ps in plan for p in ps],
            )
        finally:
            cur.close()
        drop = []
        with self._lock:
            if key in self._minis:  # another thread built it meanwhile
                drop.append(name)
                name = self._minis[key]
            else:
                self._minis[key] = name
            while len(self._minis) > MINIS:
                drop.append(self._minis.popitem(last=False)[1])
        for old in drop:
            self._query(f"DROP TABLE IF EXISTS {old}", [])
        return name

    def _from_mini(self, f: CompanyFilters, col: str | None, limit: int = 0) -> Any:
        """The count (col None) or the counts per value of col, from the mini cube; None when
        the mini cube vanished meanwhile (the caller then scans)."""
        with_city = bool(f.cities or f.exclude_cities) or col == "city"
        name = self._mini(f, with_city)
        if name is None:
            return 0 if col is None else []
        conds, params = self._where(f, skip=col, part="rest")
        try:
            if col is None:
                where = (" WHERE " + " AND ".join(conds)) if conds else ""
                return int(
                    self._query(f"SELECT coalesce(sum(n), 0) FROM {name}{where}", params)[0][0]
                )
            conds.append(f"{col} IS NOT NULL")
            return self._query(
                f"SELECT {col}, sum(n) AS n FROM {name} WHERE {' AND '.join(conds)} "
                f"GROUP BY {col} ORDER BY n DESC, {col} LIMIT ?",
                [*params, limit],
            )
        except duckdb.CatalogException:
            return None

    # -- CompanySearch API -----------------------------------------------------------------

    def count_companies(self, filters: CompanyFilters) -> int:
        """Number of companies matching filters (cached)."""
        conds, params = self._where(filters)
        key = ("count", tuple(conds), tuple(params), tuple(self._domains(filters)))
        return int(self._cached(key, lambda: self._count(filters)))

    def _count(self, f: CompanyFilters) -> int:
        sources = self._candidates(f)
        if not sources:
            return 0
        if self._cube_ok(f, None, sources):
            conds, params = self._where(f)
            where = (" WHERE " + " AND ".join(conds)) if conds else ""
            union = " UNION ALL ".join(
                f"SELECT country, region, industry, size_range, n FROM {s.alias}.cube"
                for s in sources
            )
            return int(
                self._query(f"SELECT coalesce(sum(n), 0) FROM ({union}){where}", params)[0][0]
            )
        if self._mini_ok(f):
            got = self._from_mini(f, None)
            if got is not None:
                return int(got)
        plan = self._plan(f)
        if not plan:
            return 0
        union = " UNION ALL ".join(f"SELECT count(*) AS c FROM {s.table}{w}" for s, w, _ in plan)
        params = [p for _, _, ps in plan for p in ps]
        return int(self._query(f"SELECT coalesce(sum(c), 0) FROM ({union})", params)[0][0])

    def search_companies(
        self, filters: CompanyFilters, page: int = 1, per_page: int = 25
    ) -> Page[Company]:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        total = self.count_companies(filters)
        offset = (page - 1) * per_page
        rows = self._page_rows(filters, offset, per_page) if offset < total else []
        return Page(
            items=[self._to_company(r) for r in rows], total=total, page=page, per_page=per_page
        )

    def _page_rows(self, f: CompanyFilters, offset: int, limit: int) -> list[tuple]:
        if limit >= PREFETCH_MIN and offset + limit <= PREFETCH:
            # Reading the whole result set in big pages (pages.scan_companies sorts and filters
            # up to 5,000 rows itself): fetch it once, serve the next pages from memory.
            conds, params = self._where(f)
            key = ("window", tuple(conds), tuple(params), tuple(self._domains(f)))
            with self._lock:
                rows = self._windows.get(key)
                if rows is not None:
                    self._windows.move_to_end(key)
            if rows is None:
                rows = self._top_rows(f, 0, PREFETCH)
                with self._lock:
                    self._windows[key] = rows
                    while len(self._windows) > WINDOWS:
                        self._windows.popitem(last=False)
            return rows[offset : offset + limit]
        return self._top_rows(f, offset, limit)

    def _top_rows(self, f: CompanyFilters, offset: int, limit: int) -> list[tuple]:
        plan = self._plan(f)
        if not plan:
            return []
        if len(plan) == 1:
            s, where, params = plan[0]
            return self._query(
                f"SELECT {_COLS} FROM {s.table}{where} ORDER BY {s.order} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
        k = offset + limit
        parts = [
            f"(SELECT {_COLS} FROM {s.table}{where} ORDER BY {s.order} LIMIT {k})"
            for s, where, _ in plan
        ]
        params = [p for _, _, ps in plan for p in ps]
        return self._query(
            f"SELECT {_COLS} FROM ({' UNION ALL '.join(parts)}) ORDER BY {_ORDER} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )

    def get_company(self, company_id: str) -> Company | None:
        rows = self._rows_by_key(ix.KEY_ID, [str(company_id)], "id = ?", 1)
        if rows:
            return self._to_company(rows[0])
        return self.saved(str(company_id)) if self.saved else None

    def get_companies(self, company_ids: list[str]) -> dict[str, Company]:
        """id -> Company for the ids found (one query for many ids)."""
        ids = list(dict.fromkeys(str(i) for i in company_ids if i))
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._rows_by_key(ix.KEY_ID, ids, f"id IN ({marks})", 10 * len(ids) + 10)
        out: dict[str, Company] = {}
        for r in rows:
            c = self._to_company(r)
            out.setdefault(c.id, c)
        for cid in ids:
            if cid not in out and self.saved and (c := self.saved(cid)):
                out[cid] = c
        return out

    def find_by_domain(self, domain: str, limit: int = 10) -> list[Company]:
        """Companies whose domain is exactly `domain` (normalised like the domains filter)."""
        d = normalize_domain(domain) or domain.strip().lower()
        if not d:
            return []
        return [
            self._to_company(r) for r in self._rows_by_key(ix.KEY_DOMAIN, [d], "domain = ?", limit)
        ]

    def find_by_number(self, number: str, limit: int = 10) -> list[Company]:
        """Companies with this registry number: company number, SIREN, CIK or LEI (spaces,
        dots and dashes ignored, case-insensitive)."""
        n = ix.norm_number(number)
        if not n:
            return []
        cols = ("company_number", "siren", "cik", "lei")
        raw = " OR ".join(f"{ix.norm_number_sql(c)} = ?" for c in cols)
        rows = self._rows_by_key(ix.KEY_NUMBER, [n], raw, limit, [n] * len(cols))
        return [self._to_company(r) for r in rows]

    def lookalike_companies(self, company: Company, limit: int = 25) -> list[Company]:
        """Companies like `company`: same ISIC industry, same or neighbouring size bucket, the
        same country first, then closer size, then rows with a known domain, then name."""
        if not company.industry:
            return []
        conds = ["industry = ?", "id <> ?"]
        params: list[Any] = [company.industry.strip().upper(), company.id]
        order: list[str] = []
        order_params: list[Any] = []
        if company.size_range in SIZE_RANGES:
            idx = SIZE_RANGES.index(company.size_range)
            near = [s for i, s in enumerate(SIZE_RANGES) if abs(i - idx) <= 1]
            conds.append(f"size_range IN ({','.join('?' * len(near))})")
            params += near
        if company.country:
            order.append("(country IS DISTINCT FROM ?)")
            order_params.append(company.country.upper())
        if company.size_range in SIZE_RANGES:
            order.append("(size_range <> ?)")
            order_params.append(company.size_range)
        order += ["(domain IS NULL)", "lower(name)", "id"]
        limit = max(0, int(limit))
        where, by = " AND ".join(conds), ", ".join(order)
        parts = [
            f"(SELECT {_COLS} FROM {s.table} WHERE {where} ORDER BY {by} LIMIT {limit})"
            for s in self._sources
        ]
        if not parts:
            return []
        all_params = [*([*params, *order_params] * len(parts)), *order_params, limit]
        rows = self._query(
            f"SELECT {_COLS} FROM ({' UNION ALL '.join(parts)}) ORDER BY {by} LIMIT ?",
            all_params,
        )
        return [self._to_company(r) for r in rows]

    def facet_counts(self, facet: str, filters: CompanyFilters, limit: int = 10) -> dict[str, int]:
        col = FACETS.get(facet)
        if col is None:
            raise ValueError(f"unknown facet: {facet!r}")
        limit = max(1, int(limit))
        conds, params = self._where(filters, skip=facet)
        key = ("facet", col, limit, tuple(conds), tuple(params), tuple(self._domains(filters)))
        return dict(self._cached(key, lambda: self._facet(col, filters, limit)))

    def _facet(self, col: str, f: CompanyFilters, limit: int) -> dict[str, int]:
        sources = self._candidates(f, skip=col)
        if not sources:
            return {}
        if col != "city" and self._cube_ok(f, col, sources):
            conds, params = self._where(f, skip=col)
            conds.append(f"{col} IS NOT NULL")
            union = " UNION ALL ".join(
                f"SELECT country, region, industry, size_range, n FROM {s.alias}.cube"
                for s in sources
            )
            sql = (
                f"SELECT {col}, sum(n) AS n FROM ({union}) WHERE {' AND '.join(conds)} "
                f"GROUP BY {col} ORDER BY n DESC, {col} LIMIT ?"
            )
            rows = self._query(sql, [*params, limit])
        elif self._mini_ok(f) and (got := self._from_mini(f, col, limit)) is not None:
            rows = got
        elif col == "city" and not self._where(f, skip=col)[0] and not f.domains:
            union = " UNION ALL ".join(
                f"SELECT value, n FROM {s.alias}.places WHERE kind = 'city'" for s in sources
            )
            rows = self._query(
                f"SELECT value, sum(n) AS n FROM ({union}) GROUP BY value "
                "ORDER BY n DESC, value LIMIT ?",
                [limit],
            )
        else:
            plan = self._plan(f, skip=col)
            if not plan:
                return {}
            parts = []
            for s, where, _ in plan:
                cond = (where + " AND " if where else " WHERE ") + f"{col} IS NOT NULL"
                parts.append(f"SELECT {col}, count(*) AS n FROM {s.table}{cond} GROUP BY {col}")
            params = [p for _, _, ps in plan for p in ps]
            rows = self._query(
                f"SELECT {col}, sum(n) AS n FROM ({' UNION ALL '.join(parts)}) "
                f"GROUP BY {col} ORDER BY n DESC, {col} LIMIT ?",
                [*params, limit],
            )
        return {str(k): int(n) for k, n in rows}

    def suggest_places(self, text: str, limit: int = 12) -> list[tuple[str, str, str, int]]:
        """Countries, regions and cities whose name starts with `text` (any word of it):
        (kind, value, country, companies), most companies first. Country names are matched
        by the caller, which knows their English names; here countries match on the code."""
        term = text.strip().lower()
        if not term or not self._sources:
            return []
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        start, word = f"{esc}%", f"% {esc}%"
        limit = max(1, int(limit))
        places = " UNION ALL ".join(
            f"SELECT kind, value, value_lc, country, n FROM {s.alias}.places" for s in self._sources
        )
        out: list[tuple[str, str, str, int]] = []
        for kind in ("city", "region"):
            rows = self._query(
                f"SELECT value, arg_max(country, n), sum(n) AS n FROM ({places}) "
                "WHERE kind = ? AND (value_lc LIKE ? ESCAPE '\\' OR value_lc LIKE ? ESCAPE '\\') "
                "GROUP BY value ORDER BY n DESC, value LIMIT ?",
                [kind, start, word, limit],
            )
            out += [(kind, str(v), str(cc or ""), int(n)) for v, cc, n in rows]
        if len(term) == 2:
            cubes = " UNION ALL ".join(
                f"SELECT country, n FROM {s.alias}.cube" for s in self._sources
            )
            rows = self._query(
                f"SELECT country, sum(n) FROM ({cubes}) WHERE country = ? GROUP BY country",
                [term.upper()],
            )
            out += [("country", str(v), str(v), int(n)) for v, n in rows]
        return sorted(out, key=lambda r: -r[3])[:limit]

    def pack_info(self) -> list[dict]:
        """One dict per loaded pack file: {"file", "rows", "sources", "built_at", "indexed"}."""
        info = []
        for s in self._sources:
            if s.kind == "index":
                info.append(
                    {
                        "file": str(s.file),
                        "rows": s.rows,
                        "sources": list(s.info.get("sources") or []),
                        "built_at": s.info.get("built_at"),
                        "indexed": True,
                    }
                )
                continue
            kv = ix.kv_metadata(self._con, s.file)
            info.append(
                {
                    "file": str(s.file),
                    "rows": s.rows,
                    "sources": [x for x in kv.get("sources", "").split(",") if x],
                    "built_at": kv.get("built_at"),
                    "indexed": s.kind == "memory",
                }
            )
        return info

    @staticmethod
    def _to_company(row: tuple) -> Company:
        d = dict(zip(PACK_COLUMNS, row, strict=True))
        try:
            raw = json.loads(d["sources_json"] or "[]")
        except (TypeError, ValueError):
            raw = []
        sources = [
            Provenance(
                source=s.get("source", ""),
                licence=s.get("licence", "n/a"),
                url=s.get("url"),
                fetched_at=_parse_dt(s.get("fetched_at")),
            )
            for s in raw
            if isinstance(s, dict)
        ]
        return Company(
            id=d["id"],
            name=d["name"],
            domain=d["domain"],
            legal_name=d["legal_name"],
            country=d["country"],
            region=d["region"],
            city=d["city"],
            industry=d["industry"],
            industry_label=d["industry_label"],
            size_range=d["size_range"],
            founded_year=d["founded_year"],
            registry_ids={k: d[c] for k, c in _REG_COLS if d[c]},
            links={"website": d["website"]} if d["website"] else {},
            sources=sources,
            registry=d.get("registry"),
            company_number=d.get("company_number"),
            status=d.get("status"),
            legal_form=d.get("legal_form"),
            address=d.get("address"),
            postal_code=d.get("postal_code"),
            phone=d.get("phone"),
            employees=d.get("employees"),
            revenue=d.get("revenue"),
            revenue_currency=d.get("revenue_currency"),
            revenue_year=d.get("revenue_year"),
        )
