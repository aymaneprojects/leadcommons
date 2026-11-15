"""Engage rules shared by the web pages, the scheduler and the worker.

Every outgoing email goes through `deliver()`, which re-checks demo mode, the suppression
list and the address syntax, appends the signature and the unsubscribe footer, records the
message and logs the activity. Nothing else in the code base talks to SMTP.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from email_validator import EmailNotValidError, validate_email

from leadcommons.engage import mailer
from leadcommons.engage.crypto import make_unsubscribe_token
from leadcommons.engage.render import (
    compose_text,
    footer_text,
    mailto_unsubscribe,
    person_vars,
    render,
    text_to_html,
)
from leadcommons.models import EmailStatus, utcnow
from leadcommons.store.engage import (
    TASK_KINDS,
    EngageRepo,
    EnrollmentRow,
    MailboxRow,
    SequenceRow,
    StepRow,
    repo_for,
)

log = logging.getLogger("leadcommons.engage")

STEP_TASK_KIND = {"manual_email": "email", "call": "call", "linkedin": "linkedin", "task": "action"}
STEP_LABELS = {
    "auto_email": "Automatic email",
    "manual_email": "Manual email",
    "call": "Phone call",
    "linkedin": "LinkedIn task",
    "task": "Action item",
}
TASK_LABELS = {"call": "Call", "email": "Email", "linkedin": "LinkedIn", "action": "Action"}
DEMO_SEND_REASON = (
    "Sending is turned off in the public demo. Install LeadCommons to send from your own mailbox."
)


class EngageError(Exception):
    """A user-facing refusal: the message is safe to show in a toast."""


# ---------------------------------------------------------------- small helpers


def is_demo(settings: Any) -> bool:
    return getattr(settings, "mode", "local") == "demo"


def zone(name: str | None) -> Any:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 - unknown name or no tz database
        return UTC


def clean_address(value: str | None) -> str | None:
    """Normalised address, or None when the syntax is not valid."""
    value = (value or "").strip()
    if not value or len(value) > 320:
        return None
    try:
        info = validate_email(value, check_deliverability=False)
    except EmailNotValidError:
        return None
    return info.normalized.lower()


def log_activity(store: Any, kind: str, summary: str, **kw: Any) -> None:
    try:
        from leadcommons.store.pipeline import ActivityRepo

        ActivityRepo(repo_for(store).engine).add(kind, summary, **kw)
    except Exception:  # noqa: BLE001 - the timeline must never break sending
        log.exception("could not log activity %s", kind)


def emit(event: str, **payload: Any) -> None:
    try:
        from leadcommons.workflows import emit as _emit

        _emit(event, **payload)
    except Exception:  # noqa: BLE001 - workflows never break engage
        log.exception("workflow event %s failed", event)


def company_name_of(store: Any, person: Any) -> str | None:
    cid = getattr(person, "company_id", None)
    getter = getattr(store, "get_company", None)
    if cid and callable(getter):
        try:
            company = getter(cid)
        except Exception:  # noqa: BLE001
            company = None
        if company is not None:
            return company.name
    return None


def base_url(cfg: dict[str, Any]) -> str:
    url = str(cfg.get("public_url") or cfg.get("seen_base_url") or "http://localhost:8000")
    return url.rstrip("/")


def unsubscribe_url(settings: Any, cfg: dict[str, Any], email: str, message_id: int | None) -> str:
    return f"{base_url(cfg)}/u/{make_unsubscribe_token(settings, email, message_id)}"


# ---------------------------------------------------------------- schedules


def window_of(
    seq: SequenceRow | None, mailbox: MailboxRow | None
) -> tuple[int, int, list[int], str]:
    src: Any = seq if seq is not None and seq.schedule_custom else mailbox
    if src is None:
        return 8, 18, [0, 1, 2, 3, 4], "UTC"
    return int(src.window_start), int(src.window_end), src.days, src.timezone or "UTC"


def in_window(now: datetime, start: int, end: int, days: list[int], tz: str) -> bool:
    local = now.astimezone(zone(tz))
    if local.weekday() not in days:
        return False
    if start == end:
        return True
    if start < end:
        return start <= local.hour < end
    return local.hour >= start or local.hour < end  # overnight window, e.g. 22-6


def next_window_open(
    now: datetime, start: int, end: int, days: list[int], tz: str
) -> datetime | None:
    """First moment at or after `now` inside the window (hour resolution)."""
    if not days:
        return None
    if in_window(now, start, end, days, tz):
        return now
    local = now.astimezone(zone(tz)).replace(minute=0, second=0, microsecond=0)
    for _ in range(24 * 8):
        local += timedelta(hours=1)
        if in_window(local, start, end, days, tz):
            return local.astimezone(UTC)
    return None


def day_start(now: datetime, tz: str) -> datetime:
    local = now.astimezone(zone(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def sent_today(repo: EngageRepo, mailbox: MailboxRow, now: datetime) -> int:
    return repo.sent_count_since(mailbox.id, day_start(now, mailbox.timezone))


def capacity_problem(
    repo: EngageRepo, mailbox: MailboxRow, cfg: dict[str, Any], now: datetime
) -> str | None:
    """Why this mailbox may not send right now (daily limit, spacing), or None."""
    if sent_today(repo, mailbox, now) >= max(0, int(mailbox.daily_limit or 0)):
        return f"Daily limit of {mailbox.daily_limit} reached for {mailbox.from_email}."
    gap = max(0, int(cfg.get("gap_minutes") or 0))
    if gap:
        last = repo.last_sent_at(mailbox.id)
        if last is not None and now - last < timedelta(minutes=gap):
            return "Spacing emails out."
    return None


def default_mailbox(repo: EngageRepo, mailbox_id: str | None = None) -> MailboxRow | None:
    if mailbox_id:
        found = repo.mailbox(mailbox_id)
        if found is not None:
            return found
    boxes = repo.mailboxes()
    return next((m for m in boxes if m.status == "ok"), boxes[0] if boxes else None)


# ---------------------------------------------------------------- enrollment


def check_person(store: Any, person_id: str) -> tuple[str | None, Any, str | None]:
    """(reason it cannot be emailed or None, person, address)."""
    person = store.get_person(person_id)
    if person is None:
        return "missing", None, None
    best = store.best_email(person_id)
    if best is None or not best.address:
        return "no_email", person, None
    status = getattr(best.status, "value", best.status)
    address = clean_address(best.address)
    if status == EmailStatus.INVALID.value or address is None:
        return "invalid_email", person, best.address
    if store.is_suppressed(address):
        return "suppressed", person, address
    return None, person, address


def enroll_people(
    store: Any,
    sequence_id: str,
    person_ids: list[str],
    company_names: dict[str, str] | None = None,
) -> dict[str, int]:
    repo = repo_for(store)
    seq = repo.sequence(sequence_id)
    if seq is None:
        raise EngageError("That sequence does not exist any more.")
    if seq.status == "archived":
        raise EngageError("That sequence is archived. Unarchive it first.")
    steps = repo.steps(sequence_id)
    first_delay = steps[0].delay_days if steps else 0
    counts = {
        "enrolled": 0,
        "skipped": 0,
        "suppressed": 0,
        "no_email": 0,
        "invalid_email": 0,
        "duplicate": 0,
        "missing": 0,
    }
    already = repo.enrolled_person_ids(sequence_id)
    now = utcnow()
    for pid in dict.fromkeys(p for p in person_ids if p):
        if pid in already:
            counts["duplicate"] += 1
            counts["skipped"] += 1
            continue
        reason, person, address = check_person(store, pid)
        if reason or address is None:
            counts[reason or "no_email"] += 1
            counts["skipped"] += 1
            continue
        company = (company_names or {}).get(pid) or company_name_of(store, person)
        added = repo.add_enrollment(
            sequence_id,
            pid,
            address,
            status="active",
            step_index=0,
            next_run_at=now + timedelta(days=max(0, first_delay)) if steps else None,
            vars=person_vars(person, company),
        )
        if added is None:
            counts["duplicate"] += 1
            counts["skipped"] += 1
            continue
        already.add(pid)
        counts["enrolled"] += 1
        log_activity(
            store,
            "sequence",
            f"Added to the sequence {seq.name}",
            person_id=pid,
            company_id=getattr(person, "company_id", None),
            meta={"sequence_id": sequence_id},
        )
    return counts


def enroll_summary(name: str, counts: dict[str, int]) -> str:
    n = counts.get("enrolled", 0)
    msg = f"Added {n} contact{'s' if n != 1 else ''} to {name}."
    parts = []
    labels = (
        ("duplicate", "already in it"),
        ("no_email", "no email"),
        ("invalid_email", "invalid email"),
        ("suppressed", "on the do-not-contact list"),
        ("missing", "not found"),
    )
    for key, label in labels:
        if counts.get(key):
            parts.append(f"{counts[key]} {label}")
    if parts:
        msg += " Skipped: " + ", ".join(parts) + "."
    return msg


def advance(
    repo: EngageRepo, e: EnrollmentRow, steps: list[StepRow], now: datetime, **extra: Any
) -> str:
    """Move a contact past its current step. Returns the new status."""
    nxt = e.step_index + 1
    if nxt >= len(steps):
        repo.update_enrollment(
            e.id,
            step_index=nxt,
            status="finished",
            next_run_at=None,
            task_id=None,
            finished_at=now,
            **extra,
        )
        return "finished"
    repo.update_enrollment(
        e.id,
        step_index=nxt,
        next_run_at=now + timedelta(days=max(0, steps[nxt].delay_days)),
        task_id=None,
        **extra,
    )
    return "active"


def set_contact_status(store: Any, enrollment_id: int, action: str) -> str:
    """Contact actions from the sequence page: pause, resume, replied, remove."""
    repo = repo_for(store)
    e = repo.enrollment(enrollment_id)
    if e is None:
        raise EngageError("That contact is no longer in the sequence.")
    now = utcnow()
    if action == "pause":
        if e.status != "active":
            raise EngageError("Only active contacts can be paused.")
        repo.update_enrollment(e.id, status="paused", note="Paused by you")
        return "Contact paused."
    if action == "resume":
        if e.status != "paused":
            raise EngageError("Only paused contacts can be resumed.")
        if store.is_suppressed(e.email):
            raise EngageError("This address is on the do-not-contact list.")
        task = repo.task(e.task_id) if e.task_id else None
        waiting = task is not None and task.status == "open"
        repo.update_enrollment(
            e.id,
            status="active",
            note="",
            next_run_at=None if waiting else (e.next_run_at or now),
        )
        return "Contact resumed."
    if action == "replied":
        mark_replied(store, e.id)
        return "Marked as replied. The sequence stopped for this contact."
    if action == "remove":
        if e.status not in ("active", "paused"):
            raise EngageError("This contact already left the sequence.")
        repo.update_enrollment(
            e.id, status="finished", next_run_at=None, finished_at=now, note="Removed by you"
        )
        return "Contact removed from the sequence."
    raise EngageError("Unknown action.")


def mark_replied(store: Any, enrollment_id: int, note: str = "Marked as replied") -> None:
    repo = repo_for(store)
    e = repo.enrollment(enrollment_id)
    if e is None:
        return
    seq = repo.sequence(e.sequence_id)
    repo.update_enrollment(
        e.id, status="replied", next_run_at=None, finished_at=utcnow(), note=note
    )
    log_activity(
        store,
        "email_reply",
        f"{note} in {seq.name if seq else 'a sequence'}",
        person_id=e.person_id,
        meta={"sequence_id": e.sequence_id, "manual": True},
    )
    emit("email_replied", person_id=e.person_id, sequence_id=e.sequence_id)


# ---------------------------------------------------------------- tasks


def new_task(
    store: Any,
    title: str,
    *,
    person_id: str | None = None,
    company_id: str | None = None,
    deal_id: str | None = None,
    due: datetime | None = None,
    kind: str = "action",
    priority: str = "medium",
    body: str = "",
    subject: str = "",
    **links: Any,
) -> str:
    title = (title or "").strip()[:300] or "Follow up"
    kind = kind if kind in TASK_KINDS else "action"
    priority = priority if priority in ("high", "medium", "low") else "medium"
    if person_id and not company_id:
        person = store.get_person(person_id)
        company_id = getattr(person, "company_id", None) if person else None
    task_id = repo_for(store).add_task(
        title=title,
        kind=kind,
        priority=priority,
        due_at=due,
        person_id=person_id,
        company_id=company_id,
        deal_id=deal_id,
        body=body,
        subject=subject,
        **links,
    )
    log_activity(
        store,
        "task",
        f"Task: {title}",
        person_id=person_id,
        company_id=company_id,
        deal_id=deal_id,
        meta={"task_id": task_id, "kind": kind},
    )
    return task_id


def finish_task(store: Any, task_id: str, status: str = "done", note: str = "") -> Any:
    """Complete or skip a task. A sequence waiting on it moves on to its next step."""
    repo = repo_for(store)
    task = repo.task(task_id)
    if task is None:
        raise EngageError("That task does not exist any more.")
    if task.status != "open":
        raise EngageError("This task is already closed.")
    now = utcnow()
    status = "skipped" if status == "skipped" else "done"
    repo.update_task(task_id, status=status, note=(note or "").strip()[:2000], completed_at=now)
    verb = "Completed" if status == "done" else "Skipped"
    log_activity(
        store,
        "task_done",
        f"{verb} task: {task.title}" + (f" ({note.strip()[:200]})" if note.strip() else ""),
        person_id=task.person_id,
        company_id=task.company_id,
        deal_id=task.deal_id,
        meta={"task_id": task_id, "status": status},
    )
    if status == "done":
        emit("task_completed", task_id=task_id, person_id=task.person_id)
    if task.enrollment_id:
        e = repo.enrollment(task.enrollment_id)
        if e is not None and e.task_id == task_id:
            if e.status == "active":
                advance(repo, e, repo.steps(e.sequence_id), now)
            else:  # paused: forget the task so resuming moves on
                repo.update_enrollment(
                    e.id, task_id=None, step_index=e.step_index + 1, next_run_at=now
                )
    return repo.task(task_id)


def reopen_task(store: Any, task_id: str) -> None:
    repo = repo_for(store)
    task = repo.task(task_id)
    if task is None:
        raise EngageError("That task does not exist any more.")
    repo.update_task(task_id, status="open", completed_at=None)


# ---------------------------------------------------------------- sending


def deliver(
    store: Any,
    settings: Any,
    mailbox: MailboxRow,
    *,
    to: str,
    subject: str,
    body: str,
    cfg: dict[str, Any] | None = None,
    person_id: str | None = None,
    sequence_id: str | None = None,
    enrollment_id: int | None = None,
    step_id: int | None = None,
    task_id: str | None = None,
    in_reply_to: str | None = None,
    row_id: int | None = None,
    now: datetime | None = None,
    conn: Any = None,
    keep_failed: bool = False,
) -> int:
    """Send one email and record it. Returns the message row id.

    Raises EngageError (refused before sending), mailer.MailboxError (mailbox unusable; no
    message recorded unless `row_id` was given), mailer.RecipientRefused (recorded as bounced)
    or mailer.SendError (recorded as failed).
    """
    if is_demo(settings):
        raise EngageError(DEMO_SEND_REASON)
    repo = repo_for(store)
    cfg = cfg or repo.get_settings()
    now = now or utcnow()
    address = clean_address(to)
    if address is None:
        raise EngageError(f"{to or 'This address'} is not a valid email address.")
    if store.is_suppressed(address):
        raise EngageError(f"{address} is on the do-not-contact list.")
    subject = (subject or "").strip()[:500] or "(no subject)"
    fields = {
        "direction": "out",
        "mailbox_id": mailbox.id,
        "person_id": person_id,
        "sequence_id": sequence_id,
        "enrollment_id": enrollment_id,
        "step_id": step_id,
        "task_id": task_id,
        "to_email": address,
        "from_email": mailbox.from_email,
        "subject": subject,
        "body": body,
        "in_reply_to": in_reply_to,
    }
    created = row_id is None
    if created:
        row_id = repo.add_message(status="sending", **fields)
    else:
        repo.update_message(row_id, **fields)
    assert row_id is not None
    link = unsubscribe_url(settings, cfg, address, row_id)
    footer = footer_text(cfg, link)
    outgoing = mailer.Outgoing(
        to=address,
        subject=subject,
        text=compose_text(body, mailbox.signature or "", footer),
        html=text_to_html(body, mailbox.signature or "", footer),
        from_email=mailbox.from_email,
        from_name=mailbox.from_name,
        in_reply_to=in_reply_to,
        list_unsubscribe=f"<{link}>, <{mailto_unsubscribe(mailbox.from_email)}>",
        one_click=link.startswith("https://"),
    )
    try:
        message_id = mailer.send(settings, mailbox, outgoing, conn=conn)
    except mailer.MailboxError as exc:
        if not created:
            repo.update_message(row_id, error=str(exc))  # stays scheduled; retried later
        elif keep_failed:
            repo.update_message(row_id, status="failed", error=str(exc))
        else:
            _drop_message(repo, row_id)  # the step is retried on the next run
        repo.update_mailbox(mailbox.id, status="error", last_error=str(exc))
        raise
    except mailer.RecipientRefused as exc:
        repo.update_message(row_id, status="bounced", error=str(exc), sent_at=now)
        handle_bounce(store, address, str(exc), cfg)
        raise
    except mailer.SendError as exc:
        repo.update_message(row_id, status="failed", error=str(exc))
        raise
    repo.update_message(row_id, status="sent", message_id=message_id, sent_at=now, error="")
    if mailbox.status != "ok" or mailbox.last_error:
        repo.update_mailbox(mailbox.id, status="ok", last_error="")
    company_id = None
    if person_id:
        person = store.get_person(person_id)
        company_id = getattr(person, "company_id", None) if person else None
    log_activity(
        store,
        "email_sent",
        f"Email sent: {subject}",
        person_id=person_id,
        company_id=company_id,
        meta={
            "message_id": message_id,
            "row_id": row_id,
            "sequence_id": sequence_id,
            "to": address,
        },
    )
    emit("email_sent", person_id=person_id, sequence_id=sequence_id, message_id=message_id)
    return row_id


def _drop_message(repo: EngageRepo, row_id: int) -> None:
    from sqlalchemy import delete

    from leadcommons.store.engage import MessageRow

    with repo._sessions.begin() as s:  # noqa: SLF001 - same feature
        s.execute(delete(MessageRow).where(MessageRow.id == row_id))


def handle_bounce(store: Any, address: str, reason: str, cfg: dict[str, Any]) -> None:
    repo = repo_for(store)
    if cfg.get("bounce_action", "suppress") == "suppress":
        try:
            store.add_suppression(address, reason="bounced")
        except Exception:  # noqa: BLE001
            log.exception("could not suppress a bounced address")
    for e in repo.stop_address(address, "bounced", f"Bounced: {reason[:200]}"):
        log_activity(
            store,
            "sequence",
            "Email bounced; the sequence stopped",
            person_id=e.person_id,
            meta={"sequence_id": e.sequence_id, "reason": reason[:200]},
        )


def send_email(
    store: Any,
    settings: Any,
    *,
    to: str,
    subject: str,
    body: str,
    person_id: str | None = None,
    mailbox_id: str | None = None,
    send_at: datetime | None = None,
    task_id: str | None = None,
) -> tuple[int, str]:
    """One-off email from the compose dialog. Returns (row id, "sent" | "scheduled")."""
    if is_demo(settings):
        raise EngageError(DEMO_SEND_REASON)
    repo = repo_for(store)
    cfg = repo.get_settings()
    mailbox = default_mailbox(repo, mailbox_id)
    if mailbox is None:
        raise EngageError("Connect a mailbox first in Settings, Mailboxes.")
    address = clean_address(to)
    if address is None:
        raise EngageError(f"{to or 'This address'} is not a valid email address.")
    if store.is_suppressed(address):
        raise EngageError(f"{address} is on the do-not-contact list.")
    if not (subject or "").strip():
        raise EngageError("Add a subject.")
    if not (body or "").strip():
        raise EngageError("Write a message first.")
    values = {}
    if person_id:
        person = store.get_person(person_id)
        values = person_vars(person, company_name_of(store, person))
    values["sender_name"] = mailbox.from_name
    subject, body = render(subject, values), render(body, values)
    now = utcnow()
    if send_at is not None and send_at > now + timedelta(minutes=1):
        row_id = repo.add_message(
            direction="out",
            status="scheduled",
            mailbox_id=mailbox.id,
            person_id=person_id,
            task_id=task_id,
            to_email=address,
            from_email=mailbox.from_email,
            subject=subject.strip()[:500],
            body=body,
            scheduled_at=send_at,
        )
        return row_id, "scheduled"
    problem = capacity_problem(repo, mailbox, {**cfg, "gap_minutes": 0}, now)
    if problem:
        raise EngageError(problem)
    try:
        row_id = deliver(
            store,
            settings,
            mailbox,
            to=address,
            subject=subject,
            body=body,
            cfg=cfg,
            person_id=person_id,
            task_id=task_id,
            now=now,
            keep_failed=True,
        )
    except mailer.MailboxError as exc:
        raise EngageError(f"Could not send: {exc}") from None
    except mailer.RecipientRefused as exc:
        raise EngageError(f"The server refused {address}: {exc}") from None
    except mailer.SendError as exc:
        raise EngageError(f"Could not send: {exc}") from None
    return row_id, "sent"


# ---------------------------------------------------------------- unsubscribe + replies


def unsubscribe(store: Any, address: str, reason: str = "unsubscribe link") -> int:
    """Suppress an address and stop everything going to it. Returns stopped enrollments."""
    address = address.strip().lower()
    store.add_suppression(address, reason=reason)
    repo = repo_for(store)
    stopped = repo.stop_address(address, "unsubscribed", "Unsubscribed")
    for e in stopped:
        log_activity(
            store,
            "sequence",
            "Unsubscribed; the sequence stopped",
            person_id=e.person_id,
            meta={"sequence_id": e.sequence_id},
        )
    for m in repo.messages(direction="out", statuses=["scheduled"], q=address, limit=500):
        if m.to_email == address:
            repo.update_message(m.id, status="failed", error="Unsubscribed before sending")
    return len(stopped)


_UNSUB_RE = re.compile(r"\b(unsubscribe|remove me|opt[ -]?out)\b", re.I)


def wants_out(text: str) -> bool:
    """True when the first lines of a reply (before any quote) ask to stop."""
    lines = []
    for line in (text or "").splitlines():
        if line.startswith(">") or re.match(r"^On .+wrote:$", line.strip()):
            break
        if line.strip():
            lines.append(line)
        if len(lines) >= 4:
            break
    return bool(_UNSUB_RE.search(" ".join(lines)))


def record_reply(
    store: Any,
    original: Any,
    *,
    message_id: str,
    from_email: str,
    subject: str,
    body: str,
    received_at: datetime | None = None,
    cfg: dict[str, Any] | None = None,
) -> bool:
    """Store an inbound reply to `original` (an outbound MessageRow). False if already known."""
    repo = repo_for(store)
    if message_id and repo.has_inbound(message_id):
        return False
    cfg = cfg or repo.get_settings()
    now = received_at or utcnow()
    repo.add_message(
        direction="in",
        status="received",
        mailbox_id=original.mailbox_id,
        person_id=original.person_id,
        sequence_id=original.sequence_id,
        enrollment_id=original.enrollment_id,
        to_email=original.from_email,
        from_email=(from_email or original.to_email)[:320],
        subject=(subject or "")[:500],
        body=(body or "")[:20000],
        message_id=message_id or None,
        in_reply_to=original.message_id,
        sent_at=now,
    )
    if original.replied_at is None:
        repo.update_message(original.id, replied_at=now)
    if wants_out(body):
        unsubscribe(store, original.to_email, reason="asked by reply")
    e = repo.enrollment(original.enrollment_id) if original.enrollment_id else None
    if e is not None and e.status in ("active", "paused"):
        seq = repo.sequence(e.sequence_id)
        stop = bool(cfg.get("stop_on_reply", True)) and (seq is None or seq.stop_on_reply)
        if stop:
            repo.update_enrollment(
                e.id, status="replied", next_run_at=None, finished_at=now, note="Replied"
            )
        else:
            repo.update_enrollment(e.id, note="Replied")
    log_activity(
        store,
        "email_reply",
        f"Reply received: {subject or '(no subject)'}",
        person_id=original.person_id,
        meta={"sequence_id": original.sequence_id, "message_id": message_id},
    )
    emit("email_replied", person_id=original.person_id, sequence_id=original.sequence_id)
    return True
