# ruff: noqa: B008  (FastAPI Depends defaults)
"""Engage: sequences, emails, tasks, mailboxes and sending rules. Owner: engage agent.

Every form works without JavaScript (POST then 303 back with a notice). Dialog forms post
with HTMX: a refusal answers 422 plus a toast so the dialog stays open; a success answers
a toast (or a redirect when the page behind must change). Nothing is sent in demo mode.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pepite.engage import core, crypto, mailer
from pepite.engage.core import EngageError
from pepite.engage.render import (
    VARIABLES,
    footer_text,
    missing_vars,
    person_vars,
    render,
    unknown_vars,
)
from pepite.models import utcnow
from pepite.search import PeopleFilters
from pepite.store.engage import (
    ENROLLMENT_STATUSES,
    PRIORITIES,
    STEP_KINDS,
    TASK_KINDS,
    EngageRepo,
    parse_days,
    repo_for,
)
from pepite.web.deps import get_search, get_settings, get_store, templates
from pepite.web.pages import (
    csrf,
    form_ids,
    form_values,
    is_htmx,
    safe_next,
    toast_headers,
    with_notice,
)
from pepite.web.pages import render as render_page

router = APIRouter()


def install_sender(app: Any) -> None:
    """Start the background email sender with the app (see engage.scheduler.install)."""
    from pepite.engage.scheduler import install

    install(app)


DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GUIDE_URL = "https://github.com/aymaneprojects/pepite/blob/main/docs/engage.md"

PROVIDERS: dict[str, dict[str, Any]] = {
    "gmail": {
        "label": "Gmail / Google Workspace",
        "smtp": ("smtp.gmail.com", 465, "ssl"),
        "imap": ("imap.gmail.com", 993, "ssl"),
        "hint": "Turn on 2-step verification, then create an app password and paste it here.",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "smtp": ("smtp.office365.com", 587, "starttls"),
        "imap": ("outlook.office365.com", 993, "ssl"),
        "hint": "Your administrator may need to allow SMTP sign-in (SMTP AUTH) for this mailbox.",
    },
    "fastmail": {
        "label": "Fastmail",
        "smtp": ("smtp.fastmail.com", 465, "ssl"),
        "imap": ("imap.fastmail.com", 993, "ssl"),
        "hint": "Create an app password in Settings, Privacy & Security.",
    },
    "zoho": {
        "label": "Zoho Mail",
        "smtp": ("smtp.zoho.com", 465, "ssl"),
        "imap": ("imap.zoho.com", 993, "ssl"),
        "hint": "Use an application-specific password if two-factor sign-in is on.",
    },
    "icloud": {
        "label": "iCloud Mail",
        "smtp": ("smtp.mail.me.com", 587, "starttls"),
        "imap": ("imap.mail.me.com", 993, "ssl"),
        "hint": "Create an app-specific password for your Apple account.",
    },
    "proton": {
        "label": "Proton Mail (Bridge)",
        "smtp": ("127.0.0.1", 1025, "starttls"),
        "imap": ("127.0.0.1", 1143, "starttls"),
        "hint": "Run Proton Mail Bridge on this machine and copy the password it shows.",
    },
    "ovh": {
        "label": "OVHcloud",
        "smtp": ("ssl0.ovh.net", 465, "ssl"),
        "imap": ("ssl0.ovh.net", 993, "ssl"),
        "hint": "Use the password of the email account itself.",
    },
    "custom": {
        "label": "Another provider",
        "smtp": ("", 587, "starttls"),
        "imap": ("", 993, "ssl"),
        "hint": "Your provider's help pages list the SMTP and IMAP servers.",
    },
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "blank": {
        "label": "Blank sequence",
        "lead": "Start empty and add your own steps.",
        "steps": [],
    },
    "intro": {
        "label": "Three-touch introduction",
        "lead": "An introduction and two short follow-ups in the same thread.",
        "steps": [
            {
                "kind": "auto_email",
                "delay_days": 0,
                "subject": "Quick question about {{company}}",
                "body": "Hi {{first_name|there}},\n\nI noticed {{company}} and wanted to ask "
                "whether improving how you find new customers is on your list this quarter.\n\n"
                "Would a short call next week be useful?\n\n{{sender_name}}",
            },
            {
                "kind": "auto_email",
                "delay_days": 3,
                "subject": "",
                "body": "Hi {{first_name|there}},\n\nJust bringing this back to the top of your "
                "inbox. Happy to send a two-line summary instead of a call.\n\n{{sender_name}}",
            },
            {
                "kind": "auto_email",
                "delay_days": 4,
                "subject": "",
                "body": "Hi {{first_name|there}},\n\nI will stop here. If the timing is better "
                "later, just reply to this email.\n\n{{sender_name}}",
            },
        ],
    },
    "call": {
        "label": "Email, then a call",
        "lead": "Open with an email and follow up by phone two days later.",
        "steps": [
            {
                "kind": "auto_email",
                "delay_days": 0,
                "subject": "{{first_name}}, an idea for {{company}}",
                "body": "Hi {{first_name|there}},\n\nI work with teams like yours on finding the "
                "right people to talk to. Could I call you on Thursday for ten minutes?\n\n"
                "{{sender_name}}",
            },
            {
                "kind": "call",
                "delay_days": 2,
                "note": "Mention the email from Monday and ask about their current process.",
                "priority": "high",
            },
            {
                "kind": "auto_email",
                "delay_days": 2,
                "subject": "",
                "body": "Hi {{first_name|there}},\n\nI tried to reach you by phone. Is there a "
                "better time this week?\n\n{{sender_name}}",
            },
        ],
    },
    "linkedin": {
        "label": "LinkedIn first",
        "lead": "Connect on LinkedIn by hand, then email.",
        "steps": [
            {
                "kind": "linkedin",
                "delay_days": 0,
                "note": "Send a connection request with a short, personal note.",
            },
            {
                "kind": "auto_email",
                "delay_days": 2,
                "subject": "Following my LinkedIn note",
                "body": "Hi {{first_name|there}},\n\nI sent you a short note on LinkedIn and "
                "thought email might be easier. Would you be open to a quick chat?\n\n"
                "{{sender_name}}",
            },
            {
                "kind": "task",
                "delay_days": 3,
                "subject": "Check in with {{first_name}}",
                "note": "See whether they accepted the request and reply to any message.",
            },
        ],
    },
}

SEQUENCE_STATUS_LABEL = {
    "draft": "Draft",
    "active": "Active",
    "paused": "Paused",
    "archived": "Archived",
}
CONTACT_STATUS_LABEL = {
    "active": "Active",
    "paused": "Paused",
    "finished": "Finished",
    "replied": "Replied",
    "bounced": "Bounced",
    "unsubscribed": "Unsubscribed",
}
TASK_TABS = (
    ("all", "All tasks"),
    ("call", "Calls"),
    ("email", "Emails"),
    ("linkedin", "LinkedIn"),
    ("action", "To-dos"),
    ("overdue", "Overdue"),
)
TASK_VIEWS = (
    ("open", "Open tasks"),
    ("today", "Due today"),
    ("upcoming", "Upcoming"),
    ("done", "Completed"),
    ("all", "Every task"),
)
EMAIL_TABS = (
    ("scheduled", "Scheduled"),
    ("sent", "Sent"),
    ("replies", "Replies"),
    ("failed", "Failed & bounced"),
)


# ---------------------------------------------------------------- template helpers


def _when(dt: datetime | None, fmt: str = "%b %d, %H:%M") -> str:
    return dt.astimezone(UTC).strftime(fmt) + " UTC" if dt else ""


def _rel(dt: datetime | None) -> str:
    if dt is None:
        return ""
    secs = int((dt - utcnow()).total_seconds())
    ahead = secs > 0
    secs = abs(secs)
    if secs < 60:
        return "now"
    if secs < 3600:
        n, unit = secs // 60, "min"
    elif secs < 86400:
        n, unit = secs // 3600, "h"
    else:
        n, unit = secs // 86400, "day" if secs // 86400 == 1 else "days"
    return f"in {n} {unit}" if ahead else f"{n} {unit} ago"


def days_label(days: list[int]) -> str:
    days = sorted(set(days))
    if days == list(range(7)):
        return "every day"
    if days == list(range(5)):
        return "Mon–Fri"
    if not days:
        return "no days"
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
        return f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
    return ", ".join(DAY_NAMES[d] for d in days)


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(UTC).isoformat() if dt else ""


templates.env.globals.update(
    eg_when=_when,
    eg_rel=_rel,
    eg_iso=_iso,
    eg_step_labels=core.STEP_LABELS,
    eg_task_labels=core.TASK_LABELS,
    eg_seq_status=SEQUENCE_STATUS_LABEL,
    eg_contact_status=CONTACT_STATUS_LABEL,
    eg_day_names=DAY_NAMES,
    eg_days_label=days_label,
)


def timezones() -> list[str]:
    try:
        zones = sorted(z for z in available_timezones() if "/" in z and not z.startswith("Etc/"))
    except Exception:  # noqa: BLE001 - no tz database
        zones = []
    return ["UTC", *zones]


def repo(request: Request) -> EngageRepo:
    return repo_for(request.app.state.store)


def demo(request: Request) -> bool:
    return core.is_demo(request.app.state.settings)


def remember_base_url(request: Request, r: EngageRepo, cfg: dict[str, Any]) -> None:
    """Unsubscribe links need an absolute URL; remember how this app is reached."""
    base = str(request.base_url).rstrip("/")
    if base and cfg.get("seen_base_url") != base and not cfg.get("public_url"):
        try:
            r.save_settings(seen_base_url=base)
        except Exception:  # noqa: BLE001 - never break a page for this
            pass


def page(request: Request, name: str, active: str, **ctx: Any) -> Response:
    r = repo(request)
    cfg = r.get_settings()
    remember_base_url(request, r, cfg)
    return render_page(
        request,
        name,
        active=active,
        demo=demo(request),
        demo_reason=core.DEMO_SEND_REASON,
        cfg=cfg,
        **ctx,
    )


def settings_view(request: Request, name: str, section: str, **ctx: Any) -> Response:
    from pepite.web.pages_extra import settings_ctx

    r = repo(request)
    cfg = r.get_settings()
    remember_base_url(request, r, cfg)
    return render_page(
        request,
        name,
        active="settings",
        demo=demo(request),
        demo_reason=core.DEMO_SEND_REASON,
        cfg=cfg,
        **settings_ctx(request, section, **ctx),
    )


def partial(request: Request, name: str, **ctx: Any) -> Response:
    return templates.TemplateResponse(
        request,
        name,
        {
            "demo": demo(request),
            "demo_reason": core.DEMO_SEND_REASON,
            "settings": request.app.state.settings,
            **ctx,
        },
    )


def answer(
    request: Request, message: str, back: str, *, ok: bool = True, reload: bool = False
) -> Response:
    """POST answer. HTMX: toast (422 on refusal so dialogs stay open), or a redirect when
    the page behind must change. Plain forms: 303 back with a notice."""
    if is_htmx(request):
        if ok and reload:
            return Response(status_code=200, headers={"HX-Redirect": with_notice(back, message)})
        return Response(
            status_code=200 if ok else 422, headers=toast_headers(message, "ok" if ok else "error")
        )
    return RedirectResponse(with_notice(back, message), status_code=303)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(value).strip())))
    except (TypeError, ValueError):
        return default


def _days(form: Any, key: str = "days") -> str:
    days = parse_days(",".join(str(v) for v in form.getlist(key)))
    return ",".join(str(d) for d in days)


def _local_dt(day: str, at: str, tz: str) -> datetime | None:
    """A date (and optional time) typed by the user in their time zone, as UTC."""
    try:
        d = datetime.strptime(day.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None
    try:
        t = datetime.strptime(at.strip(), "%H:%M").time() if at and at.strip() else time(9, 0)
    except ValueError:
        t = time(9, 0)
    return datetime.combine(d, t, tzinfo=core.zone(tz)).astimezone(UTC)


def person_card(store: Any, search: Any, person_id: str | None) -> dict[str, Any] | None:
    """What the tables show about a contact: name, title, company, best email."""
    if not person_id:
        return None
    person = store.get_person(person_id)
    if person is None:
        return None
    company = None
    if person.company_id:
        company = core.company_name_of(store, person)
        if company is None and search is not None:
            try:
                found = search.get_company(person.company_id)
            except Exception:  # noqa: BLE001
                found = None
            company = found.name if found else None
    best = store.best_email(person_id)
    return {
        "id": person.id,
        "name": person.full_name,
        "title": person.title or "",
        "company": company or person.company_domain or "",
        "company_id": person.company_id,
        "email": best.address if best else "",
        "email_status": getattr(best.status, "value", best.status) if best else "",
    }


def company_card(store: Any, search: Any, company_id: str | None) -> dict[str, Any] | None:
    if not company_id:
        return None
    getter = getattr(store, "get_company", None)
    company = getter(company_id) if callable(getter) else None
    if company is None and search is not None:
        try:
            company = search.get_company(company_id)
        except Exception:  # noqa: BLE001
            company = None
    return {"id": company_id, "name": company.name if company else company_id}


# ---------------------------------------------------------------- sequences


def sequence_rows(r: EngageRepo, statuses: list[str] | None, q: str) -> list[dict[str, Any]]:
    stats = r.sequence_stats()
    out = []
    for seq in r.sequences(statuses):
        if q and q.lower() not in seq.name.lower():
            continue
        st = stats.get(seq.id, {})
        kinds = [s.kind for s in r.steps(seq.id)]
        out.append({"seq": seq, "stats": st, "kinds": kinds})
    return out


def analytics(r: EngageRepo) -> dict[str, Any]:
    stats = r.sequence_stats()
    counts = r.message_counts()
    sent = counts.get("out:sent", 0) + counts.get("out:bounced", 0)
    replies = counts.get("in:received", 0)
    bounced = counts.get("out:bounced", 0)
    unsub = sum(s.get("unsubscribed", 0) for s in stats.values())
    rows = []
    for seq in r.sequences():
        st = stats.get(seq.id, {})
        s_sent = st.get("sent", 0)
        rows.append(
            {
                "seq": seq,
                "sent": s_sent,
                "replied": st.get("replied", 0),
                "bounced": st.get("bounced", 0),
                "enrolled": st.get("enrolled", 0),
                "rate": round(100 * st.get("replied", 0) / st.get("enrolled", 1))
                if st.get("enrolled")
                else 0,
            }
        )
    return {
        "sent": sent,
        "replies": replies,
        "reply_rate": round(100 * replies / sent) if sent else 0,
        "bounced": bounced,
        "bounce_rate": round(100 * bounced / sent) if sent else 0,
        "unsubscribed": unsub,
        "active": sum(s.get("active", 0) for s in stats.values()),
        "rows": rows,
    }


def health_checks(request: Request, r: EngageRepo, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Plain checks of everything that stops emails from going out or being compliant."""
    boxes = r.mailboxes()
    checks: list[dict[str, Any]] = []

    def add(state: str, title: str, text: str, href: str = "", label: str = "") -> None:
        checks.append({"state": state, "title": title, "text": text, "href": href, "label": label})

    if demo(request):
        add("warn", "Sending is off in the public demo", core.DEMO_SEND_REASON)
    if not boxes:
        add(
            "fail",
            "No mailbox connected",
            "Automatic email steps wait until a mailbox is connected.",
            "/settings/mailboxes",
            "Connect mailbox",
        )
    for m in boxes:
        if m.status == "error":
            add(
                "fail",
                f"{m.from_email} cannot send",
                m.last_error or "The last connection test failed.",
                f"/settings/mailboxes/{m.id}",
                "Fix mailbox",
            )
        elif m.status == "untested":
            add(
                "warn",
                f"{m.from_email} was never tested",
                "Run a connection test to be sure the login works.",
                "/settings/mailboxes",
                "Test it",
            )
        else:
            add("ok", f"{m.from_email} is ready", f"Up to {m.daily_limit} emails a day.")
        if not m.has_imap:
            add(
                "warn",
                f"Replies to {m.from_email} are not detected",
                "Add IMAP details so replies stop sequences on their own. "
                "You can still mark contacts as replied by hand.",
                f"/settings/mailboxes/{m.id}",
                "Add IMAP",
            )
        elif m.imap_status == "error":
            add("fail", f"Reply checks fail for {m.from_email}", m.imap_error or "IMAP error.")
    if not str(cfg.get("postal_address") or "").strip():
        add(
            "warn",
            "No postal address in the footer",
            "Many anti-spam laws ask for a postal address in every prospecting email.",
            "/settings/sending",
            "Add address",
        )
    url = core.base_url(cfg)
    if any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0")):  # noqa: S104
        add(
            "warn",
            "Unsubscribe links point to this computer",
            f"Recipients cannot open {url}. Set the public address of this app in Sending "
            "rules; until then people can still reply 'unsubscribe' (detected with IMAP).",
            "/settings/sending",
            "Set address",
        )
    for seq in r.sequences(["active", "paused", "draft"]):
        steps = r.steps(seq.id)
        if not steps:
            add(
                "warn",
                f"{seq.name} has no steps",
                "Add at least one step.",
                f"/sequences/{seq.id}",
                "Add steps",
            )
        bad = sorted({v for st in steps for v in unknown_vars(st.subject + " " + st.body)})
        if bad:
            add(
                "fail",
                f"{seq.name} uses unknown variables",
                "Not filled in: " + ", ".join("{{" + b + "}}" for b in bad),
                f"/sequences/{seq.id}",
                "Edit steps",
            )
        if steps and steps[0].kind == "auto_email" and not steps[0].subject.strip():
            add(
                "fail",
                f"The first email of {seq.name} has no subject",
                "Only follow-ups may leave the subject empty (they reply in the same thread).",
                f"/sequences/{seq.id}",
                "Edit steps",
            )
    return checks


@router.get("/sequences", response_class=HTMLResponse)
def sequences_page(request: Request) -> Response:
    r = repo(request)
    tab = request.query_params.get("tab") or "all"
    status = request.query_params.get("status") or ""
    q = (request.query_params.get("q") or "").strip()[:120]
    statuses = [status] if status in SEQUENCE_STATUS_LABEL else ["draft", "active", "paused"]
    if status == "all":
        statuses = None
    cfg = r.get_settings()
    all_seqs = r.sequences()
    counts = {k: sum(1 for s in all_seqs if s.status == k) for k in SEQUENCE_STATUS_LABEL}
    return page(
        request,
        "engage_sequences.html",
        "sequences",
        tab=tab if tab in ("all", "analytics", "health") else "all",
        sfilter=status,
        q=q,
        rows=sequence_rows(r, statuses, q) if tab == "all" else [],
        total=len(all_seqs),
        counts=counts,
        mailboxes=r.mailboxes(),
        stats=analytics(r) if tab == "analytics" else None,
        checks=health_checks(request, r, cfg) if tab == "health" else [],
        templates_list=TEMPLATES,
        ai_off=ai_off_reason(request),
    )


def ai_off_reason(request: Request) -> str:
    """Why "Draft with AI" is unavailable, or "" when it can be used."""
    from pepite.engage.ai import OFF_REASON, llm_ready

    if demo(request):
        return "Turned off in the public demo."
    return "" if llm_ready() else OFF_REASON


@router.post("/sequences/ai")
async def sequences_ai(request: Request, _: None = Depends(csrf)) -> Response:
    """Draft a sequence from a short goal with the user's own model, then open it."""
    from pepite.engage.ai import draft_sequence

    form = await form_values(request)
    back = "/sequences"
    off = ai_off_reason(request)
    if off:
        return answer(request, off, back, ok=False)
    goal = str(form.get("goal") or "").strip()
    if len(goal) < 8:
        return answer(request, "Describe the goal in a few words first.", back, ok=False)
    drafted = await run_in_threadpool(draft_sequence, goal, _int(form.get("steps"), 4, 2, 8))
    if drafted is None:
        return answer(
            request,
            "The model did not return usable steps. Try again, or start from a template.",
            back,
            ok=False,
        )
    r = repo(request)
    mailbox = core.default_mailbox(r)
    name = str(form.get("name") or "").strip()[:200] or drafted["name"]
    sid = r.create_sequence(
        name,
        drafted["steps"],
        status="draft",
        mailbox_id=mailbox.id if mailbox else None,
        stop_on_reply=bool(r.get_settings().get("stop_on_reply", True)),
    )
    msg = f"Drafted {name} with {len(drafted['steps'])} steps. Review every step before activating."
    return answer(request, msg, f"/sequences/{sid}", reload=True)


@router.post("/sequences/tip")
def sequences_tip(request: Request, _: None = Depends(csrf)) -> Response:
    repo(request).save_settings(tip_dismissed=True)
    if is_htmx(request):
        return Response(status_code=200)
    return RedirectResponse("/sequences", status_code=303)


@router.post("/sequences")
async def sequences_create(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    r = repo(request)
    template = TEMPLATES.get(str(form.get("template") or "blank"), TEMPLATES["blank"])
    name = str(form.get("name") or "").strip()[:200] or (
        template["label"] if template is not TEMPLATES["blank"] else "New sequence"
    )
    cfg = r.get_settings()
    mailbox = core.default_mailbox(r)
    steps = [
        {
            "kind": s["kind"],
            "delay_days": s.get("delay_days", 0),
            "subject": s.get("subject", ""),
            "body": s.get("body", ""),
            "note": s.get("note", ""),
            "priority": s.get("priority", "medium"),
        }
        for s in template["steps"]
    ]
    sid = r.create_sequence(
        name,
        steps,
        status="draft",
        mailbox_id=mailbox.id if mailbox else None,
        stop_on_reply=bool(cfg.get("stop_on_reply", True)),
    )
    target = f"/sequences/{sid}"
    if is_htmx(request):
        return Response(
            status_code=200, headers={"HX-Redirect": with_notice(target, f"Created {name}.")}
        )
    return RedirectResponse(with_notice(target, f"Created {name}."), status_code=303)


def _missing(request: Request, what: str) -> Response:
    from pepite.web.pages import not_found

    return not_found(request, what)


@router.get("/sequences/{sid}", response_class=HTMLResponse)
def sequence_page(
    sid: str, request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    r = repo(request)
    seq = r.sequence(sid)
    if seq is None:
        return _missing(request, "Sequence")
    tab = request.query_params.get("tab") or "steps"
    cstatus = request.query_params.get("cstatus") or ""
    steps = r.steps(sid)
    enrollments = r.enrollments(sid)
    counts = {k: sum(1 for e in enrollments if e.status == k) for k in ENROLLMENT_STATUSES}
    contacts = []
    if tab == "contacts":
        for e in enrollments:
            if cstatus and e.status != cstatus:
                continue
            step = steps[e.step_index] if e.step_index < len(steps) else None
            card = person_card(store, search, e.person_id) or {
                "id": e.person_id,
                "name": (e.vars or {}).get("full_name") or e.email,
                "title": "",
                "company": (e.vars or {}).get("company", ""),
            }
            contacts.append({"e": e, "p": card, "step": step})
    # preview: an enrolled contact, else any saved person, else sample values
    preview_pid = request.query_params.get("person_id") or ""
    preview_people = [
        {"id": e.person_id, "name": (e.vars or {}).get("full_name") or e.email}
        for e in enrollments[:50]
    ]
    mailbox = core.default_mailbox(r, seq.mailbox_id)
    start, end, days, tz = core.window_of(seq, mailbox)
    return page(
        request,
        "engage_sequence.html",
        "sequences",
        seq=seq,
        tab=tab if tab in ("steps", "contacts", "settings") else "steps",
        steps=steps,
        counts=counts,
        enrolled=len(enrollments),
        contacts=contacts,
        cstatus=cstatus,
        mailboxes=r.mailboxes(),
        mailbox=mailbox,
        window={"start": start, "end": end, "days": days, "tz": tz},
        timezones=timezones() if tab == "settings" else [],
        preview_people=preview_people,
        preview_pid=preview_pid or (preview_people[0]["id"] if preview_people else ""),
        variables=VARIABLES,
    )


@router.post("/sequences/{sid}/rename")
async def sequence_rename(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    name = str(form.get("name") or "").strip()[:200]
    back = f"/sequences/{sid}"
    if not name:
        return answer(request, "Give the sequence a name.", back, ok=False)
    repo(request).update_sequence(sid, name=name)
    return answer(request, "Renamed.", back, reload=True)


@router.post("/sequences/{sid}/status")
async def sequence_status(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    r = repo(request)
    seq = r.sequence(sid)
    back = safe_next(str(form.get("next") or ""), f"/sequences/{sid}")
    if seq is None:
        return answer(request, "That sequence does not exist any more.", back, ok=False)
    action = str(form.get("action") or "")
    if action == "activate":
        if not r.steps(sid):
            return answer(request, "Add at least one step before activating.", back, ok=False)
        r.update_sequence(sid, status="active")
        msg = f"{seq.name} is active."
        if demo(request):
            msg += " Nothing is sent in the public demo."
        elif not r.mailboxes():
            msg += " Email steps wait until you connect a mailbox."
    elif action == "pause":
        r.update_sequence(sid, status="paused")
        msg = f"{seq.name} is paused. Nobody gets the next step until you resume it."
    elif action == "archive":
        r.update_sequence(sid, status="archived")
        msg = f"{seq.name} is archived."
    elif action == "unarchive":
        r.update_sequence(sid, status="paused")
        msg = f"{seq.name} is back, paused."
    else:
        return answer(request, "Unknown action.", back, ok=False)
    return answer(request, msg, back, reload=True)


@router.post("/sequences/{sid}/duplicate")
async def sequence_duplicate(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    new = repo(request).duplicate_sequence(sid)
    if new is None:
        return answer(request, "That sequence does not exist any more.", "/sequences", ok=False)
    return answer(
        request, "Duplicated. The copy starts as a draft.", f"/sequences/{new}", reload=True
    )


@router.post("/sequences/{sid}/delete")
async def sequence_delete(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    seq = r.sequence(sid)
    if seq is None:
        return answer(request, "Already deleted.", "/sequences")
    r.delete_sequence(sid)
    return answer(request, f"Deleted {seq.name}.", "/sequences", reload=True)


@router.post("/sequences/{sid}/settings")
async def sequence_settings(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    r = repo(request)
    back = f"/sequences/{sid}?tab=settings"
    if r.sequence(sid) is None:
        return answer(request, "That sequence does not exist any more.", "/sequences", ok=False)
    mailbox_id = str(form.get("mailbox_id") or "") or None
    if mailbox_id and r.mailbox(mailbox_id) is None:
        mailbox_id = None
    custom = form.get("schedule") == "custom"
    days = _days(form)
    if custom and not days:
        return answer(request, "Pick at least one sending day.", back, ok=False)
    tz = str(form.get("timezone") or "UTC")
    r.update_sequence(
        sid,
        mailbox_id=mailbox_id,
        schedule_custom=custom,
        window_start=_int(form.get("window_start"), 8, 0, 23),
        window_end=_int(form.get("window_end"), 18, 0, 24) % 24,
        window_days=days or "0,1,2,3,4",
        timezone=tz if tz in timezones() else "UTC",
        stop_on_reply=form.get("stop_on_reply") == "on",
    )
    return answer(request, "Sequence settings saved.", back, reload=True)


# ---- steps


@router.get("/partials/sequence-step", response_class=HTMLResponse)
def step_dialog(request: Request) -> Response:
    r = repo(request)
    sid = request.query_params.get("sequence_id") or ""
    seq = r.sequence(sid)
    if seq is None:
        return Response("Sequence not found", status_code=404)
    step = None
    step_id = request.query_params.get("step_id")
    if step_id and step_id.isdigit():
        step = r.step(int(step_id))
        if step is not None and step.sequence_id != sid:
            step = None
    kind = (step.kind if step else request.query_params.get("kind")) or ""
    steps = r.steps(sid)
    cfg = r.get_settings()
    return partial(
        request,
        "_engage_step_dialog.html",
        seq=seq,
        step=step,
        kind=kind if kind in STEP_KINDS else "",
        kinds=STEP_KINDS,
        position=request.query_params.get("position") or "",
        first=(step is not None and steps and steps[0].id == step.id)
        or (step is None and not steps),
        default_delay=0 if not steps else int(cfg.get("default_delay_days") or 3),
        variables=VARIABLES,
        priorities=PRIORITIES,
    )


def _step_fields(form: Any) -> tuple[dict[str, Any] | None, str]:
    kind = str(form.get("kind") or "")
    if kind not in STEP_KINDS:
        return None, "Choose a step type."
    fields = {
        "kind": kind,
        "delay_days": _int(form.get("delay_days"), 0, 0, 365),
        "subject": str(form.get("subject") or "").strip()[:500],
        "body": str(form.get("body") or "").replace("\r\n", "\n")[:20000],
        "note": str(form.get("note") or "").strip()[:4000],
        "priority": str(form.get("priority") or "medium"),
    }
    if fields["priority"] not in PRIORITIES:
        fields["priority"] = "medium"
    if kind in ("auto_email", "manual_email") and not fields["body"].strip():
        return None, "Write the email body."
    bad = unknown_vars(fields["subject"] + " " + fields["body"])
    if bad:
        return None, "Unknown variable: " + ", ".join("{{" + b + "}}" for b in bad)
    return fields, ""


@router.post("/sequences/{sid}/steps")
async def step_add(sid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    r = repo(request)
    back = f"/sequences/{sid}"
    if r.sequence(sid) is None:
        return answer(request, "That sequence does not exist any more.", "/sequences", ok=False)
    fields, error = _step_fields(form)
    if fields is None:
        return answer(request, error, back, ok=False)
    if fields["kind"] == "auto_email" and not r.steps(sid) and not fields["subject"]:
        return answer(request, "The first email needs a subject.", back, ok=False)
    pos = str(form.get("position") or "")
    r.add_step(sid, position=int(pos) if pos.isdigit() else None, **fields)
    return answer(request, f"{core.STEP_LABELS[fields['kind']]} added.", back, reload=True)


@router.post("/sequences/{sid}/steps/{step_id}")
async def step_update(
    sid: str, step_id: int, request: Request, _: None = Depends(csrf)
) -> Response:
    form = await form_values(request)
    r = repo(request)
    back = f"/sequences/{sid}"
    step = r.step(step_id)
    if step is None or step.sequence_id != sid:
        return answer(request, "That step does not exist any more.", back, ok=False)
    fields, error = _step_fields(form)
    if fields is None:
        return answer(request, error, back, ok=False)
    steps = r.steps(sid)
    if (
        fields["kind"] == "auto_email"
        and steps
        and steps[0].id == step_id
        and not fields["subject"]
    ):
        return answer(request, "The first email needs a subject.", back, ok=False)
    r.update_step(step_id, **fields)
    return answer(request, "Step saved.", back, reload=True)


@router.post("/sequences/{sid}/steps/{step_id}/delete")
async def step_delete(
    sid: str, step_id: int, request: Request, _: None = Depends(csrf)
) -> Response:
    r = repo(request)
    step = r.step(step_id)
    if step is not None and step.sequence_id == sid:
        r.delete_step(step_id)
    return answer(request, "Step removed.", f"/sequences/{sid}", reload=True)


@router.post("/sequences/{sid}/steps/{step_id}/move")
async def step_move(sid: str, step_id: int, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    r = repo(request)
    step = r.step(step_id)
    if step is not None and step.sequence_id == sid:
        r.move_step(step_id, -1 if form.get("dir") == "up" else 1)
    return answer(request, "Step moved.", f"/sequences/{sid}", reload=True)


@router.get("/sequences/{sid}/preview", response_class=HTMLResponse)
def step_preview(
    sid: str, request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    """Rendered subject and body of one step for one contact (fetched by engage.js)."""
    r = repo(request)
    seq = r.sequence(sid)
    if seq is None:
        return Response("Sequence not found", status_code=404)
    steps = r.steps(sid)
    step_id = request.query_params.get("step_id") or ""
    step = next((s for s in steps if str(s.id) == step_id), steps[0] if steps else None)
    pid = request.query_params.get("person_id") or ""
    values: dict[str, Any] = {}
    who = ""
    enrolled = next((e for e in r.enrollments(sid) if e.person_id == pid), None) if pid else None
    if enrolled is not None:
        values = dict(enrolled.vars or {})
        who = values.get("full_name") or enrolled.email
    elif pid:
        person = store.get_person(pid)
        if person is not None:
            card = person_card(store, search, pid)
            values = person_vars(person, card["company"] if card else None)
            who = person.full_name
    mailbox = core.default_mailbox(r, seq.mailbox_id)
    values["sender_name"] = mailbox.from_name if mailbox else ""
    subject = body = ""
    missing: list[str] = []
    if step is not None:
        idx = steps.index(step)
        raw_subject = step.subject
        if step.is_email and not raw_subject.strip():
            first = next((s for s in steps[:idx] if s.is_email and s.subject.strip()), None)
            raw_subject = f"Re: {first.subject}" if first else ""
        subject = render(raw_subject, values)
        body = render(step.body if step.is_email else step.note, values)
        missing = missing_vars(
            raw_subject + " " + (step.body if step.is_email else step.note), values
        )
    return partial(
        request,
        "_engage_preview.html",
        step=step,
        subject=subject,
        body=body,
        who=who,
        missing=missing,
        signature=mailbox.signature if mailbox else "",
        cfg=r.get_settings(),
    )


# ---- contacts


@router.post("/sequences/{sid}/contacts/{eid}")
async def contact_action(
    sid: str, eid: int, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), f"/sequences/{sid}?tab=contacts")
    e = repo(request).enrollment(eid)
    if e is None or e.sequence_id != sid:
        return answer(request, "That contact is no longer in the sequence.", back, ok=False)
    try:
        msg = core.set_contact_status(store, eid, str(form.get("action") or ""))
    except EngageError as exc:
        return answer(request, str(exc), back, ok=False)
    return answer(request, msg, back, reload=True)


@router.get("/partials/sequence-contacts", response_class=HTMLResponse)
def sequence_contacts_search(
    request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    """People saved locally, for the "Add contacts" dialog (fetched by engage.js)."""
    q = (request.query_params.get("q") or "").strip()[:120]
    sid = request.query_params.get("sequence_id") or ""
    taken = repo(request).enrolled_person_ids(sid) if sid else set()
    try:
        found = store.search_people(PeopleFilters(text=q or None), page=1, per_page=30)
        people = found.items
        total = found.total
    except Exception:  # noqa: BLE001
        people, total = [], 0
    rows = []
    for p in people:
        card = person_card(store, search, p.id)
        if card is None:
            continue
        reason, _person, _addr = core.check_person(store, p.id)
        rows.append({"p": card, "reason": "duplicate" if p.id in taken else reason})
    return partial(request, "_engage_people_results.html", rows=rows, total=total, q=q)


@router.get("/partials/sequence-add", response_class=HTMLResponse)
def sequence_add_dialog(request: Request) -> Response:
    seq = repo(request).sequence(request.query_params.get("sequence_id") or "")
    if seq is None:
        return Response("Sequence not found", status_code=404)
    return partial(request, "_engage_add_contacts.html", seq=seq)


@router.get("/partials/sequence-picker", response_class=HTMLResponse)
def sequence_picker(request: Request, store: Any = Depends(get_store)) -> Response:
    ids = [i for i in (request.query_params.get("ids") or "").split(",") if i.strip()][:500]
    r = repo(request)
    stats = r.sequence_stats()
    seqs = [
        {
            "seq": s,
            "steps": stats.get(s.id, {}).get("steps", 0),
            "enrolled": stats.get(s.id, {}).get("enrolled", 0),
        }
        for s in r.sequences(["active", "paused", "draft"])
    ]
    usable = sum(1 for pid in ids if core.check_person(store, pid)[0] is None)
    return partial(
        request,
        "_engage_sequence_picker.html",
        ids=ids,
        sequences=seqs,
        usable=usable,
        has_mailbox=bool(r.mailboxes()),
        next_url=safe_next(request.query_params.get("next"), ""),
        refresh=request.query_params.get("refresh") == "1",
    )


@router.post("/sequences/enroll")
async def sequences_enroll(
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
    search: Any = Depends(get_search),
) -> Response:
    form = await form_values(request)
    r = repo(request)
    back = safe_next(str(form.get("next") or ""), "/sequences")
    ids = form_ids(form)
    for chunk in form.getlist("ids_csv"):
        ids.extend(i for i in str(chunk).split(",") if i.strip())
    ids = list(dict.fromkeys(ids))[:1000]
    if not ids:
        return answer(request, "Select at least one person first.", back, ok=False)
    sid = str(form.get("sequence_id") or "")
    new_name = str(form.get("new_name") or "").strip()[:200]
    if sid == "__new__" or (not sid and new_name):
        if not new_name:
            return answer(request, "Name the new sequence.", back, ok=False)
        mailbox = core.default_mailbox(r)
        sid = r.create_sequence(
            new_name, [], status="draft", mailbox_id=mailbox.id if mailbox else None
        )
    seq = r.sequence(sid)
    if seq is None:
        return answer(request, "Choose a sequence.", back, ok=False)
    names: dict[str, str] = {}
    for pid in ids:
        card = person_card(store, search, pid)
        if card and card.get("company"):
            names[pid] = card["company"]
    try:
        counts = core.enroll_people(store, sid, ids, company_names=names)
    except EngageError as exc:
        return answer(request, str(exc), back, ok=False)
    message = core.enroll_summary(seq.name, counts)
    if counts["enrolled"] and seq.status != "active":
        message += " The sequence is not active yet: activate it to start."
    if not counts["enrolled"]:
        return answer(request, message, back, ok=False)
    reload = form.get("refresh") == "1" or back.startswith(f"/sequences/{sid}")
    if not r.steps(sid):
        back, reload = f"/sequences/{sid}", True
        message += " Add steps next."
    return answer(request, message, back, reload=reload)


# ---------------------------------------------------------------- emails


def email_rows(r: EngageRepo, store: Any, search: Any, tab: str, q: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seqs = {s.id: s for s in r.sequences()}
    boxes = {m.id: m for m in r.mailboxes()}
    cards: dict[str, Any] = {}

    def card(pid: str | None) -> Any:
        if not pid:
            return None
        if pid not in cards:
            cards[pid] = person_card(store, search, pid)
        return cards[pid]

    if tab == "scheduled":
        for m in r.messages(direction="out", statuses=["scheduled"], q=q):
            rows.append(
                {
                    "kind": "one-off",
                    "id": m.id,
                    "to": m.to_email,
                    "p": card(m.person_id),
                    "subject": m.subject,
                    "mailbox": boxes.get(m.mailbox_id or ""),
                    "seq": None,
                    "at": m.scheduled_at,
                    "status": "scheduled",
                    "error": m.error,
                }
            )
        for e, step in r.upcoming_emails():
            seq = seqs.get(e.sequence_id)
            subject = render(step.subject, e.vars or {}) or (
                f"Re: {e.thread_subject}" if e.thread_subject else "(follow-up)"
            )
            if q and q.lower() not in f"{subject} {e.email}".lower():
                continue
            mailbox = boxes.get((seq.mailbox_id if seq else None) or "") or next(
                iter(boxes.values()), None
            )
            at = e.next_run_at
            if seq is not None and at is not None:
                start, end, days, tz = core.window_of(seq, mailbox)
                at = core.next_window_open(max(at, utcnow()), start, end, days, tz) or at
            rows.append(
                {
                    "kind": "step",
                    "id": e.id,
                    "to": e.email,
                    "p": card(e.person_id),
                    "subject": subject,
                    "mailbox": mailbox,
                    "seq": seq,
                    "at": at,
                    "status": "scheduled",
                    "error": "",
                }
            )
        rows.sort(key=lambda x: x["at"] or utcnow())
        return rows
    if tab == "replies":
        messages = r.messages(direction="in", q=q)
    elif tab == "failed":
        messages = r.messages(direction="out", statuses=["failed", "bounced"], q=q)
    else:
        messages = r.messages(direction="out", statuses=["sent"], q=q)
    for m in messages:
        rows.append(
            {
                "kind": "message",
                "id": m.id,
                "to": m.from_email if m.direction == "in" else m.to_email,
                "p": card(m.person_id),
                "subject": m.subject,
                "mailbox": boxes.get(m.mailbox_id or ""),
                "seq": seqs.get(m.sequence_id or ""),
                "at": m.sent_at or m.created_at,
                "status": m.status,
                "error": m.error,
                "replied": m.replied_at is not None,
            }
        )
    return rows


@router.get("/emails", response_class=HTMLResponse)
def emails_page(
    request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    r = repo(request)
    tab = request.query_params.get("tab") or "scheduled"
    if tab not in dict(EMAIL_TABS):
        tab = "scheduled"
    q = (request.query_params.get("q") or "").strip()[:120]
    counts = r.message_counts()
    tab_counts = {
        "scheduled": counts.get("out:scheduled", 0) + len(r.upcoming_emails(1000)),
        "sent": counts.get("out:sent", 0),
        "replies": counts.get("in:received", 0),
        "failed": counts.get("out:failed", 0) + counts.get("out:bounced", 0),
    }
    boxes = r.mailboxes()
    return page(
        request,
        "engage_emails.html",
        "emails",
        tab=tab,
        q=q,
        tabs=EMAIL_TABS,
        tab_counts=tab_counts,
        rows=email_rows(r, store, search, tab, q) if boxes or sum(tab_counts.values()) else [],
        mailboxes=boxes,
        today={m.id: core.sent_today(r, m, utcnow()) for m in boxes},
    )


@router.get("/partials/email-compose", response_class=HTMLResponse)
def email_compose(
    request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    r = repo(request)
    pid = request.query_params.get("person_id") or ""
    task = r.task(request.query_params.get("task_id") or "")
    if task is not None and not pid:
        pid = task.person_id or ""
    card = person_card(store, search, pid) if pid else None
    problem = None
    if card:
        reason, _p, _a = core.check_person(store, pid)
        problem = {
            "no_email": "No email is known for this person yet.",
            "invalid_email": "The best email for this person is invalid.",
            "suppressed": "This person is on the do-not-contact list.",
        }.get(reason or "")
    boxes = r.mailboxes()
    cfg = r.get_settings()
    return partial(
        request,
        "_engage_compose.html",
        p=card,
        to=(card or {}).get("email") or request.query_params.get("to", ""),
        task=task,
        problem=problem,
        mailboxes=boxes,
        mailbox=core.default_mailbox(r),
        variables=VARIABLES,
        tz=cfg.get("timezone") or "UTC",
        next_url=safe_next(request.query_params.get("next"), ""),
        refresh=request.query_params.get("refresh") == "1",
    )


@router.post("/emails/send")
async def email_send(
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
    settings: Any = Depends(get_settings),
) -> Response:
    form = await form_values(request)
    r = repo(request)
    back = safe_next(str(form.get("next") or ""), "/emails")
    if core.is_demo(settings):
        return answer(request, core.DEMO_SEND_REASON, back, ok=False)
    cfg = r.get_settings()
    send_at = None
    if form.get("when") == "later":
        send_at = _local_dt(
            str(form.get("date") or ""), str(form.get("time") or ""), cfg.get("timezone") or "UTC"
        )
        if send_at is None or send_at <= utcnow():
            return answer(request, "Pick a date and time in the future.", back, ok=False)
    task_id = str(form.get("task_id") or "") or None
    person_id = str(form.get("person_id") or "") or None
    try:
        row_id, state = core.send_email(
            store,
            settings,
            to=str(form.get("to") or ""),
            subject=str(form.get("subject") or ""),
            body=str(form.get("body") or "").replace("\r\n", "\n"),
            person_id=person_id,
            mailbox_id=str(form.get("mailbox_id") or "") or None,
            send_at=send_at,
            task_id=task_id,
        )
    except EngageError as exc:
        return answer(request, str(exc), back, ok=False)
    if task_id and r.task(task_id) is not None and r.task(task_id).status == "open":
        try:
            core.finish_task(
                store, task_id, note="Email sent" if state == "sent" else "Email scheduled"
            )
        except EngageError:
            pass
    message = "Email sent." if state == "sent" else f"Email scheduled for {_when(send_at)}."
    return answer(request, message, back, reload=form.get("refresh") == "1")


@router.get("/partials/email/{mid}", response_class=HTMLResponse)
def email_view(
    mid: int, request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    r = repo(request)
    m = r.message(mid)
    if m is None:
        return Response("Email not found", status_code=404)
    thread = []
    if m.direction == "in" and m.in_reply_to:
        thread = r.outbound_by_message_ids([m.in_reply_to])
    seq = r.sequence(m.sequence_id) if m.sequence_id else None
    return partial(
        request,
        "_engage_message.html",
        m=m,
        p=person_card(store, search, m.person_id),
        seq=seq,
        original=thread[0] if thread else None,
        mailbox=r.mailbox(m.mailbox_id),
    )


@router.post("/emails/{mid}/cancel")
async def email_cancel(mid: int, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    m = r.message(mid)
    if m is None or m.status != "scheduled":
        return answer(request, "That email is no longer scheduled.", "/emails", ok=False)
    from pepite.engage.core import _drop_message

    _drop_message(r, mid)
    return answer(request, "Scheduled email cancelled.", "/emails?tab=scheduled", reload=True)


@router.post("/emails/{mid}/retry")
async def email_retry(mid: int, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    m = r.message(mid)
    back = "/emails?tab=failed"
    if m is None or m.status != "failed":
        return answer(request, "Only failed emails can be sent again.", back, ok=False)
    if demo(request):
        return answer(request, core.DEMO_SEND_REASON, back, ok=False)
    r.update_message(mid, status="scheduled", scheduled_at=utcnow(), error="")
    return answer(
        request, "Queued again. It goes out on the next pass (within a minute).", back, reload=True
    )


@router.post("/emails/{mid}/replied")
async def email_mark_replied(
    mid: int, request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    r = repo(request)
    m = r.message(mid)
    back = "/emails?tab=sent"
    if m is None or m.direction != "out":
        return answer(request, "That email does not exist any more.", back, ok=False)
    r.update_message(mid, replied_at=m.replied_at or utcnow())
    if m.enrollment_id:
        core.mark_replied(store, m.enrollment_id)
    else:
        core.log_activity(
            store, "email_reply", f"Marked as replied: {m.subject}", person_id=m.person_id
        )
        core.emit("email_replied", person_id=m.person_id, sequence_id=None)
    return answer(request, "Marked as replied.", back, reload=True)


# ---------------------------------------------------------------- tasks


def task_filters(request: Request) -> dict[str, Any]:
    g = request.query_params
    view, tab = g.get("view") or "open", g.get("tab") or "all"
    return {
        "tab": tab if tab in dict(TASK_TABS) else "all",
        "view": view if view in dict(TASK_VIEWS) else "open",
        "priority": [p for p in g.getlist("priority") if p in PRIORITIES],
        "q": (g.get("q") or "").strip()[:120],
        "sort": g.get("sort") if g.get("sort") in ("due", "created", "priority") else "due",
        "done": g.get("done") == "1",
    }


def day_bounds(cfg: dict[str, Any], now: datetime) -> tuple[datetime, datetime]:
    start = core.day_start(now, cfg.get("timezone") or "UTC")
    return start, start + timedelta(days=1)


def query_tasks(
    r: EngageRepo, f: dict[str, Any], cfg: dict[str, Any], tab: str | None = None
) -> list[Any]:
    """Tasks for a tab (type, or overdue) inside the chosen view (open, today, ...)."""
    now = utcnow()
    start, end = day_bounds(cfg, now)
    tab = tab or f["tab"]
    view = "open" if tab == "overdue" else f["view"]
    kw: dict[str, Any] = {
        "kinds": [tab] if tab in TASK_KINDS else None,
        "priorities": f["priority"] or None,
        "q": f["q"],
    }
    if view == "done":
        statuses = ["done", "skipped"]
    elif view == "all" or (f["done"] and tab != "overdue"):
        statuses = ["open", "done", "skipped"]
    else:
        statuses = ["open"]
    rows = r.tasks(
        statuses=statuses,
        sort="done" if view == "done" else ("created" if f["sort"] == "created" else "due"),
        **kw,
    )

    def open_and(cond: Any) -> list[Any]:
        return [t for t in rows if t.status != "open" or cond(t)]

    if tab == "overdue":
        rows = [t for t in rows if t.due_at is not None and t.due_at < start]
    elif view == "today":
        rows = open_and(lambda t: t.due_at is not None and start <= t.due_at < end)
    elif view == "upcoming":
        rows = open_and(lambda t: t.due_at is None or t.due_at >= end)
    if f["sort"] == "priority":
        order = {p: i for i, p in enumerate(PRIORITIES)}
        rows.sort(key=lambda t: (order.get(t.priority, 1), t.due_at or now + timedelta(days=9999)))
    return rows


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(
    request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    r = repo(request)
    cfg = r.get_settings()
    f = task_filters(request)
    rows = query_tasks(r, f, cfg)
    counts = {key: len(query_tasks(r, f, cfg, key)) for key, _ in TASK_TABS}
    start, _end = day_bounds(cfg, utcnow())
    seqs = {s.id: s for s in r.sequences()}
    items = []
    for t in rows:
        items.append(
            {
                "t": t,
                "p": person_card(store, search, t.person_id),
                "c": company_card(store, search, t.company_id) if not t.person_id else None,
                "seq": seqs.get(t.sequence_id or ""),
                "overdue": t.status == "open" and t.due_at is not None and t.due_at < start,
            }
        )
    calls_today = len(
        query_tasks(r, {**f, "view": "today", "priority": [], "q": "", "done": False}, cfg, "call")
    )
    return page(
        request,
        "engage_tasks.html",
        "tasks",
        f=f,
        tabs=TASK_TABS,
        views=TASK_VIEWS,
        counts=counts,
        items=items,
        any_tasks=bool(r.tasks(limit=1)),
        priorities=PRIORITIES,
        filtered=bool(f["priority"] or f["q"]),
        calls_today=calls_today,
        params={
            "tab": "" if f["tab"] == "all" else f["tab"],
            "view": "" if f["view"] == "open" else f["view"],
            "sort": "" if f["sort"] == "due" else f["sort"],
            "q": f["q"],
            "priority": f["priority"],
            "done": "1" if f["done"] else "",
        },
    )


@router.post("/tasks/tip")
def tasks_tip(request: Request, _: None = Depends(csrf)) -> Response:
    repo(request).save_settings(tasks_tip_dismissed=True)
    if is_htmx(request):
        return Response(status_code=200)
    return RedirectResponse("/tasks", status_code=303)


@router.get("/partials/task-new", response_class=HTMLResponse)
def task_new_dialog(
    request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    g = request.query_params
    pid, cid, did = g.get("person_id") or "", g.get("company_id") or "", g.get("deal_id") or ""
    p = person_card(store, search, pid) if pid else None
    c = company_card(store, search, cid or (p or {}).get("company_id")) if (cid or p) else None
    cfg = repo(request).get_settings()
    tz = cfg.get("timezone") or "UTC"
    today = utcnow().astimezone(core.zone(tz)).date()
    return partial(
        request,
        "_engage_task_new.html",
        p=p,
        c=c,
        deal_id=did,
        kinds=TASK_KINDS,
        priorities=PRIORITIES,
        kind=g.get("kind") if g.get("kind") in TASK_KINDS else "action",
        today=today.isoformat(),
        tz=tz,
        next_url=safe_next(g.get("next"), ""),
        refresh=g.get("refresh") == "1",
    )


@router.post("/tasks")
async def task_create(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/tasks")
    title = str(form.get("title") or "").strip()[:300]
    if not title:
        return answer(request, "Give the task a title.", back, ok=False)
    cfg = repo(request).get_settings()
    due = None
    if str(form.get("due_date") or "").strip():
        due = _local_dt(
            str(form.get("due_date")), str(form.get("due_time") or ""), cfg.get("timezone") or "UTC"
        )
        if due is None:
            return answer(request, "That due date is not valid.", back, ok=False)
    kind = str(form.get("kind") or "action")
    task_id = core.new_task(
        store,
        title,
        person_id=str(form.get("person_id") or "") or None,
        company_id=str(form.get("company_id") or "") or None,
        deal_id=str(form.get("deal_id") or "") or None,
        due=due,
        kind=kind if kind in TASK_KINDS else "action",
        priority=str(form.get("priority") or "medium"),
        body=str(form.get("body") or "").strip()[:4000],
    )
    del task_id
    return answer(request, "Task created.", back, reload=form.get("refresh") == "1")


@router.get("/partials/task-done/{tid}", response_class=HTMLResponse)
def task_done_dialog(
    tid: str, request: Request, store: Any = Depends(get_store), search: Any = Depends(get_search)
) -> Response:
    task = repo(request).task(tid)
    if task is None:
        return Response("Task not found", status_code=404)
    action = request.query_params.get("action")
    return partial(
        request,
        "_engage_task_done.html",
        t=task,
        p=person_card(store, search, task.person_id),
        action="skip" if action == "skip" else "complete",
        next_url=safe_next(request.query_params.get("next"), "/tasks"),
    )


@router.post("/tasks/{tid}/{action}")
async def task_action(
    tid: str,
    action: str,
    request: Request,
    _: None = Depends(csrf),
    store: Any = Depends(get_store),
) -> Response:
    form = await form_values(request)
    back = safe_next(str(form.get("next") or ""), "/tasks")
    note = str(form.get("note") or "")
    try:
        if action == "complete":
            core.finish_task(store, tid, "done", note)
            msg = "Task completed."
        elif action == "skip":
            core.finish_task(store, tid, "skipped", note)
            msg = "Task skipped."
        elif action == "reopen":
            core.reopen_task(store, tid)
            msg = "Task reopened."
        elif action == "delete":
            repo(request).delete_task(tid)
            msg = "Task deleted."
        else:
            return answer(request, "Unknown action.", back, ok=False)
    except EngageError as exc:
        return answer(request, str(exc), back, ok=False)
    reload = form.get("refresh") != "0"
    return answer(request, msg, back, reload=reload)


# ---------------------------------------------------------------- settings: mailboxes


def mailbox_form_values(
    form: Any, settings: Any, existing: Any = None
) -> tuple[dict[str, Any] | None, str]:
    from_email = core.clean_address(str(form.get("from_email") or ""))
    if from_email is None:
        return None, "Enter a valid sender address."
    security = str(form.get("smtp_security") or "starttls")
    imap_security = str(form.get("imap_security") or "ssl")
    if security not in mailer.SECURITY or imap_security not in mailer.SECURITY:
        return None, "Choose a security setting."
    host = str(form.get("smtp_host") or "").strip()[:255]
    if not host:
        return None, "Enter the SMTP server."
    days = _days(form)
    if not days:
        return None, "Pick at least one sending day."
    tz = str(form.get("timezone") or "UTC")
    fields: dict[str, Any] = {
        "name": str(form.get("name") or "").strip()[:120],
        "from_name": str(form.get("from_name") or "").strip()[:120],
        "from_email": from_email,
        "smtp_host": host,
        "smtp_port": _int(form.get("smtp_port"), 587, 1, 65535),
        "smtp_security": security,
        "smtp_username": str(form.get("smtp_username") or "").strip()[:320],
        "imap_host": str(form.get("imap_host") or "").strip()[:255],
        "imap_port": _int(form.get("imap_port"), 993, 1, 65535),
        "imap_security": imap_security,
        "imap_username": str(form.get("imap_username") or "").strip()[:320],
        "imap_folder": str(form.get("imap_folder") or "INBOX").strip()[:120] or "INBOX",
        "daily_limit": _int(form.get("daily_limit"), 50, 1, 2000),
        "window_start": _int(form.get("window_start"), 8, 0, 23),
        "window_end": _int(form.get("window_end"), 18, 0, 24) % 24,
        "window_days": days,
        "timezone": tz if tz in timezones() else "UTC",
        "signature": str(form.get("signature") or "").replace("\r\n", "\n")[:4000],
    }
    if not core.is_demo(settings):  # the public demo never stores passwords
        for key in ("smtp_password", "imap_password"):
            typed = str(form.get(key) or "")
            if typed:
                fields[key] = crypto.encrypt(settings, typed)
            elif existing is None:
                fields[key] = ""
        if form.get("clear_imap") == "on":
            fields.update(imap_host="", imap_password="", imap_username="")
    changed_login = existing is not None and any(
        fields.get(k) != getattr(existing, k)
        for k in ("smtp_host", "smtp_port", "smtp_security", "smtp_username")
    )
    if existing is None or changed_login or "smtp_password" in fields:
        fields["status"] = "untested"
        fields["last_error"] = ""
    return fields, ""


@router.get("/settings/mailboxes", response_class=HTMLResponse)
def mailboxes_page(request: Request) -> Response:
    r = repo(request)
    boxes = r.mailboxes()
    now = utcnow()
    return settings_view(
        request,
        "engage_mailboxes.html",
        "mailboxes",
        mailboxes=boxes,
        today={m.id: core.sent_today(r, m, now) for m in boxes},
        providers=PROVIDERS,
        guide_url=GUIDE_URL,
    )


@router.get("/settings/mailboxes/new", response_class=HTMLResponse)
def mailbox_new(request: Request) -> Response:
    key = request.query_params.get("provider") or "custom"
    provider = PROVIDERS.get(key, PROVIDERS["custom"])
    cfg = repo(request).get_settings()
    return settings_view(
        request,
        "engage_mailbox_form.html",
        "mailboxes",
        m=None,
        provider_key=key if key in PROVIDERS else "custom",
        provider=provider,
        providers=PROVIDERS,
        timezones=timezones(),
        default_tz=cfg.get("timezone") or "UTC",
        guide_url=GUIDE_URL,
    )


@router.post("/settings/mailboxes")
async def mailbox_create(
    request: Request, _: None = Depends(csrf), settings: Any = Depends(get_settings)
) -> Response:
    form = await form_values(request)
    fields, error = mailbox_form_values(form, settings)
    if fields is None:
        return answer(request, error, "/settings/mailboxes/new", ok=False)
    r = repo(request)
    mid = r.create_mailbox(**fields)
    if not r.get_settings().get("timezone") or r.get_settings().get("timezone") == "UTC":
        r.save_settings(timezone=fields["timezone"])
    message = f"Mailbox {fields['from_email']} saved."
    if core.is_demo(settings):
        message += " Passwords are not stored in the public demo."
    else:
        message += " Run a connection test next."
    return answer(request, message, f"/settings/mailboxes#mbx-{mid}", reload=True)


@router.post("/settings/mailboxes/test-all")
def mailbox_test_all(
    request: Request, _: None = Depends(csrf), settings: Any = Depends(get_settings)
) -> Response:
    r = repo(request)
    back = "/settings/mailboxes"
    if core.is_demo(settings):
        return answer(
            request, "Connection tests are turned off in the public demo.", back, ok=False
        )
    boxes = r.mailboxes()
    if not boxes:
        return answer(request, "Connect a mailbox first.", back, ok=False)
    results = [_test_one(request, settings, r, m) for m in boxes]
    good = sum(1 for ok, _ in results if ok)
    msg = f"{good} of {len(results)} mailbox{'es' if len(results) != 1 else ''} passed."
    failed = [m for ok, m in results if not ok]
    if failed:
        msg += " " + failed[0]
    return answer(request, msg, back, ok=not failed, reload=True)


@router.get("/settings/mailboxes/{mid}", response_class=HTMLResponse)
def mailbox_edit(mid: str, request: Request) -> Response:
    r = repo(request)
    m = r.mailbox(mid)
    if m is None:
        return _missing(request, "Mailbox")
    return settings_view(
        request,
        "engage_mailbox_form.html",
        "mailboxes",
        m=m,
        provider_key="custom",
        provider=PROVIDERS["custom"],
        providers=PROVIDERS,
        timezones=timezones(),
        default_tz=m.timezone,
        guide_url=GUIDE_URL,
    )


@router.post("/settings/mailboxes/{mid}")
async def mailbox_update(
    mid: str, request: Request, _: None = Depends(csrf), settings: Any = Depends(get_settings)
) -> Response:
    form = await form_values(request)
    r = repo(request)
    m = r.mailbox(mid)
    if m is None:
        return answer(
            request, "That mailbox does not exist any more.", "/settings/mailboxes", ok=False
        )
    fields, error = mailbox_form_values(form, settings, existing=m)
    if fields is None:
        return answer(request, error, f"/settings/mailboxes/{mid}", ok=False)
    r.update_mailbox(mid, **fields)
    return answer(request, "Mailbox saved.", f"/settings/mailboxes#mbx-{mid}", reload=True)


def _test_one(request: Request, settings: Any, r: EngageRepo, m: Any) -> tuple[bool, str]:
    try:
        msg = mailer.test_smtp(settings, m)
    except mailer.MailboxError as exc:
        r.update_mailbox(m.id, status="error", last_error=str(exc), tested_at=utcnow())
        return False, f"{m.from_email}: {exc}"
    r.update_mailbox(m.id, status="ok", last_error="", tested_at=utcnow())
    if m.has_imap:
        try:
            msg += " " + mailer.test_imap(settings, m)
            r.update_mailbox(m.id, imap_status="ok", imap_error="")
        except mailer.MailboxError as exc:
            r.update_mailbox(m.id, imap_status="error", imap_error=str(exc))
            return False, f"{m.from_email}: sending works, but reply checks fail: {exc}"
    return True, f"{m.from_email}: {msg} Nothing was sent."


@router.post("/settings/mailboxes/{mid}/test")
def mailbox_test(
    mid: str, request: Request, _: None = Depends(csrf), settings: Any = Depends(get_settings)
) -> Response:
    r = repo(request)
    m = r.mailbox(mid)
    back = f"/settings/mailboxes#mbx-{mid}"
    if m is None:
        return answer(
            request, "That mailbox does not exist any more.", "/settings/mailboxes", ok=False
        )
    if core.is_demo(settings):
        return answer(
            request, "Connection tests are turned off in the public demo.", back, ok=False
        )
    ok, msg = _test_one(request, settings, r, m)
    return answer(request, msg, back, ok=ok, reload=ok)


@router.post("/settings/mailboxes/{mid}/poll")
def mailbox_poll(
    mid: str,
    request: Request,
    _: None = Depends(csrf),
    settings: Any = Depends(get_settings),
    store: Any = Depends(get_store),
) -> Response:
    from pepite.engage.replies import poll_mailbox

    r = repo(request)
    m = r.mailbox(mid)
    back = f"/settings/mailboxes#mbx-{mid}"
    if m is None or not m.has_imap:
        return answer(request, "Add IMAP details to check for replies.", back, ok=False)
    if core.is_demo(settings):
        return answer(request, "Reply checks are turned off in the public demo.", back, ok=False)
    found = poll_mailbox(store, settings, m)
    m = r.mailbox(mid)
    if m is not None and m.imap_status == "error":
        return answer(request, f"Reply check failed: {m.imap_error}", back, ok=False)
    return answer(
        request,
        f"Checked {m.from_email if m else 'the mailbox'}: {found['replies']} new repl"
        f"{'ies' if found['replies'] != 1 else 'y'}, {found['bounces']} bounce"
        f"{'s' if found['bounces'] != 1 else ''}.",
        back,
        reload=True,
    )


@router.post("/settings/mailboxes/{mid}/delete")
async def mailbox_delete(mid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    m = r.mailbox(mid)
    if m is None:
        return answer(request, "Already removed.", "/settings/mailboxes")
    r.delete_mailbox(mid)
    return answer(
        request,
        f"Removed {m.from_email}. Its stored passwords are gone.",
        "/settings/mailboxes",
        reload=True,
    )


# ---------------------------------------------------------------- settings: sending


@router.get("/settings/sending", response_class=HTMLResponse)
def sending_page(request: Request) -> Response:
    r = repo(request)
    cfg = r.get_settings()
    sample = footer_text(cfg, f"{core.base_url(cfg)}/u/…")
    return settings_view(
        request,
        "engage_sending.html",
        "sending",
        timezones=timezones(),
        suggested_url=str(request.base_url).rstrip("/"),
        link_base=core.base_url(cfg),
        sample=sample,
        mailboxes=r.mailboxes(),
    )


@router.post("/settings/sending")
async def sending_save(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = "/settings/sending"
    footer = str(form.get("footer_text") or "").strip()[:1000]
    if not footer:
        return answer(request, "The unsubscribe footer cannot be empty.", back, ok=False)
    url = str(form.get("public_url") or "").strip().rstrip("/")[:300]
    if url and not url.startswith(("http://", "https://")):
        return answer(
            request, "The public address must start with https:// or http://.", back, ok=False
        )
    tz = str(form.get("timezone") or "UTC")
    bounce = str(form.get("bounce_action") or "suppress")
    repo(request).save_settings(
        footer_text=footer,
        postal_address=str(form.get("postal_address") or "").strip()[:300],
        default_delay_days=_int(form.get("default_delay_days"), 3, 0, 60),
        stop_on_reply=form.get("stop_on_reply") == "on",
        bounce_action=bounce if bounce in ("suppress", "stop") else "suppress",
        gap_minutes=_int(form.get("gap_minutes"), 1, 0, 120),
        reply_poll_minutes=_int(form.get("reply_poll_minutes"), 5, 1, 1440),
        public_url=url,
        timezone=tz if tz in timezones() else "UTC",
    )
    return answer(request, "Sending rules saved.", back, reload=True)


@router.post("/settings/sending/run")
def sending_run(
    request: Request,
    _: None = Depends(csrf),
    settings: Any = Depends(get_settings),
    store: Any = Depends(get_store),
) -> Response:
    from pepite.engage.scheduler import run_due

    back = safe_next(request.query_params.get("next"), "/settings/sending")
    if core.is_demo(settings):
        return answer(request, core.DEMO_SEND_REASON, back, ok=False)
    stats = run_due(store, settings)
    if stats.get("busy"):
        return answer(request, "A pass is already running. Try again in a minute.", back, ok=False)
    msg = (
        f"Pass done: {stats.get('sent', 0)} sent, {stats.get('tasks', 0)} tasks created, "
        f"{stats.get('replies', 0)} replies found."
    )
    if stats.get("waiting"):
        msg += f" {stats['waiting']} waiting for their sending window or limit."
    return answer(request, msg, back, reload=True)


# ---------------------------------------------------------------- public unsubscribe


def _mask(address: str) -> str:
    local, _, domain = address.partition("@")
    return (local[:1] + "•" * max(2, min(6, len(local) - 1)) + "@" + domain) if domain else address


def _unsubscribe_page(
    request: Request, state: str, address: str = "", status: int = 200
) -> Response:
    return templates.TemplateResponse(
        request,
        "engage_unsubscribe.html",
        {
            "state": state,
            "masked": _mask(address) if address else "",
            "token": request.path_params.get("token", ""),
        },
        status_code=status,
    )


@router.get("/u/{token}", response_class=HTMLResponse)
def unsubscribe_page(
    token: str, request: Request, settings: Any = Depends(get_settings)
) -> Response:
    try:
        data = crypto.read_unsubscribe_token(settings, token)
    except crypto.TokenError as exc:
        return _unsubscribe_page(
            request, "expired" if exc.expired else "invalid", status=410 if exc.expired else 404
        )
    return _unsubscribe_page(request, "confirm", data["e"])


@router.post("/u/{token}", response_class=HTMLResponse)
def unsubscribe_submit(
    token: str,
    request: Request,
    _: None = Depends(csrf),
    settings: Any = Depends(get_settings),
    store: Any = Depends(get_store),
) -> Response:
    """The confirmation button, and RFC 8058 one-click unsubscribe from mail clients."""
    try:
        data = crypto.read_unsubscribe_token(settings, token)
    except crypto.TokenError as exc:
        return _unsubscribe_page(
            request, "expired" if exc.expired else "invalid", status=410 if exc.expired else 404
        )
    core.unsubscribe(store, data["e"])
    return _unsubscribe_page(request, "done", data["e"])
