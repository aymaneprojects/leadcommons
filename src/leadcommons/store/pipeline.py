"""Deals, meetings, calls, conversations/notes, activity timeline. Owner: pipeline agent.

Define tables on leadcommons.store.tables.Base and a repository class that takes the
shared Engine (Store.engine). Tables are created by Store() at start-up.

ActivityRepo is a SHARED CONTRACT used by other features (engage logs emails and tasks,
workflows log their actions): keep its method names and arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, Integer, String, Text, or_, select
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from leadcommons.models import utcnow
from leadcommons.store.codec import utc
from leadcommons.store.tables import Base


class ActivityRow(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    person_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@dataclass
class Activity:
    id: int
    kind: str  # e.g. "note", "call", "meeting", "email_sent", "email_reply", "task", "deal"
    summary: str
    person_id: str | None = None
    company_id: str | None = None
    deal_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utcnow)


class ActivityRepo:
    """Timeline of everything that happened with a person, company or deal."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def add(
        self,
        kind: str,
        summary: str,
        *,
        person_id: str | None = None,
        company_id: str | None = None,
        deal_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        with self._sessions.begin() as s:
            row = ActivityRow(
                kind=kind,
                summary=summary[:2000],
                person_id=person_id,
                company_id=company_id,
                deal_id=deal_id,
                meta=dict(meta or {}),
                created_at=utcnow(),
            )
            s.add(row)
            s.flush()
            return row.id

    def list_for(
        self,
        *,
        person_id: str | None = None,
        company_id: str | None = None,
        deal_id: str | None = None,
        kinds: list[str] | None = None,
        limit: int = 50,
    ) -> list[Activity]:
        """Newest first. With several ids, returns activities matching any of them."""
        conds = []
        if person_id:
            conds.append(ActivityRow.person_id == person_id)
        if company_id:
            conds.append(ActivityRow.company_id == company_id)
        if deal_id:
            conds.append(ActivityRow.deal_id == deal_id)
        q = select(ActivityRow)
        if conds:
            q = q.where(or_(*conds))
        if kinds:
            q = q.where(ActivityRow.kind.in_(kinds))
        q = q.order_by(ActivityRow.created_at.desc(), ActivityRow.id.desc()).limit(limit)
        with self._sessions() as s:
            return [
                Activity(
                    id=r.id,
                    kind=r.kind,
                    summary=r.summary,
                    person_id=r.person_id,
                    company_id=r.company_id,
                    deal_id=r.deal_id,
                    meta=dict(r.meta or {}),
                    created_at=utc(r.created_at) or utcnow(),
                )
                for r in s.scalars(q)
            ]
