"""SQLAlchemy tables of the local user database. Portable types only (SQLite and PostgreSQL)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CompanyRow(Base):
    """Company copies saved by the user. `data` holds the full Company as a dict."""

    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    country: Mapped[str | None] = mapped_column(String(8), index=True)
    region: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(String(16), index=True)
    industry_label: Mapped[str | None] = mapped_column(Text)
    size_range: Mapped[str | None] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonRow(Base):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    company_domain: Mapped[str | None] = mapped_column(String(255), index=True)
    company_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    seniority: Mapped[str | None] = mapped_column(String(32), index=True)
    department: Mapped[str | None] = mapped_column(Text)
    source_urls: Mapped[list[str]] = mapped_column(JSON)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EmailRow(Base):
    __tablename__ = "emails"
    __table_args__ = (UniqueConstraint("person_id", "address", name="uq_emails_person_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("people.id", ondelete="CASCADE"), index=True
    )
    address: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    pattern: Mapped[str | None] = mapped_column(Text)
    checks: Mapped[dict[str, str]] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ListRow(Base):
    __tablename__ = "lists"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ListItemRow(Base):
    __tablename__ = "list_items"

    list_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("lists.id", ondelete="CASCADE"), primary_key=True
    )
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 1-based insertion order inside the list; keeps "added order" exact even for one batch.
    position: Mapped[int] = mapped_column(Integer, index=True)


class SavedSearchRow(Base):
    __tablename__ = "saved_searches"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    filters_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SuppressionRow(Base):
    """Opt-out list. Only the sha256 of the normalised value is stored, never the value."""

    __tablename__ = "suppression"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
