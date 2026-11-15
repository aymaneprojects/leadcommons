"""Saved personas: named sets of job-title filters for the People search. Owner: filters agent.

A persona is what the "Save as new persona" button stores: include/exclude titles (or a boolean
title expression), management levels and departments. The Persona filter of the People screen
lists them; ticking one shows the people who match it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import DateTime, String, Text, create_engine, delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from leadcommons.models import utcnow
from leadcommons.store.tables import Base

# The filter keys a persona keeps (same names as the People page query string).
PERSONA_KEYS: tuple[str, ...] = (
    "title",
    "xtitle",
    "similar",
    "tmode",
    "texpr",
    "seniority",
    "dept",
)


class PersonaRow(Base):
    __tablename__ = "search_personas"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    filters_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


@dataclass
class Persona:
    id: str
    name: str
    filters: dict[str, Any] = field(default_factory=dict)


def clean_filters(raw: dict[str, Any]) -> dict[str, Any]:
    """Only the persona keys, as lists of strings (similar, tmode and texpr as strings)."""
    out: dict[str, Any] = {}
    for key in PERSONA_KEYS:
        value = raw.get(key)
        if key in ("tmode", "texpr"):
            text = str(value or "").strip()[:300]
            if text:
                out[key] = text
        elif key == "similar":
            out[key] = bool(value) if isinstance(value, bool) else str(value) != "0"
        else:
            vals = value if isinstance(value, list | tuple) else ([value] if value else [])
            vals = [str(v).strip()[:120] for v in vals if str(v).strip()][:50]
            if vals:
                out[key] = vals
    return out


_READY: WeakKeyDictionary[Engine, bool] = WeakKeyDictionary()
_FALLBACK: WeakKeyDictionary[Any, Engine] = WeakKeyDictionary()


def engine_for(store: Any) -> Engine:
    """The store's engine; test doubles without one get a private in-memory database."""
    engine = getattr(store, "engine", None)
    if engine is None:
        try:
            engine = _FALLBACK.get(store)
        except TypeError:
            engine = None
        if engine is None:
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
            try:
                _FALLBACK[store] = engine
            except TypeError:
                pass
    try:
        ready = engine in _READY
    except TypeError:
        ready = False
    if not ready:
        PersonaRow.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]
        try:
            _READY[engine] = True
        except TypeError:
            pass
    return engine


class PersonaRepo:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @staticmethod
    def _out(row: PersonaRow) -> Persona:
        try:
            filters = json.loads(row.filters_json)
        except ValueError:
            filters = {}
        return Persona(id=row.id, name=row.name, filters=clean_filters(filters))

    def list(self) -> list[Persona]:
        with self._sessions() as s:
            rows = s.scalars(select(PersonaRow).order_by(PersonaRow.created_at, PersonaRow.id))
            return [self._out(r) for r in rows]

    def get(self, persona_id: str) -> Persona | None:
        with self._sessions() as s:
            row = s.get(PersonaRow, persona_id)
            return self._out(row) if row else None

    def create(self, name: str, filters: dict[str, Any]) -> str:
        persona_id = uuid.uuid4().hex[:12]
        with self._sessions.begin() as s:
            s.add(
                PersonaRow(
                    id=persona_id,
                    name=name.strip()[:80],
                    filters_json=json.dumps(clean_filters(filters)),
                    created_at=utcnow(),
                )
            )
        return persona_id

    def delete(self, persona_id: str) -> bool:
        with self._sessions.begin() as s:
            return bool(s.execute(delete(PersonaRow).where(PersonaRow.id == persona_id)).rowcount)  # type: ignore[attr-defined]


def personas_for(store: Any) -> PersonaRepo:
    return PersonaRepo(engine_for(store))
