"""Core domain records. SHARED CONTRACT: do not change field names without updating every module.

Companies are shared (company-level, from open data packs). People and emails are personal
data: they live only in the user's local database and are never synced anywhere.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class EmailStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    CATCH_ALL = "catch_all"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    OWNER = "owner"
    FOUNDER = "founder"
    C_SUITE = "c_suite"
    PARTNER = "partner"
    VP = "vp"
    HEAD = "head"
    DIRECTOR = "director"
    MANAGER = "manager"
    SENIOR = "senior"
    ENTRY = "entry"
    INTERN = "intern"


# Employee-count buckets used everywhere (filters, packs, UI chips).
SIZE_RANGES: tuple[str, ...] = (
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1000",
    "1001-5000",
    "5001-10000",
    "10001+",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_id(prefix: str, *parts: str) -> str:
    """Stable id from natural keys, e.g. make_id("co", "lei", "5299...")."""
    digest = hashlib.sha1("|".join(p.strip().lower() for p in parts).encode()).hexdigest()[:16]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class Provenance:
    source: str  # connector name, e.g. "wikidata", "gleif", "website"
    licence: str  # SPDX-style id, e.g. "CC0-1.0", "ODbL-1.0", "n/a"
    url: str | None = None
    fetched_at: datetime = field(default_factory=utcnow)


@dataclass
class Company:
    id: str  # make_id("co", ...)
    name: str
    domain: str | None = None  # bare registrable domain, lowercase, e.g. "example.com"
    legal_name: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2, uppercase
    region: str | None = None
    city: str | None = None
    industry: str | None = None  # ISIC Rev.4 code (section letter or 2-digit division)
    industry_label: str | None = None
    size_range: str | None = None  # one of SIZE_RANGES
    founded_year: int | None = None
    registry_ids: dict[str, str] = field(default_factory=dict)  # {"lei": ..., "wikidata": "Q..."}
    links: dict[str, str] = field(default_factory=dict)  # {"website": ..., "linkedin": ...}
    sources: list[Provenance] = field(default_factory=list)


@dataclass
class Person:
    id: str  # make_id("pe", domain, full_name)
    full_name: str
    company_domain: str | None = None
    company_id: str | None = None
    title: str | None = None
    seniority: Seniority | None = None
    department: str | None = None
    source_urls: list[str] = field(default_factory=list)
    first_seen: datetime = field(default_factory=utcnow)
    last_seen: datetime = field(default_factory=utcnow)


@dataclass
class EmailCandidate:
    address: str
    status: EmailStatus = EmailStatus.UNKNOWN
    confidence: int = 0  # 0-100
    pattern: str | None = None  # e.g. "{first}.{last}"
    checks: dict[str, str] = field(default_factory=dict)  # {"syntax": "ok", "mx": "ok", ...}
    checked_at: datetime = field(default_factory=utcnow)
