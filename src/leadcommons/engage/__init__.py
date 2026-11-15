"""Engage: mailboxes, sequences, outbound email, tasks. Owner: engage agent.

SHARED CONTRACT (signatures used by workflows and the assistant; implement, keep them).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def enroll(store: Any, sequence_id: str, person_ids: list[str]) -> dict[str, int]:
    """Enroll people in a sequence. Returns {"enrolled": n, "skipped": m}; skips suppressed
    people, people without a usable email and people already active in it."""
    raise NotImplementedError


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
    raise NotImplementedError
