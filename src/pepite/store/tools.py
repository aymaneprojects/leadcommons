# ruff: noqa: E501
"""Workflows, assistant conversations, API tokens, webhooks, notifications. Owner: tools agent.

Tables live on pepite.store.tables.Base and are created by Store() at start-up.
`repo_for(store)` returns the ToolsRepo of a store (one per store object, cached on it).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Engine,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from pepite.models import utcnow
from pepite.store.codec import utc
from pepite.store.tables import Base

MAX_RUNS_PER_WORKFLOW = 200
MAX_DELIVERIES_PER_HOOK = 100
TOKEN_PREFIX = "lc_"


def _id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------------------ tables


class AssistantConversationRow(Base):
    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class AssistantMessageRow(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(12), index=True)
    role: Mapped[str] = mapped_column(String(12))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeedbackRow(Base):
    __tablename__ = "assistant_feedback"

    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1 thumbs up, -1 thumbs down
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkflowRow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    trigger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conditions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    actions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(String(12), index=True)
    trigger: Mapped[str] = mapped_column(String(60))  # event name, "schedule" or "manual"
    status: Mapped[str] = mapped_column(String(16))  # success | partial | failed | skipped
    summary: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list[Any]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookRow(Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    events: Mapped[list[Any]] = mapped_column(JSON, default=list)
    secret: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookDeliveryRow(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    webhook_id: Mapped[str] = mapped_column(String(12), index=True)
    event: Mapped[str] = mapped_column(String(60))
    ok: Mapped[bool] = mapped_column(Boolean)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiTokenRow(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationRow(Base):
    __tablename__ = "tool_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(24), default="workflow")
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    href: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


# ------------------------------------------------------------------------------ records


@dataclass
class Conversation:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: int = 0


@dataclass
class Message:
    id: int
    conversation_id: str
    role: str
    content: str
    data: dict[str, Any]
    created_at: datetime
    rating: int = 0


@dataclass
class Workflow:
    id: str
    name: str
    enabled: bool
    trigger: dict[str, Any]
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    state: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None
    last_run: WorkflowRun | None = None


@dataclass
class WorkflowRun:
    id: int
    workflow_id: str
    trigger: str
    status: str
    summary: str
    steps: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None = None


@dataclass
class Webhook:
    id: str
    url: str
    events: list[str]
    secret: str
    enabled: bool
    created_at: datetime
    last_delivery: WebhookDelivery | None = None


@dataclass
class WebhookDelivery:
    id: int
    webhook_id: str
    event: str
    ok: bool
    status_code: int | None
    error: str | None
    duration_ms: int
    created_at: datetime


@dataclass
class ApiToken:
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass
class Notification:
    id: int
    kind: str
    title: str
    body: str
    href: str | None
    created_at: datetime


def _conv(r: AssistantConversationRow, n: int = 0) -> Conversation:
    return Conversation(
        r.id, r.title, utc(r.created_at) or utcnow(), utc(r.updated_at) or utcnow(), n
    )


def _msg(r: AssistantMessageRow, rating: int = 0) -> Message:
    return Message(
        r.id,
        r.conversation_id,
        r.role,
        r.content,
        dict(r.data or {}),
        utc(r.created_at) or utcnow(),
        rating,
    )


def _run(r: WorkflowRunRow) -> WorkflowRun:
    return WorkflowRun(
        r.id,
        r.workflow_id,
        r.trigger,
        r.status,
        r.summary,
        list(r.steps or []),
        utc(r.started_at) or utcnow(),
        utc(r.finished_at),
    )


def _wf(r: WorkflowRow, last: WorkflowRun | None = None) -> Workflow:
    return Workflow(
        r.id,
        r.name,
        bool(r.enabled),
        dict(r.trigger or {}),
        list(r.conditions or []),
        list(r.actions or []),
        dict(r.state or {}),
        utc(r.created_at) or utcnow(),
        utc(r.updated_at) or utcnow(),
        utc(r.next_run_at),
        last,
    )


def _hook(r: WebhookRow, last: WebhookDelivery | None = None) -> Webhook:
    return Webhook(
        r.id,
        r.url,
        list(r.events or []),
        r.secret,
        bool(r.enabled),
        utc(r.created_at) or utcnow(),
        last,
    )


def _delivery(r: WebhookDeliveryRow) -> WebhookDelivery:
    return WebhookDelivery(
        r.id,
        r.webhook_id,
        r.event,
        bool(r.ok),
        r.status_code,
        r.error,
        r.duration_ms,
        utc(r.created_at) or utcnow(),
    )


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_secret() -> str:
    return "whsec_" + secrets.token_hex(20)


# ------------------------------------------------------------------------------ repository


class ToolsRepo:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    # ---- conversations
    def create_conversation(self, title: str = "New chat") -> str:
        cid = _id()
        now = utcnow()
        with self._sessions.begin() as s:
            s.add(
                AssistantConversationRow(id=cid, title=title[:120], created_at=now, updated_at=now)
            )
        return cid

    def get_conversation(self, cid: str) -> Conversation | None:
        with self._sessions() as s:
            r = s.get(AssistantConversationRow, cid)
            return _conv(r) if r else None

    def rename_conversation(self, cid: str, title: str) -> None:
        with self._sessions.begin() as s:
            s.execute(
                update(AssistantConversationRow)
                .where(AssistantConversationRow.id == cid)
                .values(title=title[:120])
            )

    def conversations(self, limit: int = 50) -> list[Conversation]:
        q = (
            select(AssistantConversationRow, func.count(AssistantMessageRow.id))
            .outerjoin(
                AssistantMessageRow,
                AssistantMessageRow.conversation_id == AssistantConversationRow.id,
            )
            .group_by(AssistantConversationRow.id)
            .order_by(AssistantConversationRow.updated_at.desc())
            .limit(limit)
        )
        with self._sessions() as s:
            return [_conv(r, n) for r, n in s.execute(q).all() if n]

    def latest_conversation(self) -> Conversation | None:
        items = self.conversations(1)
        return items[0] if items else None

    def delete_conversation(self, cid: str) -> None:
        with self._sessions.begin() as s:
            ids = list(
                s.scalars(
                    select(AssistantMessageRow.id).where(AssistantMessageRow.conversation_id == cid)
                )
            )
            if ids:
                s.execute(delete(FeedbackRow).where(FeedbackRow.message_id.in_(ids)))
            s.execute(delete(AssistantMessageRow).where(AssistantMessageRow.conversation_id == cid))
            s.execute(delete(AssistantConversationRow).where(AssistantConversationRow.id == cid))

    def add_message(
        self, cid: str, role: str, content: str, data: dict[str, Any] | None = None
    ) -> Message:
        now = utcnow()
        with self._sessions.begin() as s:
            row = AssistantMessageRow(
                conversation_id=cid,
                role=role,
                content=content,
                data=dict(data or {}),
                created_at=now,
            )
            s.add(row)
            s.flush()
            s.execute(
                update(AssistantConversationRow)
                .where(AssistantConversationRow.id == cid)
                .values(updated_at=now)
            )
            return _msg(row)

    def get_message(self, message_id: int) -> Message | None:
        with self._sessions() as s:
            r = s.get(AssistantMessageRow, message_id)
            if r is None:
                return None
            fb = s.get(FeedbackRow, message_id)
            return _msg(r, fb.rating if fb else 0)

    def update_message_data(self, message_id: int, data: dict[str, Any]) -> None:
        with self._sessions.begin() as s:
            r = s.get(AssistantMessageRow, message_id)
            if r is not None:
                r.data = dict(data)

    def messages(self, cid: str) -> list[Message]:
        with self._sessions() as s:
            rows = s.scalars(
                select(AssistantMessageRow)
                .where(AssistantMessageRow.conversation_id == cid)
                .order_by(AssistantMessageRow.id)
            ).all()
            fb = (
                {
                    f.message_id: f.rating
                    for f in s.scalars(
                        select(FeedbackRow).where(FeedbackRow.message_id.in_([r.id for r in rows]))
                    )
                }
                if rows
                else {}
            )
            return [_msg(r, fb.get(r.id, 0)) for r in rows]

    def set_feedback(self, message_id: int, rating: int) -> int:
        """rating 1 or -1; the same rating again clears it. Returns the stored rating (0 = none)."""
        with self._sessions.begin() as s:
            fb = s.get(FeedbackRow, message_id)
            if fb is not None and fb.rating == rating:
                s.delete(fb)
                return 0
            if fb is None:
                s.add(FeedbackRow(message_id=message_id, rating=rating, created_at=utcnow()))
            else:
                fb.rating = rating
            return rating

    def feedback_counts(self) -> dict[str, int]:
        with self._sessions() as s:
            up = s.scalar(
                select(func.count()).select_from(FeedbackRow).where(FeedbackRow.rating > 0)
            )
            down = s.scalar(
                select(func.count()).select_from(FeedbackRow).where(FeedbackRow.rating < 0)
            )
        return {"up": up or 0, "down": down or 0}

    # ---- workflows
    def create_workflow(
        self,
        name: str,
        trigger: dict[str, Any],
        conditions: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        *,
        enabled: bool = True,
        next_run_at: datetime | None = None,
    ) -> str:
        wid = _id()
        now = utcnow()
        with self._sessions.begin() as s:
            s.add(
                WorkflowRow(
                    id=wid,
                    name=name[:160],
                    enabled=enabled,
                    trigger=trigger,
                    conditions=conditions,
                    actions=actions,
                    state={},
                    created_at=now,
                    updated_at=now,
                    next_run_at=next_run_at,
                )
            )
        return wid

    def update_workflow(
        self,
        wid: str,
        *,
        name: str,
        trigger: dict[str, Any],
        conditions: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        next_run_at: datetime | None,
    ) -> bool:
        with self._sessions.begin() as s:
            r = s.get(WorkflowRow, wid)
            if r is None:
                return False
            r.name, r.trigger, r.conditions, r.actions = name[:160], trigger, conditions, actions
            r.next_run_at = next_run_at
            r.updated_at = utcnow()
            return True

    def set_enabled(self, wid: str, enabled: bool, next_run_at: datetime | None = None) -> None:
        with self._sessions.begin() as s:
            r = s.get(WorkflowRow, wid)
            if r is not None:
                r.enabled = enabled
                r.next_run_at = next_run_at if enabled else None
                r.updated_at = utcnow()

    def delete_workflow(self, wid: str) -> None:
        with self._sessions.begin() as s:
            s.execute(delete(WorkflowRunRow).where(WorkflowRunRow.workflow_id == wid))
            s.execute(delete(WorkflowRow).where(WorkflowRow.id == wid))

    def save_state(self, wid: str, state: dict[str, Any], next_run_at: datetime | None) -> None:
        with self._sessions.begin() as s:
            r = s.get(WorkflowRow, wid)
            if r is not None:
                r.state = dict(state)
                r.next_run_at = next_run_at

    def get_workflow(self, wid: str) -> Workflow | None:
        with self._sessions() as s:
            r = s.get(WorkflowRow, wid)
            return _wf(r) if r else None

    def workflows(self, *, enabled: bool | None = None) -> list[Workflow]:
        q = select(WorkflowRow).order_by(WorkflowRow.created_at, WorkflowRow.id)
        if enabled is not None:
            q = q.where(WorkflowRow.enabled == enabled)
        with self._sessions() as s:
            rows = s.scalars(q).all()
            out = []
            for r in rows:
                last = s.scalars(
                    select(WorkflowRunRow)
                    .where(WorkflowRunRow.workflow_id == r.id)
                    .order_by(WorkflowRunRow.id.desc())
                    .limit(1)
                ).first()
                out.append(_wf(r, _run(last) if last else None))
            return out

    def count_workflows(self, *, enabled: bool | None = None) -> int:
        q = select(func.count()).select_from(WorkflowRow)
        if enabled is not None:
            q = q.where(WorkflowRow.enabled == enabled)
        with self._sessions() as s:
            return s.scalar(q) or 0

    def enabled_for_event(self, event: str) -> list[Workflow]:
        with self._sessions() as s:
            rows = s.scalars(select(WorkflowRow).where(WorkflowRow.enabled.is_(True))).all()
            return [
                _wf(r)
                for r in rows
                if (r.trigger or {}).get("type") == "event"
                and (r.trigger or {}).get("event") == event
            ]

    def due_workflows(self, now: datetime) -> list[Workflow]:
        with self._sessions() as s:
            rows = s.scalars(
                select(WorkflowRow).where(
                    WorkflowRow.enabled.is_(True), WorkflowRow.next_run_at.is_not(None)
                )
            ).all()
            return [
                _wf(r)
                for r in rows
                if (r.trigger or {}).get("type") == "schedule"
                and (utc(r.next_run_at) or now) <= now
            ]

    def has_event_listeners(self) -> bool:
        with self._sessions() as s:
            return bool(
                s.scalar(
                    select(func.count())
                    .select_from(WorkflowRow)
                    .where(WorkflowRow.enabled.is_(True))
                )
            )

    def add_run(
        self,
        workflow_id: str,
        trigger: str,
        status: str,
        summary: str,
        steps: list[dict[str, Any]],
        started_at: datetime,
    ) -> int:
        with self._sessions.begin() as s:
            row = WorkflowRunRow(
                workflow_id=workflow_id,
                trigger=trigger[:60],
                status=status,
                summary=summary[:500],
                steps=steps,
                started_at=started_at,
                finished_at=utcnow(),
            )
            s.add(row)
            s.flush()
            rid = row.id
            old = list(
                s.scalars(
                    select(WorkflowRunRow.id)
                    .where(WorkflowRunRow.workflow_id == workflow_id)
                    .order_by(WorkflowRunRow.id.desc())
                    .offset(MAX_RUNS_PER_WORKFLOW)
                )
            )
            if old:
                s.execute(delete(WorkflowRunRow).where(WorkflowRunRow.id.in_(old)))
            return rid

    def runs(self, workflow_id: str | None = None, limit: int = 50) -> list[WorkflowRun]:
        q = select(WorkflowRunRow).order_by(WorkflowRunRow.id.desc()).limit(limit)
        if workflow_id:
            q = q.where(WorkflowRunRow.workflow_id == workflow_id)
        with self._sessions() as s:
            return [_run(r) for r in s.scalars(q)]

    def get_run(self, run_id: int) -> WorkflowRun | None:
        with self._sessions() as s:
            r = s.get(WorkflowRunRow, run_id)
            return _run(r) if r else None

    # ---- webhooks
    def create_webhook(self, url: str, events: list[str]) -> str:
        hid = _id()
        with self._sessions.begin() as s:
            s.add(
                WebhookRow(
                    id=hid,
                    url=url[:2000],
                    events=list(events),
                    secret=new_secret(),
                    enabled=True,
                    created_at=utcnow(),
                )
            )
        return hid

    def update_webhook(self, hid: str, *, url: str, events: list[str], enabled: bool) -> bool:
        with self._sessions.begin() as s:
            r = s.get(WebhookRow, hid)
            if r is None:
                return False
            r.url, r.events, r.enabled = url[:2000], list(events), enabled
            return True

    def rotate_secret(self, hid: str) -> None:
        with self._sessions.begin() as s:
            r = s.get(WebhookRow, hid)
            if r is not None:
                r.secret = new_secret()

    def delete_webhook(self, hid: str) -> None:
        with self._sessions.begin() as s:
            s.execute(delete(WebhookDeliveryRow).where(WebhookDeliveryRow.webhook_id == hid))
            s.execute(delete(WebhookRow).where(WebhookRow.id == hid))

    def get_webhook(self, hid: str) -> Webhook | None:
        with self._sessions() as s:
            r = s.get(WebhookRow, hid)
            return _hook(r) if r else None

    def webhooks(self, *, enabled: bool | None = None) -> list[Webhook]:
        q = select(WebhookRow).order_by(WebhookRow.created_at, WebhookRow.id)
        if enabled is not None:
            q = q.where(WebhookRow.enabled == enabled)
        with self._sessions() as s:
            out = []
            for r in s.scalars(q).all():
                last = s.scalars(
                    select(WebhookDeliveryRow)
                    .where(WebhookDeliveryRow.webhook_id == r.id)
                    .order_by(WebhookDeliveryRow.id.desc())
                    .limit(1)
                ).first()
                out.append(_hook(r, _delivery(last) if last else None))
            return out

    def webhooks_for_event(self, event: str) -> list[Webhook]:
        return [h for h in self.webhooks(enabled=True) if event in h.events]

    def add_delivery(
        self,
        webhook_id: str,
        event: str,
        ok: bool,
        status_code: int | None,
        error: str | None,
        duration_ms: int,
    ) -> None:
        with self._sessions.begin() as s:
            s.add(
                WebhookDeliveryRow(
                    webhook_id=webhook_id,
                    event=event[:60],
                    ok=ok,
                    status_code=status_code,
                    error=(error or None) and error[:300],
                    duration_ms=duration_ms,
                    created_at=utcnow(),
                )
            )
            s.flush()
            old = list(
                s.scalars(
                    select(WebhookDeliveryRow.id)
                    .where(WebhookDeliveryRow.webhook_id == webhook_id)
                    .order_by(WebhookDeliveryRow.id.desc())
                    .offset(MAX_DELIVERIES_PER_HOOK)
                )
            )
            if old:
                s.execute(delete(WebhookDeliveryRow).where(WebhookDeliveryRow.id.in_(old)))

    def deliveries(self, webhook_id: str | None = None, limit: int = 30) -> list[WebhookDelivery]:
        q = select(WebhookDeliveryRow).order_by(WebhookDeliveryRow.id.desc()).limit(limit)
        if webhook_id:
            q = q.where(WebhookDeliveryRow.webhook_id == webhook_id)
        with self._sessions() as s:
            return [_delivery(r) for r in s.scalars(q)]

    # ---- API tokens
    def create_token(self, name: str) -> tuple[str, ApiToken]:
        """Returns (plain token, record). The plain token is never stored."""
        plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
        tid = _id()
        now = utcnow()
        with self._sessions.begin() as s:
            s.add(
                ApiTokenRow(
                    id=tid,
                    name=name[:120],
                    hash=hash_token(plain),
                    prefix=plain[:8],
                    created_at=now,
                )
            )
        return plain, ApiToken(tid, name[:120], plain[:8], now)

    def tokens(self) -> list[ApiToken]:
        with self._sessions() as s:
            return [
                ApiToken(
                    r.id,
                    r.name,
                    r.prefix,
                    utc(r.created_at) or utcnow(),
                    utc(r.last_used_at),
                    utc(r.revoked_at),
                )
                for r in s.scalars(select(ApiTokenRow).order_by(ApiTokenRow.created_at.desc()))
            ]

    def revoke_token(self, tid: str) -> bool:
        with self._sessions.begin() as s:
            r = s.get(ApiTokenRow, tid)
            if r is None or r.revoked_at is not None:
                return False
            r.revoked_at = utcnow()
            return True

    def verify_token(self, plain: str) -> bool:
        """True for a live token. Records last use at most once a minute."""
        if not plain.startswith(TOKEN_PREFIX):
            return False
        with self._sessions.begin() as s:
            r = s.scalar(select(ApiTokenRow).where(ApiTokenRow.hash == hash_token(plain)))
            if r is None or r.revoked_at is not None:
                return False
            now = utcnow()
            last = utc(r.last_used_at)
            if last is None or now - last > timedelta(minutes=1):
                r.last_used_at = now
            return True

    # ---- notifications
    def add_notification(
        self, title: str, body: str = "", href: str | None = None, kind: str = "workflow"
    ) -> int:
        with self._sessions.begin() as s:
            row = NotificationRow(
                kind=kind, title=title[:300], body=body[:600], href=href, created_at=utcnow()
            )
            s.add(row)
            s.flush()
            return row.id

    def notifications(self, limit: int = 30) -> list[Notification]:
        q = select(NotificationRow).order_by(NotificationRow.id.desc()).limit(limit)
        with self._sessions() as s:
            return [
                Notification(r.id, r.kind, r.title, r.body, r.href, utc(r.created_at) or utcnow())
                for r in s.scalars(q)
            ]


# ------------------------------------------------------------------------------ access


def repo_for(store: Any) -> ToolsRepo:
    """The ToolsRepo of a store (cached on the store). A store without an engine (a test fake)
    gets a private in-memory database, so every page still works."""
    repo = getattr(store, "_lc_tools_repo", None)
    if isinstance(repo, ToolsRepo):
        return repo
    engine = getattr(store, "engine", None)
    if engine is None:
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
    Base.metadata.create_all(engine)
    repo = ToolsRepo(engine)
    try:
        store._lc_tools_repo = repo
    except Exception:  # slots or frozen object: build a new one next time
        pass
    return repo
