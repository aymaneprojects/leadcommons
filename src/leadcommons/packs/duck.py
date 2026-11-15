"""DuckDB-backed company search over Parquet data packs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from leadcommons.models import SIZE_RANGES, Company, Provenance
from leadcommons.packs.domains import normalize_domain
from leadcommons.search import KEYWORD_FIELDS, PACK_COLUMNS, CompanyFilters, Page

FACETS = {
    "country": "country",
    "industry": "industry",
    "size_range": "size_range",
    "region": "region",
    "city": "city",
}
_COLS = ", ".join(PACK_COLUMNS)
_REG_COLS = (("lei", "lei"), ("wikidata", "wikidata_id"), ("siren", "siren"), ("cik", "cik"))
_TYPES = {"founded_year": "INTEGER"}
_ORDER = "(domain IS NULL), (size_range IS NULL), lower(name), id"


def _like(term: str) -> str:
    """Escape LIKE wildcards in a user term and wrap it for substring match."""
    esc = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _lit(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


def _txt(v: object) -> str:
    return v.decode() if isinstance(v, bytes | bytearray) else str(v)


def _parse_dt(text: str | None) -> datetime:
    try:
        return datetime.fromisoformat(text) if text else datetime.now(UTC)
    except ValueError:
        return datetime.now(UTC)


class DuckDBCompanySearch:
    """Implements leadcommons.search.CompanySearch over every *.parquet in data_dir
    (or SAMPLE_PACK_DIR when data_dir has none)."""

    def __init__(self, data_dir: Path | None = None) -> None:
        from leadcommons.packs import SAMPLE_PACK_DIR

        files = self._find(data_dir) if data_dir else []
        if not files:
            files = self._find(SAMPLE_PACK_DIR)
        self.files: list[Path] = files
        self._con = duckdb.connect(":memory:")
        if files:
            lst = ", ".join(_lit(str(f)) for f in files)
            have = {
                r[0]
                for r in self._con.execute(
                    f"SELECT DISTINCT name FROM parquet_schema([{lst}])"
                ).fetchall()
            }
            # Older packs lack newer columns (siren, cik): expose them as NULL.
            proj = ", ".join(
                c if c in have else f"CAST(NULL AS {_TYPES.get(c, 'VARCHAR')}) AS {c}"
                for c in PACK_COLUMNS
            )
            self._con.execute(
                f"CREATE VIEW companies AS SELECT {proj} "
                f"FROM read_parquet([{lst}], union_by_name=true)"
            )
        else:
            proj = ", ".join(
                f"CAST(NULL AS {_TYPES.get(c, 'VARCHAR')}) AS {c}" for c in PACK_COLUMNS
            )
            self._con.execute(f"CREATE VIEW companies AS SELECT {proj} WHERE false")

    @staticmethod
    def _find(directory: Path | None) -> list[Path]:
        if directory is None or not Path(directory).is_dir():
            return []
        return sorted(Path(directory).glob("*.parquet"))

    # -- query building (all user input is bound as parameters) --------------------------

    @staticmethod
    def _where(f: CompanyFilters, skip: str | None = None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []

        def like_any(cols: tuple[str, ...], term: str) -> str:
            like = _like(term)
            params.extend([like] * len(cols))
            return "(" + " OR ".join(f"lower({c}) LIKE ? ESCAPE '\\'" for c in cols) + ")"

        def is_in(expr: str, values: list[str]) -> str:
            params.extend(values)
            return f"{expr} IN ({','.join('?' * len(values))})"

        if f.text and f.text.strip():
            conds.append(like_any(("name", "legal_name", "domain"), f.text.strip()))
        cols = KEYWORD_FIELDS.get(f.keyword_fields, KEYWORD_FIELDS["all"])
        kws = [k.strip() for k in f.keywords if k and k.strip()]
        if kws:
            joiner = " AND " if f.keyword_mode == "all" else " OR "
            conds.append("(" + joiner.join(like_any(cols, k) for k in kws) + ")")
        for k in (k.strip() for k in f.exclude_keywords if k and k.strip()):
            conds.append(f"NOT coalesce({like_any(cols, k)}, false)")
        if skip not in ("country", "region", "city"):
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
        if f.domains:
            conds.append(
                is_in("domain", [normalize_domain(d) or d.strip().lower() for d in f.domains])
            )
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def suggest_places(self, text: str, limit: int = 12) -> list[tuple[str, str, str, int]]:
        """Countries, regions and cities whose name starts with `text` (any word of it):
        (kind, value, country, companies), most companies first. Country names are matched
        by the caller, which knows their English names; here countries match on the code."""
        term = text.strip().lower()
        if not term:
            return []
        esc = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        start, word = f"{esc}%", f"% {esc}%"
        out: list[tuple[str, str, str, int]] = []
        for kind, col in (("city", "city"), ("region", "region")):
            rows = self._query(
                f"SELECT {col}, mode(country), COUNT(*) AS n FROM companies "
                f"WHERE {col} IS NOT NULL AND (lower({col}) LIKE ? ESCAPE '\\' "
                f"OR lower({col}) LIKE ? ESCAPE '\\') GROUP BY {col} ORDER BY n DESC, {col} "
                "LIMIT ?",
                [start, word, max(1, int(limit))],
            )
            out += [(kind, str(v), str(cc or ""), int(n)) for v, cc, n in rows]
        if len(term) == 2:
            rows = self._query(
                "SELECT country, COUNT(*) FROM companies WHERE country = ? GROUP BY country",
                [term.upper()],
            )
            out += [("country", str(v), str(v), int(n)) for v, n in rows]
        return sorted(out, key=lambda r: -r[3])[: max(1, int(limit))]

    def _query(self, sql: str, params: list[Any]) -> list[tuple]:
        cur = self._con.cursor()  # one cursor per call: safe across FastAPI worker threads
        try:
            return cur.execute(sql, params).fetchall()
        finally:
            cur.close()

    # -- CompanySearch API -----------------------------------------------------------------

    def search_companies(
        self, filters: CompanyFilters, page: int = 1, per_page: int = 25
    ) -> Page[Company]:
        page = max(1, int(page))
        per_page = max(1, min(int(per_page), 500))
        where, params = self._where(filters)
        total = self._query(f"SELECT COUNT(*) FROM companies{where}", params)[0][0]
        rows = self._query(
            f"SELECT {_COLS} FROM companies{where} ORDER BY {_ORDER} LIMIT ? OFFSET ?",
            [*params, per_page, (page - 1) * per_page],
        )
        return Page(
            items=[self._to_company(r) for r in rows], total=total, page=page, per_page=per_page
        )

    def get_company(self, company_id: str) -> Company | None:
        rows = self._query(f"SELECT {_COLS} FROM companies WHERE id = ? LIMIT 1", [company_id])
        return self._to_company(rows[0]) if rows else None

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
        rows = self._query(
            f"SELECT {_COLS} FROM companies WHERE {' AND '.join(conds)} "
            f"ORDER BY {', '.join(order)} LIMIT ?",
            [*params, *order_params, max(0, int(limit))],
        )
        return [self._to_company(r) for r in rows]

    def facet_counts(self, facet: str, filters: CompanyFilters, limit: int = 10) -> dict[str, int]:
        col = FACETS.get(facet)
        if col is None:
            raise ValueError(f"unknown facet: {facet!r}")
        where, params = self._where(filters, skip=facet)
        where += (" AND " if where else " WHERE ") + f"{col} IS NOT NULL"
        rows = self._query(
            f"SELECT {col}, COUNT(*) AS n FROM companies{where} "
            f"GROUP BY {col} ORDER BY n DESC, {col} LIMIT ?",
            [*params, max(1, int(limit))],
        )
        return {str(k): int(n) for k, n in rows}

    def pack_info(self) -> list[dict]:
        """One dict per loaded pack file: {"file", "rows", "sources", "built_at"}."""
        info = []
        for f in self.files:
            rows = self._query("SELECT num_rows FROM parquet_file_metadata(?)", [str(f)])
            kv = {
                _txt(k): _txt(v)
                for k, v in self._query("SELECT key, value FROM parquet_kv_metadata(?)", [str(f)])
            }
            info.append(
                {
                    "file": str(f),
                    "rows": int(sum(r[0] for r in rows)),
                    "sources": [x for x in kv.get("sources", "").split(",") if x],
                    "built_at": kv.get("built_at"),
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
        )
