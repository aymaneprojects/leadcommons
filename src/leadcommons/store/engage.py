"""Sequences, mailboxes, outbound emails, tasks. Owner: engage agent.

Tables live on leadcommons.store.tables.Base and are created by Store() at start-up.
`EngageRepo(engine)` is the only way the rest of the engage feature touches them. Every
datetime column is timezone-aware UTC on the way in and out (SQLite stores naive UTC).
"""

from __future__ import annotations

import uuid
import weakref
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from leadcommons.models import utcnow
from leadcommons.store.codec import utc
from leadcommons.store.tables import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes in Python, whatever the database does with time zones."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return utc(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return utc(value)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------- tables

STEP_KINDS = ("auto_email", "manual_email", "call", "linkedin", "task")
TASK_KINDS = ("call", "email", "linkedin", "action")
PRIORITIES = ("high", "medium", "low")
ENROLLMENT_STATUSES = ("active", "paused", "finished", "replied", "bounced", "unsubscribed")
SEQUENCE_STATUSES = ("draft", "active", "paused", "archived")


class MailboxRow(Base):
    __tablename__ = "engage_mailboxes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    from_name: Mapped[str] = mapped_column(Text, default="")
    from_email: Mapped[str] = mapped_column(String(320))
    smtp_host: Mapped[str] = mapped_column(Text, default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_security: Mapped[str] = mapped_column(String(16), default="starttls")
    smtp_username: Mapped[str] = mapped_column(Text, default="")
    smtp_password: Mapped[str] = mapped_column(Text, default="")  # Fernet token
    imap_host: Mapped[str] = mapped_column(Text, default="")
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    imap_security: Mapped[str] = mapped_column(String(16), default="ssl")
    imap_username: Mapped[str] = mapped_column(Text, default="")
    imap_password: Mapped[str] = mapped_column(Text, default="")  # Fernet token
    imap_folder: Mapped[str] = mapped_column(Text, default="INBOX")
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    window_start: Mapped[int] = mapped_column(Integer, default=8)
    window_end: Mapped[int] = mapped_column(Integer, default=18)
    window_days: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    signature: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="untested")
    last_error: Mapped[str] = mapped_column(Text, default="")
    tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    imap_status: Mapped[str] = mapped_column(String(16), default="")
    imap_error: Mapped[str] = mapped_column(Text, default="")
    imap_last_uid: Mapped[int] = mapped_column(Integer, default=0)
    imap_polled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def label(self) -> str:
        return self.name or self.from_email

    @property
    def has_imap(self) -> bool:
        return bool(self.imap_host)

    @property
    def days(self) -> list[int]:
        return parse_days(self.window_days)


class SequenceRow(Base):
    __tablename__ = "engage_sequences"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    mailbox_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    schedule_custom: Mapped[bool] = mapped_column(Boolean, default=False)
    window_start: Mapped[int] = mapped_column(Integer, default=8)
    window_end: Mapped[int] = mapped_column(Integer, default=18)
    window_days: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    stop_on_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def days(self) -> list[int]:
        return parse_days(self.window_days)


class StepRow(Base):
    __tablename__ = "engage_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("engage_sequences.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="auto_email")
    delay_days: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    note: Mapped[str] = mapped_column(Text, default="")

    @property
    def is_email(self) -> bool:
        return self.kind in ("auto_email", "manual_email")


class EnrollmentRow(Base):
    __tablename__ = "engage_enrollments"
    __table_args__ = (UniqueConstraint("sequence_id", "person_id", name="uq_engage_enrollment"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_id: Mapped[str] = mapped_column(
        String(12), ForeignKey("engage_sequences.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[str] = mapped_column(String(128), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)  # next step to run, 0-based
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(12), nullable=True)  # waiting on it
    vars: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(Text, default="")
    last_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    thread_subject: Mapped[str] = mapped_column(Text, default="")
    enrolled_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class MessageRow(Base):
    __tablename__ = "engage_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    direction: Mapped[str] = mapped_column(String(4), default="out", index=True)
    # out: scheduled | sent | failed | bounced ; in: received
    status: Mapped[str] = mapped_column(String(16), default="scheduled", index=True)
    mailbox_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    person_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sequence_id: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    enrollment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    to_email: Mapped[str] = mapped_column(String(320), default="")
    from_email: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TaskRow(Base):
    __tablename__ = "engage_tasks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="action", index=True)
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)
    person_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    company_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    deal_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sequence_id: Mapped[str | None] = mapped_column(String(12), nullable=True)
    enrollment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class EngageSettingRow(Base):
    __tablename__ = "engage_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class EngageLockRow(Base):
    __tablename__ = "engage_locks"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


# ---------------------------------------------------------------- helpers

DEFAULT_SETTINGS: dict[str, Any] = {
    "footer_text": "You are receiving this email because your work address is public. "
    "If you would rather not hear from me again, use the link below.",
    "postal_address": "",
    "default_delay_days": 3,
    "stop_on_reply": True,
    "bounce_action": "suppress",  # "suppress" (never email again) or "stop" (stop the sequence)
    "gap_minutes": 1,  # minimum minutes between two emails from one mailbox
    "reply_poll_minutes": 5,
    "public_url": "",
    "seen_base_url": "",
    "timezone": "UTC",  # the user's own time zone: task due dates and "today"
    "tip_dismissed": False,
    "tasks_tip_dismissed": False,
}


def parse_days(value: str | None) -> list[int]:
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.append(int(part))
    return sorted(set(out))



_FALLBACK: weakref.WeakKeyDictionary[Any, Engine] = weakref.WeakKeyDictionary()


def engine_of(store: Any) -> Engine:
    """The store's shared engine. Test doubles without one get a private in-memory database."""
    engine = getattr(store, "engine", None)
    if engine is not None:
        return engine
    try:
        cached = _FALLBACK.get(store)
    except TypeError:
        cached = None
    if cached is None:
        cached = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(cached)
        try:
            _FALLBACK[store] = cached
        except TypeError:
            pass
    return cached


def repo_for(store: Any) -> EngageRepo:
    return EngageRepo(engine_of(store))


# ---------------------------------------------------------------- repository


class EngageRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    # -------------------------------------------------------- settings
    def get_settings(self) -> dict[str, Any]:
        out = dict(DEFAULT_SETTINGS)
        with self._sessions() as s:
            for row in s.scalars(select(EngageSettingRow)):
                if row.key in out:
                    out[row.key] = row.value
        return out

    def save_settings(self, **values: Any) -> None:
        with self._sessions.begin() as s:
            for key, value in values.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                row = s.get(EngageSettingRow, key)
                if row is None:
                    s.add(EngageSettingRow(key=key, value=value))
                else:
                    row.value = value

    # -------------------------------------------------------- mailboxes
    def mailboxes(self) -> list[MailboxRow]:
        with self._sessions() as s:
            return list(s.scalars(select(MailboxRow).order_by(MailboxRow.created_at)))

    def mailbox(self, mailbox_id: str | None) -> MailboxRow | None:
        if not mailbox_id:
            return None
        with self._sessions() as s:
            return s.get(MailboxRow, mailbox_id)

    def create_mailbox(self, **fields: Any) -> str:
        mailbox_id = new_id()
        with self._sessions.begin() as s:
            row = MailboxRow(id=mailbox_id, created_at=utcnow(), **fields)
            s.add(row)
        return mailbox_id

    def update_mailbox(self, mailbox_id: str, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(MailboxRow, mailbox_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)

    def delete_mailbox(self, mailbox_id: str) -> None:
        with self._sessions.begin() as s:
            s.execute(delete(MailboxRow).where(MailboxRow.id == mailbox_id))
            s.execute(
                update(SequenceRow)
                .where(SequenceRow.mailbox_id == mailbox_id)
                .values(mailbox_id=None)
            )

    # -------------------------------------------------------- sequences
    def sequences(self, statuses: list[str] | None = None) -> list[SequenceRow]:
        q = select(SequenceRow).order_by(SequenceRow.created_at.desc(), SequenceRow.id)
        if statuses:
            q = q.where(SequenceRow.status.in_(statuses))
        with self._sessions() as s:
            return list(s.scalars(q))

    def sequence(self, sequence_id: str | None) -> SequenceRow | None:
        if not sequence_id:
            return None
        with self._sessions() as s:
            return s.get(SequenceRow, sequence_id)

    def create_sequence(
        self, name: str, steps: list[dict[str, Any]] | None = None, **kw: Any
    ) -> str:
        sequence_id = new_id()
        now = utcnow()
        with self._sessions.begin() as s:
            s.add(SequenceRow(id=sequence_id, name=name, created_at=now, updated_at=now, **kw))
            s.flush()
            for i, step in enumerate(steps or []):
                s.add(StepRow(sequence_id=sequence_id, position=i, **step))
        return sequence_id

    def update_sequence(self, sequence_id: str, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(SequenceRow, sequence_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = utcnow()

    def duplicate_sequence(self, sequence_id: str, name: str | None = None) -> str | None:
        seq = self.sequence(sequence_id)
        if seq is None:
            return None
        steps = [
            {
                "kind": st.kind,
                "delay_days": st.delay_days,
                "subject": st.subject,
                "body": st.body,
                "priority": st.priority,
                "note": st.note,
            }
            for st in self.steps(sequence_id)
        ]
        return self.create_sequence(
            name or f"{seq.name} (copy)",
            steps,
            status="draft",
            mailbox_id=seq.mailbox_id,
            schedule_custom=seq.schedule_custom,
            window_start=seq.window_start,
            window_end=seq.window_end,
            window_days=seq.window_days,
            timezone=seq.timezone,
            stop_on_reply=seq.stop_on_reply,
        )

    def delete_sequence(self, sequence_id: str) -> None:
        with self._sessions.begin() as s:
            s.execute(delete(StepRow).where(StepRow.sequence_id == sequence_id))
            s.execute(delete(EnrollmentRow).where(EnrollmentRow.sequence_id == sequence_id))
            s.execute(delete(SequenceRow).where(SequenceRow.id == sequence_id))

    def sequence_stats(self) -> dict[str, dict[str, int]]:
        """{sequence_id: {"steps": n, "enrolled": n, <status>: n, ...}}"""
        out: dict[str, dict[str, int]] = {}
        with self._sessions() as s:
            for sid, n in s.execute(
                select(StepRow.sequence_id, func.count()).group_by(StepRow.sequence_id)
            ):
                out.setdefault(sid, {})["steps"] = n
            for sid, status, n in s.execute(
                select(EnrollmentRow.sequence_id, EnrollmentRow.status, func.count()).group_by(
                    EnrollmentRow.sequence_id, EnrollmentRow.status
                )
            ):
                d = out.setdefault(sid, {})
                d[status] = n
                d["enrolled"] = d.get("enrolled", 0) + n
            for sid, n in s.execute(
                select(MessageRow.sequence_id, func.count())
                .where(MessageRow.direction == "out", MessageRow.status.in_(["sent", "bounced"]))
                .group_by(MessageRow.sequence_id)
            ):
                if sid:
                    out.setdefault(sid, {})["sent"] = n
        return out

    # -------------------------------------------------------- steps
    def steps(self, sequence_id: str) -> list[StepRow]:
        q = select(StepRow).where(StepRow.sequence_id == sequence_id)
        with self._sessions() as s:
            return list(s.scalars(q.order_by(StepRow.position, StepRow.id)))

    def step(self, step_id: int | None) -> StepRow | None:
        if not step_id:
            return None
        with self._sessions() as s:
            return s.get(StepRow, step_id)

    def add_step(self, sequence_id: str, position: int | None = None, **fields: Any) -> int:
        with self._sessions.begin() as s:
            rows = list(
                s.scalars(
                    select(StepRow)
                    .where(StepRow.sequence_id == sequence_id)
                    .order_by(StepRow.position, StepRow.id)
                )
            )
            at = len(rows) if position is None else max(0, min(position, len(rows)))
            for i, r in enumerate(rows):
                r.position = i if i < at else i + 1
            row = StepRow(sequence_id=sequence_id, position=at, **fields)
            s.add(row)
            s.flush()
            self._touch(s, sequence_id)
            return row.id

    def update_step(self, step_id: int, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(StepRow, step_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            self._touch(s, row.sequence_id)

    def delete_step(self, step_id: int) -> None:
        with self._sessions.begin() as s:
            row = s.get(StepRow, step_id)
            if row is None:
                return
            sequence_id, removed = row.sequence_id, row.position
            s.delete(row)
            s.flush()
            rows = s.scalars(
                select(StepRow)
                .where(StepRow.sequence_id == sequence_id)
                .order_by(StepRow.position, StepRow.id)
            )
            for i, r in enumerate(rows):
                r.position = i
            # contacts waiting on a later step move back by one so nobody skips a step
            for e in s.scalars(
                select(EnrollmentRow).where(
                    EnrollmentRow.sequence_id == sequence_id, EnrollmentRow.step_index > removed
                )
            ):
                e.step_index -= 1
            self._touch(s, sequence_id)

    def move_step(self, step_id: int, delta: int) -> None:
        with self._sessions.begin() as s:
            row = s.get(StepRow, step_id)
            if row is None:
                return
            rows = list(
                s.scalars(
                    select(StepRow)
                    .where(StepRow.sequence_id == row.sequence_id)
                    .order_by(StepRow.position, StepRow.id)
                )
            )
            i = next(k for k, r in enumerate(rows) if r.id == step_id)
            j = i + delta
            if 0 <= j < len(rows):
                rows[i], rows[j] = rows[j], rows[i]
                for k, r in enumerate(rows):
                    r.position = k
            self._touch(s, row.sequence_id)

    @staticmethod
    def _touch(s: Any, sequence_id: str) -> None:
        seq = s.get(SequenceRow, sequence_id)
        if seq is not None:
            seq.updated_at = utcnow()

    # -------------------------------------------------------- enrollments
    def enrollments(
        self,
        sequence_id: str | None = None,
        status: str | None = None,
        person_id: str | None = None,
    ) -> list[EnrollmentRow]:
        q = select(EnrollmentRow)
        if sequence_id:
            q = q.where(EnrollmentRow.sequence_id == sequence_id)
        if status:
            q = q.where(EnrollmentRow.status == status)
        if person_id:
            q = q.where(EnrollmentRow.person_id == person_id)
        with self._sessions() as s:
            return list(s.scalars(q.order_by(EnrollmentRow.enrolled_at.desc(), EnrollmentRow.id)))

    def enrollment(self, enrollment_id: int | None) -> EnrollmentRow | None:
        if not enrollment_id:
            return None
        with self._sessions() as s:
            return s.get(EnrollmentRow, enrollment_id)

    def enrolled_person_ids(self, sequence_id: str) -> set[str]:
        q = select(EnrollmentRow.person_id).where(EnrollmentRow.sequence_id == sequence_id)
        with self._sessions() as s:
            return set(s.scalars(q))

    def add_enrollment(
        self, sequence_id: str, person_id: str, email: str, **fields: Any
    ) -> int | None:
        now = utcnow()
        try:
            with self._sessions.begin() as s:
                row = EnrollmentRow(
                    sequence_id=sequence_id,
                    person_id=person_id,
                    email=email,
                    enrolled_at=now,
                    updated_at=now,
                    **fields,
                )
                s.add(row)
                s.flush()
                return row.id
        except IntegrityError:
            return None

    def update_enrollment(self, enrollment_id: int, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(EnrollmentRow, enrollment_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            row.updated_at = utcnow()

    def due_enrollments(self, now: datetime, limit: int = 500) -> list[EnrollmentRow]:
        q = (
            select(EnrollmentRow)
            .join(SequenceRow, SequenceRow.id == EnrollmentRow.sequence_id)
            .where(
                EnrollmentRow.status == "active",
                EnrollmentRow.next_run_at.is_not(None),
                EnrollmentRow.next_run_at <= now,
                SequenceRow.status == "active",
            )
            .order_by(EnrollmentRow.next_run_at, EnrollmentRow.id)
            .limit(limit)
        )
        with self._sessions() as s:
            return list(s.scalars(q))

    def upcoming_emails(self, limit: int = 200) -> list[tuple[EnrollmentRow, StepRow]]:
        """Active contacts whose next step is an automatic email, soonest first."""
        q = (
            select(EnrollmentRow, StepRow)
            .join(SequenceRow, SequenceRow.id == EnrollmentRow.sequence_id)
            .join(
                StepRow,
                (StepRow.sequence_id == EnrollmentRow.sequence_id)
                & (StepRow.position == EnrollmentRow.step_index),
            )
            .where(
                EnrollmentRow.status == "active",
                EnrollmentRow.next_run_at.is_not(None),
                SequenceRow.status == "active",
                StepRow.kind == "auto_email",
            )
            .order_by(EnrollmentRow.next_run_at, EnrollmentRow.id)
            .limit(limit)
        )
        with self._sessions() as s:
            return [(e, st) for e, st in s.execute(q).all()]

    def stop_address(self, email: str, status: str, note: str) -> list[EnrollmentRow]:
        """Stop every running enrollment for an address (or a whole @domain)."""
        email = email.strip().lower()
        cond = (
            func.lower(EnrollmentRow.email).like(f"%@{email[1:]}")
            if email.startswith("@")
            else func.lower(EnrollmentRow.email) == email
        )
        with self._sessions.begin() as s:
            rows = list(
                s.scalars(
                    select(EnrollmentRow).where(
                        cond, EnrollmentRow.status.in_(["active", "paused"])
                    )
                )
            )
            now = utcnow()
            for r in rows:
                r.status, r.note, r.next_run_at = status, note, None
                r.finished_at = now
                r.updated_at = now
            return rows

    # -------------------------------------------------------- messages
    def add_message(self, **fields: Any) -> int:
        with self._sessions.begin() as s:
            row = MessageRow(created_at=utcnow(), **fields)
            s.add(row)
            s.flush()
            return row.id

    def update_message(self, row_id: int, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(MessageRow, row_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)

    def message(self, row_id: int | None) -> MessageRow | None:
        if not row_id:
            return None
        with self._sessions() as s:
            return s.get(MessageRow, row_id)

    def messages(
        self,
        *,
        direction: str | None = None,
        statuses: list[str] | None = None,
        person_id: str | None = None,
        sequence_id: str | None = None,
        q: str = "",
        limit: int = 200,
    ) -> list[MessageRow]:
        query = select(MessageRow)
        if direction:
            query = query.where(MessageRow.direction == direction)
        if statuses:
            query = query.where(MessageRow.status.in_(statuses))
        if person_id:
            query = query.where(MessageRow.person_id == person_id)
        if sequence_id:
            query = query.where(MessageRow.sequence_id == sequence_id)
        needle = q.strip().lower()
        if needle:
            query = query.where(
                or_(
                    func.lower(MessageRow.subject).contains(needle, autoescape=True),
                    func.lower(MessageRow.to_email).contains(needle, autoescape=True),
                    func.lower(MessageRow.from_email).contains(needle, autoescape=True),
                )
            )
        order = func.coalesce(MessageRow.sent_at, MessageRow.scheduled_at, MessageRow.created_at)
        if statuses == ["scheduled"]:
            query = query.order_by(order.asc(), MessageRow.id)
        else:
            query = query.order_by(order.desc(), MessageRow.id.desc())
        with self._sessions() as s:
            return list(s.scalars(query.limit(limit)))

    def message_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._sessions() as s:
            for direction, status, n in s.execute(
                select(MessageRow.direction, MessageRow.status, func.count()).group_by(
                    MessageRow.direction, MessageRow.status
                )
            ):
                out[f"{direction}:{status}"] = n
        return out

    def sent_count_since(self, mailbox_id: str, since: datetime) -> int:
        q = select(func.count()).where(
            MessageRow.mailbox_id == mailbox_id,
            MessageRow.direction == "out",
            MessageRow.status.in_(["sent", "bounced"]),
            MessageRow.sent_at >= since,
        )
        with self._sessions() as s:
            return int(s.scalar(q) or 0)

    def last_sent_at(self, mailbox_id: str) -> datetime | None:
        q = select(func.max(MessageRow.sent_at)).where(
            MessageRow.mailbox_id == mailbox_id, MessageRow.direction == "out"
        )
        with self._sessions() as s:
            return utc(s.scalar(q))

    def first_sent_at(self, mailbox_id: str) -> datetime | None:
        q = select(func.min(MessageRow.sent_at)).where(
            MessageRow.mailbox_id == mailbox_id,
            MessageRow.direction == "out",
            MessageRow.status == "sent",
        )
        with self._sessions() as s:
            return utc(s.scalar(q))

    def outbound_by_message_ids(self, message_ids: list[str]) -> list[MessageRow]:
        ids = [m for m in message_ids if m]
        if not ids:
            return []
        q = select(MessageRow).where(MessageRow.direction == "out", MessageRow.message_id.in_(ids))
        with self._sessions() as s:
            return list(s.scalars(q))

    def has_inbound(self, message_id: str) -> bool:
        q = select(MessageRow.id).where(
            MessageRow.direction == "in", MessageRow.message_id == message_id
        )
        with self._sessions() as s:
            return s.scalar(q.limit(1)) is not None

    def due_scheduled(self, now: datetime, limit: int = 200) -> list[MessageRow]:
        q = (
            select(MessageRow)
            .where(
                MessageRow.direction == "out",
                MessageRow.status == "scheduled",
                MessageRow.scheduled_at <= now,
            )
            .order_by(MessageRow.scheduled_at, MessageRow.id)
            .limit(limit)
        )
        with self._sessions() as s:
            return list(s.scalars(q))

    # -------------------------------------------------------- tasks
    def add_task(self, **fields: Any) -> str:
        task_id = new_id()
        with self._sessions.begin() as s:
            s.add(TaskRow(id=task_id, created_at=utcnow(), **fields))
        return task_id

    def task(self, task_id: str | None) -> TaskRow | None:
        if not task_id:
            return None
        with self._sessions() as s:
            return s.get(TaskRow, task_id)

    def update_task(self, task_id: str, /, **fields: Any) -> None:
        with self._sessions.begin() as s:
            row = s.get(TaskRow, task_id)
            if row is None:
                return
            for k, v in fields.items():
                setattr(row, k, v)

    def delete_task(self, task_id: str) -> None:
        with self._sessions.begin() as s:
            s.execute(delete(TaskRow).where(TaskRow.id == task_id))

    def tasks(
        self,
        *,
        statuses: list[str] | None = None,
        kinds: list[str] | None = None,
        priorities: list[str] | None = None,
        due_before: datetime | None = None,
        due_from: datetime | None = None,
        person_id: str | None = None,
        company_id: str | None = None,
        deal_id: str | None = None,
        q: str = "",
        sort: str = "due",
        limit: int = 500,
    ) -> list[TaskRow]:
        query = select(TaskRow)
        if statuses:
            query = query.where(TaskRow.status.in_(statuses))
        if kinds:
            query = query.where(TaskRow.kind.in_(kinds))
        if priorities:
            query = query.where(TaskRow.priority.in_(priorities))
        if due_before is not None:
            query = query.where(TaskRow.due_at.is_not(None), TaskRow.due_at < due_before)
        if due_from is not None:
            query = query.where(or_(TaskRow.due_at.is_(None), TaskRow.due_at >= due_from))
        ref = []
        if person_id:
            ref.append(TaskRow.person_id == person_id)
        if company_id:
            ref.append(TaskRow.company_id == company_id)
        if deal_id:
            ref.append(TaskRow.deal_id == deal_id)
        if ref:
            query = query.where(or_(*ref))
        needle = q.strip().lower()
        if needle:
            query = query.where(
                or_(
                    func.lower(TaskRow.title).contains(needle, autoescape=True),
                    func.lower(TaskRow.body).contains(needle, autoescape=True),
                )
            )
        if sort == "done":
            query = query.order_by(TaskRow.completed_at.desc(), TaskRow.id)
        elif sort == "created":
            query = query.order_by(TaskRow.created_at.desc(), TaskRow.id)
        else:
            query = query.order_by(
                TaskRow.due_at.is_(None), TaskRow.due_at, TaskRow.created_at, TaskRow.id
            )
        with self._sessions() as s:
            return list(s.scalars(query.limit(limit)))

    # -------------------------------------------------------- locks
    def acquire_lock(self, name: str, owner: str, expires_at: datetime, now: datetime) -> bool:
        """Lease a named lock. True when this owner holds it until expires_at."""
        with self._sessions.begin() as s:
            res = s.execute(
                update(EngageLockRow)
                .where(
                    EngageLockRow.name == name,
                    or_(EngageLockRow.owner == owner, EngageLockRow.expires_at < now),
                )
                .values(owner=owner, expires_at=expires_at)
            )
            if res.rowcount:
                return True
            if s.get(EngageLockRow, name) is not None:
                return False
        try:
            with self._sessions.begin() as s:
                s.add(EngageLockRow(name=name, owner=owner, expires_at=expires_at))
            return True
        except IntegrityError:
            return False

    def release_lock(self, name: str, owner: str) -> None:
        with self._sessions.begin() as s:
            s.execute(
                delete(EngageLockRow).where(
                    EngageLockRow.name == name, EngageLockRow.owner == owner
                )
            )
