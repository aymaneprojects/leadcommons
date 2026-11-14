"""Local user database (SQLite by default, Postgres optional). Holds personal data:
people, emails, lists, saved searches, suppression list. Never synced anywhere.

SHARED CONTRACT (signatures). Owner: store agent. Replace the stubs, keep the signatures.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Engine, create_engine, delete, event, func, or_, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from leadcommons.config import Settings
from leadcommons.models import (
    Company,
    EmailCandidate,
    EmailStatus,
    Person,
    Seniority,
    utcnow,
)
from leadcommons.search import Page, PeopleFilters
from leadcommons.store.codec import (
    company_from_dict,
    company_to_dict,
    email_from_row,
    person_from_row,
    utc,
)
from leadcommons.store.tables import (
    Base,
    CompanyRow,
    EmailRow,
    ListItemRow,
    ListRow,
    PersonRow,
    SavedSearchRow,
    SuppressionRow,
)

ListKind = Literal["people", "companies"]


@dataclass
class ListInfo:
    id: str
    name: str
    kind: ListKind
    size: int
    created_at: datetime


@dataclass
class SavedSearch:
    id: str
    name: str
    kind: ListKind
    filters_json: str
    created_at: datetime


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _keep(new: Any, old: Any) -> Any:
    """New value unless it is empty, in which case the existing value is kept."""
    return old if new is None or new == "" else new


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _normalise_target(email_or_domain: str) -> tuple[str, str]:
    """Return (normalised value, kind). A leading '@' ("@example.com") means a domain."""
    value = email_or_domain.strip().lower()
    if value.startswith("@"):
        value = value[1:]
    if not value:
        raise ValueError("empty suppression value")
    return value, ("email" if "@" in value else "domain")


class Store:
    def __init__(self, url: str | None = None) -> None:
        """url: SQLAlchemy URL. Default: sqlite at $LEADCOMMONS_HOME/leadcommons.db
        ($LEADCOMMONS_HOME defaults to ~/.leadcommons). Creates tables if missing."""
        self.url = url or Settings().database_url
        parsed = make_url(self.url)
        is_sqlite = parsed.get_backend_name() == "sqlite"
        if is_sqlite and parsed.database not in (None, "", ":memory:"):
            Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._engine: Engine = create_engine(
            self.url,
            connect_args={"check_same_thread": False} if is_sqlite else {},
            pool_pre_ping=not is_sqlite,
        )
        if is_sqlite:
            event.listen(self._engine, "connect", _sqlite_pragmas)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(bind=self._engine, expire_on_commit=False)

    def close(self) -> None:
        self._engine.dispose()

    # companies saved locally (copies of pack rows the user acted on)
    def save_company(self, company: Company) -> None:
        with self._sessions.begin() as s:
            row = s.get(CompanyRow, company.id)
            if row is None:
                row = CompanyRow(id=company.id, name=company.name)
                s.add(row)
            row.name = company.name
            row.domain = company.domain
            row.country = company.country
            row.region = company.region
            row.city = company.city
            row.industry = company.industry
            row.industry_label = company.industry_label
            row.size_range = company.size_range
            row.data = company_to_dict(company)
            row.saved_at = utcnow()

    def get_company(self, company_id: str) -> Company | None:
        with self._sessions() as s:
            row = s.get(CompanyRow, company_id)
            return company_from_dict(row.data) if row is not None else None

    def saved_company_ids(self) -> set[str]:
        with self._sessions() as s:
            return set(s.scalars(select(CompanyRow.id)))

    # people and emails
    def save_person(self, person: Person) -> None:
        """Upsert. Keeps first_seen and non-empty fields when the new value is empty,
        merges source_urls without duplicates, and moves last_seen forward."""
        with self._sessions.begin() as s:
            row = s.get(PersonRow, person.id)
            if row is None:
                row = PersonRow(
                    id=person.id,
                    full_name=person.full_name,
                    first_seen=utc(person.first_seen),  # type: ignore[arg-type]
                    last_seen=utc(person.last_seen),  # type: ignore[arg-type]
                    source_urls=[],
                )
                s.add(row)
            row.full_name = _keep(person.full_name, row.full_name)
            row.company_domain = _keep(person.company_domain, row.company_domain)
            row.company_id = _keep(person.company_id, row.company_id)
            row.title = _keep(person.title, row.title)
            row.seniority = _keep(
                person.seniority.value if person.seniority else None, row.seniority
            )
            row.department = _keep(person.department, row.department)
            merged = list(row.source_urls or [])
            for url in person.source_urls:
                if url not in merged:
                    merged.append(url)
            row.source_urls = merged
            incoming = utc(person.last_seen)
            current = utc(row.last_seen)
            row.last_seen = max(x for x in (current, incoming) if x is not None)  # type: ignore[type-var]

    def get_person(self, person_id: str) -> Person | None:
        with self._sessions() as s:
            row = s.get(PersonRow, person_id)
            return person_from_row(row) if row is not None else None

    def save_email(self, person_id: str, email: EmailCandidate) -> None:
        """Upsert on (person_id, address). Addresses are stored lowercase."""
        address = email.address.strip().lower()
        with self._sessions.begin() as s:
            row = s.scalar(
                select(EmailRow).where(EmailRow.person_id == person_id, EmailRow.address == address)
            )
            if row is None:
                row = EmailRow(person_id=person_id, address=address)
                s.add(row)
            row.status = email.status.value
            row.confidence = email.confidence
            row.pattern = email.pattern
            row.checks = dict(email.checks)
            row.checked_at = utc(email.checked_at)  # type: ignore[assignment]

    def best_email(self, person_id: str) -> EmailCandidate | None:
        """Highest confidence; ties go to the most recently checked address."""
        with self._sessions() as s:
            rows = s.scalars(select(EmailRow).where(EmailRow.person_id == person_id)).all()
            if not rows:
                return None
            best = max(rows, key=lambda r: (r.confidence, utc(r.checked_at)))  # type: ignore[type-var]
            return email_from_row(best)

    def search_people(
        self, filters: PeopleFilters, page: int = 1, per_page: int = 25
    ) -> Page[Person]:
        page = max(1, page)
        per_page = max(1, per_page)
        conditions = []
        query = select(PersonRow)

        needle = (filters.text or "").strip().lower()
        if needle:
            conditions.append(
                or_(
                    func.lower(PersonRow.full_name).contains(needle, autoescape=True),
                    func.lower(PersonRow.title).contains(needle, autoescape=True),
                )
            )
        titles = [t.strip().lower() for t in filters.titles if t.strip()]
        if titles:
            conditions.append(
                or_(*(func.lower(PersonRow.title).contains(t, autoescape=True) for t in titles))
            )
        if filters.seniorities:
            conditions.append(
                PersonRow.seniority.in_([Seniority(s).value for s in filters.seniorities])
            )
        if filters.email_statuses:
            statuses = [EmailStatus(s).value for s in filters.email_statuses]
            conditions.append(
                PersonRow.id.in_(select(EmailRow.person_id).where(EmailRow.status.in_(statuses)))
            )

        company = filters.company
        domains = [d.strip().lower() for d in company.domains if d.strip()]
        if domains:
            conditions.append(func.lower(PersonRow.company_domain).in_(domains))
        company_conditions = []
        if company.countries:
            company_conditions.append(
                CompanyRow.country.in_([c.upper() for c in company.countries])
            )
        if company.regions:
            company_conditions.append(CompanyRow.region.in_(company.regions))
        if company.industries:
            company_conditions.append(CompanyRow.industry.in_(company.industries))
        if company.size_ranges:
            company_conditions.append(CompanyRow.size_range.in_(company.size_ranges))
        if company_conditions:
            query = query.join(CompanyRow, CompanyRow.id == PersonRow.company_id)
            conditions.extend(company_conditions)

        if conditions:
            query = query.where(*conditions)
        ordered = query.order_by(PersonRow.last_seen.desc(), PersonRow.id)
        with self._sessions() as s:
            total = s.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = s.scalars(ordered.offset((page - 1) * per_page).limit(per_page)).all()
            items = [person_from_row(r) for r in rows]
        return Page(items=items, total=total, page=page, per_page=per_page)

    def saved_person_ids(self) -> set[str]:
        query = (
            select(ListItemRow.record_id)
            .join(ListRow, ListRow.id == ListItemRow.list_id)
            .where(ListRow.kind == "people")
        )
        with self._sessions() as s:
            return set(s.scalars(query))

    # lists
    def create_list(self, name: str, kind: ListKind) -> str:
        list_id = uuid.uuid4().hex[:12]
        with self._sessions.begin() as s:
            s.add(ListRow(id=list_id, name=name, kind=kind, created_at=utcnow()))
        return list_id

    def add_to_list(self, list_id: str, record_ids: list[str]) -> None:
        """Append records in the given order. Records already in the list are skipped."""
        with self._sessions.begin() as s:
            present = set(
                s.scalars(select(ListItemRow.record_id).where(ListItemRow.list_id == list_id))
            )
            position = (
                s.scalar(
                    select(func.coalesce(func.max(ListItemRow.position), 0)).where(
                        ListItemRow.list_id == list_id
                    )
                )
                or 0
            )
            for record_id in record_ids:
                if record_id in present:
                    continue
                present.add(record_id)
                position += 1
                s.add(
                    ListItemRow(
                        list_id=list_id,
                        record_id=record_id,
                        added_at=utcnow(),
                        position=position,
                    )
                )

    def remove_from_list(self, list_id: str, record_ids: list[str]) -> None:
        if not record_ids:
            return
        with self._sessions.begin() as s:
            s.execute(
                delete(ListItemRow).where(
                    ListItemRow.list_id == list_id, ListItemRow.record_id.in_(record_ids)
                )
            )

    def lists(self, kind: ListKind | None = None) -> list[ListInfo]:
        query = (
            select(
                ListRow.id,
                ListRow.name,
                ListRow.kind,
                ListRow.created_at,
                func.count(ListItemRow.record_id),
            )
            .outerjoin(ListItemRow, ListItemRow.list_id == ListRow.id)
            .group_by(ListRow.id, ListRow.name, ListRow.kind, ListRow.created_at)
            .order_by(ListRow.created_at, ListRow.id)
        )
        if kind is not None:
            query = query.where(ListRow.kind == kind)
        with self._sessions() as s:
            return [
                ListInfo(
                    id=row[0],
                    name=row[1],
                    kind=row[2],  # type: ignore[arg-type]
                    created_at=utc(row[3]),  # type: ignore[arg-type]
                    size=row[4],
                )
                for row in s.execute(query).all()
            ]

    def list_members(self, list_id: str) -> list[str]:
        query = (
            select(ListItemRow.record_id)
            .where(ListItemRow.list_id == list_id)
            .order_by(ListItemRow.position)
        )
        with self._sessions() as s:
            return list(s.scalars(query))

    # saved searches
    def save_search(self, name: str, kind: ListKind, filters_json: str) -> str:
        search_id = uuid.uuid4().hex[:12]
        with self._sessions.begin() as s:
            s.add(
                SavedSearchRow(
                    id=search_id,
                    name=name,
                    kind=kind,
                    filters_json=filters_json,
                    created_at=utcnow(),
                )
            )
        return search_id

    def saved_searches(self, kind: ListKind | None = None) -> list[SavedSearch]:
        query = select(SavedSearchRow).order_by(SavedSearchRow.created_at, SavedSearchRow.id)
        if kind is not None:
            query = query.where(SavedSearchRow.kind == kind)
        with self._sessions() as s:
            return [
                SavedSearch(
                    id=r.id,
                    name=r.name,
                    kind=r.kind,  # type: ignore[arg-type]
                    filters_json=r.filters_json,
                    created_at=utc(r.created_at),  # type: ignore[arg-type]
                )
                for r in s.scalars(query)
            ]

    # suppression (opt-out); stores sha256 of lowercase email or domain only
    def add_suppression(self, email_or_domain: str, reason: str = "opt-out") -> None:
        value, kind = _normalise_target(email_or_domain)
        digest = _digest(value)
        with self._sessions.begin() as s:
            row = s.get(SuppressionRow, digest)
            if row is None:
                row = SuppressionRow(hash=digest)
                s.add(row)
            row.kind = kind
            row.reason = reason
            row.added_at = utcnow()

    def is_suppressed(self, email: str) -> bool:
        address = email.strip().lower()
        domain = address.rsplit("@", 1)[-1]
        hashes = {_digest(address), _digest(domain)}
        with self._sessions() as s:
            found = s.scalar(select(SuppressionRow.hash).where(SuppressionRow.hash.in_(hashes)))
        return found is not None


__all__ = ["ListInfo", "ListKind", "SavedSearch", "Store"]
