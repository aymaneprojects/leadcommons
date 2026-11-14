"""DuckDB-backed company search over Parquet data packs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq

from leadcommons.models import Company, Provenance
from leadcommons.packs.domains import normalize_domain
from leadcommons.search import PACK_COLUMNS, CompanyFilters, Page

FACETS = {"country": "country", "industry": "industry", "size_range": "size_range"}
_COLS = ", ".join(PACK_COLUMNS)
_ORDER = "(domain IS NULL), (size_range IS NULL), lower(name), id"


def _like(term: str) -> str:
    """Escape LIKE wildcards in a user term and wrap it for substring match."""
    esc = term.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


def _lit(path: str) -> str:
    return "'" + path.replace("'", "''") + "'"


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
            self._con.execute(
                f"CREATE VIEW companies AS SELECT * FROM read_parquet([{lst}], union_by_name=true)"
            )
        else:
            cols = ", ".join(
                f"CAST(NULL AS {'INTEGER' if c == 'founded_year' else 'VARCHAR'}) AS {c}"
                for c in PACK_COLUMNS
            )
            self._con.execute(f"CREATE VIEW companies AS SELECT {cols} WHERE false")

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
        if f.text and f.text.strip():
            like = _like(f.text.strip())
            conds.append(
                "(lower(name) LIKE ? ESCAPE '\\' OR lower(legal_name) LIKE ? ESCAPE '\\' "
                "OR lower(domain) LIKE ? ESCAPE '\\')"
            )
            params += [like] * 3
        kws = [k.strip() for k in f.keywords if k and k.strip()]
        if kws:
            parts = []
            for k in kws:
                like = _like(k)
                parts.append(
                    "(lower(name) LIKE ? ESCAPE '\\' OR lower(legal_name) LIKE ? ESCAPE '\\' "
                    "OR lower(domain) LIKE ? ESCAPE '\\' "
                    "OR lower(industry_label) LIKE ? ESCAPE '\\')"
                )
                params += [like] * 4
            conds.append("(" + " OR ".join(parts) + ")")
        if f.countries and skip != "country":
            conds.append(f"country IN ({','.join('?' * len(f.countries))})")
            params += [c.strip().upper() for c in f.countries]
        if f.regions:
            conds.append(f"lower(region) IN ({','.join('?' * len(f.regions))})")
            params += [r.strip().lower() for r in f.regions]
        if f.industries and skip != "industry":
            conds.append(f"industry IN ({','.join('?' * len(f.industries))})")
            params += [i.strip().upper() for i in f.industries]
        if f.size_ranges and skip != "size_range":
            conds.append(f"size_range IN ({','.join('?' * len(f.size_ranges))})")
            params += list(f.size_ranges)
        if f.domains:
            conds.append(f"domain IN ({','.join('?' * len(f.domains))})")
            params += [normalize_domain(d) or d.strip().lower() for d in f.domains]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

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
            pf = pq.ParquetFile(f)
            meta = {k.decode(): v.decode() for k, v in (pf.schema_arrow.metadata or {}).items()}
            sources = [s for s in meta.get("sources", "").split(",") if s]
            info.append(
                {
                    "file": str(f),
                    "rows": pf.metadata.num_rows,
                    "sources": sources,
                    "built_at": meta.get("built_at"),
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
            registry_ids={
                k: d[c] for k, c in (("lei", "lei"), ("wikidata", "wikidata_id")) if d[c]
            },
            links={"website": d["website"]} if d["website"] else {},
            sources=sources,
        )
