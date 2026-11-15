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
    # Places: countries, regions and cities are alternatives (a company in any of them matches).
    countries: list[str] = field(default_factory=list)  # ISO alpha-2
    regions: list[str] = field(default_factory=list)  # case-insensitive
    industries: list[str] = field(default_factory=list)  # ISIC codes
    size_ranges: list[str] = field(default_factory=list)  # values from SIZE_RANGES
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)  # case-insensitive
    # Exclusions. A record with no country / industry never passes a country / industry
    # exclusion; a record with no region or city passes a region or city exclusion.
    exclude_countries: list[str] = field(default_factory=list)
    exclude_regions: list[str] = field(default_factory=list)
    exclude_cities: list[str] = field(default_factory=list)
    exclude_industries: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    keyword_mode: str = "any"  # "any": one keyword is enough; "all": every keyword must match
    keyword_fields: str = "all"  # "all" | "name" (name, legal name, domain) | "industry" (label)


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
        """Counts per value for facet in {"country", "industry", "size_range", "region",
        "city"} (region and city are optional for other backends)."""
        ...


# Which text fields a keyword is looked up in (CompanyFilters.keyword_fields).
KEYWORD_FIELDS: dict[str, tuple[str, ...]] = {
    "all": ("name", "legal_name", "domain", "industry_label"),
    "name": ("name", "legal_name", "domain"),
    "industry": ("industry_label",),
}


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
    "siren",  # French SIREN (appended in v0.2; absent from older packs)
    "cik",  # SEC CIK, zero-padded to 10 digits (appended in v0.2)
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
