# ruff: noqa: B008  (FastAPI Depends defaults)
"""HTML pages (Jinja2 + HTMX). Router mounted at "/" by create_app. Owner: UI agent.

Reads app.state.{settings,search,store,jobs} only through the shared contracts.
Every form works without JavaScript; HTMX requests get partials plus an HX-Trigger toast.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from leadcommons import __version__, engage, insights
from leadcommons.ai.llm import llm_config
from leadcommons.connectors.people import source_label
from leadcommons.models import SIZE_RANGES, Company, EmailStatus, Person, Seniority, utcnow
from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.store.personas import clean_filters, personas_for
from leadcommons.web import results
from leadcommons.web import search_filters as sf
from leadcommons.web.deps import get_jobs, get_search, get_store, templates
from leadcommons.web.search_filters import (
    COUNTRIES,
    DEFAULT_PER,
    ISIC_SECTIONS,
    NO_MATCH,
    PAGE_SIZES,
    _opt_int,
    company_matches,
    parse_common,
    qs,
    remove_url,
    url_params,
)
from leadcommons.web.search_filters import buckets_for_range as buckets_for_range
from leadcommons.web.search_filters import expand_titles as expand_titles

router = APIRouter()

SCAN_CAP = 5000  # most rows scanned when a view needs the full result set (tabs, sort, exclusions)
SCAN_BATCH = 500


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


_seniority_label = sf.seniority_label
_status_label = sf.status_label


templates.env.globals.update(
    version=__version__,
    fmt_date=_fmt_date,
    ago=_ago,
    seniority_label=_seniority_label,
    status_label=_status_label,
    country_name=lambda c: COUNTRIES.get(c or "", c or ""),
    fmt_int=lambda n: f"{int(n or 0):,}",
)


def initials(name: str | None) -> str:
    words = [w for w in re.split(r"[\s\-_.]+", (name or "").strip()) if w[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def tone(name: str | None) -> int:
    """Stable avatar colour slot (0-5) from a name."""
    return sum(ord(ch) for ch in (name or "")) % 6


templates.env.globals.update(initials=initials, tone=tone, page_sizes=PAGE_SIZES)


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


templates.env.globals["qs"] = qs


def pager(
    base: str,
    params: dict[str, Any],
    page: int,
    total: int,
    per: int = DEFAULT_PER,
    capped: bool = False,
) -> dict[str, Any]:
    """Pagination model: page arrows, a page-number select and a "1 - 25 of N" range."""
    per = per if per in PAGE_SIZES else DEFAULT_PER
    pages = max(1, -(-total // per))
    page = min(max(1, page), pages)
    lo, hi = max(1, page - 2), min(pages, page + 2)

    def url(n: int) -> str:
        q = qs(params, page=n if n > 1 else None)
        return f"{base}?{q}" if q else base

    if pages <= 300:
        numbers = list(range(1, pages + 1))
    else:
        numbers = sorted(
            {*range(1, 101), *range(max(1, page - 8), min(pages, page + 8) + 1), pages}
        )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "per": per,
        "capped": capped,
        "start": (page - 1) * per + 1 if total else 0,
        "end": min(total, page * per),
        "prev": url(page - 1) if page > 1 else None,
        "next": url(page + 1) if page < pages else None,
        "links": [(n, url(n)) for n in range(lo, hi + 1)],
        "options": [(n, url(n)) for n in numbers],
    }


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


# ---------------------------------------------------------------- shared search engine
#
# Companies and people share one filter model: include lists, exclude lists, size buckets,
# keywords. Parsing, matching and the filter panel live in web/search_filters.py. The company
# pack filters companies in SQL; people are filtered in Python on a bounded scan of the local
# store (titles, departments, company attributes). SCAN_CAP bounds the scan; when a result set
# is larger the UI shows "5,000+" and says so.


class CompanyLookup:
    """Per-request memo of company records: the pack first, then locally saved copies."""

    def __init__(self, search: Any, store: Any) -> None:
        self.search, self.store = search, store
        self._memo: dict[str, Company | None] = {}

    def get(self, company_id: str | None) -> Company | None:
        if not company_id:
            return None
        if company_id not in self._memo:
            found = None
            try:
                found = self.search.get_company(company_id)
            except Exception:
                found = None
            if found is None:
                getter = getattr(self.store, "get_company", None)
                if getter:
                    try:
                        found = getter(company_id)
                    except Exception:
                        found = None
            self._memo[company_id] = found
        return self._memo[company_id]


# ---- lookalikes, insight-based filters, phones


def is_demo(request: Request) -> bool:
    return request.app.state.settings.mode == "demo"


DEMO_REASON = "Turned off in the public demo. Install LeadCommons to use it on your own data."
# Template helpers of the results screens (toolbar, selection bar, save window, profiles).
templates.env.globals["demo_reason"] = DEMO_REASON
# Set by the desktop app (Electron) when it starts the backend: pages then behave like an app.
templates.env.globals["desktop_app"] = os.environ.get("LEADCOMMONS_DESKTOP") == "1"
templates.env.globals["rx"] = results  # view menu rows, field icons, save-window labels
templates.env.globals["ui_state"] = lambda settings: load_state(settings)  # enrichment mode
templates.env.filters["tel"] = lambda phone: tel_href(phone) or "#"
templates.env.filters["source_label"] = source_label  # "wikidata.org/..." -> "Wikidata"


def safe_insights(store: Any, company_id: str | None) -> Any | None:
    """Stored insights of a company, or None (never refreshed, or the data module is not ready)."""
    if not company_id:
        return None
    try:
        return insights.get(store, company_id)
    except Exception:  # NotImplementedError until the data agent ships; never break a page
        return None


def _field(item: Any, *names: str) -> Any:
    for n in names:
        v = item.get(n) if isinstance(item, dict) else getattr(item, n, None)
        if v:
            return v
    return None


def _texts(items: Any, *names: str) -> list[str]:
    out: list[str] = []
    for it in items or []:
        v = it if isinstance(it, str) else _field(it, *names)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def insight_technologies(ins: Any) -> list[str]:
    return _texts(_field(ins, "technologies") if ins else [], "name", "technology")


def insight_jobs(ins: Any) -> list[str]:
    return _texts(_field(ins, "job_postings", "jobs") if ins else [], "title", "name")


def insight_phone(ins: Any) -> str | None:
    """First business phone number in a company's insights, if any."""
    if not ins:
        return None
    contact = _field(ins, "contact")  # CompanyInsights keeps phones under .contact
    for it in (_field(contact, "phones") if contact else None) or _field(ins, "phones") or []:
        v = it if isinstance(it, str) else _field(it, "e164", "number", "phone", "value")
        if isinstance(v, str) and v.strip():
            return v.strip()
    single = _field(ins, "phone")
    return single.strip() if isinstance(single, str) and single.strip() else None


def tel_href(phone: str | None) -> str | None:
    digits = re.sub(r"[^0-9+]", "", phone or "")
    return f"tel:{digits}" if len(digits) >= 5 else None


def company_post_filter(store: Any, p: dict[str, Any]) -> Callable[[Company], bool] | None:
    """Technologies / Job postings filters: they read the insights stored for each company,
    so only companies whose insights were refreshed can match."""
    tech = [t.lower() for t in p.get("tech") or []]
    jobs = [j.lower() for j in p.get("job") or []]
    if not tech and not jobs:
        return None
    memo: dict[str, bool] = {}

    def ok(c: Company) -> bool:
        if c.id not in memo:
            ins = safe_insights(store, c.id)
            names = [n.lower() for n in insight_technologies(ins)]
            titles = [n.lower() for n in insight_jobs(ins)]
            memo[c.id] = (not tech or any(k in n for k in tech for n in names)) and (
                not jobs or any(k in n for k in jobs for n in titles)
            )
        return memo[c.id]

    return ok


def domain_restriction(p: dict[str, Any]) -> list[str] | None:
    """Domains the results must be in (lookalikes), or None when nothing restricts them."""
    found = (p.get("_x") or {}).get("domains")
    if found is None:
        return None
    allowed = sorted(d for d in found if not p["domain"] or d == p["domain"])
    return allowed or [NO_MATCH]


def lookalike_domains(search: Any, company: Company) -> set[str] | str:
    """Domains of companies similar to `company`, or a status string: "soon" / "error"."""
    try:
        found = insights.lookalikes(search, company, 50)
    except NotImplementedError:
        return "soon"
    except Exception:
        return "error"
    return {d for c in found if (d := getattr(c, "domain", None))}


def find_company_ref(search: Any, store: Any, text: str) -> Company | None:
    found = CompanyLookup(search, store).get(text)
    if found is not None:
        return found
    for flt in (CompanyFilters(domains=[text.lower()]), CompanyFilters(text=text)):
        try:
            res = search.search_companies(flt, page=1, per_page=1)
        except Exception:
            return None
        if res.items:
            return res.items[0]  # type: ignore[no-any-return]
    return None


def resolve_extras(request: Request, p: dict[str, Any], kind: str) -> dict[str, Any]:
    """Turn the lookalike filters into a domain restriction (p["_x"]); the status of each is
    kept for the filter panel: "soon" (module not ready), "error", "notfound"."""
    search, store = request.app.state.search, request.app.state.store
    x: dict[str, Any] = {"domains": None, "seniority": None, "status": {}}
    p["_x"] = x

    def narrow(found: set[str]) -> None:
        x["domains"] = found if x["domains"] is None else x["domains"] & found

    if p.get("lookalike"):
        co = find_company_ref(search, store, p["lookalike"])
        if co is None:
            x["status"]["lookalike"] = "notfound"
            narrow(set())
        else:
            res = lookalike_domains(search, co)
            if isinstance(res, str):
                x["status"]["lookalike"] = res
            else:
                narrow(res)
    if kind == "people" and p.get("plook"):
        ref = None
        try:
            hits = store.search_people(PeopleFilters(text=p["plook"]), page=1, per_page=1)
            ref = hits.items[0] if hits.items else None
        except Exception:
            ref = None
        co = CompanyLookup(search, store).get(ref.company_id) if ref else None
        if ref is None or co is None:
            x["status"]["plook"] = "notfound"
            narrow(set())
        else:
            res = lookalike_domains(search, co)
            if isinstance(res, str):
                x["status"]["plook"] = res
            else:
                narrow(res | ({co.domain} if co.domain else set()))
                x["seniority"] = ref.seniority
    return p


def phones_for(store: Any, company_ids: Any) -> dict[str, str]:
    """company id -> business phone, for the companies whose insights hold one."""
    out: dict[str, str] = {}
    for cid in {c for c in company_ids if c}:
        phone = insight_phone(safe_insights(store, cid))
        if phone:
            out[cid] = phone
    return out


# ---- companies


def parse_company_params(request: Request) -> dict[str, Any]:
    p = parse_common(request.query_params)
    p["sort"] = p["sort"] if p["sort"] in ("relevance", "name", "size") else "relevance"
    return p


def build_company_filters(
    search: Any, p: dict[str, Any], skip: str | None = None
) -> CompanyFilters:
    """CompanyFilters for the pack; skip ("location" | "industry" | "size") leaves one group
    out (facet counts). `search` is unused: kept for callers of the old signature."""
    restricted = domain_restriction(p)
    domains = restricted if restricted is not None else ([p["domain"]] if p["domain"] else [])
    return sf.build_company_filters(p, domains, skip)


def scan_companies(search: Any, flt: CompanyFilters) -> tuple[list[Company], int, bool]:
    """Up to SCAN_CAP matching companies in pack order: (items, pack total, truncated)."""
    items: list[Company] = []
    page, total = 1, 0
    while True:
        res = search.search_companies(flt, page=page, per_page=SCAN_BATCH)
        items.extend(res.items)
        total = res.total
        if not res.items or len(items) >= total or len(items) >= SCAN_CAP:
            break
        page += 1
    return items, total, total > len(items)


def _relevance_key(c: Company) -> tuple[Any, ...]:
    return (c.domain is None, c.size_range is None, c.name.lower(), c.id)


def size_order(c: Company) -> int:
    return SIZE_RANGES.index(c.size_range) if c.size_range in SIZE_RANGES else -1


def sort_companies(items: list[Company], sort: str) -> list[Company]:
    if sort == "name":
        return sorted(items, key=lambda c: c.name.lower())
    if sort == "size":
        return sorted(items, key=size_order, reverse=True)
    return sorted(items, key=_relevance_key)


def run_companies(
    search: Any, store: Any, p: dict[str, Any], *, facets: bool = True, count_only: bool = False
) -> dict[str, Any]:
    """Run the companies search for the page, the count endpoint and the facet panel."""
    flt = build_company_filters(search, p)
    post = company_post_filter(store, p)
    saved_ids = store.saved_company_ids() if not count_only else set()
    restricted = domain_restriction(p)
    lookup = CompanyLookup(search, store)
    pool: list[Company] | None = None
    capped = False
    if post:
        scanned, _raw, capped = scan_companies(search, flt)
        pool = [c for c in scanned if post(c)]
        total_all = len(pool)
    else:
        total_all = search.search_companies(flt, page=1, per_page=1).total
    out: dict[str, Any] = {
        "total_all": total_all,
        "capped": capped,
        "saved_count": 0,
        "items": [],
        "total": total_all,
        "ind_labels": {},
    }
    if count_only:
        return out
    saved_items = [
        c
        for cid in sorted(saved_ids)[:SCAN_BATCH]
        if (c := lookup.get(cid)) is not None
        and company_matches(c, p)
        and (post is None or post(c))
        and (restricted is None or (c.domain or "") in restricted)
    ]
    out["saved_count"] = len(saved_items)
    per = p["per"]
    rows: list[Company] | None
    if p["tab"] == "saved":
        rows = sort_companies(saved_items, p["sort"])
    elif p["tab"] == "new" or p["sort"] != "relevance":
        if pool is None:
            pool, _raw, capped = scan_companies(search, flt)
            out["capped"] = capped
        rows = sort_companies(
            [c for c in pool if p["tab"] != "new" or c.id not in saved_ids], p["sort"]
        )
    else:
        rows = pool  # scanned in relevance order, or None: page straight from the pack
    if rows is None:
        total = total_all
    else:
        total = len(rows)
        if capped and p["tab"] == "all":
            total = total_all
        elif capped and p["tab"] == "new":
            total = max(total_all - len(saved_items), 0)
    page = min(p["page"], max(1, -(-total // per)))
    if rows is None:
        out["items"] = list(search.search_companies(flt, page=page, per_page=per).items)
    else:
        out["items"] = rows[(page - 1) * per : page * per]
    out["total"], out["page"] = total, page
    if facets:
        out["facets"] = company_facets(search, p)
        labels = {}
        for c in out["items"]:
            if c.industry and c.industry_label:
                labels[c.industry] = c.industry_label
        out["ind_labels"] = labels
    return out


def company_facets(search: Any, p: dict[str, Any]) -> dict[str, list[tuple[str, int]]]:
    """Counts for the location / industry / size lists, each ignoring its own group's filters."""
    specs = (
        ("country", "location", ("country", "xcountry"), 300),
        ("industry", "industry", ("industry", "xindustry"), 300),
        ("size_range", "size", ("size",), 20),
    )
    out: dict[str, list[tuple[str, int]]] = {}
    for facet, skip, keys, limit in specs:
        try:
            counts = dict(search.facet_counts(facet, build_company_filters(search, p, skip), limit))
        except Exception:
            counts = {}
        for k in keys:
            for sel in p[k]:
                counts.setdefault(sel, 0)
        selected = {v for k in keys for v in p[k]}
        out[facet] = sorted(counts.items(), key=lambda kv: (kv[0] not in selected, -kv[1], kv[0]))
    return out


# ---- people


def parse_people_params(request: Request) -> dict[str, Any]:
    g = request.query_params
    p = parse_common(g)
    p.update(sf.parse_people_extra(g))
    p["sort"] = p["sort"] if p["sort"] in ("relevance", "name", "company") else "relevance"
    return p


def people_filters(p: dict[str, Any]) -> PeopleFilters:
    """What the local store can filter itself; the rest is narrowed in run_people."""
    return PeopleFilters(
        text=p["q"] or None,
        titles=sf.store_title_terms(p),
        seniorities=[Seniority(s) for s in p["seniority"]],
        email_statuses=[EmailStatus(s) for s in p["status"]],
        company=CompanyFilters(
            domains=restricted
            if (restricted := domain_restriction(p)) is not None
            else ([p["domain"]] if p["domain"] else [])
        ),
    )


def scan_people(store: Any, flt: PeopleFilters) -> tuple[list[Person], int, bool]:
    items: list[Person] = []
    page, total = 1, 0
    while True:
        res = store.search_people(flt, page=page, per_page=SCAN_BATCH)
        items.extend(res.items)
        total = res.total
        if not res.items or len(items) >= total or len(items) >= SCAN_CAP:
            break
        page += 1
    return items, total, total > len(items)


def run_people(
    search: Any, store: Any, p: dict[str, Any], *, facets: bool = True, count_only: bool = False
) -> dict[str, Any]:
    ctx = sf.people_context(store, p)
    p["_texpr_error"] = ctx.expr_error
    cands, total_raw, capped = scan_people(store, people_filters(p))
    cands = [x for x in cands if sf.person_matches(x, p, ctx)]
    lookup = CompanyLookup(search, store)
    filtered = cands
    if sf.has_company_conditions(p):
        filtered = [x for x in cands if company_matches(lookup.get(x.company_id), p, text=False)]
    restricted = domain_restriction(p)
    if restricted is not None:
        filtered = [x for x in filtered if (x.company_domain or "") in restricted]
    seniority = (p.get("_x") or {}).get("seniority")
    if seniority is not None:
        filtered = [x for x in filtered if x.seniority == seniority]
    post = company_post_filter(store, p)
    if post is not None:
        filtered = [x for x in filtered if (c := lookup.get(x.company_id)) is not None and post(c)]
    out: dict[str, Any] = {
        "total_all": len(filtered),
        "total": len(filtered),
        "capped": capped,
        "saved_count": 0,
        "items": [],
        "lookup": lookup,
    }
    if count_only:
        return out
    if facets:
        counts: dict[str, Counter[str]] = {
            "country": Counter(),
            "industry": Counter(),
            "size": Counter(),
        }
        for x in cands:
            c = lookup.get(x.company_id)
            if c is None:
                continue
            if c.country and company_matches(c, p, skip="location", text=False):
                counts["country"][c.country.upper()] += 1
            if c.industry and company_matches(c, p, skip="industry", text=False):
                counts["industry"][c.industry.upper()] += 1
            if c.size_range and company_matches(c, p, skip="size", text=False):
                counts["size"][c.size_range] += 1
        facet_lists = {}
        for facet, keys in (
            ("country", ("country", "xcountry")),
            ("industry", ("industry", "xindustry")),
            ("size", ("size",)),
        ):
            merged = dict(counts[facet])
            for k in keys:
                for sel in p[k]:
                    merged.setdefault(sel, 0)
            selected = {v for k in keys for v in p[k]}
            facet_lists[facet] = sorted(
                merged.items(), key=lambda kv: (kv[0] not in selected, -kv[1], kv[0])
            )
        out["facets"] = {
            "country": facet_lists["country"],
            "industry": facet_lists["industry"],
            "size_range": facet_lists["size"],
        }
        titles = Counter(" ".join(x.title.split()) for x in cands if x.title)
        out["top_titles"] = titles.most_common(3)
    saved_ids = store.saved_person_ids()
    out["saved_count"] = sum(1 for x in filtered if x.id in saved_ids)
    rows = filtered
    if p["tab"] == "saved":
        rows = [x for x in rows if x.id in saved_ids]
    elif p["tab"] == "new":
        rows = [x for x in rows if x.id not in saved_ids]
    if p["sort"] == "name":
        rows = sorted(rows, key=lambda x: x.full_name.lower())
    elif p["sort"] == "company":

        def company_key(x: Person) -> str:
            c = lookup.get(x.company_id)
            return (c.name if c else x.company_domain or "￿").lower()

        rows = sorted(rows, key=company_key)
    out["total"] = len(rows)
    per = p["per"]
    page = min(p["page"], max(1, -(-len(rows) // per)))
    out["page"] = page
    out["items"] = rows[(page - 1) * per : page * per]
    return out


# ---------------------------------------------------------------- search screens: shared state

COMPANY_COLUMNS: tuple[dict[str, Any], ...] = (
    {"key": "name", "label": "Company", "locked": True, "default": True},
    {"key": "domain", "label": "Domain"},
    {"key": "location", "label": "Location", "default": True},
    {"key": "industry", "label": "Industry", "default": True},
    {"key": "size", "label": "Employees", "default": True},
    {"key": "sources", "label": "Source", "default": True},
    {"key": "phone", "label": "Phone"},
    {"key": "country", "label": "Country"},
    {"key": "city", "label": "City"},
    {"key": "region", "label": "Region"},
    {"key": "legal_name", "label": "Legal name"},
    {"key": "founded", "label": "Founded"},
    {"key": "lei", "label": "LEI"},
    {"key": "wikidata", "label": "Wikidata"},
)
PEOPLE_COLUMNS: tuple[dict[str, Any], ...] = (
    {"key": "name", "label": "Name", "locked": True, "default": True},
    {"key": "title", "label": "Job title", "default": True},
    {"key": "company", "label": "Company", "default": True},
    {"key": "email", "label": "Email", "default": True},
    {"key": "phone", "label": "Phone", "default": True},
    {"key": "source", "label": "Source", "default": True},
    {"key": "seniority", "label": "Seniority"},
    {"key": "department", "label": "Department"},
    {"key": "domain", "label": "Company domain"},
    {"key": "location", "label": "Location"},
    {"key": "industry", "label": "Industry"},
    {"key": "size", "label": "Employees"},
    {"key": "status", "label": "Email status"},
    {"key": "last_seen", "label": "Last seen"},
)
SORTS = {
    "companies": (("relevance", "Relevance"), ("name", "Name A to Z"), ("size", "Size, largest")),
    "people": (
        ("relevance", "Recently found"),
        ("name", "Name A to Z"),
        ("company", "Company A to Z"),
    ),
}


def search_ui(
    kind: str,
    p: dict[str, Any],
    store: Any,
    opts: dict[str, list[tuple[str, int]]],
    ind_labels: dict[str, str],
    demo: bool = False,
) -> dict[str, Any]:
    """Everything the shared search templates need beyond the result rows. The filter side
    (groups, chips, options) comes from search_filters.filter_ui."""
    base = "/companies" if kind == "companies" else "/people"
    params = url_params(p)
    menu = results.view_rows(kind, store, qs(params), base)  # saved-view menu (results.py)
    views, current_view = menu["rows"], menu["current"]
    return {
        "kind": kind,
        "base": base,
        "demo": demo,
        "llm": llm_config() is not None,
        "xstatus": (p.get("_x") or {}).get("status", {}),
        "workflow_url": "/workflows/new?" + urlencode({"source": kind, "query": qs(params)}),
        "p": p,
        "params": params,
        "base_qs": qs(params),
        **sf.filter_ui(kind, p, store, opts, ind_labels),
        "columns": COMPANY_COLUMNS if kind == "companies" else PEOPLE_COLUMNS,
        "sorts": SORTS[kind],
        "views": views,
        "current_view": current_view or "Default view",
        "page_sizes": PAGE_SIZES,
    }


templates.env.globals["remove_url"] = lambda ui, key, value: remove_url(
    ui["base"], ui["params"], key, value
)


def landing_context(
    kind: str, p: dict[str, Any], ui: dict[str, Any], res: dict[str, Any]
) -> dict[str, Any]:
    """The landing (AI prompt + quick filters) replaces the table when nothing is asked yet."""
    on = sf.is_landing(p, ui["active_total"]) and res["total_all"] > 0
    base = ui["base"]
    return {
        "landing": on,
        "quick": sf.quick_filters(kind, res.get("facets", {}), res.get("top_titles", []))
        if on
        else [],
        "browse_url": f"{base}?all=1",
    }


# ---------------------------------------------------------------- companies


@router.get("/companies", response_class=HTMLResponse)
def companies(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> Response:
    p = resolve_extras(request, parse_company_params(request), "companies")
    res = run_companies(search, store, p)
    ui = search_ui("companies", p, store, res["facets"], res["ind_labels"], is_demo(request))
    p["page"] = res["page"]
    counts = {
        "all": res["total_all"],
        "saved": res["saved_count"],
        "new": max(res["total_all"] - res["saved_count"], 0),
    }
    return render(
        request,
        "companies.html",
        active="companies",
        p=p,
        s=ui,
        items=res["items"],
        total=res["total"],
        saved_ids=store.saved_company_ids(),
        phones=phones_for(store, [c.id for c in res["items"]]),
        counts=counts,
        isic=ISIC_SECTIONS,
        has_filters=ui["active_total"] > 0,
        pg=pager("/companies", ui["params"], p["page"], res["total"], p["per"], res["capped"]),
        lists=store.lists("companies"),
        **landing_context("companies", p, ui, res),
    )


@router.get("/search/count", response_class=HTMLResponse)
def search_count(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> Response:
    """Live "N records found" for the All filters window (ignores tab, sort and paging)."""
    if request.query_params.get("kind") == "people":
        res = run_people(
            search,
            store,
            resolve_extras(request, parse_people_params(request), "people"),
            facets=False,
            count_only=True,
        )
    else:
        res = run_companies(
            search,
            store,
            resolve_extras(request, parse_company_params(request), "companies"),
            facets=False,
            count_only=True,
        )
    return templates.TemplateResponse(
        request,
        "_search_count.html",
        {"request": request, "total": res["total_all"], "capped": res["capped"]},
    )


@router.get("/search/suggest")
def search_suggest(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> JSONResponse:
    """Values for a filter box (titles, places, industries) as JSON: {items: [{name, value,
    label, hint}]}. name is the query parameter the value fills."""
    g = request.query_params
    kind = "people" if g.get("kind") == "people" else "companies"
    field_name = g.get("field", "")
    if field_name not in ("title", "xtitle", "place", "xplace", "industry", "xindustry"):
        return JSONResponse({"items": []}, status_code=400)
    items = sf.suggest(kind, field_name, g.get("q", ""), search, store)
    return JSONResponse({"items": items})


def _persona_back(form: Any, request: Request, drop: str | None = None) -> str:
    back = safe_next(str(form.get("next") or ""), "")
    if not back:
        ref = urlparse(request.headers.get("referer") or "")
        back = ref.path + (f"?{ref.query}" if ref.query else "") if ref.path else ""
    if not back.startswith("/people"):
        back = "/people"
    if drop:
        path, _, query = back.partition("?")
        pairs = [(k, v) for k, v in parse_qsl(query) if not (k == "persona" and v == drop)]
        pairs = [(k, v) for k, v in pairs if k != "notice"]
        back = path + (f"?{urlencode(pairs)}" if pairs else "")
    return back


def _persona_done(request: Request, message: str, back: str, kind: str = "ok") -> Response:
    """Toast, then re-render the workspace at `back` (HTMX) or redirect there."""
    if is_htmx(request):
        target = {"path": back, "target": "#workspace", "select": "#workspace", "swap": "outerHTML"}
        headers = {**toast_headers(message, kind), "HX-Location": json.dumps(target)}
        return Response(status_code=200, headers=headers)
    return RedirectResponse(with_notice(back, message), status_code=303)


@router.post("/personas")
async def personas_create(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    """Save the job-title filters of the People page as a named persona."""
    form = await form_values(request)
    back = _persona_back(form, request)
    name = " ".join(str(form.get("name") or "").split())[:80]
    filters = clean_filters(
        {
            "title": sf.split_titles(form.getlist("title")),
            "xtitle": sf.split_titles(form.getlist("xtitle")),
            "similar": str(form.get("similar") or "1"),
            "tmode": "advanced" if form.get("tmode") == "advanced" else "",
            "texpr": str(form.get("texpr") or ""),
            "seniority": [s for s in form.getlist("seniority") if s in sf.SENIORITY_VALUES],
            "dept": [d for d in form.getlist("dept") if d in sf.DEPT_LABELS],
        }
    )
    if not name:
        return done(request, "Give the persona a name.", back, "error")
    if not ({"title", "xtitle", "texpr", "seniority", "dept"} & set(filters)):
        return done(request, "Add a job title, level or department first.", back, "error")
    if filters.get("texpr"):
        try:
            sf.parse_title_expr(filters["texpr"])
        except sf.TitleExprError as e:
            return done(request, f"The title expression has a problem: {e}", back, "error")
    personas_for(store).create(name, filters)
    return _persona_done(request, f"Saved persona {name}.", back)


@router.post("/personas/{persona_id}/delete")
async def personas_delete(
    persona_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    back = _persona_back(form, request, drop=persona_id)
    repo = personas_for(store)
    persona = repo.get(persona_id)
    if persona is None or not repo.delete(persona_id):
        return _persona_done(request, "That persona was already deleted.", back, "error")
    return _persona_done(request, f"Deleted persona {persona.name}.", back)


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
    ins = safe_insights(store, company.id)
    phone = insight_phone(ins)
    return {
        "c": company,
        "people": people,
        "emails": emails,
        "is_saved": company.id in store.saved_company_ids(),
        "lists": store.lists("companies"),
        "suppressed": {a.address for a in emails.values() if a and store.is_suppressed(a.address)},
        "ins": ins,
        "phone": phone,
        "tel": tel_href(phone),
        "demo": is_demo(request),
        "cscore": company_score(company, people, ins),
        "dnc": {
            "domain": company.domain,
            "suppressed": bool(company.domain and store.is_suppressed(company.domain)),
        },
        "member_lists": results.lists_holding(store, "companies", company.id),
        "locations": results.company_locations(company, ins),
        "leads": results.lead_groups(people),
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
    ok = not is_demo(request) and _submit_find_people(jobs, company)
    msg = (
        f"Finding people at {company.domain}. Track it in the jobs menu."
        if ok
        else DEMO_REASON
        if is_demo(request)
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
    if auto_enrich(request, "companies", [company.id]):
        msg += " Looking for its people now (enrichment mode is on)."
    if is_htmx(request):
        return drawer_response(request, company, msg)
    return RedirectResponse(with_notice(f"/companies/{company_id}", msg), status_code=303)


# ---------------------------------------------------------------- people


@router.get("/people", response_class=HTMLResponse)
def people(
    request: Request, search: Any = Depends(get_search), store: Any = Depends(get_store)
) -> Response:
    p = resolve_extras(request, parse_people_params(request), "people")
    res = run_people(search, store, p)
    ui = search_ui("people", p, store, res["facets"], {}, is_demo(request))
    p["page"] = res["page"]
    items = res["items"]
    emails = {x.id: store.best_email(x.id) for x in items}
    suppressed = {e.address for e in emails.values() if e and store.is_suppressed(e.address)}
    lookup = res["lookup"]
    companies_by_id = {
        x.company_id: c for x in items if x.company_id and (c := lookup.get(x.company_id))
    }
    counts = {
        "all": res["total_all"],
        "saved": res["saved_count"],
        "new": max(res["total_all"] - res["saved_count"], 0),
    }
    return render(
        request,
        "people.html",
        active="people",
        p=p,
        s=ui,
        items=items,
        total=res["total"],
        emails=emails,
        suppressed=suppressed,
        companies=companies_by_id,
        saved_ids=store.saved_person_ids(),
        phones=phones_for(store, [x.company_id for x in items]),
        counts=counts,
        isic=ISIC_SECTIONS,
        has_filters=ui["active_total"] > 0,
        pg=pager("/people", ui["params"], p["page"], res["total"], p["per"], res["capped"]),
        lists=store.lists("people"),
        **landing_context("people", p, ui, res),
    )


def score_summary(best: Any) -> dict[str, Any]:
    """Our email confidence as a score block: number, label and a short breakdown."""
    if best is None:
        return {"value": None, "label": "No score", "tone": "plain", "rows": []}
    value = int(best.confidence or 0)
    label, tone = (
        ("Strong", "ok") if value >= 80 else ("Fair", "amber") if value >= 50 else ("Low", "red")
    )
    rows = [("Verification status", _status_label(best.status))]
    if best.pattern:
        rows.append(("Address pattern", best.pattern))
    rows.extend((k.replace("_", " ").capitalize(), str(v)) for k, v in (best.checks or {}).items())
    if best.checked_at:
        rows.append(("Last checked", _fmt_date(best.checked_at)))
    return {"value": value, "label": label, "tone": tone, "rows": rows}


def completeness(checks: list[tuple[str, bool]]) -> dict[str, Any]:
    """Share of filled-in fields as a score block: number, label, tone and one row per field."""
    value = round(100 * sum(1 for _, ok in checks if ok) / len(checks)) if checks else 0
    label, tone = (
        ("Strong", "ok") if value >= 80 else ("Fair", "amber") if value >= 50 else ("Low", "red")
    )
    return {
        "value": value,
        "label": label,
        "tone": tone,
        "rows": [(name, "Yes" if ok else "Missing") for name, ok in checks],
    }


def profile_score(person: Person, best: Any, company: Company | None) -> dict[str, Any]:
    return completeness(
        [
            ("Name", bool(person.full_name)),
            ("Job title", bool(person.title)),
            ("Seniority", bool(person.seniority)),
            ("Department", bool(person.department)),
            ("Email found", best is not None),
            ("Email verified", best is not None and best.status == EmailStatus.VALID),
            ("Company record", company is not None),
            ("Company domain", bool(person.company_domain)),
            ("Source link", bool(person.source_urls)),
        ]
    )


def company_score(company: Company, people: list[Person], ins: Any) -> dict[str, Any]:
    return completeness(
        [
            ("Domain", bool(company.domain)),
            ("Country", bool(company.country)),
            ("City", bool(company.city)),
            ("Industry", bool(company.industry or company.industry_label)),
            ("Employees", bool(company.size_range)),
            ("Founded year", bool(company.founded_year)),
            ("Registry id (LEI or Wikidata)", bool(company.registry_ids)),
            ("People found", bool(people)),
            ("Insights refreshed", ins is not None),
        ]
    )


def person_context(request: Request, person: Person) -> dict[str, Any]:
    store, search = request.app.state.store, request.app.state.search
    best = store.best_email(person.id)
    getter = getattr(store, "emails_for", None) or getattr(store, "list_emails", None)
    try:
        candidates = list(getter(person.id)) if getter else ([best] if best else [])
    except Exception:
        candidates = [best] if best else []
    company = CompanyLookup(search, store).get(person.company_id)
    colleagues: list[Person] = []
    emails: dict[str, Any] = {}
    if person.company_domain:
        res = store.search_people(
            PeopleFilters(company=CompanyFilters(domains=[person.company_domain])),
            page=1,
            per_page=50,
        )
        colleagues = [
            x for x in res.items if x.company_domain == person.company_domain and x.id != person.id
        ]
        emails = {x.id: store.best_email(x.id) for x in colleagues}
    suppressed = {c.address for c in candidates if c and store.is_suppressed(c.address)}
    domain_sup = bool(person.company_domain and store.is_suppressed(person.company_domain))
    opted_out = bool(suppressed) or domain_sup
    ins = safe_insights(store, person.company_id)
    phone = insight_phone(ins)
    return {
        "person": person,
        "best": best,
        "candidates": candidates,
        "company": company,
        "suppressed": suppressed,
        "opted_out": opted_out,
        "score": {**score_summary(best), "profile": profile_score(person, best, company)},
        "dnc": {
            "address": best.address if best else None,
            "address_suppressed": bool(suppressed) and not domain_sup,
            "domain": person.company_domain,
            "domain_suppressed": domain_sup,
            "suppressed": opted_out,
        },
        "ins": ins,
        "phone": phone,
        "tel": tel_href(phone),
        "demo": is_demo(request),
        "colleagues": colleagues,
        "emails": emails,
        "lists": store.lists("people"),
        "is_saved": person.id in store.saved_person_ids(),
        "cscore": company_score(company, [person, *colleagues], ins) if company else None,
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


def clean_domain(value: str) -> str | None:
    """'https://www.Example.com/team' -> 'example.com'; None when it is not a domain."""
    host = re.sub(r"^[a-z]+://", "", value.strip().lower()).split("/")[0].split(":")[0]
    host = host.removeprefix("www.")
    return host if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host or "") else None


@router.post("/people/new")
async def person_new(
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
    search: Any = Depends(get_search),
) -> Response:
    """Import > Single contact: add one person by hand (name and company website)."""
    from leadcommons.models import EmailCandidate, make_id
    from leadcommons.search import CompanyFilters as _CF

    form = await form_values(request)
    name = " ".join(str(form.get("full_name") or "").split())[:120]
    domain = clean_domain(str(form.get("domain") or ""))
    back = "/people?all=1"
    if not name or not domain:
        return done(request, "Give a name and the company website, e.g. acme.com.", back, "error")
    email = str(form.get("email") or "").strip().lower()[:200]
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        return done(request, "That email address does not look right.", back, "error")
    found = search.search_companies(_CF(domains=[domain]), page=1, per_page=1)
    company = found.items[0] if found.items else None
    person = Person(
        id=make_id("pe", domain, name),
        full_name=name,
        company_domain=domain,
        company_id=company.id if company else None,
        title=str(form.get("title") or "").strip()[:160] or None,
        source_urls=[],  # added by hand: no page to point to
    )
    store.save_person(person)
    if email:
        store.save_email(
            person.id,
            EmailCandidate(email, EmailStatus.UNKNOWN, 0, None, {"source": "entered by hand"}),
        )
    msg = f"Added {name}." + ("" if email else " Use Find email to look for an address.")
    if is_htmx(request):
        return Response(
            status_code=200,
            headers={**toast_headers(msg), "HX-Redirect": f"/people/{person.id}"},
        )
    return RedirectResponse(with_notice(f"/people/{person.id}", msg), status_code=303)


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
    if is_demo(request):
        return done(request, DEMO_REASON, f"/people/{person_id}", "error")
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
    if is_demo(request):
        return done(request, DEMO_REASON, back, "error")
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
    if is_demo(request):
        return done(request, DEMO_REASON, back, "error")
    n = 0
    for pid in form_ids(form)[:200]:
        person = store.get_person(pid)
        if person:
            jobs.submit("find_email", {"person_id": pid}, f"Find email for {person.full_name}")
            n += 1
    if not n:
        return done(request, "Select at least one person first.", back, "error")
    return done(request, f"Started {n} email search{'es' if n != 1 else ''}.", back)


# ---------------------------------------------------------------- do not contact


def person_drawer_response(
    request: Request, person: Person, message: str, kind: str = "ok"
) -> Response:
    resp = templates.TemplateResponse(
        request, "_person_drawer.html", {"request": request, **person_context(request, person)}
    )
    resp.headers.update(toast_headers(message, kind))
    return resp


def _unsuppress(store: Any, value: str) -> bool:
    remover = getattr(store, "remove_suppression", None)
    return bool(remover(value)) if remover else False


def _addresses(store: Any, person_id: str) -> list[str]:
    getter = getattr(store, "emails_for", None) or getattr(store, "list_emails", None)
    try:
        found = list(getter(person_id)) if getter else []
    except Exception:
        found = []
    best = store.best_email(person_id)
    if best and not any(e.address == best.address for e in found):
        found.append(best)
    return [e.address for e in found]


def _person_dnc(request: Request, person_id: str, suppress: bool) -> Response:
    store = request.app.state.store
    person = store.get_person(person_id)
    if person is None:
        return not_found(request, "Person")
    back = f"/people/{person_id}"
    addresses = _addresses(store, person_id)
    if suppress and not addresses:
        msg, kind = "No email address on file. Suppress the company domain instead.", "error"
    elif suppress:
        for a in addresses:
            store.add_suppression(a, "do not contact (profile)")
        msg, kind = f"{person.full_name} is now on your do-not-contact list.", "ok"
    else:
        n = sum(1 for a in addresses if _unsuppress(store, a))
        msg, kind = (
            (f"Removed {n} address{'es' if n != 1 else ''} from the do-not-contact list.", "ok")
            if n
            else ("Nothing to remove. The company domain may be suppressed instead.", "error")
        )
    if is_htmx(request) and request.headers.get("hx-target") == "drawer":
        return person_drawer_response(request, person, msg, kind)
    return done(request, msg, back, kind)


@router.post("/people/{person_id}/suppress")
def person_suppress(person_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    return _person_dnc(request, person_id, True)


@router.post("/people/{person_id}/unsuppress")
def person_unsuppress(person_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    return _person_dnc(request, person_id, False)


def _company_dnc(request: Request, company_id: str, suppress: bool) -> Response:
    store, search = request.app.state.store, request.app.state.search
    company = search.get_company(company_id)
    if company is None:
        return not_found(request, "Company")
    back = f"/companies/{company_id}"
    if not company.domain:
        msg, kind = f"{company.name} has no known domain to suppress.", "error"
    elif suppress:
        store.add_suppression(company.domain, "do not contact (company profile)")
        msg, kind = f"Everyone at {company.domain} is now on your do-not-contact list.", "ok"
    elif _unsuppress(store, company.domain):
        msg, kind = f"{company.domain} is no longer on the do-not-contact list.", "ok"
    else:
        msg, kind = f"{company.domain} was not on the list.", "error"
    if is_htmx(request) and request.headers.get("hx-target") == "drawer":
        return drawer_response(request, company, msg, kind)
    return done(request, msg, back, kind)


@router.post("/companies/{company_id}/suppress")
def company_suppress(company_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    return _company_dnc(request, company_id, True)


@router.post("/companies/{company_id}/unsuppress")
def company_unsuppress(company_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    return _company_dnc(request, company_id, False)


# ---------------------------------------------------------------- insights refresh + bulk tasks


def _submit_insights(jobs: Any, company: Company) -> bool:
    if not company.domain:
        return False
    jobs.submit(
        "company_insights",
        {"company_id": company.id, "domain": company.domain},
        f"Refresh insights for {company.name}",
    )
    return True


@router.post("/companies/{company_id}/refresh-insights")
def company_refresh_insights(
    company_id: str,
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    jobs: Any = Depends(get_jobs),
) -> Response:
    company = search.get_company(company_id)
    if company is None:
        return not_found(request, "Company")
    back = f"/companies/{company_id}"
    if is_demo(request):
        return done(request, DEMO_REASON, back, "error")
    if not _submit_insights(jobs, company):
        return done(request, f"{company.name} has no known domain to look at.", back, "error")
    return done(
        request, f"Refreshing insights for {company.name}. Track it in the jobs menu.", back
    )


@router.post("/bulk/refresh-insights")
async def bulk_refresh_insights(
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    jobs: Any = Depends(get_jobs),
) -> Response:
    form = await form_values(request)
    back = safe_next(form.get("next"), "/companies")
    if is_demo(request):
        return done(request, DEMO_REASON, back, "error")
    n = 0
    for cid in form_ids(form)[:100]:
        company = search.get_company(cid)
        if company and _submit_insights(jobs, company):
            n += 1
    if not n:
        return done(request, "Select companies that have a known domain first.", back, "error")
    return done(request, f"Refreshing insights for {n} compan{'ies' if n != 1 else 'y'}.", back)


@router.post("/bulk/create-tasks")
async def bulk_create_tasks(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    ids = form_ids(form)[:200]
    title = str(form.get("title") or "").strip()[:200]
    if not ids:
        return done(request, "Select at least one record first.", back, "error")
    if not title:
        return done(request, "Give the task a title.", back, "error")
    task_kind = str(form.get("task_kind") or "action")
    if task_kind not in ("call", "email", "action", "linkedin"):
        task_kind = "action"
    days = _opt_int(form.get("due_days"))
    due = utcnow() + timedelta(days=days) if days is not None and 0 <= days <= 365 else None
    n = 0
    try:
        for rid in ids:
            if kind == "people":
                person = store.get_person(rid)
                if person is None:
                    continue
                engage.create_task(
                    store,
                    title,
                    person_id=person.id,
                    company_id=person.company_id,
                    due=due,
                    kind=task_kind,
                )
            else:
                engage.create_task(store, title, company_id=rid, due=due, kind=task_kind)
            n += 1
    except NotImplementedError:
        return done(request, "Tasks are not available in this build yet.", back, "error")
    if not n:
        return done(request, "Nothing to create tasks for.", back, "error")
    return done(request, f"Created {n} task{'s' if n != 1 else ''}.", back)


@router.post("/bulk/suppress")
async def bulk_suppress(
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    store: Any = Depends(get_store),
) -> Response:
    """Do not contact: people (every known address) or companies (the whole domain)."""
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    n = 0
    for rid in form_ids(form)[:200]:
        if kind == "people":
            for address in _addresses(store, rid):
                store.add_suppression(address, "do not contact (bulk)")
                n += 1
        else:
            company = search.get_company(rid)
            if company and company.domain:
                store.add_suppression(company.domain, "do not contact (bulk)")
                n += 1
    if not n:
        return done(request, "Nothing to suppress: no address or domain is known.", back, "error")
    return done(
        request, f"Added {n} entr{'ies' if n != 1 else 'y'} to the do-not-contact list.", back
    )


# ---------------------------------------------------------------- AI research on selected rows

AI_MAX_ROWS = 25
AI_SYSTEM = (
    "You help a salesperson research prospects. You get one record and one instruction. "
    "Answer the instruction for that record only, in one short plain-text line under 200 "
    "characters, no markdown. If the record does not contain enough to tell, answer 'Unknown'."
)
AI_FORMULA_SYSTEM = (
    "You compute one value per record. You get a record and a description of the value. "
    "Reply with only that value (a number, a word or a short phrase), nothing else. "
    "If it cannot be worked out from the record, reply 'Unknown'."
)


def ask_model(instruction: str, record: str, mode: str) -> str:
    """One OpenAI-compatible chat call for one row. Never raises: returns an error text."""
    cfg = llm_config()
    if cfg is None:
        return "No model configured"
    base, model, key = cfg
    body = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": AI_FORMULA_SYSTEM if mode == "formula" else AI_SYSTEM},
            {"role": "user", "content": f"Record:\n{record}\n\nInstruction: {instruction}"},
        ],
    }
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=30.0)
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"]).strip()[:400] or "Unknown"
    except Exception as exc:
        return f"Model error ({type(exc).__name__})"


def ai_record_text(kind: str, rid: str, search: Any, store: Any) -> tuple[str, str] | None:
    """(label, text sent to the model) for one row: only the fields shown in the table."""
    if kind == "people":
        x = store.get_person(rid)
        if x is None:
            return None
        co = CompanyLookup(search, store).get(x.company_id)
        parts = [
            f"Name: {x.full_name}",
            f"Job title: {x.title or 'unknown'}",
            f"Company: {co.name if co else x.company_domain or 'unknown'}",
            f"Company domain: {x.company_domain or 'unknown'}",
        ]
        if co:
            parts += [
                f"Industry: {co.industry_label or co.industry or 'unknown'}",
                f"Country: {co.country or 'unknown'}",
                f"Employees: {co.size_range or 'unknown'}",
            ]
        return x.full_name, "\n".join(parts)
    c = search.get_company(rid)
    if c is None:
        return None
    return c.name, "\n".join(
        [
            f"Company: {c.name}",
            f"Domain: {c.domain or 'unknown'}",
            f"Industry: {c.industry_label or c.industry or 'unknown'}",
            f"Country: {c.country or 'unknown'}, city: {c.city or 'unknown'}",
            f"Employees: {c.size_range or 'unknown'}",
            f"Founded: {c.founded_year or 'unknown'}",
        ]
    )


def run_ai_rows(
    kind: str, ids: list[str], instruction: str, mode: str, search: Any, store: Any
) -> dict[str, str]:
    records = {}
    for rid in ids:
        rec = ai_record_text(kind, rid, search, store)
        if rec:
            records[rid] = rec[1]
    with ThreadPoolExecutor(max_workers=4) as pool:
        answers = list(pool.map(lambda r: ask_model(instruction, r, mode), records.values()))
    return dict(zip(records, answers, strict=True))


@router.post("/bulk/ai-prompt")
async def bulk_ai_prompt(
    request: Request,
    _: None = Depends(csrf),
    search: Any = Depends(get_search),
    store: Any = Depends(get_store),
) -> Response:
    """Run a prompt on the selected rows; the page shows the answers as a temporary column."""
    if is_demo(request):
        return JSONResponse({"error": DEMO_REASON}, status_code=403)
    if llm_config() is None:
        return JSONResponse(
            {"error": "No model configured. Set LEADCOMMONS_LLM_URL and LEADCOMMONS_LLM_MODEL."},
            status_code=400,
        )
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    instruction = str(form.get("prompt") or "").strip()[:500]
    ids = form_ids(form)[:AI_MAX_ROWS]
    if not instruction or not ids:
        return JSONResponse({"error": "Select rows and write a prompt first."}, status_code=400)
    mode = "formula" if form.get("mode") == "formula" else "prompt"
    answers = await run_in_threadpool(run_ai_rows, kind, ids, instruction, mode, search, store)
    return JSONResponse({"answers": answers, "title": instruction[:40]})


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


def add_records(
    request: Request, list_id: str, kind: str, ids: list[str], auto: bool = True
) -> int:
    """Add records to a list (companies are saved locally first). With the enrichment mode of
    Search settings on, look-ups start for what was added; returns how many were queued."""
    store, search = request.app.state.store, request.app.state.search
    if kind == "companies":
        for cid in ids:
            c = search.get_company(cid)
            if c:
                store.save_company(c)
    store.add_to_list(list_id, ids)
    return auto_enrich(request, kind, ids) if auto else 0


AUTO_KEYS = {"people": "auto_enrich_people", "companies": "auto_enrich_companies"}


def auto_enrich(request: Request, kind: str, ids: list[str]) -> int:
    """Enrichment mode (Search settings): saving people looks for their emails, saving
    companies looks for their people. Off in the demo; skips opted-out records."""
    if is_demo(request) or not load_state(request.app.state.settings).get(AUTO_KEYS[kind]):
        return 0
    store, search, jobs = request.app.state.store, request.app.state.search, request.app.state.jobs
    n = 0
    for rid in ids[:200]:
        if kind == "people":
            person = store.get_person(rid)
            status, suppressed = results.person_status(store, rid) if person else ("", True)
            if person and not suppressed and status != "valid":
                jobs.submit("find_email", {"person_id": rid}, f"Find email for {person.full_name}")
                n += 1
        else:
            company = search.get_company(rid)
            if (
                company
                and company.domain
                and not store.is_suppressed(company.domain)
                and _submit_find_people(jobs, company)
            ):
                n += 1
    return n


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
    queued = add_records(request, list_id, kind, ids)
    name = new_name or next((li.name for li in store.lists(kind) if li.id == list_id), "list")
    n = len(ids)
    msg = f"Added {n} to {name}."
    if queued:
        msg += f" Started {queued} look-up{'s' if queued != 1 else ''} (enrichment mode is on)."
    return done(request, msg, back)


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
    # the save window asks for its CSV with apply_keep=1: only the people it kept
    keep = save_options(form, kind)["keep"] if form.get("apply_keep") == "1" else None
    if keep is not None:
        store = request.app.state.store
        ids = [i for i in ids if results.person_status(store, i)[0] in keep]
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


async def _view_form(request: Request) -> tuple[Any, str, str]:
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    return form, kind, back


@router.post("/searches/{view_id}/rename")
async def searches_rename(
    view_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form, _kind, back = await _view_form(request)
    if view_id.startswith("system:"):
        return done(request, results.SYSTEM_LOCKED, back, "error")
    name = str(form.get("name") or "").strip()[:120]
    if not name:
        return done(request, "Give the search a name.", back, "error")
    if not results.rename_view(store, view_id, name):
        return done(request, "That saved search no longer exists.", back, "error")
    return done(request, f"Renamed to {name}.", back)


@router.post("/searches/{view_id}/duplicate")
async def searches_duplicate(
    view_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    _form, kind, back = await _view_form(request)
    new_id = results.duplicate_view(store, kind, view_id)
    if new_id is None:
        return done(request, "That saved search no longer exists.", back, "error")
    row = results.get_view(store, new_id)
    return done(request, f"Saved a copy as {row.name if row else 'a new search'}.", back)


@router.post("/searches/{view_id}/star")
async def searches_star(
    view_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form, _kind, back = await _view_form(request)
    if view_id.startswith("system:"):
        return done(request, results.SYSTEM_LOCKED, back, "error")
    on = str(form.get("on") or "1") == "1"
    if not results.star_view(store, view_id, on):
        return done(request, "That saved search no longer exists.", back, "error")
    return done(request, "Starred." if on else "Removed the star.", back)


@router.post("/searches/{view_id}/delete")
async def searches_delete(
    view_id: str, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    _form, _kind, back = await _view_form(request)
    if view_id.startswith("system:"):
        return done(request, results.SYSTEM_LOCKED, back, "error")
    row = results.get_view(store, view_id)
    if row is None or not results.delete_view(store, view_id):
        return done(request, "That saved search no longer exists.", back, "error")
    return done(request, f"Deleted {row.name}.", back)


@router.post("/search-settings")
async def search_settings_save(request: Request, _: None = Depends(csrf)) -> Response:
    """Search settings drawer: the enrichment mode switch (stored per screen)."""
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    if is_demo(request):
        return done(request, DEMO_REASON, back, "error")
    on = str(form.get("auto") or "0") == "1"
    set_state(request.app.state.settings, **{AUTO_KEYS[kind]: on})
    what = "emails of people you save" if kind == "people" else "people at companies you save"
    return done(request, f"Enrichment mode {'on' if on else 'off'}: {what}.", back)


def save_options(form: Any, kind: str) -> dict[str, Any]:
    """Choices of the save window. keep is None until the window sent its status boxes."""
    keep = (
        [k for k in form.getlist("keep") if k in results.STATUS_KEYS]
        if form.get("keep_sent")
        else None
    )
    mode = str(form.get("find_mode") or "")
    return {
        "keep": keep if kind == "people" else None,
        "find": str(form.get("find") or "0") == "1",
        "phones": mode in ("emails_phones", "people_phones"),
        "mode": mode or ("emails" if kind == "people" else "people"),
    }


def plan_for(request: Request, kind: str, ids: list[str], opts: dict[str, Any]) -> dict[str, Any]:
    store, search = request.app.state.store, request.app.state.search
    return results.save_plan(
        kind,
        ids,
        store=store,
        search=search,
        keep=opts["keep"],
        find=opts["find"],
        phones=opts["phones"],
        demo=is_demo(request),
        workers=int(getattr(request.app.state.settings, "max_workers", 4) or 4),
        has_insights=lambda cid: safe_insights(store, cid) is not None,
    )


@router.post("/partials/save-summary", response_class=HTMLResponse)
async def save_summary(request: Request, _: None = Depends(csrf)) -> Response:
    """Right-hand column of the save window: what Save will do for the current choices."""
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    ids = form_ids(form)[:500]
    opts = save_options(form, kind)
    store = request.app.state.store
    list_id = str(form.get("dest_list") or "")
    list_name = str(form.get("dest_new") or "").strip() if list_id == "new" else ""
    if list_id and list_id != "new":
        list_name = next((li.name for li in store.lists(kind) if li.id == list_id), "")
    seq_id = str(form.get("sequence_id") or "")
    ctx = {
        "request": request,
        "kind": kind,
        "plan": plan_for(request, kind, ids, opts),
        "opts": opts,
        "keep": opts["keep"] if opts["keep"] is not None else list(results.DEFAULT_KEEP),
        "list_name": list_name,
        "default_list": results.DEFAULT_LIST_NAME[kind],
        "sequence": results.sequence_name(store, seq_id) if seq_id else None,
        "export": str(form.get("export") or "0") == "1",
        "demo": is_demo(request),
    }
    return templates.TemplateResponse(request, "_search_save_summary.html", ctx)


@router.post("/bulk/save")
async def bulk_save(request: Request, _: None = Depends(csrf)) -> Response:
    """The save window: keep the records whose email status you chose, put them in a list,
    start the look-ups you asked for, add people to a sequence and optionally export a CSV."""
    store, search, jobs = request.app.state.store, request.app.state.search, request.app.state.jobs
    form = await form_values(request)
    kind = valid_kind(form.get("kind"))
    back = safe_next(form.get("next"), "/companies" if kind == "companies" else "/people")
    ids = form_ids(form)[:500]
    if not ids:
        return done(request, "Select at least one record first.", back, "error")
    opts = save_options(form, kind)
    plan = plan_for(request, kind, ids, opts)
    kept = plan["kept"]
    if not kept:
        return done(
            request, "None of the selected people has an email status you kept.", back, "error"
        )
    list_id = str(form.get("dest_list") or "")  # the window shares #bulk with "Add to list"
    new_name = str(form.get("dest_new") or "").strip()[:120]
    list_name = ""
    if list_id == "new":
        if not new_name:
            return done(request, "Name the new list, or pick an existing one.", back, "error")
        list_id, list_name = store.create_list(new_name, kind), new_name
    elif list_id:
        match = next((li for li in store.lists(kind) if li.id == list_id), None)
        if match is None:
            return done(request, "That list does not exist any more.", back, "error")
        list_name = match.name
    elif kind == "people":  # people are saved by being in a list: use the default one
        list_name = results.DEFAULT_LIST_NAME[kind]
        found = next((li for li in store.lists(kind) if li.name == list_name), None)
        list_id = found.id if found else store.create_list(list_name, kind)
    if list_id:
        add_records(request, list_id, kind, kept, auto=False)
    else:
        for cid in kept:
            company = search.get_company(cid)
            if company:
                store.save_company(company)
    noun = (
        ("person" if len(kept) == 1 else "people")
        if kind == "people"
        else ("company" if len(kept) == 1 else "companies")
    )
    parts = [f"Saved {len(kept)} {noun}" + (f" to {list_name}." if list_name else ".")]
    if len(kept) < len(ids):
        parts.append(f"{len(ids) - len(kept)} left out by the email status filter.")
    if opts["find"] and is_demo(request):
        parts.append("Look-ups are off in the public demo.")
    for pid in plan["email_jobs"]:
        person = store.get_person(pid)
        jobs.submit(
            "find_email",
            {"person_id": pid},
            f"Find email for {person.full_name if person else pid}",
        )
    for cid in plan["people_jobs"]:
        company = search.get_company(cid)
        if company:
            _submit_find_people(jobs, company)
    for cid in plan["insight_jobs"]:
        company = search.get_company(cid)
        if company:
            _submit_insights(jobs, company)
    queued = sum(plan["jobs"].values())
    if queued:
        parts.append(
            f"Started {queued} look-up{'s' if queued != 1 else ''}; follow them in the jobs menu."
        )
    seq_id = str(form.get("sequence_id") or "")
    if seq_id and kind == "people":
        from leadcommons.engage.core import EngageError, enroll_summary

        try:
            counts = engage.enroll(store, seq_id, kept)
            parts.append(
                enroll_summary(results.sequence_name(store, seq_id) or "the sequence", counts)
            )
        except EngageError as exc:
            parts.append(str(exc))
    export = str(form.get("export") or "0") == "1"
    if export and not is_htmx(request):  # no JavaScript: answer with the file itself
        header, rows = export_rows(request, kind, kept)
        set_state(request.app.state.settings, exported=True)
        return csv_response(rows, header, f"{kind}-saved.csv")
    message = " ".join(parts)
    if not is_htmx(request):
        return RedirectResponse(with_notice(back, message), status_code=303)
    trigger: dict[str, Any] = {
        "toast": {"message": message, "kind": "ok"},
        "lc-saved": {"kind": kind},
    }
    if export:
        trigger["lc-export"] = {"kind": kind}
    return Response(status_code=200, headers={"HX-Trigger": json.dumps(trigger)})


# ---------------------------------------------------------------- enrich


@router.get("/enrich", response_class=HTMLResponse)
def enrich_page(request: Request) -> Response:
    return render(request, "enrich.html", active="enrich")


# ---------------------------------------------------------------- settings + opt-out


def clean_suppression(value: str) -> str | None:
    v = value.strip().lower()
    if not v or len(v) > 254 or " " in v:
        return None
    if "@" in v:
        local, _, domain = v.rpartition("@")
        return v if local and "." in domain else None
    return v if "." in v else None


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
