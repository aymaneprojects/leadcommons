"""Conversions between domain dataclasses and the stored form (JSON dicts, ISO datetimes)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from leadcommons.models import Company, EmailCandidate, EmailStatus, Person, Provenance, Seniority
from leadcommons.store.tables import EmailRow, PersonRow


def utc(value: datetime | None) -> datetime | None:
    """Timezone-aware UTC. SQLite returns naive datetimes; those are stored as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    converted = utc(value)
    return converted.isoformat() if converted else None


def _parse(value: str | None) -> datetime | None:
    return utc(datetime.fromisoformat(value)) if value else None


def provenance_to_dict(p: Provenance) -> dict[str, Any]:
    return {
        "source": p.source,
        "licence": p.licence,
        "url": p.url,
        "fetched_at": _iso(p.fetched_at),
    }


def provenance_from_dict(d: dict[str, Any]) -> Provenance:
    kwargs: dict[str, Any] = {"source": d["source"], "licence": d["licence"], "url": d.get("url")}
    fetched = _parse(d.get("fetched_at"))
    if fetched is not None:
        kwargs["fetched_at"] = fetched
    return Provenance(**kwargs)


def company_to_dict(c: Company) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "domain": c.domain,
        "legal_name": c.legal_name,
        "country": c.country,
        "region": c.region,
        "city": c.city,
        "industry": c.industry,
        "industry_label": c.industry_label,
        "size_range": c.size_range,
        "founded_year": c.founded_year,
        "registry_ids": dict(c.registry_ids),
        "links": dict(c.links),
        "registry": c.registry,
        "company_number": c.company_number,
        "status": c.status,
        "legal_form": c.legal_form,
        "address": c.address,
        "postal_code": c.postal_code,
        "phone": c.phone,
        "employees": c.employees,
        "revenue": c.revenue,
        "revenue_currency": c.revenue_currency,
        "revenue_year": c.revenue_year,
        "sources": [provenance_to_dict(p) for p in c.sources],
    }


def company_from_dict(d: dict[str, Any]) -> Company:
    return Company(
        id=d["id"],
        name=d["name"],
        domain=d.get("domain"),
        legal_name=d.get("legal_name"),
        country=d.get("country"),
        region=d.get("region"),
        city=d.get("city"),
        industry=d.get("industry"),
        industry_label=d.get("industry_label"),
        size_range=d.get("size_range"),
        founded_year=d.get("founded_year"),
        registry_ids=dict(d.get("registry_ids") or {}),
        links=dict(d.get("links") or {}),
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
        sources=[provenance_from_dict(x) for x in d.get("sources") or []],
    )


def person_from_row(row: PersonRow) -> Person:
    return Person(
        id=row.id,
        full_name=row.full_name,
        company_domain=row.company_domain,
        company_id=row.company_id,
        title=row.title,
        seniority=Seniority(row.seniority) if row.seniority else None,
        department=row.department,
        source_urls=list(row.source_urls or []),
        first_seen=utc(row.first_seen),  # type: ignore[arg-type]
        last_seen=utc(row.last_seen),  # type: ignore[arg-type]
    )


def email_from_row(row: EmailRow) -> EmailCandidate:
    return EmailCandidate(
        address=row.address,
        status=EmailStatus(row.status),
        confidence=row.confidence,
        pattern=row.pattern,
        checks=dict(row.checks or {}),
        checked_at=utc(row.checked_at),  # type: ignore[arg-type]
    )
