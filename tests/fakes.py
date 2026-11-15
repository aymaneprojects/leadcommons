"""In-memory fakes of the shared contracts, for tests of web/UI code. Owner: main."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from leadcommons.models import Company, EmailCandidate, EmailStatus, Person, Provenance, make_id
from leadcommons.search import CompanyFilters, Page, PeopleFilters
from leadcommons.store import ListInfo, SavedSearch
from leadcommons.web.jobs import Job


def sample_companies() -> list[Company]:
    rows = [
        (
            "Atelier Nuage",
            "ateliernuage.example",
            "FR",
            "Lyon",
            "J",
            "Information and communication",
            "11-50",
        ),
        (
            "Nordwind Data",
            "nordwind.example",
            "DE",
            "Hamburg",
            "J",
            "Information and communication",
            "51-200",
        ),
        (
            "Brücke Software",
            "bruecke.example",
            "DE",
            "Berlin",
            "J",
            "Information and communication",
            "11-50",
        ),
        (
            "Moss and Pine",
            "mosspine.example",
            "FR",
            "Paris",
            "M",
            "Professional activities",
            "1-10",
        ),
        ("Kestrel Systems", "kestrel.example", "GB", "Leeds", "C", "Manufacturing", "201-500"),
    ]
    out = []
    for name, domain, cc, city, isic, label, size in rows:
        out.append(
            Company(
                id=make_id("co", domain),
                name=name,
                domain=domain,
                country=cc,
                city=city,
                industry=isic,
                industry_label=label,
                size_range=size,
                links={"website": f"https://{domain}"},
                sources=[Provenance(source="fake", licence="CC0-1.0")],
            )
        )
    return out


class FakeSearch:
    def __init__(self, companies: list[Company] | None = None) -> None:
        self.companies = companies if companies is not None else sample_companies()

    def _match(self, c: Company, f: CompanyFilters) -> bool:
        if f.text and f.text.lower() not in f"{c.name} {c.domain or ''}".lower():
            return False
        places = (
            (f.countries, c.country),
            ([r.lower() for r in f.regions], (c.region or "").lower()),
            ([x.lower() for x in f.cities], (c.city or "").lower()),
        )
        if any(vals for vals, _ in places) and not any(have in vals for vals, have in places):
            return False
        if f.exclude_countries and (not c.country or c.country in f.exclude_countries):
            return False
        if (c.region or "").lower() in [r.lower() for r in f.exclude_regions]:
            return False
        if (c.city or "").lower() in [x.lower() for x in f.exclude_cities]:
            return False
        if f.industries and c.industry not in f.industries:
            return False
        if f.exclude_industries and (not c.industry or c.industry in f.exclude_industries):
            return False
        # Include keywords are not matched by this fake (tests rely on it); exclusions are.
        hay = f"{c.name} {c.legal_name or ''} {c.domain or ''} {c.industry_label or ''}".lower()
        if any(k.lower() in hay for k in f.exclude_keywords):
            return False
        if f.size_ranges and c.size_range not in f.size_ranges:
            return False
        if f.domains and c.domain not in f.domains:
            return False
        return True

    def search_companies(self, filters: CompanyFilters, page: int = 1, per_page: int = 25):
        hits = [c for c in self.companies if self._match(c, filters)]
        start = (page - 1) * per_page
        return Page(
            items=hits[start : start + per_page], total=len(hits), page=page, per_page=per_page
        )

    def get_company(self, company_id: str) -> Company | None:
        return next((c for c in self.companies if c.id == company_id), None)

    def facet_counts(self, facet: str, filters: CompanyFilters, limit: int = 10) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.companies:
            if self._match(c, filters):
                key = getattr(c, facet)
                if key:
                    counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1])[:limit])

    def pack_info(self) -> list[dict]:
        return [
            {
                "file": "fake.parquet",
                "rows": len(self.companies),
                "sources": ["fake"],
                "built_at": None,
            }
        ]


class FakeStore:
    def __init__(self) -> None:
        self.companies: dict[str, Company] = {}
        self.people: dict[str, Person] = {}
        self.emails: dict[str, list[EmailCandidate]] = {}
        self._lists: dict[str, ListInfo] = {}
        self.members: dict[str, list[str]] = {}
        self.searches: dict[str, SavedSearch] = {}
        self.suppressed: set[str] = set()

    def save_company(self, company: Company) -> None:
        self.companies[company.id] = company

    def saved_company_ids(self) -> set[str]:
        return set(self.companies)

    def save_person(self, person: Person) -> None:
        self.people[person.id] = person

    def get_person(self, person_id: str) -> Person | None:
        return self.people.get(person_id)

    def save_email(self, person_id: str, email: EmailCandidate) -> None:
        self.emails.setdefault(person_id, []).append(email)

    def best_email(self, person_id: str) -> EmailCandidate | None:
        cands = self.emails.get(person_id) or []
        return max(cands, key=lambda e: e.confidence) if cands else None

    def search_people(self, filters: PeopleFilters, page: int = 1, per_page: int = 25):
        hits = list(self.people.values())
        if filters.text:
            t = filters.text.lower()
            hits = [p for p in hits if t in f"{p.full_name} {p.title or ''}".lower()]
        if filters.email_statuses:
            hits = [
                p
                for p in hits
                if (e := self.best_email(p.id)) and e.status in filters.email_statuses
            ]
        start = (page - 1) * per_page
        return Page(
            items=hits[start : start + per_page], total=len(hits), page=page, per_page=per_page
        )

    def saved_person_ids(self) -> set[str]:
        return {
            rid
            for lid, ids in self.members.items()
            if self._lists[lid].kind == "people"
            for rid in ids
        }

    def create_list(self, name: str, kind: str) -> str:
        lid = uuid.uuid4().hex[:12]
        self._lists[lid] = ListInfo(
            id=lid, name=name, kind=kind, size=0, created_at=datetime.now(UTC)
        )
        self.members[lid] = []
        return lid

    def add_to_list(self, list_id: str, record_ids: list[str]) -> None:
        for rid in record_ids:
            if rid not in self.members[list_id]:
                self.members[list_id].append(rid)
        self._lists[list_id] = replace(self._lists[list_id], size=len(self.members[list_id]))

    def remove_from_list(self, list_id: str, record_ids: list[str]) -> None:
        self.members[list_id] = [r for r in self.members[list_id] if r not in record_ids]
        self._lists[list_id] = replace(self._lists[list_id], size=len(self.members[list_id]))

    def lists(self, kind: str | None = None) -> list[ListInfo]:
        return [li for li in self._lists.values() if kind is None or li.kind == kind]

    def list_members(self, list_id: str) -> list[str]:
        return list(self.members.get(list_id, []))

    def save_search(self, name: str, kind: str, filters_json: str) -> str:
        sid = uuid.uuid4().hex[:12]
        self.searches[sid] = SavedSearch(sid, name, kind, filters_json, datetime.now(UTC))
        return sid

    def saved_searches(self, kind: str | None = None) -> list[SavedSearch]:
        return [s for s in self.searches.values() if kind is None or s.kind == kind]

    def add_suppression(self, email_or_domain: str, reason: str = "opt-out") -> None:
        self.suppressed.add(email_or_domain.strip().lower())

    def is_suppressed(self, email: str) -> bool:
        e = email.strip().lower()
        return e in self.suppressed or e.split("@")[-1] in self.suppressed


class FakeJobs:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.jobs: dict[str, Job] = {}

    def submit(self, kind, payload, label) -> str:
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = Job(id=jid, kind=kind, label=label, payload=payload, status="running")
        return jid

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[Job]:
        return list(self.jobs.values())[-limit:]

    def running_count(self) -> int:
        return sum(1 for j in self.jobs.values() if j.status in ("queued", "running"))

    def shutdown(self) -> None:
        pass


def sample_person(company: Company, name: str = "Claire Martin", title: str = "CEO") -> Person:
    return Person(
        id=make_id("pe", company.domain or "", name),
        full_name=name,
        company_domain=company.domain,
        company_id=company.id,
        title=title,
        source_urls=[f"https://{company.domain}/team"],
    )


def sample_email(
    address: str, status: EmailStatus = EmailStatus.VALID, confidence: int = 90
) -> EmailCandidate:
    return EmailCandidate(
        address=address, status=status, confidence=confidence, pattern="{first}.{last}"
    )
