"""Workflow automation: "when this happens, do that". Owner: tools agent.

SHARED CONTRACT: other features call `emit(event, **payload)` at key moments; it must never
raise into the caller and must be cheap when no workflow listens. Events and payload keys:

    person_found        person_id, company_id, domain
    email_found         person_id, address, status
    added_to_list       list_id, record_ids, kind
    email_sent          person_id, sequence_id (optional), message_id
    email_replied       person_id, sequence_id (optional)
    task_completed      task_id, person_id (optional)
    deal_stage_changed  deal_id, stage, previous_stage

The engine (workflows.engine) evaluates the enabled workflows listening to the event, runs
their actions and logs every run. It needs the running app's store, search, jobs and settings:
create_app binds them through `bind_app` (see web/auth.py), so events raised in a job thread or
an API call reach the same database as the pages. Without a bound app, emit does nothing.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

EVENTS = (
    "person_found",
    "email_found",
    "added_to_list",
    "email_sent",
    "email_replied",
    "task_completed",
    "deal_stage_changed",
)

EVENT_LABELS = {
    "person_found": "A person is found",
    "email_found": "An email address is found",
    "added_to_list": "Records are added to a list",
    "email_sent": "An email is sent",
    "email_replied": "A reply arrives",
    "task_completed": "A task is completed",
    "deal_stage_changed": "A deal changes stage",
}

_app: Any = None  # the bound FastAPI app (its .state holds store, search, jobs, settings)
_guard = threading.local()  # workflows never trigger workflows: nested emits are ignored


def bind_app(app: Any) -> None:
    """Remember the running app so emit() can reach its store. Called by create_app."""
    global _app
    _app = app


def bound_app() -> Any:
    return _app


def emit(event: str, **payload: Any) -> None:
    """Notify workflows and outgoing webhooks of an event. Never raises."""
    try:
        if event not in EVENTS or _app is None or getattr(_guard, "depth", 0) > 0:
            return
        from pepite.workflows import engine

        engine.dispatch(_app, event, payload)
    except Exception:  # an automation bug must never break the caller
        log.exception("workflow emit(%s) failed", event)


class running:
    """Context manager the engine uses while it executes actions (blocks re-entrant emits)."""

    def __enter__(self) -> None:
        _guard.depth = getattr(_guard, "depth", 0) + 1

    def __exit__(self, *exc: object) -> None:
        _guard.depth = max(0, getattr(_guard, "depth", 1) - 1)
