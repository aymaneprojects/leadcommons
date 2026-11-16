"""Company insights storage: one JSON document per company. Owner: data agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Engine, String
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from pepite.models import utcnow
from pepite.store.codec import utc
from pepite.store.tables import Base


class CompanyInsightsRow(Base):
    __tablename__ = "company_insights"

    company_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InsightsRepo:
    """Read and write stored CompanyInsights (imported lazily to keep the store import-light)."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def get(self, company_id: str):  # -> CompanyInsights | None
        from pepite.insights.model import CompanyInsights

        with self._sessions() as s:
            row = s.get(CompanyInsightsRow, company_id)
            if row is None:
                return None
            insights = CompanyInsights.from_dict(row.data)
            insights.refreshed_at = utc(row.refreshed_at) or insights.refreshed_at
            return insights

    def save(self, insights) -> None:  # insights: CompanyInsights
        with self._sessions.begin() as s:
            row = s.get(CompanyInsightsRow, insights.company_id)
            if row is None:
                row = CompanyInsightsRow(company_id=insights.company_id)
                s.add(row)
            row.data = insights.to_dict()
            row.refreshed_at = insights.refreshed_at

    def delete(self, company_id: str) -> None:
        with self._sessions.begin() as s:
            row = s.get(CompanyInsightsRow, company_id)
            if row is not None:
                s.delete(row)
