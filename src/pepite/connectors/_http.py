"""Polite HTTP helper: User-Agent, spacing between requests, backoff, Retry-After."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

import httpx

from pepite.connectors.base import USER_AGENT

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            from datetime import UTC, datetime

            return max(0.0, (parsedate_to_datetime(raw) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError):
            return None


class PoliteClient:
    """Sequential GET client with minimum spacing and exponential backoff."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        min_interval: float = 1.0,
        max_retries: int = 5,
        timeout: float = 90.0,
        sleep: Callable[[float], None] = time.sleep,
        max_wait: float = 60.0,
        max_total_wait: float = 240.0,
        wait_429: float = 30.0,
        user_agent: str = USER_AGENT,
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._sleep = sleep
        self.max_wait = max_wait
        self.max_total_wait = max_total_wait
        self.wait_429 = wait_429
        self.user_agent = user_agent
        self._last = 0.0

    def get_json(
        self,
        url: str,
        params: dict[str, str | int],
        accept: str,
        auth: tuple[str, str] | None = None,
    ) -> dict:
        """GET and decode JSON, retrying transient failures; raises httpx errors when exhausted."""
        return self._get(url, params, accept, auth).json()

    def get_text(
        self,
        url: str,
        params: dict[str, str | int],
        accept: str,
        auth: tuple[str, str] | None = None,
    ) -> str:
        """Same as get_json for text bodies (XML, HTML)."""
        return self._get(url, params, accept, auth).text

    def _get(
        self,
        url: str,
        params: dict[str, str | int],
        accept: str,
        auth: tuple[str, str] | None,
    ) -> httpx.Response:
        headers = {"User-Agent": self.user_agent, "Accept": accept}
        attempt = 0
        waited = 0.0
        while True:
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            try:
                kwargs = {"auth": auth} if auth else {}
                resp = self._client.get(url, params=params, headers=headers, **kwargs)
                self._last = time.monotonic()
                if resp.status_code not in RETRY_STATUSES:
                    resp.raise_for_status()
                    return resp
                delay = _retry_after(resp)
                if resp.status_code == 429 and delay is None:
                    delay = self.wait_429  # no Retry-After given: be generous
            except (httpx.TransportError, httpx.TimeoutException):
                self._last = time.monotonic()
                delay = None
            attempt += 1
            pause = (
                min(self.max_wait, delay) if delay is not None else min(self.max_wait, 2.0**attempt)
            )
            if attempt > self.max_retries or waited + pause > self.max_total_wait:
                raise httpx.HTTPError(f"giving up on {url} after {attempt} attempts")
            log.warning("retrying %s in %.0fs (attempt %d)", url.split("?")[0], pause, attempt)
            waited += pause
            self._sleep(pause)
