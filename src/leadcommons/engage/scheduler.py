"""The sender: runs due sequence steps and scheduled emails, then checks for replies.

`run_due(store, settings, now)` does one pass and is safe to call from several processes:
a lease in the database lets only one pass run at a time. The web app runs it every
60 seconds in a background thread (local and server modes, never in demo or with
sync_jobs); `leadcommons worker` runs it in a loop for Docker.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timedelta
from typing import Any

from leadcommons.engage import core, mailer
from leadcommons.engage.render import render
from leadcommons.models import utcnow
from leadcommons.store.engage import EngageRepo, MailboxRow, StepRow, repo_for

log = logging.getLogger("leadcommons.engage")

INTERVAL = 60.0
LOCK = "scheduler"
LEASE = timedelta(minutes=5)


def _owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"[:64]


class _Run:
    """State for one pass: open SMTP connections, mailboxes that failed, counters."""

    def __init__(self, store: Any, settings: Any, now: datetime) -> None:
        self.store, self.settings, self.now = store, settings, now
        self.repo: EngageRepo = repo_for(store)
        self.cfg = self.repo.get_settings()
        self.mailboxes = {m.id: m for m in self.repo.mailboxes()}
        self.broken: set[str] = set()
        self.conns: dict[str, Any] = {}
        self.stats = {
            "sent": 0,
            "failed": 0,
            "bounced": 0,
            "tasks": 0,
            "finished": 0,
            "waiting": 0,
            "stopped": 0,
            "replies": 0,
        }

    def mailbox_for(self, mailbox_id: str | None) -> MailboxRow | None:
        if mailbox_id and mailbox_id in self.mailboxes:
            return self.mailboxes[mailbox_id]
        ok = [m for m in self.mailboxes.values() if m.status == "ok"]
        boxes = ok or list(self.mailboxes.values())
        return boxes[0] if boxes else None

    def conn(self, mailbox: MailboxRow) -> Any:
        if mailbox.id not in self.conns:
            self.conns[mailbox.id] = mailer.smtp_connect(self.settings, mailbox)
        return self.conns[mailbox.id]

    def close(self) -> None:
        for conn in self.conns.values():
            mailer._quit(conn)  # noqa: SLF001 - same feature
        self.conns.clear()

    def send(self, mailbox: MailboxRow, **kw: Any) -> int | None:
        """deliver() with counters; None when nothing was sent."""
        try:
            conn = self.conn(mailbox)
            row_id = core.deliver(
                self.store, self.settings, mailbox, cfg=self.cfg, now=self.now, conn=conn, **kw
            )
        except mailer.MailboxError as exc:
            self.broken.add(mailbox.id)
            self.repo.update_mailbox(mailbox.id, status="error", last_error=str(exc))
            self.conns.pop(mailbox.id, None)
            return None
        except mailer.RecipientRefused:
            self.stats["bounced"] += 1
            return None
        except mailer.SendError:
            self.stats["failed"] += 1
            raise
        self.stats["sent"] += 1
        return row_id

    def blocked(self, mailbox: MailboxRow) -> bool:
        if mailbox.id in self.broken:
            return True
        return core.capacity_problem(self.repo, mailbox, self.cfg, self.now) is not None


def run_due(store: Any, settings: Any, now: datetime | None = None) -> dict[str, int]:
    """One scheduler pass. Never sends in demo mode."""
    if core.is_demo(settings):
        return {"sent": 0, "disabled": 1}
    now = now or utcnow()
    repo = repo_for(store)
    owner = _owner()
    if not repo.acquire_lock(LOCK, owner, now + LEASE, now):
        return {"sent": 0, "busy": 1}
    run = _Run(store, settings, now)
    try:
        _scheduled_messages(run)
        _due_steps(run)
        _poll_replies(run)
    finally:
        run.close()
        repo.release_lock(LOCK, owner)
    return run.stats


# ---------------------------------------------------------------- one-off scheduled emails


def _scheduled_messages(run: _Run) -> None:
    for m in run.repo.due_scheduled(run.now):
        mailbox = run.mailbox_for(m.mailbox_id)
        if mailbox is None or run.blocked(mailbox):
            continue
        try:
            run.send(
                mailbox,
                to=m.to_email,
                subject=m.subject,
                body=m.body,
                person_id=m.person_id,
                task_id=m.task_id,
                row_id=m.id,
            )
        except core.EngageError as exc:
            run.repo.update_message(m.id, status="failed", error=str(exc))
            run.stats["failed"] += 1
        except mailer.SendError:
            pass


# ---------------------------------------------------------------- sequence steps


def _due_steps(run: _Run) -> None:
    repo, now = run.repo, run.now
    seqs: dict[str, Any] = {}
    steps_of: dict[str, list[StepRow]] = {}
    for e in repo.due_enrollments(now):
        if e.sequence_id not in seqs:
            seqs[e.sequence_id] = repo.sequence(e.sequence_id)
            steps_of[e.sequence_id] = repo.steps(e.sequence_id)
        seq, steps = seqs[e.sequence_id], steps_of[e.sequence_id]
        if seq is None:
            continue
        if e.step_index >= len(steps):
            repo.update_enrollment(e.id, status="finished", next_run_at=None, finished_at=now)
            run.stats["finished"] += 1
            continue
        if run.store.is_suppressed(e.email):
            repo.update_enrollment(
                e.id,
                status="unsubscribed",
                next_run_at=None,
                finished_at=now,
                note="On the do-not-contact list",
            )
            run.stats["stopped"] += 1
            continue
        step = steps[e.step_index]
        values = {**(e.vars or {})}
        if step.kind == "auto_email":
            _email_step(run, seq, steps, e, step, values)
        else:
            _task_step(run, seq, e, step, values)


def _email_step(
    run: _Run, seq: Any, steps: list[StepRow], e: Any, step: StepRow, values: dict[str, Any]
) -> None:
    repo, now = run.repo, run.now
    mailbox = run.mailbox_for(seq.mailbox_id)
    if mailbox is None:
        if e.note != "Waiting for a mailbox":
            repo.update_enrollment(e.id, note="Waiting for a mailbox")
        run.stats["waiting"] += 1
        return
    start, end, days, tz = core.window_of(seq, mailbox)
    if not core.in_window(now, start, end, days, tz) or run.blocked(mailbox):
        run.stats["waiting"] += 1
        return
    values["sender_name"] = mailbox.from_name
    own_subject = render(step.subject, values).strip()
    threaded = not own_subject and bool(e.last_message_id)
    if own_subject:
        subject = own_subject
    elif e.thread_subject:
        subject = (
            e.thread_subject
            if e.thread_subject.lower().startswith("re:")
            else f"Re: {e.thread_subject}"
        )
    else:
        subject = "(no subject)"
    try:
        row_id = run.send(
            mailbox,
            to=e.email,
            subject=subject,
            body=render(step.body, values),
            person_id=e.person_id,
            sequence_id=seq.id,
            enrollment_id=e.id,
            step_id=step.id,
            in_reply_to=e.last_message_id if threaded else None,
        )
    except core.EngageError as exc:  # suppressed or invalid since enrollment
        repo.update_enrollment(
            e.id, status="unsubscribed", next_run_at=None, finished_at=now, note=str(exc)[:300]
        )
        run.stats["stopped"] += 1
        return
    except mailer.SendError as exc:
        repo.update_enrollment(
            e.id, status="paused", note=f"Paused after a send error: {exc}"[:300]
        )
        return
    if row_id is None:
        return  # mailbox unusable (retried next pass) or bounced (enrollment already stopped)
    sent = repo.message(row_id)
    status = core.advance(
        repo,
        e,
        steps,
        now,
        last_message_id=sent.message_id if sent else e.last_message_id,
        thread_subject=e.thread_subject or subject,
        note="",
    )
    if status == "finished":
        run.stats["finished"] += 1


def _task_step(run: _Run, seq: Any, e: Any, step: StepRow, values: dict[str, Any]) -> None:
    repo = run.repo
    mailbox = run.mailbox_for(seq.mailbox_id)
    values["sender_name"] = mailbox.from_name if mailbox else ""
    kind = core.STEP_TASK_KIND.get(step.kind, "action")
    name = values.get("full_name") or e.email
    titles = {
        "manual_email": f"Email {name}",
        "call": f"Call {name}",
        "linkedin": f"Connect with {name} on LinkedIn",
        "task": render(step.subject, values).strip() or f"Follow up with {name}",
    }
    task_id = core.new_task(
        run.store,
        titles.get(step.kind, f"Follow up with {name}"),
        person_id=e.person_id,
        due=run.now,
        kind=kind,
        priority=step.priority or "medium",
        subject=render(step.subject, values) if step.kind == "manual_email" else "",
        body=render(step.body if step.kind == "manual_email" else step.note, values),
        sequence_id=seq.id,
        enrollment_id=e.id,
        step_id=step.id,
    )
    repo.update_enrollment(e.id, task_id=task_id, next_run_at=None, note="Waiting on a task")
    run.stats["tasks"] += 1


# ---------------------------------------------------------------- replies


def _poll_replies(run: _Run) -> None:
    from leadcommons.engage.replies import poll_mailbox

    every = timedelta(minutes=max(1, int(run.cfg.get("reply_poll_minutes") or 5)))
    for mailbox in run.repo.mailboxes():
        if not mailbox.has_imap:
            continue
        if mailbox.imap_polled_at and run.now - mailbox.imap_polled_at < every:
            continue
        found = poll_mailbox(run.store, run.settings, mailbox, run.now)
        run.stats["replies"] += found["replies"]
        run.stats["bounced"] += found["bounces"]


# ---------------------------------------------------------------- background loop


def scheduler_allowed(settings: Any) -> bool:
    if core.is_demo(settings) or getattr(settings, "sync_jobs", False):
        return False
    flag = os.environ.get("LEADCOMMONS_ENGAGE_SCHEDULER", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


class Background:
    """Daemon thread calling run_due every `interval` seconds until stopped."""

    def __init__(
        self, store: Any, settings: Any, interval: float = INTERVAL, delay: float = 15.0
    ) -> None:
        self.store, self.settings = store, settings
        self.interval, self.delay = interval, delay
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="engage-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(self.delay):
            return
        while not self._stop.is_set():
            try:
                stats = run_due(self.store, self.settings)
                if stats.get("sent") or stats.get("replies"):
                    log.info("engage pass: %s", stats)
            except Exception:  # noqa: BLE001 - keep the loop alive
                log.exception("engage scheduler pass failed")
            if self._stop.wait(self.interval):
                return


def install(app: Any) -> None:
    """Start the background sender with the app (skipped in demo, with sync_jobs, or when
    LEADCOMMONS_ENGAGE_SCHEDULER=0, e.g. when a separate worker container runs it)."""
    settings = app.state.settings
    if not scheduler_allowed(settings) or getattr(app.state.store, "engine", None) is None:
        return
    holder: dict[str, Background] = {}

    def _start() -> None:
        bg = Background(app.state.store, settings)
        holder["bg"] = bg
        bg.start()

    def _stop() -> None:
        bg = holder.pop("bg", None)
        if bg is not None:
            bg.stop()

    app.on_event("startup")(_start)
    app.on_event("shutdown")(_stop)


def loop_forever(store: Any, settings: Any, interval: float = INTERVAL, once: bool = False) -> None:
    """`leadcommons worker`: run passes until interrupted."""
    stop = threading.Event()
    while True:
        stats = run_due(store, settings)
        log.info("engage pass: %s", stats)
        if once or stop.wait(interval):
            return
