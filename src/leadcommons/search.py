"""Search contract shared by the data pack backend, the local store and the web UI.

SHARED CONTRACT: owners implement these protocols; the UI depends only on this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from leadcommons.models import Company, EmailStatus, Person, Seniority


@dataclass
class CompanyFilters:
    text: str | None = None  # free text over name / legal name / domain
    countries: list[str] = field(default_factory=list)  # ISO alpha-2
    regions: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)  # ISIC codes
    size_ranges: list[str] = field(default_factory=list)  # values from SIZE_RANGES
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)


@dataclass
class PeopleFilters:
    text: str | None = None  # free text over name / title
    titles: list[str] = field(default_factory=list)
    seniorities: list[Seniority] = field(default_factory=list)
    email_statuses: list[EmailStatus] = field(default_factory=list)
    company: CompanyFilters = field(default_factory=CompanyFilters)


@dataclass
class Page[T]:
    items: list[T]
    total: int
    page: int = 1
    per_page: int = 25


class CompanySearch(Protocol):
    """Read-only search over the shared company data packs (DuckDB over Parquet)."""

    def search_companies(
        self, filters: CompanyFilters, page: int = 1, per_page: int = 25
    ) -> Page[Company]: ...

    def get_company(self, company_id: str) -> Company | None: ...

    def facet_counts(self, facet: str, filters: CompanyFilters, limit: int = 10) -> dict[str, int]:
        """Counts per value for facet in {"country", "industry", "size_range"}."""
        ...


# Parquet columns of a company data pack. One row per company.
PACK_COLUMNS: tuple[str, ...] = (
    "id",
    "name",
    "legal_name",
    "domain",
    "country",
    "region",
    "city",
    "industry",
    "industry_label",
    "size_range",
    "founded_year",
    "lei",
    "wikidata_id",
    "website",
    "sources_json",  # JSON list of {"source","licence","url","fetched_at"}
)


__all__ = [
    "CompanyFilters",
    "PeopleFilters",
    "Page",
    "CompanySearch",
    "PACK_COLUMNS",
    "Company",
    "Person",
]
