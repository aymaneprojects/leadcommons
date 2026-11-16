"""Company insights partials (technologies, jobs, phones, trends). Owner: data agent.

`GET /partials/insights?company_id=&tab=` returns the Company insights card (tabs, refresh
state, tab body) as an HTML fragment for the company drawer/page and the person page.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from pepite import insights
from pepite.models import Company, utcnow
from pepite.web.deps import templates
from pepite.web.pages import csrf

router = APIRouter()

__all__ = ["DEMO_REASON", "csrf", "refreshing_response", "router"]

DEMO_REASON = (
    "Disabled in the public demo. Install Pépite on your computer or server to refresh "
    "company insights."
)
TABS = (  # reference order; News and Website visitors are drawn disabled in the template
    ("overview", "Overview"),
    ("technologies", "Technologies"),
    ("funding", "Funding"),
    ("jobs", "Job postings"),
    ("trends", "Employee trends"),
    ("contact", "Contact"),
)
TAB_KEYS = {k for k, _ in TABS}
MAX_JOB_ROWS = 100
CHART_W, CHART_H, PAD_L, PAD_R, PAD_T, PAD_B = 640, 170, 52, 16, 14, 28


def ins_ago(dt: datetime | None) -> str:
    """ "just now", "5 min ago", "3 h ago", "2 d ago" (naive datetimes are read as UTC)."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=utcnow().tzinfo)
    secs = max(0, int((utcnow() - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def ins_money(amount: float | None, currency: str | None) -> str:
    if amount is None:
        return ""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= limit:
            text = f"{amount / limit:.1f}".rstrip("0").rstrip(".") + suffix
            break
    else:
        text = f"{amount:,.0f}"
    return f"{text} {currency}" if currency else text


def employee_chart(points: list[Any]) -> dict[str, Any] | None:
    """Geometry of the employee line chart (SVG units); None with under 2 dated points."""
    dated = sorted((p for p in points if p.date), key=lambda p: p.date)
    if len(dated) < 2:
        return None
    lo = min(p.count for p in dated)
    hi = max(p.count for p in dated)
    span = (hi - lo) or 1
    inner_w = CHART_W - PAD_L - PAD_R
    inner_h = CHART_H - PAD_T - PAD_B
    coords = []
    for i, p in enumerate(dated):
        x = PAD_L + inner_w * i / (len(dated) - 1)
        y = PAD_T + inner_h * (1 - (p.count - lo) / span)
        coords.append({"x": round(x, 1), "y": round(y, 1), "date": p.date, "count": p.count})
    return {
        "w": CHART_W,
        "h": CHART_H,
        "points": coords,
        "line": " ".join(f"{c['x']},{c['y']}" for c in coords),
        "lo": lo,
        "hi": hi,
        "left": PAD_L,
        "bottom": CHART_H - PAD_B,
        "top": PAD_T,
        "right": CHART_W - PAD_R,
    }


SOCIAL_LABELS = {
    "linkedin": "LinkedIn", "x": "X", "facebook": "Facebook", "instagram": "Instagram",
    "youtube": "YouTube", "github": "GitHub", "tiktok": "TikTok", "xing": "XING",
}  # fmt: skip


def ins_social(network: str) -> str:
    return SOCIAL_LABELS.get(network, network.capitalize())


templates.env.globals.update(ins_ago=ins_ago, ins_money=ins_money, ins_social=ins_social)


def _find_company(request: Request, company_id: str) -> Company | None:
    company = request.app.state.search.get_company(company_id)
    if company is None and hasattr(request.app.state.store, "get_company"):
        company = request.app.state.store.get_company(company_id)
    return company


def _stored(store: Any, company_id: str) -> Any | None:
    try:
        return insights.get(store, company_id)
    except Exception:  # never break a page for missing insights
        return None


def _panel_context(
    request: Request, company: Company, tab: str, job_id: str | None, error: str | None = None
) -> dict[str, Any]:
    tab = tab if tab in TAB_KEYS else "overview"
    ins = _stored(request.app.state.store, company.id)
    job = request.app.state.jobs.get(job_id) if job_id else None
    status = getattr(job, "status", None)
    refreshing = status in ("queued", "running")
    if status == "failed" and not error:
        error = getattr(job, "error", None) or "The refresh failed."
    demo = not request.app.state.settings.enrichment_enabled
    counts = {}
    if ins is not None:
        counts = {
            "technologies": len(ins.technologies),
            "jobs": len(ins.jobs.postings),
            "trends": len(ins.trends.employees),
            "funding": len(ins.trends.listings) + len(ins.trends.revenue),
            "contact": len(ins.contact.phones) + len(ins.contact.emails),
        }
    jobs_view = None
    if ins is not None and tab in {"overview", "jobs"}:
        jobs_view = {
            "by_department": ins.jobs.by_department(),
            "rows": ins.jobs.postings[:MAX_JOB_ROWS],
            "hidden": max(0, len(ins.jobs.postings) - MAX_JOB_ROWS),
        }
    return {
        "c": company,
        "ins": ins,
        "tab": tab,
        "tabs": TABS,
        "counts": counts,
        "refreshing": refreshing,
        "job_id": job_id if refreshing else None,
        "error": error,
        "demo": demo,
        "demo_reason": DEMO_REASON,
        "can_refresh": bool(company.domain) and not demo,
        "jobs_view": jobs_view,
        "chart": employee_chart(ins.trends.employees) if ins and tab == "trends" else None,
        "growth_1y": ins.trends.growth(1) if ins else None,
        "growth_2y": ins.trends.growth(2) if ins else None,
    }


def _render(request: Request, company: Company, tab: str, job_id: str | None) -> Response:
    ctx = _panel_context(request, company, tab, job_id)
    return templates.TemplateResponse(request, "_insights_panel.html", ctx)


def refreshing_response(request: Request, company: Company, tab: str, job_id: str) -> Response:
    """The panel right after a refresh was queued (it polls until the job is done)."""
    return _render(request, company, tab, job_id)


@router.get("/partials/insights", response_class=HTMLResponse)
def insights_partial(
    request: Request,
    company_id: str = "",
    tab: str = "overview",
    job: str | None = None,
) -> Response:
    company = _find_company(request, company_id) if company_id else None
    if company is None:
        return HTMLResponse(
            '<section class="ins ins-missing"><p class="muted">Company not found, so there are '
            "no insights to show.</p></section>",
            status_code=404,
        )
    return _render(request, company, tab, job)
