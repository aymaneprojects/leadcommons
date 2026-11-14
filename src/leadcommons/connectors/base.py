"""Connector contract: one plug-in per public data source. SHARED CONTRACT."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from leadcommons.models import Company

USER_AGENT = (
    "LeadCommons/0.1 (+https://github.com/leadcommons/leadcommons; open-source prospecting)"
)


class Connector(Protocol):
    name: str  # short id, e.g. "wikidata"
    licence: str  # SPDX id of the data, e.g. "CC0-1.0"

    def iter_companies(
        self, *, country: str | None = None, limit: int | None = None
    ) -> Iterator[Company]:
        """Yield normalised companies (with Provenance in .sources). Must be polite: rate-limit,
        send USER_AGENT, retry with backoff on 429/5xx."""
        ...
