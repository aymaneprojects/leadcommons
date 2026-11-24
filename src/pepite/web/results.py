"""Helpers for the results screens: saved views, the save window and the company drawer.

Owner: results agent. No routes here (they live in web/pages.py); everything is plain functions
over the store so the routes stay thin and the logic is easy to test.
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any
from urllib.parse import parse_qsl

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from pepite.models import EmailStatus, Seniority, utcnow
from pepite.store.tables import SavedSearchRow

# ---------------------------------------------------------------- saved views

# Built-in views: (key, name, query). They cannot be renamed or deleted; duplicate one to get
# an editable copy.
SYSTEM_VIEWS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "people": (
        ("default", "Default view", "all=1"),
        ("saved", "People in your lists", "tab=saved"),
        ("new", "People not in a list yet", "tab=new"),
        ("valid", "People with a valid email", "status=valid"),
    ),
    "companies": (
        ("default", "Default view", "all=1"),
        ("saved", "Companies you saved", "tab=saved"),
        ("new", "Companies not saved yet", "tab=new"),
    ),
}
SYSTEM_LOCKED = "Built-in view. Duplicate it to get a copy you can rename, star or delete."
SHARED_OFF = (
    "Pépite keeps your searches on this computer for one person, so there is no one to "
    "share them with. Copy the page address to pass a search on."
)


def _sessions(store: Any) -> Any:
    return sessionmaker(bind=store.engine, expire_on_commit=False)


def view_payload(filters_json: str) -> dict[str, Any]:
    """{"query": str, "starred": bool} from a stored filters_json (old rows hold a bare query)."""
    try:
        raw = json.loads(filters_json)
    except ValueError:
        return {"query": filters_json, "starred": False}
    if isinstance(raw, dict):
        return {"query": str(raw.get("query") or ""), "starred": bool(raw.get("starred"))}
    return {"query": str(raw), "starred": False}


IGNORED_PARAMS = {"all", "page", "notice"}  # "all=1" only skips the landing screen


def _pairs(query: str) -> list[tuple[str, str]]:
    return sorted((k, v) for k, v in parse_qsl(query) if k not in IGNORED_PARAMS)


def _same_query(a: str, b: str) -> bool:
    return _pairs(a) == _pairs(b)


def view_rows(kind: str, store: Any, current_qs: str, base: str) -> dict[str, Any]:
    """Rows of the saved-view menu (built-in first), and the name of the view in use."""
    rows: list[dict[str, Any]] = []
    current = ""
    for key, name, query in SYSTEM_VIEWS.get(kind, ()):
        active = _same_query(query, current_qs)
        if active and not current:
            current = name
        rows.append(
            {
                "id": f"system:{key}",
                "name": name,
                "url": f"{base}?{query}" if query else base,
                "query": query,
                "system": True,
                "starred": False,
                "active": active,
            }
        )
    for s in store.saved_searches(kind):
        payload = view_payload(s.filters_json)
        query = payload["query"]
        active = bool(query) and _same_query(query, current_qs)
        if active:
            current = s.name  # a saved view wins over a built-in one with the same filters
        rows.append(
            {
                "id": s.id,
                "name": s.name,
                "url": f"{base}?{query}" if query else base,
                "query": query,
                "system": False,
                "starred": payload["starred"],
                "active": active,
            }
        )
    if current:
        for r in rows:
            r["active"] = r["name"] == current and r["active"]
    return {"rows": rows, "current": current}


def get_view(store: Any, view_id: str) -> SavedSearchRow | None:
    with _sessions(store)() as s:
        return s.get(SavedSearchRow, view_id)


def rename_view(store: Any, view_id: str, name: str) -> bool:
    with _sessions(store).begin() as s:
        row = s.get(SavedSearchRow, view_id)
        if row is None:
            return False
        row.name = name
        return True


def star_view(store: Any, view_id: str, on: bool) -> bool:
    with _sessions(store).begin() as s:
        row = s.get(SavedSearchRow, view_id)
        if row is None:
            return False
        payload = view_payload(row.filters_json)
        row.filters_json = json.dumps({"query": payload["query"], "starred": bool(on)})
        return True


def delete_view(store: Any, view_id: str) -> bool:
    with _sessions(store).begin() as s:
        res = s.execute(delete(SavedSearchRow).where(SavedSearchRow.id == view_id))
        return bool(res.rowcount)


def copy_name(name: str, taken: set[str]) -> str:
    base = f"{name} (copy)"
    if base not in taken:
        return base
    n = 2
    while f"{name} (copy {n})" in taken:
        n += 1
    return f"{name} (copy {n})"


def duplicate_view(store: Any, kind: str, view_id: str) -> str | None:
    """Copy a saved or built-in view. Returns the new id, or None when the view is unknown."""
    if view_id.startswith("system:"):
        key = view_id.split(":", 1)[1]
        match = next((v for v in SYSTEM_VIEWS.get(kind, ()) if v[0] == key), None)
        if match is None:
            return None
        name, query = match[1], match[2]
    else:
        row = get_view(store, view_id)
        if row is None:
            return None
        name, query, kind = row.name, view_payload(row.filters_json)["query"], row.kind
    with _sessions(store)() as s:
        taken = set(s.scalars(select(SavedSearchRow.name).where(SavedSearchRow.kind == kind)))
    new_id = uuid.uuid4().hex[:12]
    with _sessions(store).begin() as s:
        s.add(
            SavedSearchRow(
                id=new_id,
                name=copy_name(name, taken)[:120],
                kind=kind,
                filters_json=json.dumps({"query": query, "starred": False}),
                created_at=utcnow(),
            )
        )
    return new_id


# ---------------------------------------------------------------- columns

# Shown in the field editor as the type of each column (icon name of the results sprite).
FIELD_ICONS = {
    "name": "user",
    "title": "briefcase",
    "company": "building-2",
    "email": "mail",
    "phone": "phone",
    "source": "link",
    "sources": "link",
    "seniority": "gauge",
    "department": "users",
    "domain": "globe",
    "location": "map-pin",
    "industry": "factory",
    "size": "users",
    "status": "mail-check",
    "last_seen": "clock",
    "country": "map-pin",
    "city": "map-pin",
    "region": "map-pin",
    "legal_name": "file-text",
    "founded": "calendar",
    "lei": "hash",
    "wikidata": "hash",
}


# ---------------------------------------------------------------- save window

STATUS_KEYS = ("valid", "catch_all", "unknown", "invalid", "none")
STATUS_LABELS = {
    "valid": "Valid",
    "catch_all": "Catch-all",
    "unknown": "Not confirmed",
    "invalid": "Invalid",
    "none": "No email yet",
}
STATUS_HELP = {
    "valid": "The mail server accepted this exact address.",
    "catch_all": "The server accepts any address at this domain, so this one cannot be confirmed.",
    "unknown": "The server did not give a clear answer.",
    "invalid": "The mail server rejected this address.",
    "none": "No address found yet. Turn on look-ups to search for one.",
}
DEFAULT_KEEP = ("valid", "catch_all", "unknown", "none")
DEFAULT_LIST_NAME = {"people": "Saved people", "companies": "Saved companies"}
# Rough seconds per job, used only for the time estimate in the save window.
SECONDS = {"find_email": 12, "find_people": 40, "company_insights": 25}


def person_status(store: Any, person_id: str) -> tuple[str, bool]:
    """(status key, suppressed) of a person's best email; "none" without an email."""
    best = store.best_email(person_id)
    if best is None:
        return "none", False
    status = best.status.value if isinstance(best.status, EmailStatus) else str(best.status)
    return (status if status in STATUS_KEYS else "unknown"), bool(store.is_suppressed(best.address))


def estimate_text(jobs: dict[str, int], workers: int) -> str:
    total = sum(SECONDS.get(k, 20) * n for k, n in jobs.items())
    if not total:
        return "Instant"
    seconds = total / max(1, workers)
    if seconds < 60:
        return "Under a minute"
    minutes = math.ceil(seconds / 60)
    return f"About {minutes} minute{'s' if minutes != 1 else ''}"


def save_plan(
    kind: str,
    ids: list[str],
    *,
    store: Any,
    search: Any,
    keep: list[str] | None,
    find: bool,
    phones: bool,
    demo: bool,
    workers: int,
    has_insights: Any,
) -> dict[str, Any]:
    """What Save will do for the selection: counts per email status, records kept, look-ups to
    queue and a time estimate. has_insights(company_id) tells whether insights exist."""
    keep_set = set(keep if keep is not None else DEFAULT_KEEP)
    find = find and not demo
    plan: dict[str, Any] = {
        "kind": kind,
        "selected": len(ids),
        "counts": dict.fromkeys(STATUS_KEYS, 0),
        "kept": [],
        "new": 0,
        "suppressed": 0,
        "email_jobs": [],
        "people_jobs": [],
        "insight_jobs": [],
        "no_domain": 0,
    }
    seen_companies: set[str] = set()
    if kind == "people":
        saved = store.saved_person_ids()
        for pid in ids:
            person = store.get_person(pid)
            if person is None:
                continue
            status, suppressed = person_status(store, pid)
            plan["counts"][status] += 1
            if status not in keep_set:
                continue
            plan["kept"].append(pid)
            if pid not in saved:
                plan["new"] += 1
            if suppressed:
                plan["suppressed"] += 1
                continue  # never look up details for someone who opted out
            if find and status != "valid":
                plan["email_jobs"].append(pid)
            cid = person.company_id
            if find and phones and cid and cid not in seen_companies:
                seen_companies.add(cid)
                company = search.get_company(cid)
                if company is not None and company.domain and not has_insights(cid):
                    plan["insight_jobs"].append(cid)
    else:
        saved = store.saved_company_ids()
        for cid in ids:
            company = search.get_company(cid)
            if company is None:
                continue
            plan["kept"].append(cid)
            if cid not in saved:
                plan["new"] += 1
            if not company.domain:
                plan["no_domain"] += 1
                continue
            if company.domain and store.is_suppressed(company.domain):
                plan["suppressed"] += 1
                continue
            if find:
                plan["people_jobs"].append(cid)
                if phones and not has_insights(cid):
                    plan["insight_jobs"].append(cid)
    jobs = {
        "find_email": len(plan["email_jobs"]),
        "find_people": len(plan["people_jobs"]),
        "company_insights": len(plan["insight_jobs"]),
    }
    plan["jobs"] = jobs
    plan["estimate"] = estimate_text(jobs, workers)
    with_email = plan["counts"]["valid"]
    plan["valid_share"] = round(100 * with_email / len(ids)) if ids and kind == "people" else 0
    return plan


# ---------------------------------------------------------------- company drawer

LEADER = {Seniority.OWNER, Seniority.FOUNDER, Seniority.C_SUITE, Seniority.PARTNER}
DIRECTOR_UP = LEADER | {Seniority.VP, Seniority.HEAD, Seniority.DIRECTOR}


def lead_groups(people: list[Any]) -> dict[str, list[Any]]:
    """Suggested leads split like a sales team reads them: everyone, leaders, director and up."""
    return {
        "all": people,
        "leaders": [p for p in people if p.seniority in LEADER],
        "directors": [p for p in people if p.seniority in DIRECTOR_UP],
    }


def company_locations(company: Any, ins: Any) -> list[dict[str, Any]]:
    """Places we hold for a company: the head office from the open data, the address published
    on its website, and the places its open jobs are based. Nothing is guessed."""
    out: list[dict[str, Any]] = []
    hq_parts = [company.city, company.region]
    if company.city or company.region or company.country:
        out.append(
            {
                "kind": "Head office",
                "text": ", ".join(p for p in hq_parts if p),
                "country": company.country,
                "source": ", ".join(sorted({s.source for s in company.sources})) or "open data",
            }
        )
    address = getattr(getattr(ins, "contact", None), "address", None) if ins else None
    if address:
        text = address.get("text") or ", ".join(
            v
            for v in (
                address.get("street"),
                " ".join(x for x in (address.get("postal_code"), address.get("city")) if x),
                address.get("region"),
            )
            if v
        )
        if text:
            out.append(
                {
                    "kind": "Address on its website",
                    "text": text,
                    "country": address.get("country"),
                    "source": "company website",
                }
            )
    postings = getattr(getattr(ins, "jobs", None), "postings", None) if ins else None
    places: dict[str, int] = {}
    for job in postings or []:
        loc = (job.location or "").strip()
        if loc:
            places[loc] = places.get(loc, 0) + 1
    for loc, n in sorted(places.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
        out.append(
            {
                "kind": "Hiring here",
                "text": loc,
                "country": None,
                "source": f"{n} open job{'s' if n != 1 else ''}",
            }
        )
    return out


def sequence_choices(store: Any) -> list[dict[str, str]]:
    """Sequences people can be added to from the save window (archived ones are left out)."""
    try:
        from pepite.store.engage import repo_for

        rows = repo_for(store).sequences()
    except Exception:  # engage tables missing (tests with a fake store): offer none
        return []
    return [
        {"id": r.id, "name": r.name, "status": r.status} for r in rows if r.status != "archived"
    ]


def sequence_name(store: Any, sequence_id: str) -> str | None:
    return next((s["name"] for s in sequence_choices(store) if s["id"] == sequence_id), None)


def lists_holding(store: Any, kind: str, record_id: str) -> list[Any]:
    return [li for li in store.lists(kind) if record_id in set(store.list_members(li.id))]
