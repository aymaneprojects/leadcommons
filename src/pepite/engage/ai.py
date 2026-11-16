"""Draft a sequence from a short goal with the user's own model (optional).

Uses the same OpenAI-compatible server as the rest of Pépite (PEPITE_LLM_URL and
PEPITE_LLM_MODEL). Only the goal typed by the user is sent: no contact data. The answer
is validated strictly (known step types, known variables, bounded lengths); anything else is
dropped. Never called in demo mode.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from pepite.engage.render import VARIABLES

log = logging.getLogger("pepite.engage")

TIMEOUT_SECONDS = 60.0
MAX_STEPS = 8
STEP_KINDS = ("auto_email", "manual_email", "call", "linkedin", "task")
OFF_REASON = (
    "To turn this on, set PEPITE_LLM_URL and PEPITE_LLM_MODEL (any OpenAI-compatible "
    "server, for example Ollama) and restart."
)
_ALLOWED = ", ".join("{{" + v + "}}" for v in VARIABLES)
SYSTEM_PROMPT = f"""You write short, polite B2B prospecting sequences.
Answer with JSON only: {{"name": str, "steps": [step, ...]}} with 2 to {MAX_STEPS} steps.
Each step: {{"kind": one of {list(STEP_KINDS)}, "delay_days": integer 0-14 (wait after the
previous step; 0 for the first), "subject": str, "body": str, "note": str}}.
Emails ("auto_email", "manual_email") need a body of at most 90 words, plain text, no links,
no tracking, no fake familiarity, ending with the sender's name as {{{{sender_name}}}}. Leave
the subject empty on follow-ups so they reply in the same thread; the first email needs one.
Other steps ("call", "linkedin", "task") use "note" for what the user should do; LinkedIn
steps are always done by hand. Allowed variables only: {_ALLOWED};
add a fallback like {{{{first_name|there}}}}."""

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*(\|[^}]*)?\}\}")


def llm_ready() -> bool:
    from pepite.ai.llm import llm_config

    return llm_config() is not None


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()[:limit]

    def keep(m: re.Match[str]) -> str:
        return m.group(0) if m.group(1).lower() in VARIABLES else ""

    return _VAR_RE.sub(keep, text)


def validate(payload: Any) -> dict[str, Any] | None:
    """{"name": str, "steps": [...]} ready for EngageRepo.create_sequence, or None."""
    if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
        return None
    steps: list[dict[str, Any]] = []
    for raw in payload["steps"][:MAX_STEPS]:
        if not isinstance(raw, dict) or raw.get("kind") not in STEP_KINDS:
            continue
        try:
            delay = max(0, min(30, int(raw.get("delay_days") or 0)))
        except (TypeError, ValueError):
            delay = 0
        step = {
            "kind": raw["kind"],
            "delay_days": 0 if not steps else delay,
            "subject": _clean_text(raw.get("subject"), 200),
            "body": _clean_text(raw.get("body"), 3000),
            "note": _clean_text(raw.get("note"), 1000),
            "priority": "medium",
        }
        if step["kind"] in ("auto_email", "manual_email") and not step["body"]:
            continue
        steps.append(step)
    if not steps:
        return None
    first_email = next((s for s in steps if s["kind"] == "auto_email"), None)
    if first_email is not None and not first_email["subject"]:
        first_email["subject"] = "Quick question for {{company|your team}}"
    name = _clean_text(payload.get("name"), 120) or "Drafted sequence"
    return {"name": re.sub(r"\{\{.*?\}\}", "", name).strip() or "Drafted sequence", "steps": steps}


def _extract_json(content: str) -> Any:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return json.loads(fence.group(1) if fence else text)


def draft_sequence(goal: str, steps: int = 4) -> dict[str, Any] | None:
    """Ask the configured model for a sequence. None when it is off or the answer is unusable."""
    from pepite.ai.llm import llm_config

    cfg = llm_config()
    if cfg is None:
        return None
    base, model, key = cfg
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    want = max(2, min(MAX_STEPS, steps))
    body = {
        "model": model,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Goal: {goal.strip()[:600]}\nNumber of steps: {want}"},
        ],
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions", json=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return validate(_extract_json(content))
    except Exception as exc:  # noqa: BLE001 - network, HTTP, JSON: the caller says so
        log.warning("sequence draft failed (%s)", type(exc).__name__)
        return None
