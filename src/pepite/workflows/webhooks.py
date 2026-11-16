"""Outgoing webhooks: signed JSON POSTs to URLs the user chose. Owner: tools agent.

Every delivery carries `X-Pepite-Event` and, when a secret is set,
`X-Pepite-Signature: sha256=<hex hmac of the raw body>`. Nothing is sent in demo mode.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from pepite import __version__
from pepite.models import utcnow

log = logging.getLogger(__name__)

TIMEOUT = 10.0
SIGNATURE_HEADER = "X-Pepite-Signature"
EVENT_HEADER = "X-Pepite-Event"
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lc-hook")


@dataclass
class Result:
    ok: bool
    status_code: int | None
    error: str | None
    duration_ms: int


def check_url(url: str) -> str | None:
    """None when the URL is acceptable, otherwise a sentence for the user."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "Enter a full URL starting with http:// or https://."
    if len(url) > 2000:
        return "That URL is too long."
    return None


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def envelope(event: str, data: dict[str, Any], **extra: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "event": event,
        "sent_at": utcnow().isoformat(),
        "source": f"pepite/{__version__}",
        "data": data,
    }
    out.update({k: v for k, v in extra.items() if v})
    return out


def post_json(
    url: str, body: dict[str, Any], *, secret: str | None = None, timeout: float = TIMEOUT
) -> Result:
    """POST `body` as JSON. Never raises: failures come back in the Result."""
    raw = json.dumps(body, default=str, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Pepite/{__version__}",
        EVENT_HEADER: str(body.get("event", "")),
    }
    if secret:
        headers[SIGNATURE_HEADER] = sign(secret, raw)
    started = time.monotonic()
    try:
        resp = httpx.post(
            url, content=raw, headers=headers, timeout=timeout, follow_redirects=False
        )
        ms = int((time.monotonic() - started) * 1000)
        if resp.status_code >= 400:
            return Result(False, resp.status_code, f"HTTP {resp.status_code}", ms)
        return Result(True, resp.status_code, None, ms)
    except Exception as exc:  # network, TLS, timeout, invalid URL
        ms = int((time.monotonic() - started) * 1000)
        return Result(False, None, f"{type(exc).__name__}: {exc}"[:240], ms)


def deliver(ctx: Any, hook: Any, event: str, data: dict[str, Any]) -> Result:
    """One logged delivery to a stored webhook."""
    res = post_json(hook.url, envelope(event, data), secret=hook.secret)
    try:
        ctx.repo.add_delivery(hook.id, event, res.ok, res.status_code, res.error, res.duration_ms)
    except Exception:
        log.exception("could not log webhook delivery")
    return res


def fan_out(ctx: Any, hooks: list[Any], event: str, data: dict[str, Any]) -> None:
    """Deliver an event to every hook: inline when jobs run inline, else on a small pool."""
    for hook in hooks:
        if getattr(ctx.settings, "sync_jobs", False):
            deliver(ctx, hook, event, data)
        else:
            try:
                _pool.submit(deliver, ctx, hook, event, data)
            except RuntimeError:
                pass
