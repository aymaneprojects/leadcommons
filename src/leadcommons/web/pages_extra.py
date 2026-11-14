# ruff: noqa: B008  (FastAPI Depends defaults)
"""Shell pages: settings sections, onboarding hub, notifications, palette, engage empty states.

Owner: shell UI agent. Included by create_app before leadcommons.web.pages, so a route defined
here takes over the same path in pages.py.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl

import dns.exception
import dns.resolver
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from leadcommons.search import CompanyFilters, PeopleFilters
from leadcommons.web.deps import get_jobs, get_search, get_store, templates
from leadcommons.web.pages import (
    clean_suppression,
    csrf,
    done,
    form_values,
    render,
)

router = APIRouter()

REPO_URL = "https://github.com/aymaneprojects/leadcommons"
ROADMAP_URL = f"{REPO_URL}#roadmap"
ISSUES_URL = f"{REPO_URL}/issues"
DNS_TIMEOUT = 5.0

# ---------------------------------------------------------------- settings

SETTINGS_SECTIONS = [
    ("overview", "/settings", "Workspace overview", "house"),
    ("packs", "/settings/data-packs", "Data packs", "database"),
    ("email", "/settings/email-health", "Email verification & health", "mail-check"),
    ("privacy", "/settings/privacy", "Privacy & opt-out", "shield-check"),
    ("account", "/settings/account", "Account", "user"),
    ("about", "/settings/about", "About", "info"),
]


def _pack_summary(search: Any) -> dict[str, Any]:
    info = getattr(search, "pack_info", None)
    try:
        packs = list(info()) if info else []
    except Exception:
        packs = []
    sources: list[str] = []
    for pk in packs:
        for s in pk.get("sources") or []:
            if s not in sources:
                sources.append(s)
    built = sorted(str(pk["built_at"]) for pk in packs if pk.get("built_at"))
    return {
        "packs": packs,
        "files": len(packs),
        "rows": sum(int(pk.get("rows") or 0) for pk in packs),
        "sources": sources,
        "built_at": built[-1] if built else None,
    }


def _workspace_counts(store: Any) -> dict[str, int | None]:
    def safe(fn: Any) -> int | None:
        try:
            return int(fn())
        except Exception:
            return None

    return {
        "people": safe(lambda: store.search_people(PeopleFilters(), page=1, per_page=1).total),
        "lists": safe(lambda: len(store.lists())),
        "companies_saved": safe(lambda: len(store.saved_company_ids())),
        "searches": safe(lambda: len(store.saved_searches())),
    }


def settings_ctx(request: Request, section: str, **extra: Any) -> dict[str, Any]:
    return {
        "section": section,
        "sections": SETTINGS_SECTIONS,
        "pack": _pack_summary(request.app.state.search),
        "counts": _workspace_counts(request.app.state.store),
        **extra,
    }


def settings_page(request: Request, template: str, section: str, **extra: Any) -> Response:
    active = "email-health" if section == "email" else "settings"
    return render(request, template, active=active, **settings_ctx(request, section, **extra))


@router.get("/settings", response_class=HTMLResponse)
def settings_overview(request: Request) -> Response:
    return settings_page(request, "settings.html", "overview")


@router.get("/settings/data-packs", response_class=HTMLResponse)
def settings_packs(request: Request) -> Response:
    return settings_page(request, "shell_settings_packs.html", "packs")


@router.get("/settings/privacy", response_class=HTMLResponse)
def settings_privacy(request: Request) -> Response:
    return settings_page(request, "shell_settings_privacy.html", "privacy")


@router.get("/settings/account", response_class=HTMLResponse)
def settings_account(request: Request) -> Response:
    return settings_page(request, "shell_settings_account.html", "account")


@router.get("/settings/about", response_class=HTMLResponse)
def settings_about(request: Request) -> Response:
    return settings_page(request, "shell_settings_about.html", "about")


@router.post("/settings/suppression")
async def settings_suppress(
    request: Request, _: None = Depends(csrf), store: Any = Depends(get_store)
) -> Response:
    """Same behaviour as pages.py, but returns to the privacy section."""
    form = await form_values(request)
    back = "/settings/privacy"
    value = clean_suppression(str(form.get("value") or ""))
    if not value:
        return done(request, "Enter a valid email address or domain.", back, "error")
    store.add_suppression(value, "manual")
    return done(request, f"{value} will never be contacted or exported.", back)


# ---- email setup & health: real DNS checks (MX, SPF, DMARC, optional DKIM)

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN_RE = re.compile(rf"^(?:{_LABEL}\.)+{_LABEL}$")
_SELECTOR_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9])?$")


def clean_domain(raw: str) -> str | None:
    """Accept 'example.com', 'https://www.example.com/x', 'me@example.com'. None if invalid."""
    v = raw.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    if "@" in v:
        v = v.rsplit("@", 1)[1]
    v = re.split(r"[/?#:\s]", v, maxsplit=1)[0].rstrip(".")
    if not v or len(v) > 253:
        return None
    try:
        v = v.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    return v if _DOMAIN_RE.match(v) else None


def _txt_value(rdata: Any) -> str:
    strings = getattr(rdata, "strings", None)
    if strings is not None:
        return "".join(
            s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s) for s in strings
        )
    return str(rdata).strip('"')


class _Lookup:
    """Result of one DNS query: records, or a polite reason why there are none."""

    def __init__(
        self,
        records: list[str] | None = None,
        problem: str | None = None,
        missing: bool = False,
        nxdomain: bool = False,
    ) -> None:
        self.records = records or []
        self.problem = problem  # None, or a human sentence
        self.missing = missing  # True when the name/record simply does not exist
        self.nxdomain = nxdomain  # True when DNS says the name itself does not exist


def _resolve(name: str, rtype: str) -> _Lookup:
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = DNS_TIMEOUT
        resolver.lifetime = DNS_TIMEOUT
        answer = resolver.resolve(name, rtype)
        if rtype == "TXT":
            return _Lookup([_txt_value(r) for r in answer])
        return _Lookup([f"{r.preference} {str(r.exchange).rstrip('.') or '.'}" for r in answer])
    except dns.resolver.NXDOMAIN:
        return _Lookup(missing=True, nxdomain=True)
    except dns.resolver.NoAnswer:
        return _Lookup(missing=True)
    except dns.exception.Timeout:
        return _Lookup(problem="The DNS server did not answer within 5 seconds. Try again.")
    except dns.resolver.NoNameservers:
        return _Lookup(problem="No DNS server could answer this query.")
    except dns.exception.DNSException:
        return _Lookup(problem="The DNS lookup failed. Check your network connection.")
    except Exception:
        return _Lookup(problem="The DNS lookup could not be completed.")


def _check_mx(lk: _Lookup) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "mx",
        "label": "MX records",
        "what": "Where mail for the domain is delivered.",
    }
    if lk.problem:
        return {**row, "state": "error", "summary": lk.problem, "records": []}
    if lk.nxdomain:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": "This domain name does not exist in DNS.",
            "hint": "Check the spelling, or register the domain first.",
        }
    if lk.missing or not lk.records:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": "No MX records found. This domain cannot receive email.",
            "hint": "Add MX records at your DNS provider (your mail host gives you the values).",
        }
    if all(r.split(" ", 1)[1] == "." for r in lk.records):
        return {
            **row,
            "state": "warn",
            "records": lk.records,
            "summary": "The domain publishes a null MX: it says it receives no email.",
        }
    n = len(lk.records)
    return {
        **row,
        "state": "ok",
        "records": lk.records,
        "summary": f"{n} mail server{'s' if n != 1 else ''} found.",
    }


def _check_spf(lk: _Lookup) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "spf",
        "label": "SPF",
        "what": "Which servers may send mail for the domain.",
    }
    if lk.problem:
        return {**row, "state": "error", "summary": lk.problem, "records": []}
    spf = [r for r in lk.records if r.lower().startswith("v=spf1")]
    if lk.nxdomain:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": "This domain name does not exist in DNS.",
        }
    if not spf:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": "No SPF record found.",
            "hint": "Publish a TXT record starting with v=spf1 that lists your sending services.",
        }
    if len(spf) > 1:
        return {
            **row,
            "state": "fail",
            "records": spf,
            "summary": "More than one SPF record found. Receivers treat this as an error.",
            "hint": "Merge them into a single TXT record.",
        }
    rec = spf[0].lower()
    if rec.rstrip().endswith("+all") or rec.rstrip().endswith(" all"):
        return {
            **row,
            "state": "warn",
            "records": spf,
            "summary": "The record allows any server to send as this domain.",
            "hint": "End it with ~all or -all.",
        }
    if rec.rstrip().endswith("?all"):
        return {
            **row,
            "state": "warn",
            "records": spf,
            "summary": "The record is neutral about unknown servers.",
            "hint": "End it with ~all or -all.",
        }
    return {**row, "state": "ok", "records": spf, "summary": "A single SPF record is published."}


def _check_dmarc(lk: _Lookup) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "dmarc",
        "label": "DMARC",
        "what": "What receivers do with mail that fails SPF and DKIM.",
    }
    if lk.problem:
        return {**row, "state": "error", "summary": lk.problem, "records": []}
    rec = [r for r in lk.records if r.lower().startswith("v=dmarc1")]
    if not rec:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": "No DMARC record found.",
            "hint": "Publish a TXT record at _dmarc with v=DMARC1; p=none to start monitoring.",
        }
    m = re.search(r"(?:^|;)\s*p\s*=\s*([a-z]+)", rec[0], re.I)
    policy = m.group(1).lower() if m else ""
    if policy == "none":
        return {
            **row,
            "state": "warn",
            "records": rec,
            "summary": "Policy is none: failures are reported but not acted on.",
            "hint": "Move to p=quarantine or p=reject once reports look clean.",
        }
    if policy in ("quarantine", "reject"):
        return {**row, "state": "ok", "records": rec, "summary": f"Policy is {policy}."}
    return {
        **row,
        "state": "warn",
        "records": rec,
        "summary": "The record has no readable policy (p=).",
    }


def _check_dkim(lk: _Lookup, selector: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "key": "dkim",
        "label": f"DKIM ({selector})",
        "what": "A public key receivers use to verify signed mail.",
    }
    if lk.problem:
        return {**row, "state": "error", "summary": lk.problem, "records": []}
    rec = [r for r in lk.records if "p=" in r.replace(" ", "").lower()]
    if not rec:
        return {
            **row,
            "state": "fail",
            "records": [],
            "summary": f"No DKIM key found at {selector}._domainkey.",
            "hint": "Check the selector name with your mail provider.",
        }
    short = [r if len(r) <= 120 else r[:117] + "..." for r in rec]
    return {**row, "state": "ok", "records": short, "summary": "A DKIM public key is published."}


def run_dns_checks(domain: str, selector: str = "") -> list[dict[str, Any]]:
    jobs: dict[str, tuple[str, str]] = {
        "mx": (domain, "MX"),
        "spf": (domain, "TXT"),
        "dmarc": (f"_dmarc.{domain}", "TXT"),
    }
    if selector:
        jobs["dkim"] = (f"{selector}._domainkey.{domain}", "TXT")
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {k: pool.submit(_resolve, *v) for k, v in jobs.items()}
        found = {k: f.result() for k, f in futures.items()}
    rows = [_check_mx(found["mx"]), _check_spf(found["spf"]), _check_dmarc(found["dmarc"])]
    if selector:
        rows.append(_check_dkim(found["dkim"], selector))
    return rows


@router.get("/settings/email-health", response_class=HTMLResponse)
def settings_email(request: Request) -> Response:
    raw = (request.query_params.get("domain") or "").strip()
    selector = (request.query_params.get("selector") or "").strip().lower()
    tab = request.query_params.get("tab") or "dns"
    error: str | None = None
    domain: str | None = None
    checks: list[dict[str, Any]] = []
    if selector and not _SELECTOR_RE.match(selector):
        error = "The DKIM selector may only contain letters, digits, dots, dashes and underscores."
        selector = ""
    if raw:
        domain = clean_domain(raw)
        if domain is None:
            error = "That does not look like a domain name. Try something like example.com."
        elif error is None:
            checks = run_dns_checks(domain, selector)
    summary = {
        s: sum(1 for c in checks if c["state"] == s) for s in ("ok", "warn", "fail", "error")
    }
    return settings_page(
        request,
        "shell_settings_email.html",
        "email",
        tab="mailboxes" if tab == "mailboxes" else "dns",
        raw_domain=raw,
        selector=selector,
        domain=domain,
        checks=checks,
        summary=summary,
        error=error,
        roadmap_url=ROADMAP_URL,
    )


@router.get("/email-health")
def email_health_redirect() -> Response:
    return RedirectResponse("/settings/email-health", status_code=302)


# ---------------------------------------------------------------- onboarding hub


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_hub(request: Request) -> Response:
    return render(request, "shell_onboarding.html", active="onboarding")


# ---------------------------------------------------------------- engage empty states

ENGAGE = {
    "sequences": {
        "title": "Sequences",
        "icon": "send",
        "headline": "Sequences are coming in v0.3",
        "lead": "Reach out to the people on your lists with a series of emails sent on a schedule.",
        "cta": "Create sequence",
        "mailbox_note": True,
        "features": [
            (
                "list",
                "Built step by step",
                "Chain emails with delays between them. Planned for v0.3.",
            ),
            (
                "clock",
                "Sends on your schedule",
                "Pick sending windows and daily limits. Planned for v0.3.",
            ),
            (
                "corner-down-left",
                "Stops when they reply",
                "Contacts who answer leave the sequence. Planned for v0.3.",
            ),
        ],
        "meanwhile": ("Export a list as CSV and use your own mailer.", "/lists", "Open lists"),
    },
    "emails": {
        "title": "Emails",
        "icon": "mail",
        "headline": "Sending from LeadCommons is coming in v0.3",
        "lead": (
            "A single place for the emails you send and the replies you get back. "
            "Nothing is sent from LeadCommons today."
        ),
        "cta": "Compose email",
        "mailbox_note": True,
        "features": [
            (
                "mail-check",
                "Your own mailbox",
                "Connect a mailbox you already own. Planned for v0.3.",
            ),
            (
                "inbox",
                "One inbox for replies",
                "See answers next to the person they came from. Planned for v0.3.",
            ),
            (
                "shield-check",
                "Respects opt-outs",
                "Suppressed addresses will never be emailed. Already true for exports.",
            ),
        ],
        "meanwhile": (
            "Check that your domain is set up to send mail.",
            "/settings/email-health",
            "Run a DNS check",
        ),
    },
    "tasks": {
        "title": "Tasks",
        "icon": "square-check-big",
        "headline": "Tasks are coming in v0.3",
        "lead": "Follow-ups and reminders tied to the people you prospect, so nothing slips.",
        "cta": "New task",
        "mailbox_note": False,
        "features": [
            ("calendar-check", "Due dates", "Give every follow-up a day. Planned for v0.3."),
            (
                "users",
                "Tied to people",
                "Open a task from the person or company it is about. Planned for v0.3.",
            ),
            (
                "clipboard-list",
                "One list to work through",
                "Everything due today in one place. Planned for v0.3.",
            ),
        ],
        "meanwhile": ("Group the people to follow up with in a list.", "/lists", "Open lists"),
    },
    "deals": {
        "title": "Deals",
        "icon": "handshake",
        "headline": "Deals are coming in v0.3",
        "lead": "A light pipeline to follow opportunities from first contact to a close.",
        "cta": "New deal",
        "mailbox_note": False,
        "features": [
            ("kanban", "Pipeline stages", "Move deals across stages you define. Planned for v0.3."),
            (
                "building-2",
                "Linked to companies",
                "Each deal points to a company you saved. Planned for v0.3.",
            ),
            ("file-text", "Notes", "Keep context next to every deal. Planned for v0.3."),
        ],
        "meanwhile": ("Save the companies you want to work on.", "/companies", "Find companies"),
    },
}


def _engage_route(slug: str) -> None:
    @router.get(f"/{slug}", response_class=HTMLResponse, name=f"engage_{slug}")
    def engage(request: Request) -> Response:
        return render(
            request,
            "shell_engage.html",
            active=slug,
            slug=slug,
            page=ENGAGE[slug],
            roadmap_url=ROADMAP_URL,
            issues_url=ISSUES_URL,
        )


for _slug in ENGAGE:
    _engage_route(_slug)


# ---------------------------------------------------------------- command palette


def _saved_search_href(kind: str, filters_json: str) -> str:
    base = "/people" if kind == "people" else "/companies"
    try:
        query = str(json.loads(filters_json).get("query") or "")
    except Exception:
        query = ""
    pairs = [(k, v) for k, v in parse_qsl(query) if k != "notice"]
    if not pairs:
        return base
    from urllib.parse import urlencode

    return f"{base}?{urlencode(pairs)}"


@router.get("/partials/palette", response_class=HTMLResponse)
def palette_partial(
    request: Request,
    q: str = "",
    store: Any = Depends(get_store),
    search: Any = Depends(get_search),
) -> Response:
    q = q.strip()[:80]
    recent: list[dict[str, str]] = []
    try:
        saved = list(reversed(store.saved_searches()))
    except Exception:
        saved = []
    ql = q.lower()
    for s in saved:
        if ql and ql not in s.name.lower():
            continue
        recent.append(
            {
                "name": s.name,
                "kind": s.kind,
                "href": _saved_search_href(s.kind, s.filters_json),
            }
        )
        if len(recent) >= 4:
            break
    companies: list[Any] = []
    if len(q) >= 2:
        try:
            companies = list(
                search.search_companies(CompanyFilters(text=q), page=1, per_page=5).items
            )[:5]
        except Exception:
            companies = []
    return templates.TemplateResponse(
        request,
        "_shell_palette_results.html",
        {"request": request, "recent": recent, "companies": companies},
    )


# ---------------------------------------------------------------- notifications


def _job_link(job: Any) -> tuple[str, str] | None:
    if job.status != "done":
        return None
    if job.kind == "enrich_csv":
        return f"/api/enrich/{job.id}/result.csv", "Download CSV"
    if job.kind == "find_email":
        pid = (job.payload or {}).get("person_id")
        return (f"/people/{pid}", "View person") if pid else ("/people", "View people")
    cid = (job.payload or {}).get("company_id")
    return (f"/companies/{cid}", "View company") if cid else ("/people", "View people")


def _job_detail(job: Any) -> str:
    if job.status == "failed":
        return (job.error or "The job failed.")[:140]
    res = job.result or {}
    bits = []
    if "people" in res:
        bits.append(f"{res['people']} people found")
    if "emails" in res:
        bits.append(f"{res['emails']} emails")
    return " · ".join(bits)


@router.get("/partials/notifications", response_class=HTMLResponse)
def notifications_partial(request: Request, jobs: Any = Depends(get_jobs)) -> Response:
    try:
        recent = [j for j in jobs.recent(100) if j.status in ("done", "failed")]
    except Exception:
        recent = []

    def when(j: Any) -> datetime:
        return j.finished_at or j.created_at

    recent.sort(key=when, reverse=True)
    items = []
    for j in recent[:20]:
        link = _job_link(j)
        items.append(
            {
                "id": j.id,
                "label": j.label,
                "status": j.status,
                "ts": when(j),
                "detail": _job_detail(j),
                "href": link[0] if link else None,
                "href_label": link[1] if link else None,
            }
        )
    return templates.TemplateResponse(
        request, "_shell_notifications.html", {"request": request, "items": items}
    )
