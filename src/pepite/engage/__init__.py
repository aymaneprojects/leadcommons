"""Engage: mailboxes, sequences, outbound email, tasks. Owner: engage agent.

SHARED CONTRACT (signatures used by workflows and the assistant; implement, keep them).
The rules live in pepite.engage.core; the sender in pepite.engage.scheduler.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def enroll(store: Any, sequence_id: str, person_ids: list[str]) -> dict[str, int]:
    """Enroll people in a sequence. Returns {"enrolled": n, "skipped": m}; skips suppressed
    people, people without a usable email and people already active in it.

    The result also breaks the skips down: duplicate, no_email, invalid_email, suppressed,
    missing. Raises pepite.engage.core.EngageError when the sequence is missing or
    archived."""
    from pepite.engage.core import enroll_people

    return enroll_people(store, sequence_id, list(person_ids or []))


def create_task(
    store: Any,
    title: str,
    *,
    person_id: str | None = None,
    company_id: str | None = None,
    deal_id: str | None = None,
    due: datetime | None = None,
    kind: str = "action",
) -> str:
    """Create a task (kind: call, email, action, linkedin) and return its id."""
    from pepite.engage.core import new_task

    return new_task(
        store,
        title,
        person_id=person_id,
        company_id=company_id,
        deal_id=deal_id,
        due=due,
        kind=kind,
    )
