"""Data model of company insights. Plain dataclasses that round-trip through JSON."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import Any

from pepite.models import utcnow


@dataclass
class Technology:
    name: str
    category: str
    evidence: str  # where it matched, e.g. "script src: https://cdn.shopify.com/..."
    source: str  # html | script | meta | header | cookie | dns


@dataclass
class JobPosting:
    title: str
    source: str  # greenhouse | lever | ashby | workable | recruitee | smartrecruiters
    url: str | None = None
    department: str | None = None
    location: str | None = None
    remote: bool | None = None
    posted_at: str | None = None  # ISO date or datetime as given by the board


@dataclass
class Jobs:
    ats: str | None = None  # applicant tracking system detected on the careers page
    board: str | None = None  # board token / account name
    board_url: str | None = None
    postings: list[JobPosting] = field(default_factory=list)
    error: str | None = None  # why the board could not be read, if it could not

    def __iter__(self):
        return iter(self.postings)

    def __len__(self) -> int:
        return len(self.postings)

    def by_department(self) -> list[tuple[str, int]]:
        """Hiring signal: open postings per department, biggest first."""
        counts = Counter((p.department or "Other") for p in self.postings)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass
class Phone:
    number: str  # as written on the page
    e164: str | None  # normalised, None when the country is unknown
    source: str  # page URL or "json-ld"
    label: str | None = None


@dataclass
class Contact:
    phones: list[Phone] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)  # generic role mailboxes only
    address: dict[str, str] | None = None  # from JSON-LD PostalAddress
    social: dict[str, str] = field(default_factory=dict)  # network -> company page URL


@dataclass
class EmployeePoint:
    date: str  # ISO date (or year) the count refers to; "" when Wikidata gives no point in time
    count: int


@dataclass
class RevenuePoint:
    year: int | None
    amount: float
    currency: str | None  # label such as "United States dollar" or ISO code when known


@dataclass
class Listing:
    exchange: str
    ticker: str | None = None


@dataclass
class Trends:
    wikidata_id: str | None = None
    wikidata_url: str | None = None
    employees: list[EmployeePoint] = field(default_factory=list)
    revenue: list[RevenuePoint] = field(default_factory=list)
    listings: list[Listing] = field(default_factory=list)
    owned_by: list[str] = field(default_factory=list)

    def growth(self, years: int = 1) -> float | None:
        """Employee growth in percent between the latest point and the one ~`years` earlier."""
        pts = [(_parse_date(p.date), p.count) for p in self.employees]
        pts = sorted((d, n) for d, n in pts if d is not None)
        if len(pts) < 2:
            return None
        latest_d, latest_n = pts[-1]
        target = latest_d.replace(year=latest_d.year - years) if latest_d.year > years else None
        if target is None:
            return None
        best = min(pts[:-1], key=lambda p: abs((p[0] - target).days))
        if abs((best[0] - target).days) > 183 or best[1] <= 0:
            return None
        return round((latest_n - best[1]) * 100 / best[1], 1)


def _parse_date(text: str) -> datetime | None:
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(text[:size], fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


@dataclass
class CompanyInsights:
    company_id: str
    domain: str | None = None
    technologies: list[Technology] = field(default_factory=list)
    jobs: Jobs = field(default_factory=Jobs)
    contact: Contact = field(default_factory=Contact)
    trends: Trends = field(default_factory=Trends)
    pages_crawled: int = 0
    notes: list[str] = field(default_factory=list)  # honest limits of this refresh
    refreshed_at: datetime = field(default_factory=utcnow)

    @property
    def phones(self) -> list[Phone]:
        """Business phone numbers (shortcut used by the search pages)."""
        return self.contact.phones

    @property
    def job_postings(self) -> list[JobPosting]:
        """Open positions (shortcut used by the search pages)."""
        return self.jobs.postings

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["refreshed_at"] = self.refreshed_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompanyInsights:
        jobs = d.get("jobs") or {}
        contact = d.get("contact") or {}
        trends = d.get("trends") or {}
        return cls(
            company_id=d["company_id"],
            domain=d.get("domain"),
            technologies=[_build(Technology, t) for t in d.get("technologies") or []],
            jobs=Jobs(
                **{
                    **_known(Jobs, jobs),
                    "postings": [_build(JobPosting, p) for p in jobs.get("postings") or []],
                }
            ),
            contact=Contact(
                phones=[_build(Phone, p) for p in contact.get("phones") or []],
                emails=list(contact.get("emails") or []),
                address=contact.get("address"),
                social=dict(contact.get("social") or {}),
            ),
            trends=Trends(
                wikidata_id=trends.get("wikidata_id"),
                wikidata_url=trends.get("wikidata_url"),
                employees=[_build(EmployeePoint, p) for p in trends.get("employees") or []],
                revenue=[_build(RevenuePoint, p) for p in trends.get("revenue") or []],
                listings=[_build(Listing, p) for p in trends.get("listings") or []],
                owned_by=list(trends.get("owned_by") or []),
            ),
            pages_crawled=int(d.get("pages_crawled") or 0),
            notes=list(d.get("notes") or []),
            refreshed_at=datetime.fromisoformat(d["refreshed_at"])
            if d.get("refreshed_at")
            else utcnow(),
        )

    def technologies_by_category(self) -> list[tuple[str, list[Technology]]]:
        groups: dict[str, list[Technology]] = {}
        for t in self.technologies:
            groups.setdefault(t.category, []).append(t)
        return sorted(groups.items())


def _known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names and k != "postings"}


def _build(cls: type, data: dict[str, Any]) -> Any:
    return cls(**_known(cls, data))
