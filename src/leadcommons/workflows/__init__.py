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
"""

from __future__ import annotations

from typing import Any

EVENTS = (
    "person_found",
    "email_found",
    "added_to_list",
    "email_sent",
    "email_replied",
    "task_completed",
    "deal_stage_changed",
)


def emit(event: str, **payload: Any) -> None:
    """Notify workflows of an event. No-op until the workflow engine is installed."""
    return None
