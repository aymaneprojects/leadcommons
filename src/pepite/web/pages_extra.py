# ruff: noqa: B008  (FastAPI Depends defaults)
"""Shell pages: settings sections, onboarding hub, notifications, palette, engage empty states.

Owner: shell UI agent. Included by create_app before pepite.web.pages, so a route defined
here takes over the same path in pages.py.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode

import dns.exception
import dns.resolver
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from pepite.models import utcnow
from pepite.search import CompanyFilters, PeopleFilters
from pepite.web.deps import get_jobs, get_search, get_store, templates
from pepite.web.pages import (
    clean_suppression,
    csrf,
    done,
    form_values,
    is_htmx,
    load_state,
    render,
    set_state,
)

router = APIRouter()

REPO_URL = "https://github.com/aymaneprojects/leadcommons"
ROADMAP_URL = f"{REPO_URL}#roadmap"
ISSUES_URL = f"{REPO_URL}/issues"
DNS_TIMEOUT = 5.0

# ---------------------------------------------------------------- settings

SETTINGS_SECTIONS = [
    ("overview", "/settings", "Workspace overview", "house"),
    ("account", "/settings/account", "Account", "user"),
    ("mailboxes", "/settings/mailboxes", "Mailboxes", "mail"),
    ("sending", "/settings/sending", "Sending rules", "timer"),
    ("email", "/settings/email-health", "Email setup and health", "mail-check"),
    ("packs", "/settings/data-packs", "Data packs", "database"),
    ("privacy", "/settings/privacy", "Unsubscribes & opt-out", "shield-check"),
    ("deal-stages", "/settings/deal-stages", "Deal stages", "kanban"),
    ("integrations", "/settings/integrations", "Integrations", "plug"),
    ("api", "/settings/api", "API tokens", "key-round"),
    ("about", "/settings/about", "About", "info"),
]
# How the settings menu groups its sections (like a collapsible tree).
SETTINGS_GROUPS = [
    ("", ["overview"]),
    ("Personal", ["account"]),
    ("Outbound", ["mailboxes", "sending", "email", "privacy"]),
    ("Data management", ["packs"]),
    ("Deals", ["deal-stages"]),
    ("Integrations", ["integrations", "api"]),
    ("About", ["about"]),
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
    ctx = email_health_ctx(request)
    return settings_page(
        request,
        "settings.html",
        "overview",
        verdict=ctx["verdict"],
        mailbox_count=len(ctx["mailboxes"]),
    )


PACK_JOB_LABEL = "Download the latest company data"
PACK_DEMO_NOTICE = "/settings/data-packs?notice=Downloads+are+turned+off+in+the+public+demo."


def _pack_job(jobs: Any) -> Any:
    """The newest pack download job, if any (shown on the Data packs page)."""
    recent = getattr(jobs, "recent", None)
    for job in recent(50) if callable(recent) else []:
        if getattr(job, "kind", "") == "download_pack":
            return job
    return None


def fmt_bytes(n: Any) -> str:
    """1234567 -> "1.2 MB" (decimal units, like download sizes elsewhere)."""
    size = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1000 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit in ("B", "KB") else f"{size:,.1f} {unit}"
        size /= 1000
    return f"{size:,.1f} GB"


templates.env.globals.setdefault("fmt_bytes", fmt_bytes)


def _country_names() -> dict[str, str]:
    from pepite.web.search_filters import COUNTRIES

    return dict(COUNTRIES)


def _catalog(settings: Any, *, refresh: bool = False, offline: bool = False) -> Any:
    from pepite.packs import DEFAULT_PACKS_REPO, pack_catalog

    repo = getattr(settings, "packs_repo", None) or DEFAULT_PACKS_REPO
    try:
        return pack_catalog(settings.data_dir, repo, refresh=refresh, offline=offline)
    except Exception as exc:  # never break the settings page on this
        from pepite.packs import PackCatalog

        return PackCatalog(repo=repo, error=f"Could not read the list of countries: {exc}"[:200])


def _packs_ctx(request: Request, catalog: Any) -> dict[str, Any]:
    from pepite.packs import default_country

    settings = request.app.state.settings
    return {
        "catalog": catalog,
        "pack_job": _pack_job(request.app.state.jobs),
        "downloads_on": bool(getattr(settings, "enrichment_enabled", True)),
        "country_names": _country_names(),
        "own_country": default_country(),
    }


@router.get("/settings/data-packs", response_class=HTMLResponse)
def settings_packs(request: Request) -> Response:
    from pepite.packs import has_own_pack

    settings = request.app.state.settings
    # The page never waits for GitHub: a recent saved list is shown at once, otherwise the
    # country table loads itself (htmx) from /settings/data-packs/countries.
    check = request.query_params.get("check") == "1"
    catalog = _catalog(settings, refresh=check, offline=not check)
    if catalog.error and not catalog.countries and not catalog.legacy and not check:
        catalog = None
    first = None
    job = _pack_job(request.app.state.jobs)
    if job is not None and job.status == "done" and (job.payload or {}).get("first_run"):
        names = _country_names()
        done = [names.get(cc, cc) for cc in (job.result or {}).get("countries") or []]
        first = ", ".join(done) or None
    needs = getattr(request.app.state.search, "needs_index", None)
    return settings_page(
        request,
        "shell_settings_packs.html",
        "packs",
        own_pack=has_own_pack(settings.data_dir),
        needs_index=list(needs) if isinstance(needs, list) else [],
        first_run_country=first,
        **_packs_ctx(request, catalog),
    )


@router.get("/settings/data-packs/countries", response_class=HTMLResponse)
def settings_packs_countries(request: Request) -> Response:
    """The country picker alone (htmx), read from GitHub when the saved list is old."""
    settings = request.app.state.settings
    refresh = request.query_params.get("refresh") == "1"
    catalog = _catalog(settings, refresh=refresh)
    return templates.TemplateResponse(
        request, "_pack_countries.html", {"request": request, **_packs_ctx(request, catalog)}
    )


@router.post("/settings/data-packs/countries")
async def settings_packs_countries_post(
    request: Request, _: None = Depends(csrf), jobs: Any = Depends(get_jobs)
) -> Response:
    """Download, update or remove countries (a background job; search switches when done)."""
    if not getattr(request.app.state.settings, "enrichment_enabled", True):
        return RedirectResponse(PACK_DEMO_NOTICE, status_code=303)
    form = await request.form()
    action = str(form.get("action") or "")
    names = _country_names()
    countries = [
        cc
        for cc in dict.fromkeys(str(v).strip().upper() for v in form.getlist("country"))
        if re.fullmatch(r"[A-Z]{2}", cc)
    ]
    if action in ("install", "remove") and not countries:
        return done(request, "Pick a country first.", "/settings/data-packs", "error")
    shown = ", ".join(names.get(cc, cc) for cc in countries)
    if action == "install":
        payload: dict[str, Any] = {"action": "install", "countries": countries}
        label, msg = f"Download company data: {shown}", f"Downloading {shown}."
    elif action == "remove":
        payload = {"action": "remove", "countries": countries}
        label, msg = f"Remove company data: {shown}", f"Removing {shown}."
    elif action == "update":
        payload = {"action": "update"}
        label, msg = "Update company data", "Updating the countries that have newer data."
    elif action == "index":
        payload = {"action": "index"}
        label, msg = "Prepare company data for fast search", "Preparing the data for fast search."
    else:
        return done(request, "Unknown action.", "/settings/data-packs", "error")
    jobs.submit("download_pack", payload, label)
    return RedirectResponse(
        "/settings/data-packs?"
        + urlencode({"notice": f"{msg} Search switches to it when the job is done."}),
        status_code=303,
    )


@router.post("/settings/data-packs/download")
def settings_packs_download(request: Request, jobs: Any = Depends(get_jobs)) -> Response:
    """Start a background download of the newest published data (the installed countries,
    or the user's own country on a first download); search switches when done."""
    if not getattr(request.app.state.settings, "enrichment_enabled", True):
        return RedirectResponse(PACK_DEMO_NOTICE, status_code=303)
    jobs.submit("download_pack", {"action": "latest"}, PACK_JOB_LABEL)
    return RedirectResponse(
        "/settings/data-packs?notice=Downloading+the+latest+company+data.+"
        "Search+switches+to+it+when+the+download+is+done.",
        status_code=303,
    )


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


# DKIM cannot be discovered from the domain alone, so without a selector we try the names the big
# mail providers use. A miss is reported as "not found", never as a failure.
COMMON_DKIM_SELECTORS = (
    "default",
    "google",
    "selector1",
    "selector2",
    "k1",
    "s1",
    "s2",
    "mail",
    "dkim",
    "smtp",
)
HEALTH_TABS = ("overview", "domains", "policies")
SELF_HOST_DOC = f"{REPO_URL}/blob/main/docs/server.md"
SETUP_PROMPT = "Help me set up a mailbox and authenticate my sending domain"
LLM_HINT = (
    "Needs a language model. Start Pépite with PEPITE_LLM_URL and "
    "PEPITE_LLM_MODEL set to turn this on."
)
DEMO_AI_HINT = "The language model is turned off in the public demo."


def llm_available(settings: Any) -> bool:
    from pepite.ai.llm import llm_config

    return settings.mode != "demo" and llm_config() is not None


def ai_unavailable_reason(settings: Any) -> str:
    """Empty when the Assistant can use a language model, else the sentence for the tooltip."""
    if settings.mode == "demo":
        return DEMO_AI_HINT
    return "" if llm_available(settings) else LLM_HINT


def run_domain_health(domain: str, selector: str = "") -> list[dict[str, Any]]:
    """MX, SPF, DMARC and DKIM for one sending domain, with the existing check code.

    With a selector DKIM is checked as is. Without one the common selector names are tried; if
    none is published the DKIM row says so with state "unknown" (left out of the verdict).
    """
    if selector:
        return run_dns_checks(domain, selector)
    jobs: dict[str, tuple[str, str]] = {
        "mx": (domain, "MX"),
        "spf": (domain, "TXT"),
        "dmarc": (f"_dmarc.{domain}", "TXT"),
    }
    for sel in COMMON_DKIM_SELECTORS:
        jobs[f"dkim:{sel}"] = (f"{sel}._domainkey.{domain}", "TXT")
    with ThreadPoolExecutor(max_workers=min(len(jobs), 8)) as pool:
        futures = {k: pool.submit(_resolve, *v) for k, v in jobs.items()}
        found = {k: f.result() for k, f in futures.items()}
    rows = [_check_mx(found["mx"]), _check_spf(found["spf"]), _check_dmarc(found["dmarc"])]
    dkim_rows = [_check_dkim(found[f"dkim:{sel}"], sel) for sel in COMMON_DKIM_SELECTORS]
    hit = next((r for r in dkim_rows if r["state"] == "ok"), None)
    if hit is not None:
        rows.append(hit)
    else:
        broken = all(r["state"] == "error" for r in dkim_rows)
        rows.append(
            {
                "key": "dkim",
                "label": "DKIM",
                "what": "A public key receivers use to verify signed mail.",
                "state": "error" if broken else "unknown",
                "records": [],
                "summary": (
                    dkim_rows[0]["summary"]
                    if broken
                    else "No DKIM key found under the usual selector names."
                ),
                "hint": "Check the domain with your DKIM selector to be sure.",
            }
        )
    return rows


def _sending_domains(store: Any) -> tuple[list[Any], list[str]]:
    """The mailboxes through the engage store, and the distinct domains they send from."""
    from pepite.store.engage import repo_for as engage_repo

    try:
        boxes = list(engage_repo(store).mailboxes())
    except Exception:
        boxes = []
    domains: list[str] = []
    for m in boxes:
        d = clean_domain(m.from_email or "")
        if d and d not in domains:
            domains.append(d)
    return boxes, domains


def health_verdict(domains: list[str], saved: dict[str, Any]) -> dict[str, str]:
    """Overall deliverability from the saved checks: not set up, not checked, needs work, good."""
    if not domains:
        return {
            "key": "none",
            "label": "Not set up",
            "tip": "Connect a mailbox and run a check to see how your domain is set up.",
        }
    states: list[str] = []
    for d in domains:
        for c in (saved.get(d) or {}).get("checks", []):
            if c.get("state") != "unknown":
                states.append(str(c.get("state")))
    if not states or all(st == "error" for st in states):
        return {
            "key": "unchecked",
            "label": "Not checked yet",
            "tip": "Press Run a check to look up MX, SPF, DKIM and DMARC for your sending domains.",
        }
    if any(st in ("fail", "warn") for st in states):
        return {
            "key": "work",
            "label": "Needs work",
            "tip": "At least one DNS record is missing or worth reviewing. See the Domains tab.",
        }
    return {
        "key": "good",
        "label": "Good",
        "tip": "MX, SPF and DMARC pass for every sending domain.",
    }


def email_health_ctx(request: Request) -> dict[str, Any]:
    """Mailboxes, domains, saved checks and the verdict, for the page and the overview card."""
    store = request.app.state.store
    boxes, domains = _sending_domains(store)
    saved = load_state(request.app.state.settings).get("email_health") or {}
    if not isinstance(saved, dict):
        saved = {}
    rows = []
    for d in domains:
        info = saved.get(d) or {}
        rows.append(
            {
                "domain": d,
                "selector": str(info.get("selector") or ""),
                "checked_at": _parse_when(info.get("at")),
                "checks": list(info.get("checks") or []),
                "boxes": [m for m in boxes if clean_domain(m.from_email or "") == d],
            }
        )
    return {
        "mailboxes": boxes,
        "domain_rows": rows,
        "verdict": health_verdict(domains, saved),
    }


def _parse_when(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


@router.post("/settings/email-health/run")
async def email_health_run(request: Request, _: None = Depends(csrf)) -> Response:
    """Re-run the DNS checks for every sending domain (or one) and keep the result."""
    form = await form_values(request)
    back = "/settings/email-health?tab=domains"
    _, domains = _sending_domains(request.app.state.store)
    only = clean_domain(str(form.get("domain") or "")) if form.get("domain") else None
    targets = [only] if only and only in domains else domains
    if not targets:
        return done(
            request, "Connect a mailbox first. Then there is a domain to check.", back, "error"
        )
    settings = request.app.state.settings
    saved = load_state(settings).get("email_health") or {}
    if not isinstance(saved, dict):
        saved = {}
    selectors = {str(k): str(v) for k, v in (saved.get("_selectors") or {}).items()}
    for d in targets:
        sel = str(form.get("selector") or "").strip().lower() if len(targets) == 1 else ""
        if sel and not _SELECTOR_RE.match(sel):
            return done(request, "That DKIM selector is not valid.", back, "error")
        sel = sel or selectors.get(d, "")
        checks = run_domain_health(d, sel)
        saved[d] = {
            "at": utcnow().isoformat(),
            "selector": sel,
            "checks": [
                {"key": c["key"], "label": c["label"], "state": c["state"], "summary": c["summary"]}
                for c in checks
            ],
        }
        if sel:
            selectors[d] = sel
    saved["_selectors"] = selectors
    set_state(settings, email_health=saved, dns_checked=True)
    n = len(targets)
    return done(request, f"Checked {n} domain{'s' if n != 1 else ''}.", back)


@router.get("/settings/email-health", response_class=HTMLResponse)
def settings_email(request: Request) -> Response:
    raw = (request.query_params.get("domain") or "").strip()
    selector = (request.query_params.get("selector") or "").strip().lower()
    tab = request.query_params.get("tab") or "overview"
    if tab not in HEALTH_TABS:
        tab = "overview"
    error: str | None = None
    domain: str | None = None
    checks: list[dict[str, Any]] = []
    if selector and not _SELECTOR_RE.match(selector):
        error = "The DKIM selector may only contain letters, digits, dots, dashes and underscores."
        selector = ""
    if raw:
        tab = "overview"
        domain = clean_domain(raw)
        if domain is None:
            error = "That does not look like a domain name. Try something like example.com."
        elif error is None:
            checks = run_dns_checks(domain, selector)
            set_state(request.app.state.settings, dns_checked=True)
    summary = {
        s: sum(1 for c in checks if c["state"] == s) for s in ("ok", "warn", "fail", "error")
    }
    cfg: dict[str, Any] = {}
    if tab == "policies":
        try:
            from pepite.store.engage import repo_for as engage_repo

            cfg = engage_repo(request.app.state.store).get_settings()
        except Exception:
            cfg = {}
    settings = request.app.state.settings
    return settings_page(
        request,
        "shell_settings_email.html",
        "email",
        tab=tab,
        raw_domain=raw,
        selector=selector,
        domain=domain,
        checks=checks,
        summary=summary,
        error=error,
        roadmap_url=ROADMAP_URL,
        policy=cfg,
        ai_reason=ai_unavailable_reason(settings),
        setup_prompt=SETUP_PROMPT,
        **email_health_ctx(request),
    )


@router.get("/email-health")
def email_health_redirect() -> Response:
    return RedirectResponse("/settings/email-health", status_code=302)


# ---------------------------------------------------------------- onboarding hub


def _count(db: Any, table: str, where: str = "") -> int:
    return int(db.count(table, where) or 0)


def hub_groups(request: Request) -> list[dict[str, Any]]:
    """The getting-started checklist: groups of tasks, each with one real action."""
    from pepite.store.tools import repo_for
    from pepite.workflows.stats import Db

    app = request.app
    store, jobs, settings = app.state.store, app.state.jobs, app.state.settings
    state = load_state(settings)
    try:
        recent = jobs.recent(200)
    except Exception:
        recent = []
    db = Db(repo_for(store).engine)
    try:
        has_people = any(j.kind == "find_people" for j in recent) or (
            store.search_people(PeopleFilters(), page=1, per_page=1).total > 0
        )
    except Exception:
        has_people = False
    try:
        has_email = _count(db, "emails") > 0
        if not has_email and not db.has("emails"):
            has_email = bool(
                any(
                    store.best_email(p.id)
                    for p in store.search_people(PeopleFilters(), 1, 50).items
                )
            )
    except Exception:
        has_email = False
    try:
        saved = sorted(store.saved_company_ids())
    except Exception:
        saved = []
    phone_done = any(j.kind == "company_insights" and j.status == "done" for j in recent)
    if not phone_done and db.has("company_insights"):
        phone_done = any(
            '"number"' in str(r[0]) or '"phones": [{' in str(r[0])
            for r in db.rows("SELECT data FROM company_insights LIMIT 200")
        )
    phone_href = f"/companies/{saved[0]}" if saved else "/companies"
    try:
        lists_n, searches_n = len(store.lists()), len(store.saved_searches())
    except Exception:
        lists_n = searches_n = 0
    sent_done = _count(db, "engage_messages", "direction = 'out' AND status = 'sent'") > 0
    wf_n = _count(db, "workflows")
    T = lambda key, done, verb, text, label, href: {  # noqa: E731
        "key": key,
        "done": bool(done),
        "verb": verb,
        "text": text,
        "label": label,
        "href": href,
    }
    groups = [
        {
            "title": "Find the people you want to reach",
            "desc": "Find people and companies that match your ideal customer profile.",
            "tasks": [
                T(
                    "save-company",
                    bool(saved),
                    "Save a company",
                    "to find the right people inside it",
                    "Save a company",
                    "/companies",
                ),
                T(
                    "find-people",
                    has_people,
                    "Find people",
                    "at a company you saved",
                    "Find people",
                    "/companies",
                ),
                T(
                    "find-email",
                    has_email,
                    "Find an email",
                    "for a person on your list",
                    "Find emails",
                    "/people",
                ),
                T(
                    "company-phone",
                    phone_done,
                    "Find a company phone number",
                    "to reach a business by phone",
                    "Find numbers",
                    phone_href,
                ),
                T(
                    "create-list",
                    lists_n > 0,
                    "Create a list",
                    "to keep your prospects organised",
                    "Create a list",
                    "/lists?create=people",
                ),
                T(
                    "save-search",
                    searches_n > 0,
                    "Save your search",
                    "and reuse it, or let a workflow watch it",
                    "Search companies",
                    "/companies",
                ),
            ],
        },
        {
            "title": "Prepare your mailbox for sending",
            "desc": "Make sure your emails land in the inbox.",
            "tasks": [
                T(
                    "dns-check",
                    state.get("dns_checked"),
                    "Check your domain",
                    "for SPF, DKIM and DMARC",
                    "Run a check",
                    "/settings/email-health",
                ),
                T(
                    "mailbox",
                    _count(db, "engage_mailboxes") > 0,
                    "Connect a mailbox",
                    "you already own to send from",
                    "Add a mailbox",
                    "/settings/mailboxes",
                ),
                T(
                    "sending-limits",
                    state.get("sending_visited") or _count(db, "engage_settings") > 0,
                    "Set your sending limits",
                    "so you ramp up safely",
                    "Open sending",
                    "/settings/sending",
                ),
            ],
        },
        {
            "title": "Scale your outreach",
            "desc": "Send personalised emails at scale and automate the routine.",
            "tasks": [
                T(
                    "sequence",
                    _count(db, "engage_sequences") > 0,
                    "Create a sequence",
                    "of emails and follow-up steps",
                    "New sequence",
                    "/sequences",
                ),
                T(
                    "first-email",
                    sent_done,
                    "Send your first email",
                    "from a sequence or the compose window",
                    "Open emails",
                    "/emails",
                ),
                T(
                    "workflow",
                    wf_n > 0,
                    "Automate a step",
                    "with a workflow, for example adding new matches to a list",
                    "New workflow",
                    "/workflows/new",
                ),
            ],
        },
        {
            "title": "Book meetings from your outreach",
            "desc": "Make it easy to follow up and book time.",
            "tasks": [
                T(
                    "task",
                    _count(db, "engage_tasks") > 0,
                    "Create a task",
                    "so no follow-up slips",
                    "New task",
                    "/tasks",
                ),
                T(
                    "meeting",
                    _count(db, "meetings") > 0,
                    "Log a meeting",
                    "with a prospect",
                    "Open meetings",
                    "/meetings",
                ),
                T(
                    "deal",
                    _count(db, "deals") > 0,
                    "Create a deal",
                    "to follow an opportunity",
                    "Open deals",
                    "/deals",
                ),
            ],
        },
        {
            "title": "Bring your own data, and your own tools",
            "desc": "Import a file, export your work, or connect an AI assistant.",
            "tasks": [
                T(
                    "csv-enrich",
                    any(j.kind == "enrich_csv" for j in recent) or state.get("enriched"),
                    "Try CSV enrichment",
                    "with a file of names and companies",
                    "Enrich a CSV",
                    "/enrich",
                ),
                T(
                    "export-list",
                    state.get("exported"),
                    "Export a list",
                    "as a CSV file",
                    "Open lists",
                    "/lists",
                ),
                T(
                    "connect-ai",
                    state.get("api_token_created") or state.get("mcp_card_dismissed"),
                    "Connect an AI assistant or the API",
                    "with a token or the MCP server",
                    "Open integrations",
                    "/settings/integrations",
                ),
            ],
        },
    ]
    first = next((t for g in groups for t in g["tasks"] if not t["done"]), None)
    for g in groups:
        for t in g["tasks"]:
            t["primary"] = t is first
        g["done"] = sum(1 for t in g["tasks"] if t["done"])
    return groups


def hub_stats(request: Request) -> tuple[int, int]:
    """(done, total) of the checklist; computed once per request."""
    cached = getattr(request.state, "hub_stats", None)
    if cached is None:
        try:
            groups = hub_groups(request)
            cached = (
                sum(g["done"] for g in groups),
                sum(len(g["tasks"]) for g in groups),
            )
        except Exception:
            cached = (0, 1)
        request.state.hub_stats = cached
    return cached


templates.env.globals["hub_stats"] = hub_stats
templates.env.globals["self_host_doc"] = SELF_HOST_DOC
templates.env.globals["settings_sections"] = lambda: SETTINGS_SECTIONS
templates.env.globals["settings_groups"] = lambda: SETTINGS_GROUPS


@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_hub(request: Request) -> Response:
    groups = hub_groups(request)
    return render(
        request,
        "shell_onboarding.html",
        active="onboarding",
        groups=groups,
        hub_done=sum(g["done"] for g in groups),
        hub_total=sum(len(g["tasks"]) for g in groups),
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> Response:
    from pepite.store.tools import repo_for

    state = load_state(request.app.state.settings)
    groups = hub_groups(request)
    last = None
    try:
        last = repo_for(request.app.state.store).latest_conversation()
    except Exception:
        last = None
    dismissed = str(state.get("assistant_banner_seen") or "")
    show = bool(last) and dismissed != f"{last.id}:{last.updated_at.isoformat()}"
    return render(
        request,
        "home.html",
        active="home",
        groups=groups,
        last_chat=last if show else None,
        hub_done=sum(g["done"] for g in groups),
        hub_total=sum(len(g["tasks"]) for g in groups),
    )


@router.post("/dismiss-banner")
async def dismiss_banner(request: Request, _: None = Depends(csrf)) -> Response:
    from pepite.store.tools import repo_for

    form = await form_values(request)
    try:
        last = repo_for(request.app.state.store).latest_conversation()
    except Exception:
        last = None
    if last:
        set_state(
            request.app.state.settings,
            assistant_banner_seen=f"{last.id}:{last.updated_at.isoformat()}",
        )
    set_state(request.app.state.settings, banner_dismissed=True)
    if is_htmx(request) or form.get("hx") == "1":
        return Response("", status_code=200)
    return RedirectResponse("/", status_code=303)


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
    flows: list[Any] = []
    people_hits: list[Any] = []
    try:
        from pepite.store.tools import repo_for

        flows = [w for w in repo_for(store).workflows() if not ql or ql in w.name.lower()][:4]
    except Exception:
        flows = []
    if len(q) >= 2:
        try:
            people_hits = list(store.search_people(PeopleFilters(text=q), page=1, per_page=4).items)
        except Exception:
            people_hits = []
    return templates.TemplateResponse(
        request,
        "_shell_palette_results.html",
        {
            "request": request,
            "recent": recent,
            "companies": companies,
            "workflows": flows,
            "people": people_hits,
            "q": q,
        },
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


def _activity_href(a: Any) -> str | None:
    if a.person_id:
        return f"/people/{a.person_id}"
    if a.company_id:
        return f"/companies/{a.company_id}"
    if a.deal_id:
        return f"/deals/{a.deal_id}"
    return None


@router.get("/partials/notifications", response_class=HTMLResponse)
def notifications_partial(request: Request, jobs: Any = Depends(get_jobs)) -> Response:
    from pepite.store.pipeline import ActivityRepo
    from pepite.store.tools import repo_for

    store = request.app.state.store
    try:
        recent = [j for j in jobs.recent(100) if j.status in ("done", "failed")]
    except Exception:
        recent = []

    def when(j: Any) -> datetime:
        return j.finished_at or j.created_at

    recent.sort(key=when, reverse=True)
    items: list[dict[str, Any]] = []
    for j in recent[:20]:
        link = _job_link(j)
        items.append(
            {
                "id": f"job-{j.id}",
                "label": j.label,
                "status": j.status,
                "ts": when(j),
                "detail": _job_detail(j),
                "href": link[0] if link else None,
                "href_label": link[1] if link else None,
                "icon": "circle-check" if j.status == "done" else "circle-alert",
            }
        )
    activities: list[Any] = []
    try:
        repo = repo_for(store)
        for r in repo.runs(None, 10):
            ok = r.status in ("success", "skipped")
            name = (repo.get_workflow(r.workflow_id) or None) and repo.get_workflow(
                r.workflow_id
            ).name
            items.append(
                {
                    "id": f"run-{r.id}",
                    "label": f"Workflow “{name or r.workflow_id}” {'ran' if ok else r.status}",
                    "status": "done" if ok else "failed",
                    "ts": r.finished_at or r.started_at,
                    "detail": r.summary,
                    "href": f"/workflows/{r.workflow_id}#runs",
                    "href_label": "View run",
                    "icon": "workflow",
                }
            )
        for n in repo.notifications(15):
            items.append(
                {
                    "id": f"n-{n.id}",
                    "label": n.title,
                    "status": "done",
                    "ts": n.created_at,
                    "detail": n.body,
                    "href": n.href,
                    "href_label": "Open" if n.href else None,
                    "icon": "bell",
                }
            )
    except Exception:
        pass
    engine = getattr(store, "engine", None)
    if engine is not None:
        try:
            acts = ActivityRepo(engine).list_for(limit=30)
            activities = [
                {
                    "id": f"act-{a.id}",
                    "kind": a.kind,
                    "summary": a.summary,
                    "ts": a.created_at,
                    "href": _activity_href(a),
                }
                for a in acts
            ]
            for a in acts:
                if a.kind == "email_reply":
                    items.append(
                        {
                            "id": f"act-{a.id}",
                            "label": "New reply",
                            "status": "done",
                            "ts": a.created_at,
                            "detail": a.summary,
                            "href": _activity_href(a),
                            "href_label": "Open",
                            "icon": "mail",
                        }
                    )
        except Exception:
            activities = []
    try:
        has_hooks = bool(repo_for(store).webhooks())
    except Exception:
        has_hooks = True
    if not has_hooks:  # a standing notice, always in the "Older" group
        items.append(
            {
                "id": "sys-integrations",
                "label": "No webhook or integration is connected yet.",
                "status": "done",
                "ts": datetime(2000, 1, 1, tzinfo=utcnow().tzinfo),
                "detail": "",
                "href": "/settings/integrations",
                "href_label": "Go to Integrations",
                "icon": "plug",
                "system": True,
            }
        )
    items.sort(key=lambda i: i["ts"], reverse=True)
    return templates.TemplateResponse(
        request,
        "_shell_notifications.html",
        {
            "request": request,
            "items": items[:30],
            "activities": activities,
            "today": utcnow().date(),
        },
    )
