# ruff: noqa: B008  (FastAPI Depends defaults)
"""HTML pages (Jinja2 + HTMX). Router mounted at "/" by create_app. Owner: UI agent.

Reads app.state.{settings,search,store,jobs} only through the shared contracts.
Every form works without JavaScript; HTMX requests get partials plus an HX-Trigger toast.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from leadcommons import __version__
from leadcommons.models import SIZE_RANGES, Company, EmailStatus, Person, Seniority, utcnow
from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.web.deps import get_jobs, get_search, get_store, templates

router = APIRouter()

PER_PAGE = 25
WIDE = 1000  # cap when a view needs the full result set (saved/new tabs, sorting)

COUNTRIES = {
    "AT": "Austria",
    "AU": "Australia",
    "BE": "Belgium",
    "BR": "Brazil",
    "CA": "Canada",
    "CH": "Switzerland",
    "CN": "China",
    "CZ": "Czechia",
    "DE": "Germany",
    "DK": "Denmark",
    "ES": "Spain",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "HU": "Hungary",
    "IE": "Ireland",
    "IN": "India",
    "IT": "Italy",
    "JP": "Japan",
    "LU": "Luxembourg",
    "MX": "Mexico",
    "NL": "Netherlands",
    "NO": "Norway",
    "NZ": "New Zealand",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SE": "Sweden",
    "SG": "Singapore",
    "US": "United States",
    "ZA": "South Africa",
    "AE": "United Arab Emirates",
    "AR": "Argentina",
    "BG": "Bulgaria",
    "CI": "Côte d'Ivoire",
    "CL": "Chile",
    "CO": "Colombia",
    "CY": "Cyprus",
    "DZ": "Algeria",
    "EE": "Estonia",
    "EG": "Egypt",
    "HK": "Hong Kong",
    "HR": "Croatia",
    "ID": "Indonesia",
    "IL": "Israel",
    "IS": "Iceland",
    "KE": "Kenya",
    "KR": "South Korea",
    "LT": "Lithuania",
    "LV": "Latvia",
    "MA": "Morocco",
    "MT": "Malta",
    "MY": "Malaysia",
    "NG": "Nigeria",
    "PE": "Peru",
    "PH": "Philippines",
    "PK": "Pakistan",
    "RS": "Serbia",
    "RU": "Russia",
    "SA": "Saudi Arabia",
    "SI": "Slovenia",
    "SK": "Slovakia",
    "SN": "Senegal",
    "TH": "Thailand",
    "TN": "Tunisia",
    "TR": "Türkiye",
    "TW": "Taiwan",
    "UA": "Ukraine",
    "VN": "Vietnam",
}
ISIC_SECTIONS = {
    "A": "Agriculture, forestry, fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam",
    "E": "Water supply and waste",
    "F": "Construction",
    "G": "Wholesale and retail trade",
    "H": "Transport and storage",
    "I": "Accommodation and food",
    "J": "Information and communication",
    "K": "Finance and insurance",
    "L": "Real estate",
    "M": "Professional activities",
    "N": "Administrative services",
    "O": "Public administration",
    "P": "Education",
    "Q": "Health and social work",
    "R": "Arts and recreation",
    "S": "Other services",
    "T": "Households as employers",
    "U": "Extraterritorial bodies",
}
SOON_PAGES = {
    "sequences": ("Sequences", "send", "Multi-step outreach sequences with automatic follow-ups."),
    "emails": ("Emails", "mail", "A shared inbox for the emails you send from LeadCommons."),
    "tasks": ("Tasks", "square-check-big", "Reminders and to-dos tied to the people you prospect."),
    "deals": ("Deals", "handshake", "A simple pipeline to track opportunities to a close."),
    "email-health": (
        "Email health",
        "heart-pulse",
        "Domain warm-up, bounce and deliverability checks.",
    ),
}


# ---------------------------------------------------------------- helpers


def _fmt_date(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    secs = int((utcnow() - dt).total_seconds()) if dt.tzinfo else 0
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def _seniority_label(value: Any) -> str:
    v = getattr(value, "value", value) or ""
    return {"c_suite": "C-suite", "vp": "VP"}.get(v) or v.replace("_", " ").title()


def _status_label(value: Any) -> str:
    v = getattr(value, "value", value) or ""
    return {"catch_all": "Catch-all"}.get(v, v.title())


templates.env.globals.update(
    version=__version__,
    fmt_date=_fmt_date,
    ago=_ago,
    seniority_label=_seniority_label,
    status_label=_status_label,
    country_name=lambda c: COUNTRIES.get(c or "", c or ""),
    fmt_int=lambda n: f"{int(n or 0):,}",
)


def csrf(request: Request) -> None:
    """Reject cross-site form posts: Origin/Referer, when present, must match the Host."""
    src = request.headers.get("origin") or request.headers.get("referer")
    if src and urlparse(src).netloc != request.headers.get("host", ""):
        raise HTTPException(403, "Cross-site request blocked")


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def safe_next(value: str | None, default: str) -> str:
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return default


def with_notice(url: str, message: str) -> str:
    base, _, frag = url.partition("#")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({'notice': message})}" + (f"#{frag}" if frag else "")


def toast_headers(message: str, kind: str = "ok") -> dict[str, str]:
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "kind": kind}})}


def done(request: Request, message: str, back: str, kind: str = "ok", **extra: str) -> Response:
    """Standard POST answer: empty 200 + toast for HTMX, 303 back otherwise."""
    if is_htmx(request):
        return Response(status_code=200, headers={**toast_headers(message, kind), **extra})
    return RedirectResponse(with_notice(back, message), status_code=303)


async def form_values(request: Request) -> Any:
    return await request.form()


def form_ids(form: Any) -> list[str]:
    out: list[str] = []
    for key in ("ids", "record_ids"):
        out.extend(str(v) for v in form.getlist(key) if str(v).strip())
    return list(dict.fromkeys(out))


# --- tiny persistent UI state (banner dismissed, exported a list) in settings.home


def _state_path(settings: Any) -> Any:
    return settings.home / "ui_state.json"


def load_state(settings: Any) -> dict[str, Any]:
    try:
        return json.loads(_state_path(settings).read_text())
    except Exception:
        return {}


def set_state(settings: Any, **values: Any) -> None:
    try:
        state = load_state(settings)
        state.update(values)
        settings.home.mkdir(parents=True, exist_ok=True)
        _state_path(settings).write_text(json.dumps(state))
    except Exception:
        pass


def onboarding(request: Request) -> list[dict[str, Any]]:
    store, jobs, settings = (
        request.app.state.store,
        request.app.state.jobs,
        request.app.state.settings,
    )
    state = load_state(settings)
    try:
        recent = jobs.recent(200)
    except Exception:
        recent = []
    has_people = any(j.kind == "find_people" for j in recent)
    has_email = False
    try:
        people = store.search_people(PeopleFilters(), page=1, per_page=50)
        has_people = has_people or people.total > 0
        has_email = any(store.best_email(p.id) for p in people.items)
    except Exception:
        pass
    tasks = [
        (
            "save-company",
            bool(store.saved_company_ids()),
            "Save a company",
            "to your workspace",
            "Find companies",
            "/companies",
        ),
        (
            "find-people",
            has_people,
            "Find people",
            "at a company you saved",
            "Open companies",
            "/companies",
        ),
        (
            "find-email",
            has_email,
            "Find an email",
            "for a person on your list",
            "Find people",
            "/people",
        ),
        (
            "create-list",
            bool(store.lists()),
            "Create a list",
            "to group companies or people",
            "Create list",
            "/lists",
        ),
        (
            "save-search",
            bool(store.saved_searches()),
            "Save a search",
            "so you can rerun it later",
            "Search companies",
            "/companies",
        ),
        (
            "export-list",
            bool(state.get("exported")),
            "Export a list",
            "as a CSV file",
            "Open lists",
            "/lists",
        ),
        (
            "csv-enrich",
            any(j.kind == "enrich_csv" for j in recent) or bool(state.get("enriched")),
            "Try CSV enrichment",
            "with a file of names and companies",
            "Enrich a CSV",
            "/enrich",
        ),
    ]
    out = [
        {"key": k, "done": d, "verb": v, "text": t, "label": lb, "href": h}
        for k, d, v, t, lb, h in tasks
    ]
    first = next((r for r in out if not r["done"]), None)
    for r in out:
        r["primary"] = r is first
    return out


def current_url(request: Request) -> str:
    pairs = [(k, v) for k, v in request.query_params.multi_items() if k != "notice"]
    return request.url.path + (f"?{urlencode(pairs)}" if pairs else "")


def render(
    request: Request, name: str, active: str = "", status: int = 200, **ctx: Any
) -> Response:
    settings = request.app.state.settings
    tasks = onboarding(request)
    base = {
        "settings": settings,
        "active": active,
        "jobs_running": request.app.state.jobs.running_count(),
        "tasks": tasks,
        "tasks_done": sum(1 for t in tasks if t["done"]),
        "notice": (request.query_params.get("notice") or "")[:240],
        "current_url": current_url(request),
    }
    return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status)


def not_found(request: Request, what: str) -> Response:
    return render(
        request,
        "soon.html",
        status=404,
        active="",
        title=f"{what} not found",
        ico="circle-alert",
        blurb="It may have been removed, or the link is wrong.",
        soon=False,
    )


def qs(params: dict[str, Any], **over: Any) -> str:
    """Build a query string from list/str params, dropping empties. over=None removes a key."""
    merged = {**params, **over}
    items: list[tuple[str, str]] = []
    for k, v in merged.items():
        if v in (None, "", [], 0):
            continue
        if isinstance(v, list | tuple):
            items.extend((k, str(x)) for x in v)
        else:
            items.append((k, str(v)))
    return urlencode(items)


templates.env.globals["qs"] = qs


def pager(base: str, params: dict[str, Any], page: int, total: int) -> dict[str, Any]:
    pages = max(1, -(-total // PER_PAGE))
    lo, hi = max(1, page - 2), min(pages, page + 2)

    def url(n: int) -> str:
        q = qs(params, page=n if n > 1 else None)
        return f"{base}?{q}" if q else base

    return {
        "page": page,
        "pages": pages,
        "total": total,
        "start": (page - 1) * PER_PAGE + 1 if total else 0,
        "end": min(total, page * PER_PAGE),
        "prev": url(page - 1) if page > 1 else None,
        "next": url(page + 1) if page < pages else None,
        "links": [(n, url(n)) for n in range(lo, hi + 1)],
    }


def group_by_domain_name(search: Any, ids: set[str]) -> dict[str, Company]:
    out: dict[str, Company] = {}
    for cid in ids:
        try:
            c = search.get_company(cid)
        except Exception:
            c = None
        if c:
            out[cid] = c
    return out


# ---------------------------------------------------------------- home


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    state = load_state(request.app.state.settings)
    return render(
        request, "home.html", active="home", show_banner=not state.get("banner_dismissed")
    )


@router.post("/dismiss-banner")
def dismiss_banner(request: Request, _: None = Depends(csrf)) -> Response:
    set_state(request.app.state.settings, banner_dismissed=True)
    if is_htmx(request):
        return Response("", status_code=200)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------- companies


def parse_company_params(request: Request) -> dict[str, Any]:
    g = request.query_params
    keywords = [k.strip() for raw in g.getlist("keyword") for k in raw.split(",") if k.strip()]
    try:
        page = max(1, int(g.get("page", "1")))
    except ValueError:
        page = 1
    sort = g.get("sort", "relevance")
    tab = g.get("tab", "all")
    return {
        "q": g.get("q", "").strip(),
        "country": [c.upper() for c in g.getlist("country") if c],
        "industry": [c for c in g.getlist("industry") if c],
        "size": [s for s in g.getlist("size") if s in SIZE_RANGES],
        "keyword": keywords,
        "domain": g.get("domain", "").strip().lower(),
        "sort": sort if sort in ("relevance", "name", "size") else "relevance",
        "tab": tab if tab in ("all", "new", "saved") else "all",
        "page": page,
    }


def company_filters(p: dict[str, Any], **drop: bool) -> CompanyFilters:
    return CompanyFilters(
        text=p["q"] or None,
        countries=[] if drop.get("country") else p["country"],
        industries=[] if drop.get("industry") else p["industry"],
        size_ranges=[] if drop.get("size") else p["size"],
        keywords=p["keyword"],
        domains=[p["domain"]] if p["domain"] else [],
    )


def has_company_filters(p: dict[str, Any]) -> bool:
    return any(p[k] for k in ("q", "country", "industry", "size", "keyword", "domain"))


def facet_options(search: Any, p: dict[str, Any], facet: str, key: str, limit: int) -> list:
    """Facet values with counts (computed without the facet's own filter), selected first."""
    flt = company_filters(p, **{key: True})
    try:
        counts = dict(search.facet_counts(facet, flt, limit))
    except Exception:
        counts = {}
    for sel in p[key]:
        counts.setdefault(sel, 0)
    return sorted(counts.items(), key=lambda kv: (kv[0] not in p[key], -kv[1], kv[0]))


def size_order(c: Company) -> int:
    return SIZE_RANGES.index(c.size_range) if c.size_range in SIZE_RANGES else -1


def active_chips(p: dict[str, Any], base: str, labels: dict[str, str]) -> list[dict[str, str]]:
    chips = []
    if p["q"]:
        chips.append({"label": f"“{p['q']}”", "url": f"{base}?{qs(p, q=None, page=None)}"})
    for key in ("country", "industry", "size", "keyword", "status", "seniority"):
        for v in p.get(key, []):
            rest = [x for x in p[key] if x != v]
            label = labels.get(f"{key}:{v}", v)
            chips.append({"label": label, "url": f"{base}?{qs(p, **{key: rest, 'page': None})}"})
    for key in ("domain", "title"):
        if p.get(key):
            chips.append({"label": p[key], "url": f"{base}?{qs(p, **{key: None, 'page': None})}"})
    return chips


@router.get("/companies", response_class=HTMLResponse)
def companies(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> Response:
    p = parse_company_params(request)
    flt = company_filters(p)
    saved_ids = store.saved_company_ids()
    wide = p["tab"] != "all" or p["sort"] != "relevance"
    all_res = search.search_companies(
        flt, page=1 if wide else p["page"], per_page=WIDE if wide else PER_PAGE
    )
    total_all = all_res.total
    if wide:
        items = list(all_res.items)
        saved_count = sum(1 for c in items if c.id in saved_ids)
        if p["tab"] == "saved":
            items = [c for c in items if c.id in saved_ids]
        elif p["tab"] == "new":
            items = [c for c in items if c.id not in saved_ids]
        if p["sort"] == "name":
            items.sort(key=lambda c: c.name.lower())
        elif p["sort"] == "size":
            items.sort(key=size_order, reverse=True)
        total = len(items)
        start = (p["page"] - 1) * PER_PAGE
        items = items[start : start + PER_PAGE]
    else:
        items, total = list(all_res.items), all_res.total
        saved_count = 0
        if saved_ids:
            saved_count = sum(
                1
                for c in search.search_companies(flt, page=1, per_page=WIDE).items
                if c.id in saved_ids
            )

    countries = facet_options(search, p, "country", "country", 12)
    industries = facet_options(search, p, "industry", "industry", 12)
    sizes_counts = facet_options(search, p, "size_range", "size", 12)
    size_map = dict(sizes_counts)
    sizes = [(s, size_map.get(s, 0)) for s in SIZE_RANGES]
    ind_labels = {c.industry: c.industry_label for c in items if c.industry and c.industry_label}
    labels = {f"country:{c}": COUNTRIES.get(c, c) for c, _ in countries}
    labels.update(
        {f"industry:{c}": ISIC_SECTIONS.get(c) or ind_labels.get(c) or c for c, _ in industries}
    )
    labels.update({f"keyword:{k}": k for k in p["keyword"]})
    labels.update({f"size:{s}": f"{s} employees" for s in SIZE_RANGES})

    params = {
        k: v for k, v in p.items() if k not in ("page",) and v not in ("", [], "relevance", "all")
    }
    saved_searches = store.saved_searches("companies")
    views = []
    for s in saved_searches:
        try:
            raw = json.loads(s.filters_json)
            query = raw.get("query", "") if isinstance(raw, dict) else str(raw)
        except ValueError:
            query = s.filters_json
        views.append({"name": s.name, "url": f"/companies?{query}" if query else "/companies"})
    base_qs = qs(params)
    return render(
        request,
        "companies.html",
        active="companies",
        p=p,
        items=items,
        total=total,
        saved_ids=saved_ids,
        counts={"all": total_all, "saved": saved_count, "new": max(total_all - saved_count, 0)},
        countries=countries,
        industries=industries,
        sizes=sizes,
        isic=ISIC_SECTIONS,
        ind_labels=ind_labels,
        countries_names=COUNTRIES,
        has_filters=has_company_filters(p),
        chips=active_chips(p, "/companies", labels),
        pg=pager("/companies", params, p["page"], total),
        views=views,
        tab_params=params,
        lists=store.lists("companies"),
        base_qs=base_qs,
        size_ranges=SIZE_RANGES,
        keyword_text=", ".join(p["keyword"]),
    )


def company_context(request: Request, company: Company) -> dict[str, Any]:
    store = request.app.state.store
    people: list[Person] = []
    emails: dict[str, Any] = {}
    if company.domain:
        res = store.search_people(
            PeopleFilters(company=CompanyFilters(domains=[company.domain])), page=1, per_page=50
        )
        people = [x for x in res.items if x.company_domain == company.domain]
        emails = {x.id: store.best_email(x.id) for x in people}
    return {
        "c": company,
        "people": people,
        "emails": emails,
        "is_saved": company.id in store.saved_company_ids(),
        "lists": store.lists("companies"),
        "suppressed": {a.address for a in emails.values() if a and store.is_suppressed(a.address)},
    }


def drawer_response(
    request: Request, company: Company, message: str | None = None, kind: str = "ok"
) -> Response:
    headers = toast_headers(message, kind) if message else {}
    resp = templates.TemplateResponse(
        request, "_company_drawer.html", {"request": request, **company_context(request, company)}
    )
    resp.headers.update(headers)
    return resp


@router.get("/companies/{company_id}", response_class=HTMLResponse)
def company_detail(
    company_id: str, request: Request, search: Any = Depends(get_search)
) -> Response:
    company = search.get_company(company_id)
    if company is None:
        return not_found(request, "Company")
    if is_htmx(request):
        return drawer_response(request, company)
    return render(
        request, "company_page.html", active="companies", **company_context(request, company)
    )


def _submit_find_people(jobs: Any, company: Company) -> bool:
    if not company.domain:
        return False
    jobs.submit(
        "find_people",
        {"domain": company.domain, "company_id": company.id},
        f"Find people at {company.domain}",
    )
    return True


@router.post("/companies/{company_id}/find-people")
def company_find_people(
    company_id: str,
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    jobs: Any = Depends(get_jobs),
) -> Response:
    company = search.get_company(company_id)
    if company is None:
        return not_found(request, "Company")
    ok = _submit_find_people(jobs, company)
    msg = (
        f"Finding people at {company.domain}. Track it in the jobs menu."
        if ok
        else f"{company.name} has no known domain, so people cannot be searched yet."
    )
    kind = "ok" if ok else "error"
    if is_htmx(request):
        return drawer_response(request, company, msg, kind)
    return RedirectResponse(with_notice(f"/companies/{company_id}", msg), status_code=303)


@router.post("/companies/{company_id}/save")
def company_save(
    company_id: str,
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    store: Any = Depends(get_store),
) -> Response:
    company = search.get_company(company_id)
    if company is None:
        return not_found(request, "Company")
    store.save_company(company)
    msg = f"Saved {company.name}."
    if is_htmx(request):
        return drawer_response(request, company, msg)
    return RedirectResponse(with_notice(f"/companies/{company_id}", msg), status_code=303)


# ---------------------------------------------------------------- people


def parse_people_params(request: Request) -> dict[str, Any]:
    g = request.query_params
    valid_sen = {s.value for s in Seniority}
    valid_status = {s.value for s in EmailStatus}
    try:
        page = max(1, int(g.get("page", "1")))
    except ValueError:
        page = 1
    tab = g.get("tab", "all")
    return {
        "q": g.get("q", "").strip(),
        "title": g.get("title", "").strip(),
        "seniority": [s for s in g.getlist("seniority") if s in valid_sen],
        "status": [s for s in g.getlist("status") if s in valid_status],
        "country": [c.upper() for c in g.getlist("country") if c],
        "domain": g.get("domain", "").strip().lower(),
        "tab": tab if tab in ("all", "new", "saved") else "all",
        "page": page,
    }


def people_filters(p: dict[str, Any]) -> PeopleFilters:
    return PeopleFilters(
        text=p["q"] or None,
        titles=[t.strip() for t in p["title"].split(",") if t.strip()],
        seniorities=[Seniority(s) for s in p["seniority"]],
        email_statuses=[EmailStatus(s) for s in p["status"]],
        company=CompanyFilters(
            countries=p["country"], domains=[p["domain"]] if p["domain"] else []
        ),
    )


@router.get("/people", response_class=HTMLResponse)
def people(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> Response:
    p = parse_people_params(request)
    flt = people_filters(p)
    saved_ids = store.saved_person_ids()
    wide = p["tab"] != "all"
    res = store.search_people(
        flt, page=1 if wide else p["page"], per_page=WIDE if wide else PER_PAGE
    )
    total_all = res.total
    items = list(res.items)
    if wide:
        saved_count = sum(1 for x in items if x.id in saved_ids)
        items = [x for x in items if (x.id in saved_ids) == (p["tab"] == "saved")]
        total = len(items)
        start = (p["page"] - 1) * PER_PAGE
        items = items[start : start + PER_PAGE]
    else:
        total = total_all
        saved_count = 0
        if saved_ids:
            saved_count = sum(
                1
                for x in store.search_people(flt, page=1, per_page=WIDE).items
                if x.id in saved_ids
            )
    emails = {x.id: store.best_email(x.id) for x in items}
    suppressed = {e.address for e in emails.values() if e and store.is_suppressed(e.address)}
    companies_by_id = group_by_domain_name(search, {x.company_id for x in items if x.company_id})
    params = {k: v for k, v in p.items() if k != "page" and v not in ("", [], "all")}
    labels = {f"seniority:{s.value}": _seniority_label(s) for s in Seniority}
    labels.update({f"status:{s.value}": _status_label(s) for s in EmailStatus})
    labels.update({f"country:{c}": COUNTRIES.get(c, c) for c in p["country"]})
    return render(
        request,
        "people.html",
        active="people",
        p=p,
        items=items,
        total=total,
        emails=emails,
        suppressed=suppressed,
        companies=companies_by_id,
        saved_ids=saved_ids,
        counts={"all": total_all, "saved": saved_count, "new": max(total_all - saved_count, 0)},
        has_filters=any(p[k] for k in ("q", "title", "seniority", "status", "country", "domain")),
        chips=active_chips(p, "/people", labels),
        pg=pager("/people", params, p["page"], total),
        tab_params=params,
        lists=store.lists("people"),
        seniorities=list(Seniority),
        statuses=list(EmailStatus),
        countries_names=COUNTRIES,
    )


def person_context(request: Request, person: Person) -> dict[str, Any]:
    store, search = request.app.state.store, request.app.state.search
    best = store.best_email(person.id)
    getter = getattr(store, "emails_for", None) or getattr(store, "list_emails", None)
    try:
        candidates = list(getter(person.id)) if getter else ([best] if best else [])
    except Exception:
        candidates = [best] if best else []
    company = search.get_company(person.company_id) if person.company_id else None
    return {
        "person": person,
        "best": best,
        "candidates": candidates,
        "company": company,
        "suppressed": {c.address for c in candidates if c and store.is_suppressed(c.address)},
        "lists": store.lists("people"),
        "is_saved": person.id in store.saved_person_ids(),
    }


@router.get("/people/{person_id}", response_class=HTMLResponse)
def person_detail(person_id: str, request: Request, store: Any = Depends(get_store)) -> Response:
    person = store.get_person(person_id)
    if person is None:
        return not_found(request, "Person")
    ctx = person_context(request, person)
    if is_htmx(request):
        return templates.TemplateResponse(
            request, "_person_drawer.html", {"request": request, **ctx}
        )
    return render(request, "person_page.html", active="people", **ctx)


@router.post("/people/{person_id}/find-email")
def person_find_email(
    person_id: str,
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
    jobs: Any = Depends(get_jobs),
) -> Response:
    person = store.get_person(person_id)
    if person is None:
        return not_found(request, "Person")
    jobs.submit("find_email", {"person_id": person.id}, f"Find email for {person.full_name}")
    return done(request, f"Finding an email for {person.full_name}.", f"/people/{person_id}")


# ---------------------------------------------------------------- bulk actions


@router.post("/bulk/find-people")
async def bulk_find_people(
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    jobs: Any = Depends(get_jobs),
) -> Response:
    form = await form_values(request)
    back = safe_next(form.get("next"), "/companies")
    n = skipped = 0
    for cid in form_ids(form)[:200]:
        company = search.get_company(cid)
        if company and _submit_find_people(jobs, company):
            n += 1
        else:
            skipped += 1
    if not n and not skipped:
        return done(request, "Select at least one company first.", back, "error")
    msg = f"Started {n} people search{'es' if n != 1 else ''}."
    if skipped:
        msg += f" {skipped} skipped (no domain)."
    return done(request, msg, back, "ok" if n else "error")


@router.post("/bulk/find-emails")
async def bulk_find_emails(
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
    jobs: Any = Depends(get_jobs),
) -> Response:
    form = await form_values(request)
    back = safe_next(form.get("next"), "/people")
    n = 0
    for pid in form_ids(form)[:200]:
        person = store.get_person(pid)
        if person:
            jobs.submit("find_email", {"person_id": pid}, f"Find email for {person.full_name}")
            n += 1
    if not n:
        return done(request, "Select at least one person first.", back, "error")
    return done(request, f"Started {n} email search{'es' if n != 1 else ''}.", back)


# ---------------------------------------------------------------- lists + CSV


def csv_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_response(rows: list[list[Any]], header: list[str], filename: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([csv_cell(c) for c in r])
    return Response(
        buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


PEOPLE_HEADER = [
    "name",
    "title",
    "company_domain",
    "email",
    "email_status",
    "confidence",
    "source_urls",
]
COMPANY_HEADER = [
    "name",
    "domain",
    "country",
    "city",
    "industry",
    "size_range",
    "lei",
    "wikidata_id",
]


def people_rows(store: Any, ids: list[str]) -> list[list[Any]]:
    rows = []
    for pid in ids:
        person = store.get_person(pid)
        if not person:
            continue
        best = store.best_email(pid)
        email, status, conf = "", "", ""
        if best:
            if store.is_suppressed(best.address) or (
                person.company_domain and store.is_suppressed(person.company_domain)
            ):
                status = "suppressed"
            else:
                email, status, conf = best.address, best.status.value, best.confidence
        rows.append(
            [
                person.full_name,
                person.title,
                person.company_domain,
                email,
                status,
                conf,
                " | ".join(person.source_urls),
            ]
        )
    return rows


def company_rows(search: Any, ids: list[str]) -> list[list[Any]]:
    rows = []
    for cid in ids:
        c = search.get_company(cid)
        if c:
            rows.append(
                [
                    c.name,
                    c.domain,
                    c.country,
                    c.city,
                    c.industry_label or c.industry,
                    c.size_range,
                    c.registry_ids.get("lei"),
                    c.registry_ids.get("wikidata"),
                ]
            )
    return rows


def export_rows(request: Request, kind: str, ids: list[str]) -> tuple[list[str], list[list[Any]]]:
    if kind == "people":
        return PEOPLE_HEADER, people_rows(request.app.state.store, ids)
    return COMPANY_HEADER, company_rows(request.app.state.search, ids)


@router.get("/lists", response_class=HTMLResponse)
def lists_page(request: Request, store: Any = Depends(get_store)) -> Response:
    return render(request, "lists.html", active="lists", lists=store.lists())


def valid_kind(kind: str | None) -> str:
    return kind if kind in ("people", "companies") else "companies"


def add_records(request: Request, list_id: str, kind: str, ids: list[str]) -> None:
    store, search = request.app.state.store, request.app.state.search
    if kind == "companies":
        for cid in ids:
            c = search.get_company(cid)
            if c:
                store.save_company(c)
    store.add_to_list(list_id, ids)


@router.post("/lists")
async def lists_create(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    name = str(form.get("name") or "").strip()[:120]
    kind = valid_kind(form.get("kind"))
    if not name:
        return done(request, "Give the list a name.", "/lists", "error")
    list_id = store.create_list(name, kind)
    ids = form_ids(form)
    if ids:
        add_records(request, list_id, kind, ids)
    if is_htmx(request):
        return Response("", headers={"HX-Redirect": f"/lists/{list_id}"})
    return RedirectResponse(
        with_notice(f"/lists/{list_id}", f"Created list {name}."), status_code=303
    )


@router.post("/lists/save-to")
async def lists_save_to(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    """Bulk 'Save to list': add selected ids to an existing list, or create a new one."""
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    ids = form_ids(form)
    if not ids:
        return done(request, "Select at least one record first.", back, "error")
    list_id = str(form.get("list_id") or "")
    new_name = str(form.get("new_name") or "").strip()[:120]
    if new_name:
        list_id = store.create_list(new_name, kind)
    elif not list_id or list_id == "new":
        return done(request, "Choose a list or name a new one.", back, "error")
    elif list_id not in {li.id for li in store.lists(kind)}:
        return done(request, "That list does not exist.", back, "error")
    add_records(request, list_id, kind, ids)
    name = new_name or next((li.name for li in store.lists(kind) if li.id == list_id), "list")
    n = len(ids)
    return done(request, f"Added {n} to {name}.", back)


def list_or_404(store: Any, list_id: str) -> Any:
    return next((li for li in store.lists() if li.id == list_id), None)


@router.get("/lists/{list_id}", response_class=HTMLResponse)
def list_detail(
    request: Request,
    list_id: str,
    store: Any = Depends(get_store),
    search: Any = Depends(get_search),
) -> Response:
    info = list_or_404(store, list_id)
    if info is None:
        return not_found(request, "List")
    ids = store.list_members(list_id)
    members: list[Any] = []
    emails: dict[str, Any] = {}
    suppressed: set[str] = set()
    if info.kind == "people":
        members = [x for x in (store.get_person(i) for i in ids) if x]
        emails = {x.id: store.best_email(x.id) for x in members}
        suppressed = {e.address for e in emails.values() if e and store.is_suppressed(e.address)}
    else:
        members = [x for x in (search.get_company(i) for i in ids) if x]
    return render(
        request,
        "list_detail.html",
        active="lists",
        info=info,
        members=members,
        emails=emails,
        suppressed=suppressed,
    )


@router.post("/lists/{list_id}/add")
async def list_add(
    list_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    info = list_or_404(store, list_id)
    if info is None:
        return not_found(request, "List")
    form = await form_values(request)
    ids = form_ids(form)
    add_records(request, list_id, info.kind, ids)
    return done(request, f"Added {len(ids)} to {info.name}.", f"/lists/{list_id}")


@router.post("/lists/{list_id}/remove")
async def list_remove(
    list_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    info = list_or_404(store, list_id)
    if info is None:
        return not_found(request, "List")
    form = await form_values(request)
    ids = form_ids(form)
    store.remove_from_list(list_id, ids)
    return done(request, f"Removed {len(ids)} from {info.name}.", f"/lists/{list_id}")


@router.get("/lists/{list_id}/export.csv")
def list_export(list_id: str, request: Request, store: Any = Depends(get_store)) -> Response:
    info = list_or_404(store, list_id)
    if info is None:
        return not_found(request, "List")
    header, rows = export_rows(request, info.kind, store.list_members(list_id))
    set_state(request.app.state.settings, exported=True)
    slug = "".join(ch if ch.isalnum() else "-" for ch in info.name.lower()).strip("-") or "list"
    return csv_response(rows, header, f"{slug}.csv")


@router.post("/bulk/export.csv")
async def bulk_export(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    ids = form_ids(form)
    header, rows = export_rows(request, kind, ids)
    set_state(request.app.state.settings, exported=True)
    return csv_response(rows, header, f"{kind}-selection.csv")


# ---------------------------------------------------------------- searches


@router.post("/searches")
async def searches_create(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    pairs = parse_qsl(str(form.get("query") or "").lstrip("?")[:2000])
    query = urlencode([(k, v) for k, v in pairs if k != "notice"])
    base = "/companies" if kind == "companies" else "/people"
    back = f"{base}?{query}" if query else base
    name = str(form.get("name") or "").strip()[:120]
    if not name:
        return done(request, "Give the search a name.", back, "error")
    store.save_search(name, kind, json.dumps({"query": query}))
    return done(request, f"Saved search {name}.", back)


# ---------------------------------------------------------------- enrich


@router.get("/enrich", response_class=HTMLResponse)
def enrich_page(request: Request) -> Response:
    return render(request, "enrich.html", active="enrich")


# ---------------------------------------------------------------- settings + opt-out


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, search: Any = Depends(get_search)) -> Response:
    info = getattr(search, "pack_info", None)
    try:
        packs = list(info()) if info else []
    except Exception:
        packs = []
    return render(request, "settings.html", active="settings", packs=packs)


def clean_suppression(value: str) -> str | None:
    v = value.strip().lower()
    if not v or len(v) > 254 or " " in v:
        return None
    if "@" in v:
        local, _, domain = v.rpartition("@")
        return v if local and "." in domain else None
    return v if "." in v else None


@router.post("/settings/suppression")
async def settings_suppress(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    value = clean_suppression(str(form.get("value") or ""))
    if not value:
        return done(request, "Enter a valid email address or domain.", "/settings", "error")
    store.add_suppression(value, "manual")
    return done(request, f"{value} will never be contacted or exported.", "/settings")


@router.get("/opt-out", response_class=HTMLResponse)
def opt_out_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "optout.html", {"request": request, "state": "form", "error": None, "value": ""}
    )


@router.post("/opt-out", response_class=HTMLResponse)
async def opt_out_submit(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    raw = str(form.get("email") or "")
    value = clean_suppression(raw)
    if not value:
        return templates.TemplateResponse(
            request,
            "optout.html",
            {
                "request": request,
                "state": "form",
                "value": raw[:254],
                "error": "Please enter a valid email address or domain.",
            },
            status_code=400,
        )
    store.add_suppression(value, "opt-out")
    return templates.TemplateResponse(
        request, "optout.html", {"request": request, "state": "done", "value": value, "error": None}
    )


# ---------------------------------------------------------------- assistant, soon, partials


@router.get("/assistant", response_class=HTMLResponse)
def assistant(request: Request) -> Response:
    return render(request, "assistant.html", active="assistant")


def _soon_route(slug: str) -> None:
    title, ico, blurb = SOON_PAGES[slug]

    @router.get(f"/{slug}", response_class=HTMLResponse, name=f"soon_{slug}")
    def soon(request: Request) -> Response:
        return render(
            request, "soon.html", active=slug, title=title, ico=ico, blurb=blurb, soon=True
        )


for _slug in SOON_PAGES:
    _soon_route(_slug)


@router.get("/partials/jobs-pill", response_class=HTMLResponse)
def jobs_pill(request: Request, jobs: Any = Depends(get_jobs)) -> Response:
    return templates.TemplateResponse(
        request, "_jobs_pill.html", {"request": request, "n": jobs.running_count()}
    )


@router.get("/partials/jobs", response_class=HTMLResponse)
def jobs_list(request: Request, jobs: Any = Depends(get_jobs)) -> Response:
    recent = sorted(jobs.recent(10), key=lambda j: j.created_at, reverse=True)
    return templates.TemplateResponse(
        request, "_jobs.html", {"request": request, "jobs": recent, "n": jobs.running_count()}
    )
