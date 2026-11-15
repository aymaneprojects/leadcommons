"""The shared contract for bulk register sources."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from leadcommons.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    shard_select,
    write_shard,
)
from leadcommons.packs.duck import DuckDBCompanySearch
from leadcommons.search import PACK_COLUMNS


def test_shard_select_writes_every_pack_column_in_order(tmp_path: Path) -> None:
    con = duckdb.connect()
    con.execute("CREATE TABLE raw AS SELECT 'Acme' AS n, '0123' AS num, 12 AS staff")
    src = BulkSource(
        "demo", "Demo", "CC0-1.0", "https://x", "https://x", None, lambda o: ShardStats("demo")
    )
    sql = shard_select(
        {
            "id": "'co_demo_' || num",
            "name": "n",
            "country": "'GB'",
            "registry": "'GB-CH'",
            "company_number": "num",
            "employees": "staff",
            "sources_json": f"'{provenance_json(src)}'",
        },
        "raw",
    )
    rows = write_shard(con, sql, tmp_path / "demo.parquet")
    assert rows == 1
    cols = [
        r[0]
        for r in con.execute(f"DESCRIBE SELECT * FROM '{tmp_path / 'demo.parquet'}'").fetchall()
    ]
    assert cols == list(PACK_COLUMNS)
    co = DuckDBCompanySearch(tmp_path).get_company("co_demo_0123")
    assert (
        co and co.company_number == "0123" and co.employees == 12 and co.sources[0].source == "demo"
    )


def test_shard_select_rejects_unknown_columns() -> None:
    with pytest.raises(ValueError):
        shard_select({"nope": "1"}, "raw")


def test_build_options_defaults(tmp_path: Path) -> None:
    opts = BuildOptions(work_dir=tmp_path, out_dir=tmp_path)
    assert opts.limit is None and opts.countries is None
