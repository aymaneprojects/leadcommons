"""Enrichment: find people on a company's public pages, guess and verify their emails.

SHARED CONTRACT (signatures). Owner: enrich agent. Replace the stubs, keep the signatures.
All network access must respect robots.txt, send connectors.base.USER_AGENT and rate-limit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace

from leadcommons.enrich.crawl import Site, get_site, normalise_domain
from leadcommons.enrich.emails import extract_emails
from leadcommons.enrich.patterns import rank_guesses
from leadcommons.enrich.people import extract_people, gather_people
from leadcommons.enrich.score import confidence
from leadcommons.enrich.verify import verify_addresses
from leadcommons.models import Company, EmailCandidate, EmailStatus, Person

MAX_PROBES = 3  # candidates tried over SMTP by find_email


def _site_people(site: Site) -> list[Person]:
    if site.people is None:
        site.people = extract_people(site.pages, site.domain)
    return site.people


def _site_emails(site: Site) -> list[str]:
    if site.emails is None:
        site.emails = extract_emails(site.pages, site.domain)
    return site.emails


def find_people(
    domain: str,
    *,
    company: Company | None = None,
    max_pages: int = 10,
    is_suppressed: Callable[[str], bool] | None = None,
    known_addresses: Callable[[str], Iterable[str]] | None = None,
    report: list | None = None,
    sources: Iterable | None = None,
) -> list[Person]:
    """People of one company: the official sources that apply to `company` (Wikidata, SEC
    insider filings, Companies House, Brreg; chosen by its registry ids) merged with the people
    on its public pages (team, about, leadership, legal notice / mentions légales, contact).

    Duplicates are merged, the official title wins, every source URL is kept. `is_suppressed`
    (store.is_suppressed) stops everything for an opted-out domain; `known_addresses(person_id)`
    drops people with an opted-out address on file. `report` receives one SourceReport per
    source (ok, skipped with a reason, error) for the UI."""
    domain = domain.strip().lower() if domain else ""

    def website() -> list[Person]:
        site = get_site(domain, max_pages=max_pages)
        return [replace(p, source_urls=list(p.source_urls)) for p in _site_people(site)]

    return gather_people(
        domain or None,
        website if domain else None,
        company=company,
        sources=sources,
        is_suppressed=is_suppressed,
        known_addresses=known_addresses,
        report=report,
    )


def find_public_emails(domain: str, *, max_pages: int = 10) -> list[str]:
    """Email addresses on `domain` visible on its public pages (used to infer the pattern)."""
    return list(_site_emails(get_site(domain, max_pages=max_pages)))


def guess_emails(
    person: Person, domain: str, known_emails: Sequence[str] = ()
) -> list[EmailCandidate]:
    """Candidate addresses for `person`, best first, using the pattern inferred from
    known_emails when possible ({first}.{last}, {f}{last}, {first}, ...)."""
    guesses = rank_guesses(person.full_name, normalise_domain(domain), known_emails)
    return [
        EmailCandidate(g.address, confidence=round(g.points), pattern=g.pattern) for g in guesses
    ]


def verify_email(address: str, *, smtp_probe: bool = False) -> EmailCandidate:
    """Syntax + MX + catch-all checks; optional live RCPT probe (off by default)."""
    return verify_addresses([address], smtp_probe=smtp_probe)[0]


def find_email(person: Person, domain: str, *, smtp_probe: bool = False) -> EmailCandidate | None:
    """Convenience: public emails -> pattern -> candidates -> verify; best candidate or None."""
    site = get_site(domain)
    names = [p.full_name for p in _site_people(site)]
    guesses = rank_guesses(person.full_name, site.domain, _site_emails(site), names)
    # Without a probe every candidate on the domain verifies the same, so check only the best.
    batch = guesses[:MAX_PROBES] if smtp_probe else guesses[:1]
    best: EmailCandidate | None = None
    for guess, cand in zip(
        batch, verify_addresses([g.address for g in batch], smtp_probe=smtp_probe), strict=True
    ):
        if cand.status is EmailStatus.INVALID:
            continue
        cand.pattern = guess.pattern
        cand.confidence = confidence(guess.points, cand.status, cand.checks)
        if best is None or cand.confidence > best.confidence:
            best = cand
    return best
