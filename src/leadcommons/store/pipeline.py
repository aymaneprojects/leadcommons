"""Deals, meetings, calls, conversations/notes, activity timeline. Owner: pipeline agent.

Define tables on leadcommons.store.tables.Base and a repository class that takes the
shared Engine (Store.engine). Tables are created by Store() at start-up.

ActivityRepo is a SHARED CONTRACT used by other features (engage logs emails and tasks,
workflows log their actions): keep its method names and arguments.

`Pipeline(engine)` bundles everything this feature owns: deal stages, deals, meetings, calls,
phone numbers kept by hand, the booking-link setting and an ActivityRepo (`.activity`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Engine,
    Float,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from leadcommons.models import utcnow
from leadcommons.store.codec import utc
from leadcommons.store.tables import Base

# ---------------------------------------------------------------- activity (shared contract)


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


def _activity(r: ActivityRow) -> Activity:
    return Activity(
        id=r.id,
        kind=r.kind,
        summary=r.summary,
        person_id=r.person_id,
        company_id=r.company_id,
        deal_id=r.deal_id,
        meta=dict(r.meta or {}),
        created_at=utc(r.created_at) or utcnow(),
    )


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
            return [_activity(r) for r in s.scalars(q)]

    # -- additions used by the conversations page (the methods above are the contract)

    def search(
        self,
        *,
        text: str | None = None,
        kinds: list[str] | None = None,
        person_id: str | None = None,
        company_id: str | None = None,
        limit: int = 500,
    ) -> list[Activity]:
        """Newest first. `text` matches the summary; person/company must both match if given."""
        q = select(ActivityRow)
        if person_id:
            q = q.where(ActivityRow.person_id == person_id)
        if company_id:
            q = q.where(ActivityRow.company_id == company_id)
        if kinds:
            q = q.where(ActivityRow.kind.in_(kinds))
        if text:
            like = f"%{text.strip().lower()}%"
            q = q.where(func.lower(ActivityRow.summary).like(like))
        q = q.order_by(ActivityRow.created_at.desc(), ActivityRow.id.desc()).limit(limit)
        with self._sessions() as s:
            return [_activity(r) for r in s.scalars(q)]

    def get(self, activity_id: int) -> Activity | None:
        with self._sessions() as s:
            row = s.get(ActivityRow, activity_id)
            return _activity(row) if row else None

    def delete(self, activity_id: int) -> bool:
        with self._sessions.begin() as s:
            row = s.get(ActivityRow, activity_id)
            if row is None:
                return False
            s.delete(row)
            return True


# ---------------------------------------------------------------- tables


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class DealStageRow(Base):
    __tablename__ = "deal_stages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="open")  # open | won | lost
    probability: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class DealRow(Base):
    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    stage_id: Mapped[str] = mapped_column(String(32), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    company_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    person_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    person_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    probability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeetingRow(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    start: Mapped[datetime] = mapped_column(DateTime, index=True)  # wall-clock time, no zone
    duration_min: Mapped[int] = mapped_column(Integer, default=30)
    person_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    person_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    deal_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    agenda: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CallRow(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    person_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    person_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    deal_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), index=True)
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    followup_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PersonPhoneRow(Base):
    """Phone numbers typed in by hand (the person record itself has no phone field)."""

    __tablename__ = "pipeline_phones"

    person_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    phone: Mapped[str] = mapped_column(String(64))


class PipelineSettingRow(Base):
    __tablename__ = "pipeline_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------- value objects

DEFAULT_STAGES: tuple[tuple[str, str, int], ...] = (
    ("Qualification", "open", 10),
    ("Discovery", "open", 25),
    ("Proposal", "open", 50),
    ("Negotiation", "open", 75),
    ("Won", "won", 100),
    ("Lost", "lost", 0),
)
STAGE_KINDS = ("open", "won", "lost")
CALL_OUTCOMES = ("connected", "no_answer", "voicemail", "wrong_number", "busy")
MEETING_STATUSES = ("scheduled", "done", "no_show", "cancelled")


@dataclass
class Stage:
    id: str
    name: str
    position: int
    kind: str = "open"
    probability: int = 0
    archived: bool = False


@dataclass
class Deal:
    id: str
    name: str
    stage_id: str
    position: int = 0
    company_id: str | None = None
    company_name: str | None = None
    person_id: str | None = None
    person_name: str | None = None
    amount: float | None = None
    currency: str = "EUR"
    close_date: date | None = None
    probability: int | None = None
    source: str | None = None
    notes: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)


@dataclass
class Meeting:
    id: str
    title: str
    start: datetime
    duration_min: int = 30
    person_id: str | None = None
    person_name: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    deal_id: str | None = None
    location: str | None = None
    video_url: str | None = None
    agenda: str | None = None
    outcome: str | None = None
    status: str = "scheduled"
    created_at: datetime = field(default_factory=utcnow)

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.duration_min or 30)


@dataclass
class Call:
    id: str
    outcome: str
    person_id: str | None = None
    person_name: str | None = None
    company_id: str | None = None
    company_name: str | None = None
    deal_id: str | None = None
    phone: str | None = None
    duration_sec: int = 0
    notes: str | None = None
    followup_task_id: str | None = None
    created_at: datetime = field(default_factory=utcnow)


def _to(cls: type, row: Any) -> Any:
    names = cls.__dataclass_fields__.keys()  # type: ignore[attr-defined]
    data = {n: getattr(row, n) for n in names if hasattr(row, n)}
    for key in ("created_at", "updated_at"):
        if key in data:
            data[key] = utc(data[key]) or utcnow()
    return cls(**data)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# ---------------------------------------------------------------- facade


class Pipeline:
    """Everything the Win-deals pages store. Cheap to build: one per Store engine."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self.activity = ActivityRepo(engine)

    # ---- stages

    def _seed_stages(self) -> None:
        with self._sessions.begin() as s:
            if s.scalar(select(func.count()).select_from(DealStageRow)):
                return
            for i, (name, kind, prob) in enumerate(DEFAULT_STAGES):
                s.add(
                    DealStageRow(
                        id=new_id("st"), name=name, position=i, kind=kind, probability=prob
                    )
                )

    def list_stages(self, include_archived: bool = False) -> list[Stage]:
        self._seed_stages()
        q = select(DealStageRow).order_by(DealStageRow.position, DealStageRow.name)
        if not include_archived:
            q = q.where(DealStageRow.archived.is_(False))
        with self._sessions() as s:
            return [_to(Stage, r) for r in s.scalars(q)]

    def get_stage(self, stage_id: str) -> Stage | None:
        self._seed_stages()
        with self._sessions() as s:
            row = s.get(DealStageRow, stage_id)
            return _to(Stage, row) if row else None

    def add_stage(self, name: str, kind: str = "open", probability: int = 0) -> Stage:
        self._seed_stages()
        name = (name or "").strip()[:80]
        if not name:
            raise ValueError("A stage needs a name.")
        kind = kind if kind in STAGE_KINDS else "open"
        with self._sessions.begin() as s:
            last = s.scalar(select(func.max(DealStageRow.position))) or 0
            # new open stages go before the first closed stage so Won and Lost stay at the end
            position = last + 1
            if kind == "open":
                first_closed = s.scalar(
                    select(func.min(DealStageRow.position)).where(DealStageRow.kind != "open")
                )
                if first_closed is not None:
                    for r in s.scalars(
                        select(DealStageRow).where(DealStageRow.position >= first_closed)
                    ):
                        r.position += 1
                    position = first_closed
            row = DealStageRow(
                id=new_id("st"),
                name=name,
                position=position,
                kind=kind,
                probability=max(0, min(100, int(probability))),
            )
            s.add(row)
            s.flush()
            return _to(Stage, row)

    def update_stage(
        self,
        stage_id: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        probability: int | None = None,
    ) -> Stage | None:
        with self._sessions.begin() as s:
            row = s.get(DealStageRow, stage_id)
            if row is None:
                return None
            if name is not None and name.strip():
                row.name = name.strip()[:80]
            if kind in STAGE_KINDS:
                row.kind = str(kind)
            if probability is not None:
                row.probability = max(0, min(100, int(probability)))
            s.flush()
            return _to(Stage, row)

    def shift_stage(self, stage_id: str, delta: int) -> None:
        """Move a stage one place left (-1) or right (+1) among the active ones."""
        stages = self.list_stages()
        ids = [st.id for st in stages]
        if stage_id not in ids:
            return
        i = ids.index(stage_id)
        j = i + (1 if delta > 0 else -1)
        if not 0 <= j < len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        self.order_stages(ids)

    def order_stages(self, ids: list[str]) -> None:
        """Set the order of the listed stages (archived ones keep a place after them)."""
        with self._sessions.begin() as s:
            rows = {r.id: r for r in s.scalars(select(DealStageRow))}
            ordered = [i for i in dict.fromkeys(ids) if i in rows]
            rest = [i for i in sorted(rows, key=lambda k: rows[k].position) if i not in ordered]
            for n, sid in enumerate(ordered + rest):
                rows[sid].position = n

    def deals_in_stage(self, stage_id: str) -> int:
        with self._sessions() as s:
            return int(
                s.scalar(
                    select(func.count()).select_from(DealRow).where(DealRow.stage_id == stage_id)
                )
                or 0
            )

    def archive_stage(self, stage_id: str) -> str | None:
        """Archive a stage. Returns an error message when it cannot be done."""
        stages = self.list_stages()
        target = next((st for st in stages if st.id == stage_id), None)
        if target is None:
            return "Stage not found."
        n = self.deals_in_stage(stage_id)
        if n:
            return f"Move its {n} deal{'s' if n != 1 else ''} to another stage first."
        if target.kind == "open" and sum(1 for st in stages if st.kind == "open") <= 1:
            return "Keep at least one open stage."
        with self._sessions.begin() as s:
            row = s.get(DealStageRow, stage_id)
            if row:
                row.archived = True
        return None

    def restore_stage(self, stage_id: str) -> None:
        with self._sessions.begin() as s:
            row = s.get(DealStageRow, stage_id)
            if row:
                row.archived = False

    # ---- deals

    def create_deal(self, name: str, stage_id: str | None = None, **fields: Any) -> Deal:
        stages = self.list_stages()
        if not stages:
            raise ValueError("No deal stages.")
        stage = next((st for st in stages if st.id == stage_id), stages[0])
        name = (name or "").strip()[:200]
        if not name:
            raise ValueError("A deal needs a name.")
        with self._sessions.begin() as s:
            top = s.scalar(select(func.min(DealRow.position)).where(DealRow.stage_id == stage.id))
            row = DealRow(
                id=new_id("de"),
                name=name,
                stage_id=stage.id,
                position=(top - 1) if top is not None else 0,
            )
            self._apply_deal_fields(row, fields)
            if row.probability is None:
                row.probability = stage.probability
            s.add(row)
            s.flush()
            return _to(Deal, row)

    @staticmethod
    def _apply_deal_fields(row: DealRow, fields: dict[str, Any]) -> None:
        for key in (
            "company_id",
            "company_name",
            "person_id",
            "person_name",
            "source",
            "notes",
        ):
            if key in fields:
                setattr(row, key, _clean(fields[key]))
        if "amount" in fields:
            row.amount = fields["amount"]
        if "currency" in fields and fields["currency"]:
            row.currency = str(fields["currency"]).upper()[:3]
        if "close_date" in fields:
            row.close_date = fields["close_date"]
        if "probability" in fields:
            row.probability = fields["probability"]
        if fields.get("name"):
            row.name = str(fields["name"]).strip()[:200]
        row.updated_at = utcnow()

    def get_deal(self, deal_id: str) -> Deal | None:
        with self._sessions() as s:
            row = s.get(DealRow, deal_id)
            return _to(Deal, row) if row else None

    def update_deal(self, deal_id: str, **fields: Any) -> Deal | None:
        with self._sessions.begin() as s:
            row = s.get(DealRow, deal_id)
            if row is None:
                return None
            self._apply_deal_fields(row, fields)
            s.flush()
            return _to(Deal, row)

    def delete_deal(self, deal_id: str) -> bool:
        with self._sessions.begin() as s:
            row = s.get(DealRow, deal_id)
            if row is None:
                return False
            s.delete(row)
            return True

    def list_deals(
        self,
        *,
        text: str | None = None,
        stage_id: str | None = None,
        company_id: str | None = None,
        person_id: str | None = None,
    ) -> list[Deal]:
        q = select(DealRow)
        if stage_id:
            q = q.where(DealRow.stage_id == stage_id)
        if company_id:
            q = q.where(DealRow.company_id == company_id)
        if person_id:
            q = q.where(DealRow.person_id == person_id)
        if text:
            like = f"%{text.strip().lower()}%"
            q = q.where(
                or_(
                    func.lower(DealRow.name).like(like),
                    func.lower(func.coalesce(DealRow.company_name, "")).like(like),
                    func.lower(func.coalesce(DealRow.person_name, "")).like(like),
                )
            )
        q = q.order_by(DealRow.position, DealRow.created_at.desc())
        with self._sessions() as s:
            return [_to(Deal, r) for r in s.scalars(q)]

    def move_deal(
        self, deal_id: str, stage_id: str, position: int | None = None
    ) -> tuple[Deal, str] | None:
        """Put a deal in a stage (optionally at an index). Returns (deal, previous_stage_id)."""
        self._seed_stages()
        with self._sessions.begin() as s:
            row = s.get(DealRow, deal_id)
            stage = s.get(DealStageRow, stage_id)
            if row is None or stage is None or stage.archived:
                return None
            previous = row.stage_id
            if previous != stage_id:
                old = s.get(DealStageRow, previous)
                # keep a hand-set probability; follow the stage when it was following the old one
                if row.probability is None or (
                    old is not None and row.probability == old.probability
                ):
                    row.probability = stage.probability
                row.stage_id = stage_id
            row.updated_at = utcnow()
            column = [
                r
                for r in s.scalars(
                    select(DealRow)
                    .where(DealRow.stage_id == stage_id, DealRow.id != deal_id)
                    .order_by(DealRow.position, DealRow.created_at.desc())
                )
            ]
            index = len(column) if position is None else max(0, min(int(position), len(column)))
            column.insert(index, row)
            for n, r in enumerate(column):
                r.position = n
            s.flush()
            return _to(Deal, row), previous

    def stage_totals(self) -> dict[str, dict[str, Any]]:
        """{stage_id: {"count": n, "by_currency": {"EUR": total}}} over all deals."""
        out: dict[str, dict[str, Any]] = {}
        for d in self.list_deals():
            t = out.setdefault(d.stage_id, {"count": 0, "by_currency": {}})
            t["count"] += 1
            if d.amount:
                t["by_currency"][d.currency] = t["by_currency"].get(d.currency, 0) + d.amount
        return out

    # ---- meetings

    def create_meeting(self, title: str, start: datetime, **fields: Any) -> Meeting:
        title = (title or "").strip()[:200]
        if not title:
            raise ValueError("A meeting needs a title.")
        with self._sessions.begin() as s:
            row = MeetingRow(
                id=new_id("mt"),
                title=title,
                start=start.replace(tzinfo=None, second=0, microsecond=0),
                duration_min=max(5, min(int(fields.get("duration_min") or 30), 24 * 60)),
            )
            for key in (
                "person_id",
                "person_name",
                "company_id",
                "company_name",
                "deal_id",
                "location",
                "video_url",
                "agenda",
            ):
                setattr(row, key, _clean(fields.get(key)))
            s.add(row)
            s.flush()
            return _to(Meeting, row)

    def get_meeting(self, meeting_id: str) -> Meeting | None:
        with self._sessions() as s:
            row = s.get(MeetingRow, meeting_id)
            return _to(Meeting, row) if row else None

    def update_meeting(self, meeting_id: str, **fields: Any) -> Meeting | None:
        with self._sessions.begin() as s:
            row = s.get(MeetingRow, meeting_id)
            if row is None:
                return None
            for key in ("title", "location", "video_url", "agenda", "outcome"):
                if key in fields:
                    setattr(row, key, _clean(fields[key]) if key != "title" else fields[key])
            if fields.get("start"):
                row.start = fields["start"].replace(tzinfo=None, second=0, microsecond=0)
            if fields.get("duration_min"):
                row.duration_min = int(fields["duration_min"])
            if fields.get("status") in MEETING_STATUSES:
                row.status = fields["status"]
            s.flush()
            return _to(Meeting, row)

    def delete_meeting(self, meeting_id: str) -> bool:
        with self._sessions.begin() as s:
            row = s.get(MeetingRow, meeting_id)
            if row is None:
                return False
            s.delete(row)
            return True

    def list_meetings(
        self,
        when: str = "all",
        *,
        now: datetime | None = None,
        person_id: str | None = None,
        company_id: str | None = None,
        text: str | None = None,
    ) -> list[Meeting]:
        """when: upcoming (soonest first), past (latest first) or all. Times are wall-clock."""
        now = (now or datetime.now()).replace(tzinfo=None)
        q = select(MeetingRow)
        if person_id:
            q = q.where(MeetingRow.person_id == person_id)
        if company_id:
            q = q.where(MeetingRow.company_id == company_id)
        if text:
            like = f"%{text.strip().lower()}%"
            q = q.where(
                or_(
                    func.lower(MeetingRow.title).like(like),
                    func.lower(func.coalesce(MeetingRow.person_name, "")).like(like),
                    func.lower(func.coalesce(MeetingRow.company_name, "")).like(like),
                )
            )
        if when == "upcoming":
            q = q.where(and_(MeetingRow.start >= now, MeetingRow.status == "scheduled")).order_by(
                MeetingRow.start
            )
        elif when == "past":
            q = q.where(or_(MeetingRow.start < now, MeetingRow.status != "scheduled")).order_by(
                MeetingRow.start.desc()
            )
        else:
            q = q.order_by(MeetingRow.start.desc())
        with self._sessions() as s:
            return [_to(Meeting, r) for r in s.scalars(q)]

    # ---- calls

    def log_call(self, outcome: str, **fields: Any) -> Call:
        if outcome not in CALL_OUTCOMES:
            raise ValueError("Unknown call outcome.")
        with self._sessions.begin() as s:
            row = CallRow(
                id=new_id("ca"),
                outcome=outcome,
                duration_sec=max(0, int(fields.get("duration_sec") or 0)),
                created_at=fields.get("created_at") or utcnow(),
            )
            for key in (
                "person_id",
                "person_name",
                "company_id",
                "company_name",
                "deal_id",
                "phone",
                "notes",
                "followup_task_id",
            ):
                setattr(row, key, _clean(fields.get(key)))
            s.add(row)
            s.flush()
            return _to(Call, row)

    def list_calls(
        self,
        *,
        outcome: str | None = None,
        text: str | None = None,
        since: datetime | None = None,
        person_id: str | None = None,
        company_id: str | None = None,
        limit: int = 300,
    ) -> list[Call]:
        q = select(CallRow)
        if outcome:
            q = q.where(CallRow.outcome == outcome)
        if since:
            q = q.where(CallRow.created_at >= since)
        if person_id:
            q = q.where(CallRow.person_id == person_id)
        if company_id:
            q = q.where(CallRow.company_id == company_id)
        if text:
            like = f"%{text.strip().lower()}%"
            q = q.where(
                or_(
                    func.lower(func.coalesce(CallRow.person_name, "")).like(like),
                    func.lower(func.coalesce(CallRow.company_name, "")).like(like),
                    func.lower(func.coalesce(CallRow.notes, "")).like(like),
                    func.coalesce(CallRow.phone, "").like(f"%{text.strip()}%"),
                )
            )
        q = q.order_by(CallRow.created_at.desc()).limit(limit)
        with self._sessions() as s:
            return [_to(Call, r) for r in s.scalars(q)]

    def last_calls(self, person_ids: list[str]) -> dict[str, Call]:
        """Most recent call per person."""
        if not person_ids:
            return {}
        q = (
            select(CallRow)
            .where(CallRow.person_id.in_(person_ids))
            .order_by(CallRow.created_at.desc())
        )
        out: dict[str, Call] = {}
        with self._sessions() as s:
            for r in s.scalars(q):
                if r.person_id and r.person_id not in out:
                    out[r.person_id] = _to(Call, r)
        return out

    def call_counts(self) -> dict[str, int]:
        with self._sessions() as s:
            rows = s.execute(select(CallRow.outcome, func.count()).group_by(CallRow.outcome))
            return {o: int(n) for o, n in rows}

    # ---- phones typed by hand

    def set_phone(self, person_id: str, phone: str) -> None:
        phone = (phone or "").strip()[:64]
        with self._sessions.begin() as s:
            row = s.get(PersonPhoneRow, person_id)
            if not phone:
                if row:
                    s.delete(row)
                return
            if row is None:
                s.add(PersonPhoneRow(person_id=person_id, phone=phone))
            else:
                row.phone = phone

    def phones(self, person_ids: list[str]) -> dict[str, str]:
        if not person_ids:
            return {}
        with self._sessions() as s:
            rows = s.scalars(select(PersonPhoneRow).where(PersonPhoneRow.person_id.in_(person_ids)))
            return {r.person_id: r.phone for r in rows}

    # ---- settings

    def get_setting(self, key: str, default: str = "") -> str:
        with self._sessions() as s:
            row = s.get(PipelineSettingRow, key)
            return row.value if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._sessions.begin() as s:
            row = s.get(PipelineSettingRow, key)
            if row is None:
                s.add(PipelineSettingRow(key=key, value=value))
            else:
                row.value = value
