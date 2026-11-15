"""Win deals: deals, meetings, calls, conversations, activity. Owner: pipeline agent.

Everything here works without JavaScript (plain forms and redirects); HTMX and pipeline.js only
add dialogs, drag and drop and in-place refreshes. See docs/pipeline.md.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote, urlencode, urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from leadcommons import workflows
from leadcommons.models import utcnow
from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.store.pipeline import (
    CALL_OUTCOMES,
    MEETING_STATUSES,
    STAGE_KINDS,
    Activity,
    Call,
    Deal,
    Meeting,
    Pipeline,
    Stage,
)
from leadcommons.web.deps import templates
from leadcommons.web.pages import (
    CompanyLookup,
    csrf,
    form_values,
    is_htmx,
    not_found,
    render,
    safe_next,
    toast_headers,
    with_notice,
)

router = APIRouter()

CURRENCIES = (
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "CAD",
    "AUD",
    "SEK",
    "NOK",
    "DKK",
    "PLN",
    "CZK",
    "JPY",
    "INR",
    "BRL",
    "ZAR",
)
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "INR": "₹", "CAD": "CA$", "AUD": "A$"}
DURATIONS = (15, 30, 45, 60, 90, 120)
OUTCOME_LABELS = {
    "connected": "Connected",
    "no_answer": "No answer",
    "voicemail": "Left voicemail",
    "wrong_number": "Wrong number",
    "busy": "Busy",
}
OUTCOME_TONE = {
    "connected": "ok",
    "no_answer": "amber",
    "voicemail": "plain",
    "wrong_number": "red",
    "busy": "amber",
}
MEETING_LABELS = {
    "scheduled": "Scheduled",
    "done": "Held",
    "no_show": "No-show",
    "cancelled": "Cancelled",
}
# kind -> (icon in pipeline-sprite.svg, label)
KINDS: dict[str, tuple[str, str]] = {
    "note": ("sticky-note", "Note"),
    "call": ("phone", "Call"),
    "meeting": ("calendar", "Meeting"),
    "email_sent": ("send", "Email sent"),
    "email_reply": ("reply", "Reply"),
    "task": ("list-checks", "Task"),
    "task_done": ("circle-check", "Task done"),
    "deal": ("dollar-sign", "Deal"),
    "deal_stage": ("arrow-right", "Stage change"),
    "sequence": ("send", "Sequence"),
    "workflow": ("zap", "Workflow"),
}
CONVERSATION_FILTERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("all", "All", ("note", "call", "meeting", "email_sent", "email_reply")),
    ("note", "Notes", ("note",)),
    ("call", "Calls", ("call",)),
    ("meeting", "Meetings", ("meeting",)),
    ("email", "Emails", ("email_sent", "email_reply")),
]
BOOKING_KEY = "booking_url"


# ---------------------------------------------------------------- formatting helpers


def money(amount: float | None, currency: str | None = "EUR") -> str:
    if amount is None:
        return "—"
    cur = (currency or "EUR").upper()
    num = f"{amount:,.0f}" if float(amount).is_integer() else f"{amount:,.2f}"
    sym = SYMBOLS.get(cur)
    return f"{sym}{num}" if sym else f"{num} {cur}"


def totals_label(by_currency: dict[str, float] | None) -> str:
    if not by_currency:
        return "—"
    return " · ".join(money(v, c) for c, v in sorted(by_currency.items()))


def when_label(dt: datetime | None) -> str:
    if not dt:
        return ""
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')} {dt.year} · {dt.strftime('%H:%M')}"


def duration_label(seconds: int | None) -> str:
    s = int(seconds or 0)
    if s <= 0:
        return "—"
    m, sec = divmod(s, 60)
    return f"{m} min {sec:02d} s" if m else f"{sec} s"


def day_label(d: date | None) -> str:
    return f"{d.day} {d.strftime('%b %Y')}" if d else "—"


def tel(phone: str | None) -> str:
    return "tel:" + re.sub(r"[^\d+]", "", phone or "")


def _hours(minutes: int) -> str:
    return f"{minutes} min" if minutes < 60 else f"{minutes / 60:g} h"


templates.env.globals.update(
    pl_money=money,
    pl_totals=totals_label,
    pl_when=when_label,
    pl_duration=duration_label,
    pl_day=day_label,
    pl_tel=tel,
    pl_hours=_hours,
    pl_kinds=KINDS,
    pl_outcomes=OUTCOME_LABELS,
    pl_outcome_tone=OUTCOME_TONE,
    pl_meeting_labels=MEETING_LABELS,
    pl_currencies=CURRENCIES,
    pl_durations=DURATIONS,
)


def parse_amount(value: Any) -> float | None:
    text = str(value or "").strip().replace(" ", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        raise ValueError("The amount must be a number.") from None
    if number < 0 or number > 1e12:
        raise ValueError("The amount must be a positive number.")
    return number


def parse_day(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError("Use a valid date.") from None


def parse_local_dt(value: Any) -> datetime:
    text = str(value or "").strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        raise ValueError("Choose a start date and time.") from None


def parse_percent(value: Any) -> int | None:
    text = str(value or "").strip().rstrip("%")
    if not text:
        return None
    try:
        return max(0, min(100, int(float(text))))
    except ValueError:
        raise ValueError("Probability must be a number between 0 and 100.") from None


def parse_duration_sec(minutes: Any, seconds: Any) -> int:
    def num(v: Any) -> int:
        try:
            return max(0, int(float(str(v or "0").strip() or 0)))
        except ValueError:
            return 0

    return num(minutes) * 60 + min(num(seconds), 59)


def valid_url(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Use a full link starting with https://")
    return text[:500]


# ---------------------------------------------------------------- calendar links


def _ics_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> list[str]:
    raw = line.encode()
    if len(raw) <= 75:
        return [line]
    out: list[str] = []
    current = ""
    for ch in line:
        limit = 75 if not out else 74
        if len((current + ch).encode()) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    out.append(current)
    return [out[0]] + [" " + part for part in out[1:]]


def meeting_description(m: Meeting) -> str:
    parts = []
    if m.agenda:
        parts.append(m.agenda)
    if m.video_url:
        parts.append(f"Video call: {m.video_url}")
    who = ", ".join(x for x in (m.person_name, m.company_name) if x)
    if who:
        parts.append(f"With: {who}")
    return "\n\n".join(parts)


def build_ics(m: Meeting) -> str:
    """A single-event iCalendar file. Times are floating (no zone): the wall-clock time typed in."""
    fmt = "%Y%m%dT%H%M%S"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//LeadCommons//Meetings//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{m.id}@leadcommons.local",
        f"DTSTAMP:{utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{m.start.strftime(fmt)}",
        f"DTEND:{m.end.strftime(fmt)}",
        f"SUMMARY:{_ics_text(m.title)}",
    ]
    desc = meeting_description(m)
    if desc:
        lines.append(f"DESCRIPTION:{_ics_text(desc)}")
    place = m.location or m.video_url
    if place:
        lines.append(f"LOCATION:{_ics_text(place)}")
    if m.video_url:
        lines.append(f"URL:{m.video_url}")
    lines.append("STATUS:CANCELLED" if m.status == "cancelled" else "STATUS:CONFIRMED")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold(line))
    return "\r\n".join(folded) + "\r\n"


def google_calendar_url(m: Meeting) -> str:
    fmt = "%Y%m%dT%H%M%S"
    params = {
        "action": "TEMPLATE",
        "text": m.title,
        "dates": f"{m.start.strftime(fmt)}/{m.end.strftime(fmt)}",
        "details": meeting_description(m),
        "location": m.location or m.video_url or "",
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params, quote_via=quote)


def outlook_calendar_url(m: Meeting) -> str:
    params = {
        "path": "/calendar/action/compose",
        "rru": "addevent",
        "subject": m.title,
        "startdt": m.start.strftime("%Y-%m-%dT%H:%M:%S"),
        "enddt": m.end.strftime("%Y-%m-%dT%H:%M:%S"),
        "body": meeting_description(m),
        "location": m.location or m.video_url or "",
    }
    return "https://outlook.live.com/calendar/0/deeplink/compose?" + urlencode(
        params, quote_via=quote
    )


templates.env.globals.update(pl_google_url=google_calendar_url, pl_outlook_url=outlook_calendar_url)


# ---------------------------------------------------------------- request helpers


def pipe(request: Request) -> Pipeline:
    state = request.app.state
    store = state.store
    cached = getattr(state, "pipeline", None)
    if cached is None or getattr(state, "pipeline_engine", None) is not store.engine:
        state.pipeline = Pipeline(store.engine)
        state.pipeline_engine = store.engine
    return state.pipeline  # type: ignore[no-any-return]


def lookup(request: Request) -> CompanyLookup:
    return CompanyLookup(request.app.state.search, request.app.state.store)


def current_next(request: Request, default: str) -> str:
    """Page the dialog was opened from (HTMX sends it), so forms return there."""
    src = request.headers.get("hx-current-url") or request.headers.get("referer") or ""
    parsed = urlparse(src)
    if parsed.netloc and parsed.netloc != request.headers.get("host", ""):
        return default
    pairs = [
        p for p in (parsed.query.split("&") if parsed.query else []) if not p.startswith("notice=")
    ]
    url = parsed.path + (("?" + "&".join(pairs)) if pairs else "")
    return safe_next(url, default)


def refs(request: Request, person_id: str | None, company_id: str | None) -> dict[str, Any]:
    """Resolve names for a person and/or company; a person brings their company along."""
    store = request.app.state.store
    look = lookup(request)
    person_id = (person_id or "").strip() or None
    company_id = (company_id or "").strip() or None
    person_name = None
    if person_id:
        try:
            person = store.get_person(person_id)
        except Exception:
            person = None
        if person is not None:
            person_name = person.full_name
            company_id = company_id or person.company_id
        else:
            person_id = None
    company = look.get(company_id)
    if company_id and company is None:
        company_id = None
    return {
        "person_id": person_id,
        "person_name": person_name,
        "company_id": company_id,
        "company_name": company.name if company else None,
    }


def reply(
    request: Request,
    message: str,
    back: str,
    *,
    kind: str = "ok",
    reload: bool = True,
    status: int | None = None,
) -> Response:
    """POST answer. HTMX: toast + an event other fragments listen to (+ page reload if asked)."""
    if is_htmx(request):
        headers = toast_headers(message, kind)
        trigger = json.loads(headers["HX-Trigger"])
        trigger["pl-changed"] = {"message": message}
        headers["HX-Trigger"] = json.dumps(trigger)
        if reload and kind == "ok":
            headers["HX-Redirect"] = with_notice(back, message)
        code = status or (200 if kind == "ok" else 422)
        return Response(status_code=code, headers=headers)
    return RedirectResponse(with_notice(back, message), status_code=303)


def deal_url(deal_id: str) -> str:
    return f"/deals/{deal_id}"


def log_stage_change(
    request: Request, pl: Pipeline, deal: Deal, previous_id: str | None, stage: Stage
) -> None:
    prev = pl.get_stage(previous_id) if previous_id else None
    pl.activity.add(
        "deal_stage",
        f"{deal.name}: {prev.name + ' → ' if prev else ''}{stage.name}",
        person_id=deal.person_id,
        company_id=deal.company_id,
        deal_id=deal.id,
        meta={
            "stage": stage.name,
            "stage_id": stage.id,
            "previous_stage": prev.name if prev else None,
            "previous_stage_id": previous_id,
        },
    )
    try:
        workflows.emit(
            "deal_stage_changed",
            deal_id=deal.id,
            stage=stage.name,
            previous_stage=prev.name if prev else None,
        )
    except Exception:
        pass  # emit must never break a page


def deal_form_fields(form: Any, pl: Pipeline) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": str(form.get("name") or "").strip(),
        "company_id": form.get("company_id"),
        "company_name": form.get("company_name"),
        "person_id": form.get("person_id"),
        "person_name": form.get("person_name"),
        "amount": parse_amount(form.get("amount")),
        "currency": str(form.get("currency") or "EUR").upper(),
        "close_date": parse_day(form.get("close_date")),
        "probability": parse_percent(form.get("probability")),
        "source": form.get("source"),
        "notes": form.get("notes"),
    }
    if not fields["name"]:
        raise ValueError("Give the deal a name.")
    if fields["currency"] not in CURRENCIES:
        fields["currency"] = "EUR"
    return fields


def activity_context(
    request: Request,
    items: list[Activity],
    *,
    person_id: str | None = None,
    company_id: str | None = None,
    deal_id: str | None = None,
) -> list[dict[str, Any]]:
    """Activities plus the names to show as links (only for ids not already in scope)."""
    store, look, pl = request.app.state.store, lookup(request), pipe(request)
    people: dict[str, str | None] = {}
    deals: dict[str, str | None] = {}
    out = []
    for a in items:
        links = []
        if a.person_id and a.person_id != person_id:
            if a.person_id not in people:
                try:
                    p = store.get_person(a.person_id)
                except Exception:
                    p = None
                people[a.person_id] = p.full_name if p else None
            if people[a.person_id]:
                links.append(("user", people[a.person_id], f"/people/{a.person_id}"))
        if a.company_id and a.company_id != company_id and not (a.person_id and not company_id):
            c = look.get(a.company_id)
            if c:
                links.append(("building-2", c.name, f"/companies/{c.id}"))
        if a.deal_id and a.deal_id != deal_id:
            if a.deal_id not in deals:
                d = pl.get_deal(a.deal_id)
                deals[a.deal_id] = d.name if d else None
            if deals[a.deal_id]:
                links.append(("dollar-sign", deals[a.deal_id], deal_url(a.deal_id)))
        icon, label = KINDS.get(a.kind, ("circle-dot", a.kind.replace("_", " ").title()))
        out.append({"a": a, "icon": icon, "label": label, "links": links})
    return out


# ---------------------------------------------------------------- picker (company / person)


@router.get("/partials/pick", response_class=HTMLResponse)
def pick(request: Request, kind: str = "company", q: str = "") -> Response:
    """Typeahead results for the company and person fields in dialogs."""
    q = q.strip()
    results: list[dict[str, str]] = []
    if len(q) >= 2:
        if kind == "person":
            try:
                page = request.app.state.store.search_people(
                    PeopleFilters(text=q), page=1, per_page=8
                )
            except Exception:
                page = None
            look = lookup(request)
            for p in page.items if page else []:
                c = look.get(p.company_id)
                sub = " · ".join(x for x in (p.title, c.name if c else p.company_domain) if x)
                results.append(
                    {
                        "id": p.id,
                        "label": p.full_name,
                        "sub": sub,
                        "company_id": p.company_id or "",
                        "company_name": c.name if c else "",
                    }
                )
        else:
            try:
                page = request.app.state.search.search_companies(
                    CompanyFilters(text=q), page=1, per_page=8
                )
            except Exception:
                page = None
            for c in page.items if page else []:
                sub = " · ".join(x for x in (c.city, c.country, c.domain) if x)
                results.append({"id": c.id, "label": c.name, "sub": sub})
    return templates.TemplateResponse(
        request, "_pipeline_pick.html", {"results": results, "kind": kind, "q": q}
    )


# ---------------------------------------------------------------- activity + notes


@router.get("/partials/activity", response_class=HTMLResponse)
def activity_partial(
    request: Request,
    person_id: str = "",
    company_id: str = "",
    deal_id: str = "",
    kinds: str = "",
    limit: int = 50,
) -> Response:
    pl = pipe(request)
    wanted = [k for k in kinds.split(",") if k.strip()] or None
    if not (person_id or company_id or deal_id):
        items: list[Activity] = []
    else:
        items = pl.activity.list_for(
            person_id=person_id or None,
            company_id=company_id or None,
            deal_id=deal_id or None,
            kinds=wanted,
            limit=max(1, min(limit, 200)),
        )
    ctx = activity_context(
        request,
        items,
        person_id=person_id or None,
        company_id=company_id or None,
        deal_id=deal_id or None,
    )
    return templates.TemplateResponse(
        request,
        "_pipeline_activity.html",
        {
            "rows": ctx,
            "person_id": person_id,
            "company_id": company_id,
            "deal_id": deal_id,
            "kinds": kinds,
        },
    )


def dialog_ctx(
    request: Request,
    *,
    person_id: str = "",
    company_id: str = "",
    deal_id: str = "",
    reload: str = "1",
    default_next: str = "/",
) -> dict[str, Any]:
    r = refs(request, person_id, company_id)
    deal = pipe(request).get_deal(deal_id) if deal_id else None
    return {
        "ref": r,
        "deal": deal,
        "deal_id": deal.id if deal else "",
        "next": current_next(request, default_next),
        "reload": "0" if reload == "0" else "1",
    }


@router.get("/partials/note-new", response_class=HTMLResponse)
def note_new(
    request: Request,
    person_id: str = "",
    company_id: str = "",
    deal_id: str = "",
    reload: str = "1",
) -> Response:
    ctx = dialog_ctx(
        request, person_id=person_id, company_id=company_id, deal_id=deal_id, reload=reload
    )
    return templates.TemplateResponse(request, "_pipeline_note_new.html", ctx)


@router.post("/notes")
async def notes_create(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/conversations")
    reload = str(form.get("reload") or "1") != "0"
    body = str(form.get("body") or "").strip()
    if not body:
        return reply(request, "Write something first.", back, kind="error")
    r = refs(request, form.get("person_id"), form.get("company_id"))
    deal_id = str(form.get("deal_id") or "").strip() or None
    deal = pipe(request).get_deal(deal_id) if deal_id else None
    if deal:
        r["person_id"] = r["person_id"] or deal.person_id
        r["company_id"] = r["company_id"] or deal.company_id
    elif deal_id:
        deal_id = None
    if not (r["person_id"] or r["company_id"] or deal_id):
        return reply(request, "Pick a person, company or deal for this note.", back, kind="error")
    pipe(request).activity.add(
        "note",
        body,
        person_id=r["person_id"],
        company_id=r["company_id"],
        deal_id=deal_id,
        meta={"person": r["person_name"], "company": r["company_name"]},
    )
    return reply(request, "Note saved.", back, reload=reload)


# ---------------------------------------------------------------- deals


def stage_columns(pl: Pipeline, deals: list[Deal]) -> list[dict[str, Any]]:
    stages = pl.list_stages()
    cols = []
    for st in stages:
        items = [d for d in deals if d.stage_id == st.id]
        by_cur: dict[str, float] = {}
        for d in items:
            if d.amount:
                by_cur[d.currency] = by_cur.get(d.currency, 0) + d.amount
        cols.append({"stage": st, "deals": items, "count": len(items), "total": by_cur})
    return cols


SORTS = {
    "name": lambda d: d.name.lower(),
    "company": lambda d: (d.company_name or "~").lower(),
    "amount": lambda d: d.amount or 0,
    "close": lambda d: d.close_date or date.max,
    "updated": lambda d: d.updated_at,
    "stage": lambda d: d.position,
}


@router.get("/deals", response_class=HTMLResponse)
def deals_page(
    request: Request,
    view: str = "board",
    q: str = "",
    stage: str = "",
    sort: str = "updated",
    dir: str = "desc",
) -> Response:
    pl = pipe(request)
    view = "table" if view == "table" else "board"
    stages = pl.list_stages()
    stage_by_id = {s.id: s for s in stages}
    everything = pl.list_deals()
    deals = pl.list_deals(text=q or None, stage_id=stage if stage in stage_by_id else None)
    sort = sort if sort in SORTS else "updated"
    if view == "table":
        order = {s.id: i for i, s in enumerate(stages)}
        key = SORTS[sort] if sort != "stage" else (lambda d: order.get(d.stage_id, 99))
        deals = sorted(deals, key=key, reverse=(dir != "asc"))
    open_total: dict[str, float] = {}
    for d in everything:
        st = stage_by_id.get(d.stage_id)
        if st and st.kind == "open" and d.amount:
            open_total[d.currency] = open_total.get(d.currency, 0) + d.amount
    return render(
        request,
        "pipeline_deals.html",
        active="deals",
        view=view,
        q=q,
        stage_filter=stage,
        sort=sort,
        dir=dir,
        stages=stages,
        stage_by_id=stage_by_id,
        columns=stage_columns(pl, deals) if view == "board" else [],
        deals=deals,
        deal_total=len(everything),
        open_total=open_total,
    )


@router.get("/partials/deal-new", response_class=HTMLResponse)
def deal_new(
    request: Request, company_id: str = "", person_id: str = "", stage_id: str = ""
) -> Response:
    pl = pipe(request)
    ctx = dialog_ctx(request, person_id=person_id, company_id=company_id, default_next="/deals")
    stages = pl.list_stages()
    first_open = next((s for s in stages if s.kind == "open"), stages[0])
    chosen = next((s for s in stages if s.id == stage_id), first_open)
    name = ""
    if ctx["ref"]["company_name"]:
        name = f"{ctx['ref']['company_name']} deal"
    return templates.TemplateResponse(
        request,
        "_pipeline_deal_new.html",
        {**ctx, "stages": stages, "stage": chosen, "suggested_name": name},
    )


@router.post("/deals")
async def deals_create(request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/deals")
    try:
        fields = deal_form_fields(form, pl)
    except ValueError as exc:
        return reply(request, str(exc), back, kind="error")
    r = refs(request, fields["person_id"], fields["company_id"])
    for key in ("person_id", "company_id"):
        fields[key] = r[key]
    fields["person_name"] = r["person_name"] or fields["person_name"]
    fields["company_name"] = r["company_name"] or fields["company_name"]
    stage = pl.get_stage(str(form.get("stage_id") or ""))
    deal = pl.create_deal(
        fields.pop("name"), stage.id if stage and not stage.archived else None, **fields
    )
    created_stage = pl.get_stage(deal.stage_id)
    pl.activity.add(
        "deal",
        f"Deal created: {deal.name}"
        + (f" ({money(deal.amount, deal.currency)})" if deal.amount else ""),
        person_id=deal.person_id,
        company_id=deal.company_id,
        deal_id=deal.id,
        meta={"stage": created_stage.name if created_stage else None},
    )
    target = back if back != "/deals" and not back.startswith("/deals") else "/deals"
    return reply(request, f"Deal “{deal.name}” created.", target)


def load_deal(request: Request, deal_id: str) -> Deal | None:
    return pipe(request).get_deal(deal_id)


def deal_context(request: Request, deal: Deal) -> dict[str, Any]:
    pl = pipe(request)
    tasks = pl.activity.list_for(deal_id=deal.id, kinds=["task", "task_done"], limit=20)
    return {
        "deal": deal,
        "stages": pl.list_stages(),
        "stage": pl.get_stage(deal.stage_id),
        "deal_tasks": tasks,
        "meetings": [m for m in pl.list_meetings("all") if m.deal_id == deal.id],
    }


@router.get("/deals/{deal_id}", response_class=HTMLResponse)
def deal_detail(deal_id: str, request: Request) -> Response:
    deal = load_deal(request, deal_id)
    if deal is None:
        return not_found(request, "Deal")
    ctx = deal_context(request, deal)
    if is_htmx(request):
        return templates.TemplateResponse(request, "_pipeline_deal_drawer.html", ctx)
    return render(request, "pipeline_deal.html", active="deals", page_mode=True, **ctx)


@router.post("/deals/{deal_id}")
async def deal_update(deal_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), deal_url(deal_id))
    deal = pl.get_deal(deal_id)
    if deal is None:
        return reply(request, "Deal not found.", "/deals", kind="error", status=404)
    try:
        fields = deal_form_fields(form, pl)
    except ValueError as exc:
        return reply(request, str(exc), back, kind="error")
    r = refs(request, fields["person_id"], fields["company_id"])
    fields["person_id"], fields["company_id"] = r["person_id"], r["company_id"]
    fields["person_name"] = r["person_name"] or fields["person_name"]
    fields["company_name"] = r["company_name"] or fields["company_name"]
    updated = pl.update_deal(deal_id, **fields)
    new_stage = str(form.get("stage_id") or "")
    if updated and new_stage and new_stage != updated.stage_id:
        moved = pl.move_deal(deal_id, new_stage)
        stage = pl.get_stage(new_stage)
        if moved and stage:
            log_stage_change(request, pl, moved[0], moved[1], stage)
    return reply(
        request, "Deal saved.", back, reload=not is_htmx(request) or back != deal_url(deal_id)
    )


@router.post("/deals/{deal_id}/move")
async def deal_move(deal_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    """Drag and drop (JSON when asked), the card menu and the keyboard all end up here."""
    pl = pipe(request)
    form = await form_values(request)
    wants_json = "application/json" in request.headers.get("accept", "")
    back = safe_next(str(form.get("next") or ""), "/deals")
    stage_id = str(form.get("stage_id") or "")
    position = None
    if str(form.get("position") or "").strip().lstrip("-").isdigit():
        position = int(str(form.get("position")))
    deal = pl.get_deal(deal_id)
    stage = pl.get_stage(stage_id)
    if deal is None or stage is None or stage.archived:
        if wants_json:
            return JSONResponse({"ok": False, "error": "Deal or stage not found."}, status_code=404)
        return reply(request, "Deal or stage not found.", back, kind="error")
    result = pl.move_deal(deal_id, stage_id, position)
    assert result is not None
    moved, previous_id = result
    if previous_id != stage_id:
        log_stage_change(request, pl, moved, previous_id, stage)
    if wants_json:
        totals = pl.stage_totals()
        return JSONResponse(
            {
                "ok": True,
                "deal_id": deal_id,
                "stage_id": stage_id,
                "stage": stage.name,
                "previous_stage_id": previous_id,
                "changed": previous_id != stage_id,
                "probability": moved.probability,
                "totals": {
                    s.id: {
                        "count": totals.get(s.id, {}).get("count", 0),
                        "amount": totals_label(totals.get(s.id, {}).get("by_currency")),
                    }
                    for s in pl.list_stages()
                },
            }
        )
    msg = f"Moved to {stage.name}." if previous_id != stage_id else "Deal already there."
    return reply(request, msg, back, reload=True)


@router.post("/deals/{deal_id}/delete")
async def deal_delete(deal_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    deal = pl.get_deal(deal_id)
    if deal is None:
        return reply(request, "Deal not found.", "/deals", kind="error", status=404)
    pl.delete_deal(deal_id)
    pl.activity.add(
        "deal",
        f"Deal deleted: {deal.name}",
        person_id=deal.person_id,
        company_id=deal.company_id,
        meta={"deleted": True},
    )
    return reply(request, f"Deal “{deal.name}” deleted.", "/deals")


# ---------------------------------------------------------------- deal stages (settings)


def settings_sections() -> list[tuple[str, str, str, str]]:
    try:
        from leadcommons.web.pages_extra import SETTINGS_SECTIONS

        base = list(SETTINGS_SECTIONS)
    except Exception:
        base = [("overview", "/settings", "Workspace overview", "house")]
    if not any(s[0] == "deal-stages" for s in base):
        base.append(("deal-stages", "/settings/deal-stages", "Deal stages", "kanban"))
    return base


@router.get("/settings/deal-stages", response_class=HTMLResponse)
def stages_page(request: Request) -> Response:
    pl = pipe(request)
    all_stages = pl.list_stages(include_archived=True)
    counts = {s.id: pl.deals_in_stage(s.id) for s in all_stages}
    return render(
        request,
        "pipeline_stages.html",
        active="settings",
        section="deal-stages",
        sections=settings_sections(),
        stages=[s for s in all_stages if not s.archived],
        archived=[s for s in all_stages if s.archived],
        counts=counts,
        kinds=STAGE_KINDS,
    )


BACK_STAGES = "/settings/deal-stages"


@router.post("/settings/deal-stages")
async def stage_add(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    try:
        prob = parse_percent(form.get("probability")) or 0
        stage = pipe(request).add_stage(
            str(form.get("name") or ""), str(form.get("kind") or "open"), prob
        )
    except ValueError as exc:
        return reply(request, str(exc), BACK_STAGES, kind="error")
    return reply(request, f"Stage “{stage.name}” added.", BACK_STAGES)


@router.post("/settings/deal-stages/order")
async def stage_order(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    ids = [str(i) for i in form.getlist("ids")]
    pipe(request).order_stages(ids)
    return reply(request, "Order updated.", BACK_STAGES, reload=False)


@router.post("/settings/deal-stages/{stage_id}")
async def stage_update(stage_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    pl = pipe(request)
    name = str(form.get("name") or "").strip()
    if not name:
        return reply(request, "A stage needs a name.", BACK_STAGES, kind="error")
    try:
        prob = parse_percent(form.get("probability"))
    except ValueError as exc:
        return reply(request, str(exc), BACK_STAGES, kind="error")
    stage = pl.update_stage(stage_id, name=name, kind=str(form.get("kind") or ""), probability=prob)
    if stage is None:
        return reply(request, "Stage not found.", BACK_STAGES, kind="error", status=404)
    return reply(request, f"Stage “{stage.name}” saved.", BACK_STAGES)


@router.post("/settings/deal-stages/{stage_id}/shift")
async def stage_shift(stage_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    pipe(request).shift_stage(stage_id, -1 if str(form.get("dir")) == "up" else 1)
    return reply(request, "Order updated.", BACK_STAGES)


@router.post("/settings/deal-stages/{stage_id}/archive")
async def stage_archive(stage_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    problem = pipe(request).archive_stage(stage_id)
    if problem:
        return reply(request, problem, BACK_STAGES, kind="error")
    return reply(request, "Stage archived.", BACK_STAGES)


@router.post("/settings/deal-stages/{stage_id}/restore")
async def stage_restore(stage_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    pipe(request).restore_stage(stage_id)
    return reply(request, "Stage restored.", BACK_STAGES)


# ---------------------------------------------------------------- meetings


def next_hour() -> datetime:
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    return now + timedelta(hours=1)


@router.get("/meetings", response_class=HTMLResponse)
def meetings_page(request: Request, tab: str = "upcoming", q: str = "") -> Response:
    pl = pipe(request)
    tab = "past" if tab == "past" else "upcoming"
    upcoming = pl.list_meetings("upcoming", text=q or None)
    past = pl.list_meetings("past", text=q or None)
    return render(
        request,
        "pipeline_meetings.html",
        active="meetings",
        tab=tab,
        q=q,
        meetings=upcoming if tab == "upcoming" else past,
        n_upcoming=len(upcoming),
        n_past=len(past),
        booking_url=pl.get_setting(BOOKING_KEY),
        now=datetime.now(),
    )


@router.get("/partials/meeting-new", response_class=HTMLResponse)
def meeting_new(
    request: Request, person_id: str = "", company_id: str = "", deal_id: str = ""
) -> Response:
    ctx = dialog_ctx(
        request,
        person_id=person_id,
        company_id=company_id,
        deal_id=deal_id,
        default_next="/meetings",
    )
    r = ctx["ref"]
    who = r["person_name"] or r["company_name"]
    return templates.TemplateResponse(
        request,
        "_pipeline_meeting_new.html",
        {
            **ctx,
            "start": next_hour().strftime("%Y-%m-%dT%H:%M"),
            "suggested_title": f"Meeting with {who}" if who else "",
        },
    )


@router.post("/meetings")
async def meetings_create(request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/meetings")
    try:
        title = str(form.get("title") or "").strip()
        if not title:
            raise ValueError("Give the meeting a title.")
        start = parse_local_dt(form.get("start"))
        duration = int(str(form.get("duration") or "30") or 30)
        video = valid_url(form.get("video_url"))
    except ValueError as exc:
        return reply(request, str(exc), back, kind="error")
    r = refs(request, form.get("person_id"), form.get("company_id"))
    r["person_name"] = r["person_name"] or str(form.get("person_name") or "").strip() or None
    r["company_name"] = r["company_name"] or str(form.get("company_name") or "").strip() or None
    deal_id = str(form.get("deal_id") or "").strip() or None
    deal = pl.get_deal(deal_id) if deal_id else None
    if deal:
        r["person_id"] = r["person_id"] or deal.person_id
        r["person_name"] = r["person_name"] or deal.person_name
        r["company_id"] = r["company_id"] or deal.company_id
        r["company_name"] = r["company_name"] or deal.company_name
    else:
        deal_id = None
    meeting = pl.create_meeting(
        title,
        start,
        duration_min=duration,
        location=form.get("location"),
        video_url=video,
        agenda=form.get("agenda"),
        deal_id=deal_id,
        **r,
    )
    pl.activity.add(
        "meeting",
        f"Meeting scheduled: {meeting.title}, {when_label(meeting.start)}",
        person_id=meeting.person_id,
        company_id=meeting.company_id,
        deal_id=deal_id,
        meta={"meeting_id": meeting.id, "start": meeting.start.isoformat(), "event": "scheduled"},
    )
    return reply(request, f"Meeting “{meeting.title}” scheduled.", back)


@router.get("/partials/meeting/{meeting_id}", response_class=HTMLResponse)
def meeting_dialog(meeting_id: str, request: Request) -> Response:
    meeting = pipe(request).get_meeting(meeting_id)
    if meeting is None:
        return not_found(request, "Meeting")
    return templates.TemplateResponse(
        request,
        "_pipeline_meeting_dlg.html",
        {
            "m": meeting,
            "next": current_next(request, "/meetings"),
            "statuses": MEETING_STATUSES,
            "now": datetime.now(),
        },
    )


@router.get("/meetings/{meeting_id}.ics")
def meeting_ics(meeting_id: str, request: Request) -> Response:
    meeting = pipe(request).get_meeting(meeting_id)
    if meeting is None:
        return not_found(request, "Meeting")
    slug = re.sub(r"[^a-z0-9]+", "-", meeting.title.lower()).strip("-")[:40] or "meeting"
    return Response(
        build_ics(meeting),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'},
    )


@router.post("/meetings/booking-link")
async def booking_link(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/meetings")
    try:
        url = valid_url(form.get("booking_url"))
    except ValueError as exc:
        return reply(request, str(exc), back, kind="error")
    pipe(request).set_setting(BOOKING_KEY, url or "")
    return reply(request, "Booking link saved." if url else "Booking link removed.", back)


@router.post("/meetings/{meeting_id}/outcome")
async def meeting_outcome(meeting_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/meetings")
    meeting = pl.get_meeting(meeting_id)
    if meeting is None:
        return reply(request, "Meeting not found.", back, kind="error", status=404)
    status = str(form.get("status") or "done")
    status = status if status in MEETING_STATUSES and status != "scheduled" else "done"
    notes = str(form.get("outcome") or "").strip()
    pl.update_meeting(meeting_id, outcome=notes, status=status)
    pl.activity.add(
        "meeting",
        f"{MEETING_LABELS[status]}: {meeting.title}" + (f". {notes}" if notes else ""),
        person_id=meeting.person_id,
        company_id=meeting.company_id,
        deal_id=meeting.deal_id,
        meta={"meeting_id": meeting.id, "event": status},
    )
    return reply(request, "Meeting outcome saved.", back)


@router.post("/meetings/{meeting_id}/delete")
async def meeting_delete(meeting_id: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/meetings")
    if not pipe(request).delete_meeting(meeting_id):
        return reply(request, "Meeting not found.", back, kind="error", status=404)
    return reply(request, "Meeting deleted.", back)


# ---------------------------------------------------------------- calls


def insight_phone(insight: Any) -> str | None:
    """First business phone in a CompanyInsights record (shape owned by the insights agent)."""
    if not insight:
        return None

    def read(key: str) -> Any:
        if isinstance(insight, dict):
            return insight.get(key)
        return getattr(insight, key, None)

    for key in ("phone", "main_phone", "business_phone", "phones", "phone_numbers"):
        value = read(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list | tuple):
            for item in value:
                if isinstance(item, dict):
                    item = item.get("number") or item.get("phone") or item.get("value")
                else:
                    item = getattr(item, "number", None) or item
                if isinstance(item, str) and item.strip():
                    return item.strip()
    return None


def company_phone(
    request: Request, company_id: str | None, memo: dict[str, str | None]
) -> str | None:
    if not company_id:
        return None
    if company_id not in memo:
        try:
            from leadcommons import insights

            memo[company_id] = insight_phone(insights.get(request.app.state.store, company_id))
        except Exception:  # NotImplementedError until the insights agent ships, or no data
            memo[company_id] = None
    return memo[company_id]


def calling_list(request: Request, q: str, only: str) -> list[dict[str, Any]]:
    store, pl, look = request.app.state.store, pipe(request), lookup(request)
    try:
        page = store.search_people(PeopleFilters(text=q or None), page=1, per_page=300)
        people = list(page.items)
    except Exception:
        people = []
    ids = [p.id for p in people]
    typed = pl.phones(ids)
    last = pl.last_calls(ids)
    memo: dict[str, str | None] = {}
    rows = []
    for p in people:
        company = look.get(p.company_id)
        phone, source = None, ""
        own = getattr(p, "phone", None)
        if own:
            phone, source = str(own), "Person"
        elif typed.get(p.id):
            phone, source = typed[p.id], "Added by you"
        elif last.get(p.id) and last[p.id].phone:
            phone, source = last[p.id].phone, "Last call"
        else:
            cp = company_phone(request, p.company_id, memo)
            if cp:
                phone, source = cp, "Company"
        rows.append(
            {
                "p": p,
                "company": company,
                "phone": phone,
                "source": source,
                "last": last.get(p.id),
            }
        )
    if only == "uncalled":
        rows = [r for r in rows if r["phone"] and not r["last"]]
    elif only == "nophone":
        rows = [r for r in rows if not r["phone"]]
    else:
        rows = [r for r in rows if r["phone"]]
    rows.sort(key=lambda r: (r["last"] is not None, r["p"].full_name.lower()))
    return rows


@router.get("/calls", response_class=HTMLResponse)
def calls_page(
    request: Request,
    tab: str = "list",
    q: str = "",
    outcome: str = "",
    period: str = "all",
    only: str = "phone",
) -> Response:
    pl = pipe(request)
    tab = "history" if tab == "history" else "list"
    outcome = outcome if outcome in CALL_OUTCOMES else ""
    since = None
    if period in ("7", "30"):
        since = utcnow() - timedelta(days=int(period))
    counts = pl.call_counts()
    history = (
        pl.list_calls(outcome=outcome or None, text=q or None, since=since)
        if tab == "history"
        else []
    )
    rows = calling_list(request, q, only) if tab == "list" else []
    return render(
        request,
        "pipeline_calls.html",
        active="calls",
        tab=tab,
        q=q,
        outcome=outcome,
        period=period,
        only=only,
        rows=rows,
        history=history,
        n_calls=sum(counts.values()),
        outcomes=CALL_OUTCOMES,
    )


@router.get("/partials/call-log", response_class=HTMLResponse)
def call_log(
    request: Request,
    person_id: str = "",
    company_id: str = "",
    deal_id: str = "",
    phone: str = "",
) -> Response:
    ctx = dialog_ctx(
        request,
        person_id=person_id,
        company_id=company_id,
        deal_id=deal_id,
        default_next="/calls?tab=history",
    )
    pl = pipe(request)
    if not phone and ctx["ref"]["person_id"]:
        pid = ctx["ref"]["person_id"]
        phone = pl.phones([pid]).get(pid, "") or (
            (pl.last_calls([pid]).get(pid) or Call(id="", outcome="")).phone or ""
        )
        if not phone:
            phone = company_phone(request, ctx["ref"]["company_id"], {}) or ""
    return templates.TemplateResponse(
        request,
        "_pipeline_call_log.html",
        {
            **ctx,
            "phone": phone,
            "outcomes": CALL_OUTCOMES,
            "tomorrow": date.today() + timedelta(days=1),
        },
    )


@router.post("/calls")
async def calls_create(request: Request, _: None = Depends(csrf)) -> Response:
    pl = pipe(request)
    store = request.app.state.store
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/calls?tab=history")
    outcome = str(form.get("outcome") or "")
    if outcome not in CALL_OUTCOMES:
        return reply(request, "Choose how the call went.", back, kind="error")
    r = refs(request, form.get("person_id"), form.get("company_id"))
    r["person_name"] = r["person_name"] or str(form.get("person_name") or "").strip() or None
    r["company_name"] = r["company_name"] or str(form.get("company_name") or "").strip() or None
    phone = str(form.get("phone") or "").strip()[:64] or None
    if not (r["person_id"] or r["company_id"] or r["person_name"] or r["company_name"] or phone):
        return reply(
            request, "Say who you called: a person, a company or a number.", back, kind="error"
        )
    deal_id = str(form.get("deal_id") or "").strip() or None
    if deal_id and pl.get_deal(deal_id) is None:
        deal_id = None
    seconds = parse_duration_sec(form.get("minutes"), form.get("seconds"))
    notes = str(form.get("notes") or "").strip()
    task_id = None
    task_note = ""
    if form.get("followup"):
        title = str(form.get("followup_title") or "").strip() or (
            f"Follow up with {r['person_name'] or r['company_name'] or 'caller'}"
        )
        due = None
        try:
            day = parse_day(form.get("followup_due"))
            due = (
                datetime(day.year, day.month, day.day, 9, 0, tzinfo=utcnow().tzinfo)
                if day
                else None
            )
        except ValueError:
            due = None
        try:
            from leadcommons import engage

            task_id = engage.create_task(
                store,
                title,
                person_id=r["person_id"],
                company_id=r["company_id"],
                deal_id=deal_id,
                due=due,
                kind="call",
            )
        except Exception:  # engage not installed yet, or its tasks unavailable
            task_note = " The follow-up task could not be created (tasks are unavailable)."
    call = pl.log_call(
        outcome,
        phone=phone,
        duration_sec=seconds,
        notes=notes,
        deal_id=deal_id,
        followup_task_id=task_id,
        **r,
    )
    if phone and r["person_id"] and form.get("remember"):
        pl.set_phone(r["person_id"], phone)
    bits = [OUTCOME_LABELS[outcome]]
    if seconds:
        bits.append(duration_label(seconds))
    pl.activity.add(
        "call",
        "Call · " + " · ".join(bits) + (f": {notes}" if notes else ""),
        person_id=r["person_id"],
        company_id=r["company_id"],
        deal_id=deal_id,
        meta={
            "call_id": call.id,
            "outcome": outcome,
            "duration_sec": seconds,
            "phone": phone,
            "person": r["person_name"],
            "company": r["company_name"],
            "task_id": task_id,
        },
    )
    return reply(request, "Call logged." + task_note, back)


@router.post("/calls/phone")
async def calls_phone(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/calls?only=nophone")
    person_id = str(form.get("person_id") or "").strip()
    phone = str(form.get("phone") or "").strip()
    if not person_id or request.app.state.store.get_person(person_id) is None:
        return reply(request, "Person not found.", back, kind="error", status=404)
    if phone and not re.search(r"\d{5,}", phone):
        return reply(request, "That does not look like a phone number.", back, kind="error")
    pipe(request).set_phone(person_id, phone)
    return reply(request, "Number saved." if phone else "Number removed.", back)


# ---------------------------------------------------------------- conversations


def thread_list(request: Request, kinds: list[str], q: str) -> list[dict[str, Any]]:
    store, look, pl = request.app.state.store, lookup(request), pipe(request)
    acts = pl.activity.search(kinds=kinds, limit=1500)
    threads: dict[str, dict[str, Any]] = {}
    for a in acts:
        if a.person_id:
            key = f"p:{a.person_id}"
        elif a.company_id:
            key = f"c:{a.company_id}"
        else:
            continue
        t = threads.get(key)
        if t is None:
            threads[key] = t = {"key": key, "last": a, "n": 0, "texts": []}
        t["n"] += 1
        if len(t["texts"]) < 40:
            t["texts"].append(a.summary.lower())
    out = []
    needle = q.strip().lower()
    for t in threads.values():
        kind, _, ident = t["key"].partition(":")
        if kind == "p":
            try:
                person = store.get_person(ident)
            except Exception:
                person = None
            company = look.get(person.company_id) if person else None
            t.update(
                kind="person",
                id=ident,
                name=person.full_name
                if person
                else (t["last"].meta.get("person") or "Unknown person"),
                sub=" · ".join(
                    x
                    for x in ((person.title if person else None), company.name if company else None)
                    if x
                ),
            )
        else:
            company = look.get(ident)
            t.update(
                kind="company",
                id=ident,
                name=company.name
                if company
                else (t["last"].meta.get("company") or "Unknown company"),
                sub="Company",
            )
        if needle and needle not in t["name"].lower() and needle not in t["sub"].lower():
            if not any(needle in x for x in t["texts"]):
                continue
        out.append(t)
    return out


@router.get("/conversations", response_class=HTMLResponse)
def conversations_page(
    request: Request, q: str = "", kind: str = "all", thread: str = ""
) -> Response:
    pl = pipe(request)
    filters = {k: (label, kinds) for k, label, kinds in CONVERSATION_FILTERS}
    kind = kind if kind in filters else "all"
    kinds = list(filters[kind][1])
    threads = thread_list(request, kinds, q)
    selected = next((t for t in threads if t["key"] == thread), threads[0] if threads else None)
    rows: list[dict[str, Any]] = []
    if selected:
        if selected["kind"] == "person":
            items = pl.activity.search(person_id=selected["id"], kinds=kinds, limit=200)
            rows = activity_context(request, items, person_id=selected["id"])
        else:
            items = pl.activity.search(company_id=selected["id"], kinds=kinds, limit=200)
            rows = activity_context(request, items, company_id=selected["id"])
    counts = {
        k: len(pl.activity.search(kinds=list(v[1]), limit=1500)) if k != "all" else None
        for k, v in filters.items()
    }
    counts["all"] = sum(v or 0 for k, v in counts.items() if k != "all")
    return render(
        request,
        "pipeline_conversations.html",
        active="conversations",
        q=q,
        kind=kind,
        filters=CONVERSATION_FILTERS,
        counts=counts,
        threads=threads,
        selected=selected,
        rows=rows,
        total_all=counts["all"],
    )
