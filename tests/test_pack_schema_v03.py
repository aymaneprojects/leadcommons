"""Pack schema v0.3: new company fields survive a pack round trip and old packs still load."""

from __future__ import annotations

from pathlib import Path

import duckdb

from leadcommons.models import Company
from leadcommons.packs.builder import company_to_row, pack_schema, write_parquet
from leadcommons.packs.duck import DuckDBCompanySearch
from leadcommons.search import PACK_COLUMNS, CompanyFilters
from leadcommons.store.codec import company_from_dict, company_to_dict

FULL = Company(
    id="co_test1",
    name="Example Ltd",
    country="GB",
    registry="GB-CH",
    company_number="01234567",
    status="active",
    legal_form="Private Limited Company",
    address="1 Example Street",
    postal_code="EC1A 1AA",
    phone="+442071234567",
    employees=42,
    revenue=1_250_000.0,
    revenue_currency="GBP",
    revenue_year=2025,
)


def test_new_fields_round_trip_through_a_pack(tmp_path: Path) -> None:
    write_parquet(tmp_path / "companies-1.parquet", [company_to_row(FULL)], {})
    got = DuckDBCompanySearch(tmp_path).get_company("co_test1")
    assert got is not None
    for field in (
        "registry",
        "company_number",
        "status",
        "legal_form",
        "address",
        "postal_code",
        "phone",
        "employees",
        "revenue",
        "revenue_currency",
        "revenue_year",
    ):
        assert getattr(got, field) == getattr(FULL, field), field


def test_schema_types() -> None:
    types = {f.name: str(f.type) for f in pack_schema()}
    assert list(types) == list(PACK_COLUMNS)
    assert types["employees"] == "int32" and types["revenue_year"] == "int32"
    assert types["revenue"] == "double" and types["phone"] == "string"


def test_old_packs_without_new_columns_still_load(tmp_path: Path) -> None:
    old = tmp_path / "companies-old.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT 'co_old' AS id, 'Old Co' AS name, 'FR' AS country) TO '{old}' (FORMAT PARQUET)"
    )
    search = DuckDBCompanySearch(tmp_path)
    page = search.search_companies(CompanyFilters(text="old"), per_page=5)
    assert page.items and page.items[0].employees is None and page.items[0].registry is None


def test_store_keeps_the_new_fields() -> None:
    assert company_from_dict(company_to_dict(FULL)) == FULL
