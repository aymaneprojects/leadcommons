"""Company insights: technologies, job postings, business phones, social links, trends.
Owner: data agent. SHARED CONTRACT (signatures; implement, keep them).

Everything comes from public sources: the company's own website (polite crawler, robots.txt
respected), public DNS, the job-board APIs applicant-tracking systems expose for embedding,
and Wikidata. No logins, no scraping of third-party sites, no personal data in the result.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pepite.insights import boards, dnsinfo, fetch, techs, trends
from pepite.insights import contact as contact_mod
from pepite.insights.model import (
    CompanyInsights,
    Contact,
    EmployeePoint,
    JobPosting,
    Jobs,
    Listing,
    Phone,
    RevenuePoint,
    Technology,
    Trends,
)
from pepite.insights.similar import lookalikes as _lookalikes
from pepite.insights.similar import rank_lookalikes, similar_people

log = logging.getLogger(__name__)

__all__ = [
    "CompanyInsights",
    "Contact",
    "EmployeePoint",
    "JobPosting",
    "Jobs",
    "Listing",
    "Phone",
    "RefreshDisabled",
    "RevenuePoint",
    "Technology",
    "Trends",
    "get",
    "lookalikes",
    "rank_lookalikes",
    "refresh",
    "similar_people",
]


class RefreshDisabled(RuntimeError):
    """Raised when a refresh is attempted where crawling is not allowed (public demo)."""


def _repo(store: Any):
    from pepite.store.insights import InsightsRepo

    return InsightsRepo(store.engine)


def get(store: Any, company_id: str) -> CompanyInsights | None:
    """Stored CompanyInsights for a company, or None if never refreshed."""
    return _repo(store).get(company_id)


def refresh(store: Any, company: Any, *, settings: Any = None) -> CompanyInsights:
    """Crawl the company's public site (and public job boards / Wikidata) and store the result.

    Pass `settings` to enforce demo mode: when `settings.enrichment_enabled` is false nothing
    is fetched and RefreshDisabled is raised.
    """
    if settings is not None and not getattr(settings, "enrichment_enabled", True):
        raise RefreshDisabled("Refreshing insights is disabled in the public demo.")
    domain = company.domain
    result = CompanyInsights(company_id=company.id, domain=domain)
    notes = result.notes
    if domain:
        try:
            snap = fetch.get_snapshot(domain)
            result.pages_crawled = len(snap.pages)
            if not snap.pages:
                notes.append(
                    f"{domain} could not be read: unreachable, blocked by robots.txt or not HTML."
                )
            dns = dnsinfo.lookup(domain)
            result.technologies = techs.detect(
                [p.html for p in snap.pages],
                headers=snap.headers,
                cookies=snap.cookies,
                dns_records=dns,
            )
            result.contact = contact_mod.extract(snap.pages, domain, company.country)
            result.jobs = boards.collect_jobs(snap.links)
            if not result.jobs.ats and snap.pages:
                notes.append(
                    "No supported job board (Greenhouse, Lever, Ashby, Workable, "
                    "Recruitee, SmartRecruiters) is linked from the site."
                )
        except Exception as exc:  # keep partial results; the page says what failed
            log.exception("insights crawl failed for %s", domain)
            notes.append(f"Website analysis stopped early ({type(exc).__name__}).")
    else:
        notes.append("This company has no known website, so only Wikidata was checked.")
    try:
        found = trends.fetch_trends(
            company.name, domain, (company.registry_ids or {}).get("wikidata")
        )
        if found is None:
            notes.append(
                "No matching Wikidata entry, so employee, revenue and listing data is empty."
            )
        else:
            result.trends = found
    except (httpx.HTTPError, ValueError) as exc:
        log.info("wikidata failed for %s: %s", company.id, exc)
        notes.append("Wikidata could not be reached.")
    _repo(store).save(result)
    return _repo(store).get(company.id) or result


def lookalikes(search: Any, company: Any, limit: int = 25) -> list[Any]:
    """Companies similar to `company` in the packs (industry, size, country)."""
    return _lookalikes(search, company, limit)
