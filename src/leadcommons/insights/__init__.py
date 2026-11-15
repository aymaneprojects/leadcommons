"""Company insights: technologies, job postings, business phones, social links, trends.
Owner: data agent. SHARED CONTRACT (signatures; implement, keep them).
"""

from __future__ import annotations

from typing import Any


def get(store: Any, company_id: str) -> Any | None:
    """Stored CompanyInsights for a company, or None if never refreshed."""
    raise NotImplementedError


def refresh(store: Any, company: Any) -> Any:
    """Crawl the company's public site (and public job boards / Wikidata) and store the result."""
    raise NotImplementedError


def lookalikes(search: Any, company: Any, limit: int = 25) -> list[Any]:
    """Companies similar to `company` in the packs (industry, size, country)."""
    raise NotImplementedError
