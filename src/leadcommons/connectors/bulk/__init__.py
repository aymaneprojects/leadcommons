"""Bulk sources: official registers downloaded whole and turned into company shards.

SHARED CONTRACT (lead): every source module in this package defines one `BulkSource` and
registers it with `register(...)`. The packs workflow runs, per source:

    leadcommons pack fetch-source <name> --out shards/ [--limit N] [--country FR ...]

then `leadcommons pack merge shards/ --out packs-out/` links and dedupes the shards into one
Parquet file per country (see packs/merge.py).

A shard is a Parquet file with exactly `PACK_COLUMNS` (leadcommons.search), in that order, one row
per company, active companies only unless the source says otherwise. Fill the linking keys the
source has (lei, siren, cik, registry + company_number, wikidata_id, domain, postal_code) so the
merge can join sources. `sources_json` holds one provenance entry for this source. Never write
names of natural persons into a shard: packs hold companies only.

Downloads can be large (GB): stream to disk in `work_dir`, transform with DuckDB set operations
(no per-row Python loops over millions of rows), and keep memory bounded.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from leadcommons.search import PACK_COLUMNS, PACK_FLOAT_COLUMNS, PACK_INT_COLUMNS


@dataclass
class BuildOptions:
    work_dir: Path  # raw downloads; may be deleted after the build
    out_dir: Path  # shards are written here
    limit: int | None = None  # max rows per shard (test runs)
    countries: frozenset[str] | None = None  # keep only these ISO codes (None = all)
    contact: str | None = None  # declared contact for sources that ask for one (SEC)


@dataclass
class ShardStats:
    source: str
    rows: int = 0
    files: list[Path] = field(default_factory=list)
    skipped: int = 0  # rows dropped (inactive, no name, outside countries...)
    notes: str = ""


@dataclass(frozen=True)
class BulkSource:
    name: str  # cli id, e.g. "companies_house"
    title: str  # "UK Companies House"
    licence: str  # e.g. "OGL-UK-3.0", "CC0-1.0", "etalab-2.0", "NLOD-2.0", "CC-BY-4.0"
    licence_url: str
    homepage: str
    countries: frozenset[str] | None  # None: worldwide (GLEIF, Wikidata)
    build: Callable[[BuildOptions], ShardStats]
    attribution: str = ""  # sentence for ATTRIBUTION.md when the licence asks for one


SOURCES: dict[str, BulkSource] = {}


def register(source: BulkSource) -> BulkSource:
    SOURCES[source.name] = source
    return source


def provenance_json(
    source: BulkSource, url: str | None = None, fetched_at: datetime | None = None
) -> str:
    """The `sources_json` value for every row of a shard from this source."""
    when = (fetched_at or datetime.now(UTC)).replace(microsecond=0).isoformat()
    return json.dumps(
        [
            {
                "source": source.name,
                "licence": source.licence,
                "url": url or source.homepage,
                "fetched_at": when,
            }
        ]
    )


def _sql_type(col: str) -> str:
    if col in PACK_INT_COLUMNS:
        return "INTEGER"
    return "DOUBLE" if col in PACK_FLOAT_COLUMNS else "VARCHAR"


def shard_select(exprs: Mapping[str, str], from_sql: str) -> str:
    """SELECT with every PACK_COLUMN in order: given SQL expressions, NULL for the others.

    `exprs` maps a pack column to a DuckDB SQL expression over `from_sql`.
    """
    unknown = set(exprs) - set(PACK_COLUMNS)
    if unknown:
        raise ValueError(f"not pack columns: {sorted(unknown)}")
    cols = [
        f"CAST({exprs[c]} AS {_sql_type(c)}) AS {c}"
        if c in exprs
        else f"CAST(NULL AS {_sql_type(c)}) AS {c}"
        for c in PACK_COLUMNS
    ]
    return "SELECT " + ",\n  ".join(cols) + f"\nFROM {from_sql}"


def write_shard(con, select_sql: str, path: Path) -> int:
    """COPY a shard_select() query to Parquet (zstd). Returns the number of rows written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    target = str(path).replace("'", "''")
    con.execute(f"COPY ({select_sql}) TO '{target}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    return int(con.execute(f"SELECT count(*) FROM read_parquet('{target}')").fetchone()[0])


def load_all() -> dict[str, BulkSource]:
    """Import every source module so they register themselves."""
    import importlib
    import pkgutil

    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    return SOURCES


__all__ = [
    "SOURCES",
    "BuildOptions",
    "BulkSource",
    "ShardStats",
    "load_all",
    "provenance_json",
    "register",
    "shard_select",
    "write_shard",
]
