# ruff: noqa: B008  (FastAPI Depends/Query defaults)
"""JSON API, mounted by the app under /api. Reads app.state.{settings,search,store,jobs}."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from leadcommons.models import EmailStatus, Seniority
from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.web.deps import get_jobs, get_search, get_settings, get_store

router = APIRouter()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PER_PAGE = 100


def to_jsonable(obj: Any) -> Any:
    """Serialise dataclasses / enums / datetimes into plain JSON-compatible values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


class Pagination:
    def __init__(
        self, page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=MAX_PER_PAGE)
    ) -> None:
        self.page = page
        self.per_page = per_page


def _company_filters(
    q: str | None,
    country: list[str],
    industry: list[str],
    size: list[str],
    keyword: list[str],
    domain: list[str],
) -> CompanyFilters:
    return CompanyFilters(
        text=(q or "").strip() or None,
        countries=[c.upper() for c in country],
        industries=industry,
        size_ranges=size,
        keywords=keyword,
        domains=[d.lower() for d in domain],
    )


def _person_dict(store: Any, person: Any, *, with_emails: bool = False) -> dict[str, Any]:
    data = to_jsonable(person)
    best = store.best_email(person.id)
    data["email"] = to_jsonable(best) if best else None
    if with_emails:
        data["emails"] = [to_jsonable(best)] if best else []
    return data


# -- companies ---------------------------------------------------------------------------


@router.get("/companies")
def list_companies(
    q: str | None = None,
    country: list[str] = Query(default=[]),
    industry: list[str] = Query(default=[]),
    size: list[str] = Query(default=[]),
    keyword: list[str] = Query(default=[]),
    domain: list[str] = Query(default=[]),
    pg: Pagination = Depends(),
    search: Any = Depends(get_search),
) -> dict[str, Any]:
    filters = _company_filters(q, country, industry, size, keyword, domain)
    result = search.search_companies(filters, page=pg.page, per_page=pg.per_page)
    return {
        "items": to_jsonable(result.items),
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    }


@router.get("/companies/{company_id}")
def get_company(company_id: str, search: Any = Depends(get_search)) -> dict[str, Any]:
    company = search.get_company(company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    return to_jsonable(company)


@router.post("/companies/{company_id}/find-people")
def company_find_people(
    company_id: str, search: Any = Depends(get_search), jobs: Any = Depends(get_jobs)
) -> dict[str, str]:
    company = search.get_company(company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    if not company.domain:
        raise HTTPException(400, "Company has no known domain")
    job_id = jobs.submit(
        "find_people",
        {"domain": company.domain, "company_id": company.id},
        f"Find people at {company.domain}",
    )
    return {"job_id": job_id}


@router.get("/companies/{company_id}/insights")
def company_insights(
    company_id: str, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> dict[str, Any]:
    from leadcommons import insights

    if search.get_company(company_id) is None:
        raise HTTPException(404, "Company not found")
    found = insights.get(store, company_id)
    if found is None:
        return {"company_id": company_id, "refreshed": False, "insights": None}
    return {"company_id": company_id, "refreshed": True, "insights": to_jsonable(found)}


@router.post("/companies/{company_id}/insights/refresh")
def company_insights_refresh(
    company_id: str,
    request: Request,
    search: Any = Depends(get_search),
    jobs: Any = Depends(get_jobs),
    settings: Any = Depends(get_settings),
) -> Any:
    from leadcommons.web import pages_insights

    pages_insights.csrf(request)
    company = search.get_company(company_id)
    if company is None:
        raise HTTPException(404, "Company not found")
    if not settings.enrichment_enabled:
        raise HTTPException(403, pages_insights.DEMO_REASON)
    if not company.domain:
        raise HTTPException(400, "Company has no known domain")
    job_id = jobs.submit(
        "company_insights",
        {"company_id": company.id, "domain": company.domain},
        f"Refresh insights for {company.name}",
    )
    if request.headers.get("hx-request") == "true":
        return pages_insights.refreshing_response(
            request, company, request.query_params.get("tab", "overview"), job_id
        )
    return {"job_id": job_id}


# -- people ------------------------------------------------------------------------------


@router.get("/people")
def list_people(
    q: str | None = None,
    title: list[str] = Query(default=[]),
    seniority: list[Seniority] = Query(default=[]),
    email_status: list[EmailStatus] = Query(default=[]),
    country: list[str] = Query(default=[]),
    domain: list[str] = Query(default=[]),
    pg: Pagination = Depends(),
    store: Any = Depends(get_store),
) -> dict[str, Any]:
    filters = PeopleFilters(
        text=(q or "").strip() or None,
        titles=title,
        seniorities=seniority,
        email_statuses=email_status,
        company=_company_filters(None, country, [], [], [], domain),
    )
    result = store.search_people(filters, page=pg.page, per_page=pg.per_page)
    return {
        "items": [_person_dict(store, p) for p in result.items],
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
    }


@router.get("/people/{person_id}")
def get_person(person_id: str, store: Any = Depends(get_store)) -> dict[str, Any]:
    person = store.get_person(person_id)
    if person is None:
        raise HTTPException(404, "Person not found")
    return _person_dict(store, person, with_emails=True)


@router.post("/people/{person_id}/find-email")
def person_find_email(
    person_id: str, store: Any = Depends(get_store), jobs: Any = Depends(get_jobs)
) -> dict[str, str]:
    person = store.get_person(person_id)
    if person is None:
        raise HTTPException(404, "Person not found")
    if not person.company_domain:
        raise HTTPException(400, "Person has no company domain")
    job_id = jobs.submit(
        "find_email", {"person_id": person.id}, f"Find email for {person.full_name}"
    )
    return {"job_id": job_id}


# -- jobs --------------------------------------------------------------------------------


def _job_dict(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "label": job.label,
        "result": to_jsonable(job.result),
        "error": job.error,
        "created_at": to_jsonable(job.created_at),
        "finished_at": to_jsonable(job.finished_at),
    }


@router.get("/jobs")
def list_jobs(
    limit: int = Query(20, ge=1, le=200), jobs: Any = Depends(get_jobs)
) -> dict[str, Any]:
    return {"items": [_job_dict(j) for j in jobs.recent(limit)], "running": jobs.running_count()}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, jobs: Any = Depends(get_jobs)) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return _job_dict(job)


# -- CSV enrichment ----------------------------------------------------------------------


@router.post("/enrich")
async def enrich_upload(
    file: UploadFile = File(...),
    settings: Any = Depends(get_settings),
    jobs: Any = Depends(get_jobs),
) -> dict[str, str]:
    name = file.filename or ""
    if not name.lower().endswith(".csv"):
        raise HTTPException(400, "Only .csv files are accepted")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large (max 5 MB)")
    if not data.strip():
        raise HTTPException(400, "File is empty")
    uploads = settings.home / "uploads"
    exports = settings.home / "exports"
    uploads.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    src = uploads / f"{uid}.csv"
    src.write_bytes(data)
    out = exports / f"{uid}-enriched.csv"
    job_id = jobs.submit(
        "enrich_csv", {"input": str(src), "output": str(out)}, f"Enrich {name[:80]}"
    )
    return {"job_id": job_id}


@router.get("/enrich/{job_id}/result.csv")
def enrich_result(job_id: str, jobs: Any = Depends(get_jobs)) -> FileResponse:
    job = jobs.get(job_id)
    if job is None or job.kind != "enrich_csv":
        raise HTTPException(404, "Job not found")
    if job.status in ("queued", "running"):
        raise HTTPException(409, "Job is not finished yet")
    if job.status == "failed":
        raise HTTPException(409, f"Job failed: {job.error or 'unknown error'}")
    path = (job.result or {}).get("output")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "Result file not found")
    return FileResponse(path, media_type="text/csv", filename="enriched.csv")


# -- lists -------------------------------------------------------------------------------


class ListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["people", "companies"] = "people"


class ListItems(BaseModel):
    record_ids: list[str] = Field(min_length=1, max_length=1000)


def _require_list(store: Any, list_id: str) -> Any:
    info = next((li for li in store.lists() if li.id == list_id), None)
    if info is None:
        raise HTTPException(404, "List not found")
    return info


@router.get("/lists")
def get_lists(
    kind: Literal["people", "companies"] | None = None, store: Any = Depends(get_store)
) -> dict[str, Any]:
    return {"items": to_jsonable(store.lists(kind))}


@router.post("/lists", status_code=201)
def create_list(body: ListCreate, store: Any = Depends(get_store)) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Name must not be blank")
    list_id = store.create_list(name, body.kind)
    return {"id": list_id, "name": name, "kind": body.kind}


@router.get("/lists/{list_id}/items")
def list_items(
    list_id: str, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> dict[str, Any]:
    info = _require_list(store, list_id)
    ids = store.list_members(list_id)
    items: list[dict[str, Any]] = []
    for rid in ids:
        if info.kind == "people":
            person = store.get_person(rid)
            if person is not None:
                items.append(_person_dict(store, person))
        else:
            company = search.get_company(rid)
            if company is not None:
                items.append(to_jsonable(company))
    return {"list": to_jsonable(info), "record_ids": ids, "items": items}


@router.post("/lists/{list_id}/items")
def add_list_items(
    list_id: str, body: ListItems, store: Any = Depends(get_store)
) -> dict[str, Any]:
    _require_list(store, list_id)
    store.add_to_list(list_id, body.record_ids)
    return {"added": len(body.record_ids)}


# -- suppression / packs -----------------------------------------------------------------


class SuppressionBody(BaseModel):
    value: str = Field(min_length=1, max_length=254)


@router.post("/suppression", status_code=201)
def add_suppression(body: SuppressionBody, store: Any = Depends(get_store)) -> dict[str, bool]:
    value = body.value.strip().lower()
    if not value or any(c.isspace() for c in value):
        raise HTTPException(422, "Provide an email address or a domain")
    store.add_suppression(value)
    return {"ok": True}


@router.get("/packs")
def packs(search: Any = Depends(get_search)) -> dict[str, Any]:
    info = getattr(search, "pack_info", None)
    return {"items": to_jsonable(info() if callable(info) else [])}


# -- AI search ---------------------------------------------------------------------------


class AiParseBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    kind: Literal["companies", "people"] = "companies"


@router.post("/ai/parse")
def ai_parse(body: AiParseBody) -> dict[str, Any]:
    from leadcommons.ai import ignored_filters, parse_query, to_url

    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Text must not be blank")
    parsed = parse_query(text, body.kind)
    query, url = to_url(parsed, body.kind)
    return {
        "engine": parsed.engine,
        "explanation": parsed.explanation,
        "unparsed": parsed.unparsed,
        "ignored": ignored_filters(parsed, body.kind),
        "query": query,
        "url": url,
    }
