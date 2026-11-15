"""The workflow engine: subjects, conditions, actions, run log. Owner: tools agent.

A workflow is {trigger, conditions, actions}:

* trigger: {"type": "event", "event": <workflows.EVENTS>}, {"type": "schedule", "frequency":
  "daily"|"weekly", "weekday": 0-6, "hour": 0-23 (UTC), "source": "companies"|"people",
  "query": <search query string>} or {"type": "manual", "list_id": <optional list>}
* conditions: all must hold for a record: {"field", "op", "value"} (see FIELDS)
* actions: run in order for each matching record (see ACTIONS); a failing action never stops
  the others and never raises out of the engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from leadcommons import workflows
from leadcommons.models import SIZE_RANGES, Company, EmailCandidate, Person, Seniority, utcnow
from leadcommons.store.tools import ToolsRepo, Workflow, repo_for

log = logging.getLogger(__name__)

MAX_SUBJECTS = 100  # records handled by one run
MAX_STEPS_LOGGED = 120
MAX_SEEN = 5000
MAX_ROWS = 40  # condition and action rows read from the builder form
DEMO_REASON = "Turned off in the public demo."

# field -> (label, operators, value kind)
FIELDS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "country": ("Country", ("is", "is_not"), "country"),
    "industry": ("Industry (ISIC section letter)", ("is", "is_not"), "text"),
    "size": ("Company size", ("is", "is_not"), "size"),
    "domain": ("Company domain", ("is", "contains"), "text"),
    "title": ("Job title", ("contains", "not_contains"), "text"),
    "seniority": ("Seniority", ("is", "is_not"), "seniority"),
    "email_status": ("Email status", ("is", "is_not"), "status"),
    "list": ("List membership", ("in_list", "not_in_list"), "list"),
    "stage": ("Deal stage", ("is", "is_not"), "text"),
}
OPERATORS = {
    "is": "is",
    "is_not": "is not",
    "contains": "contains",
    "not_contains": "does not contain",
    "in_list": "is in list",
    "not_in_list": "is not in list",
}
# action -> (label, needs: person | company | none, icon)
ACTIONS: dict[str, tuple[str, str, str]] = {
    "add_to_list": ("Add to a list", "any", "list-plus"),
    "enroll": ("Enrol in a sequence", "person", "send"),
    "create_task": ("Create a task", "any", "square-check-big"),
    "find_email": ("Find the email address", "person", "mail-check"),
    "find_people": ("Find people at the company", "company", "user-search"),
    "refresh_insights": ("Refresh company insights", "company", "lightbulb"),
    "webhook": ("Send to a webhook URL", "none", "webhook"),
    "notify": ("Add a notification", "none", "bell"),
}
TASK_KINDS = ("action", "call", "email", "linkedin")


# ---------------------------------------------------------------------- context


@dataclass
class Ctx:
    store: Any
    search: Any
    jobs: Any
    settings: Any
    repo: ToolsRepo
    _lists: dict[str, set[str]] = field(default_factory=dict)

    @property
    def demo(self) -> bool:
        return getattr(self.settings, "mode", "") == "demo"

    def members(self, list_id: str) -> set[str]:
        if list_id not in self._lists:
            try:
                self._lists[list_id] = set(self.store.list_members(list_id))
            except Exception:
                self._lists[list_id] = set()
        return self._lists[list_id]


def make_ctx(app: Any) -> Ctx:
    st = app.state
    return Ctx(st.store, st.search, st.jobs, st.settings, repo_for(st.store))


@dataclass
class Subject:
    """What one run step acts on: a person, a company, or neither (deal and task events)."""

    person: Person | None = None
    company: Company | None = None
    email: EmailCandidate | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def record_id(self) -> str | None:
        if self.person:
            return self.person.id
        return self.company.id if self.company else None

    @property
    def label(self) -> str:
        if self.person:
            return self.person.full_name
        if self.company:
            return self.company.name
        return str(self.payload.get("deal_id") or self.payload.get("task_id") or "event")


def load_company(ctx: Ctx, company_id: str | None, domain: str | None = None) -> Company | None:
    if company_id:
        for src in (ctx.store, ctx.search):
            try:
                found = src.get_company(company_id)
            except Exception:
                found = None
            if found is not None:
                return found
    if domain:
        try:
            page = ctx.search.search_companies(_domain_filters(domain), page=1, per_page=1)
            return page.items[0] if page.items else None
        except Exception:
            return None
    return None


def _domain_filters(domain: str) -> Any:
    from leadcommons.search import CompanyFilters

    return CompanyFilters(domains=[domain.lower()])


def person_subject(ctx: Ctx, person_id: str, payload: dict[str, Any]) -> Subject | None:
    try:
        person = ctx.store.get_person(person_id)
    except Exception:
        person = None
    if person is None:
        return None
    company = load_company(
        ctx, person.company_id or payload.get("company_id"), person.company_domain
    )
    email = None
    try:
        email = ctx.store.best_email(person.id)
    except Exception:
        pass
    if payload.get("address"):
        email = EmailCandidate(
            address=str(payload["address"]),
            status=_status(payload.get("status"))
            or (email.status if email else None)
            or _unknown(),
        )
    return Subject(person=person, company=company, email=email, payload=payload)


def _unknown() -> Any:
    from leadcommons.models import EmailStatus

    return EmailStatus.UNKNOWN


def _status(value: Any) -> Any:
    from leadcommons.models import EmailStatus

    try:
        return EmailStatus(getattr(value, "value", value))
    except ValueError:
        return None


def company_subject(ctx: Ctx, company_id: str, payload: dict[str, Any]) -> Subject | None:
    company = load_company(ctx, company_id)
    return Subject(company=company, payload=payload) if company else None


def build_subjects(ctx: Ctx, event: str, payload: dict[str, Any]) -> list[Subject]:
    """The records an event is about (people, companies), or one bare subject."""
    out: list[Subject] = []
    if event == "added_to_list":
        kind = payload.get("kind")
        for rid in list(payload.get("record_ids") or [])[:MAX_SUBJECTS]:
            s = (
                company_subject(ctx, str(rid), payload)
                if kind == "companies"
                else person_subject(ctx, str(rid), payload)
            )
            if s is not None:
                out.append(s)
        return out
    pid = payload.get("person_id")
    if pid:
        s = person_subject(ctx, str(pid), payload)
        if s is not None:
            return [s]
    return [Subject(payload=payload)]


# ---------------------------------------------------------------------- conditions


def _norm(v: Any) -> str:
    return str(getattr(v, "value", v) or "").strip().lower()


def check_condition(ctx: Ctx, subject: Subject, cond: dict[str, Any]) -> bool:
    fld, op, want = str(cond.get("field")), str(cond.get("op")), _norm(cond.get("value"))
    person, company, email = subject.person, subject.company, subject.email
    if fld == "list":
        rid = subject.record_id
        inside = bool(rid) and rid in ctx.members(str(cond.get("value")))
        return inside if op == "in_list" else not inside
    have = ""
    if fld == "country":
        have = _norm(company.country) if company else ""
        want = _country_code(want)
    elif fld == "industry":
        have = _norm(company.industry) if company else ""
    elif fld == "size":
        have = _norm(company.size_range) if company else ""
    elif fld == "domain":
        have = (
            _norm(company.domain or (person.company_domain if person else ""))
            if (company or person)
            else ""
        )
    elif fld == "title":
        have = _norm(person.title) if person else ""
    elif fld == "seniority":
        have = _norm(person.seniority) if person else ""
    elif fld == "email_status":
        have = _norm(email.status) if email else ""
    elif fld == "stage":
        have = _norm(subject.payload.get("stage"))
    else:
        return False
    if op == "is":
        return bool(have) and have == want
    if op == "is_not":
        return have != want
    if op == "contains":
        return bool(want) and want in have
    if op == "not_contains":
        return want not in have
    return False


def _country_code(value: str) -> str:
    """'france' -> 'fr'; two-letter codes pass through."""
    if len(value) == 2:
        return value
    try:
        from leadcommons.web.pages import COUNTRIES

        for code, name in COUNTRIES.items():
            if name.lower() == value:
                return code.lower()
    except Exception:
        pass
    return value


def conditions_met(ctx: Ctx, subject: Subject, conditions: list[dict[str, Any]]) -> bool:
    for cond in conditions:
        try:
            if not check_condition(ctx, subject, cond):
                return False
        except Exception:
            log.exception("workflow condition %r failed", cond)
            return False
    return True


# ---------------------------------------------------------------------- actions


def fill(text: str, subject: Subject) -> str:
    """{name} {first_name} {title} {company} {domain} {email} in task titles and messages."""
    p, c = subject.person, subject.company
    values = {
        "name": p.full_name if p else (c.name if c else ""),
        "first_name": p.full_name.split(" ")[0] if p else "",
        "title": (p.title or "") if p else "",
        "company": c.name if c else "",
        "domain": (c.domain if c and c.domain else (p.company_domain if p else "")) or "",
        "email": subject.email.address if subject.email else "",
    }
    out = text
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def step(action: dict[str, Any], status: str, detail: str, subject: Subject) -> dict[str, Any]:
    label = ACTIONS.get(str(action.get("type")), (str(action.get("type")),))[0]
    return {
        "action": action.get("type"),
        "label": label,
        "status": status,  # ok | failed | skipped
        "detail": detail,
        "subject": subject.label,
    }


def run_action(ctx: Ctx, subject: Subject, action: dict[str, Any]) -> dict[str, Any]:
    kind = str(action.get("type"))
    spec = ACTIONS.get(kind)
    if spec is None:
        return step(action, "failed", f"Unknown action {kind!r}.", subject)
    needs = spec[1]
    try:
        if needs == "person" and subject.person is None:
            return step(
                action, "skipped", "This step needs a person; the trigger has none.", subject
            )
        if needs == "company" and subject.company is None:
            return step(action, "skipped", "This step needs a company; none is known.", subject)
        if needs == "any" and subject.record_id is None and kind != "create_task":
            return step(action, "skipped", "There is no record to act on.", subject)
        handler = _HANDLERS[kind]
        status, detail = handler(ctx, subject, action)
        return step(action, status, detail, subject)
    except NotImplementedError:
        return step(action, "failed", "This feature is not available in this version yet.", subject)
    except Exception as exc:
        log.warning("workflow action %s failed: %s", kind, exc)
        return step(action, "failed", f"{type(exc).__name__}: {exc}"[:240], subject)


def _a_add_to_list(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    list_id = str(a.get("list_id") or "")
    info = next((li for li in ctx.store.lists() if li.id == list_id), None)
    if info is None:
        return "failed", "The list no longer exists."
    kind = "people" if s.person else "companies"
    noun = "people" if s.person else "companies"
    if info.kind != kind:
        return (
            "failed",
            f"{info.name} is a {info.kind} list; this record belongs in a {noun} list.",
        )
    if s.person is None and s.company is not None:
        ctx.store.save_company(s.company)
    ctx.store.add_to_list(list_id, [s.record_id or ""])
    ctx._lists.pop(list_id, None)
    return "ok", f"Added to {info.name}."


def _a_enroll(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    from leadcommons import engage

    res = engage.enroll(ctx.store, str(a.get("sequence_id") or ""), [s.person.id])  # type: ignore[union-attr]
    n, k = int(res.get("enrolled", 0)), int(res.get("skipped", 0))
    if n:
        return "ok", "Enrolled in the sequence."
    return "skipped", f"Not enrolled ({k} skipped: no usable email, opted out or already active)."


def _a_create_task(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    from leadcommons import engage

    title = fill(str(a.get("title") or "Follow up with {name}"), s).strip() or "Follow up"
    days = _int(a.get("due_days"), 0)
    kind = a.get("kind") if a.get("kind") in TASK_KINDS else "action"
    due = utcnow() + timedelta(days=days) if days else None
    cid = s.company.id if s.company else None
    tid = engage.create_task(
        ctx.store, title, person_id=s.person.id if s.person else None, company_id=cid,
        due=due, kind=str(kind),
    )  # fmt: skip
    return "ok", f"Created task {title!r} ({tid})."


def _a_find_email(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    if ctx.demo:
        return "skipped", DEMO_REASON
    p = s.person
    assert p is not None
    if not p.company_domain:
        return "failed", "The person has no company domain."
    ctx.jobs.submit("find_email", {"person_id": p.id}, f"Find email for {p.full_name}")
    return "ok", "Started a find-email job."


def _a_find_people(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    if ctx.demo:
        return "skipped", DEMO_REASON
    c = s.company
    assert c is not None
    if not c.domain:
        return "failed", f"{c.name} has no known domain."
    ctx.jobs.submit(
        "find_people", {"domain": c.domain, "company_id": c.id}, f"Find people at {c.domain}"
    )
    return "ok", f"Started finding people at {c.domain}."


def _a_refresh_insights(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    if ctx.demo:
        return "skipped", DEMO_REASON
    c = s.company
    assert c is not None
    if not c.domain:
        return "failed", f"{c.name} has no known domain."
    ctx.jobs.submit(
        "company_insights",
        {"company_id": c.id, "domain": c.domain},
        f"Refresh insights for {c.domain}",
    )  # type: ignore[arg-type]
    return "ok", "Started an insights refresh."


def _a_webhook(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    from leadcommons.workflows import webhooks

    if ctx.demo:
        return "skipped", DEMO_REASON
    url = str(a.get("url") or "").strip()
    err = webhooks.check_url(url)
    if err:
        return "failed", err
    body = webhooks.envelope("workflow", subject_data(s), workflow=str(a.get("_workflow") or ""))
    res = webhooks.post_json(url, body)
    if res.ok:
        return "ok", f"POST {_host(url)} answered {res.status_code}."
    return "failed", f"POST {_host(url)} failed: {res.error}"


def _a_notify(ctx: Ctx, s: Subject, a: dict[str, Any]) -> tuple[str, str]:
    text = fill(str(a.get("message") or "Workflow notification for {name}"), s).strip()
    href = None
    if s.person:
        href = f"/people/{s.person.id}"
    elif s.company:
        href = f"/companies/{s.company.id}"
    ctx.repo.add_notification(text, "", href, "workflow")
    return "ok", "Added a notification."


_HANDLERS = {
    "add_to_list": _a_add_to_list,
    "enroll": _a_enroll,
    "create_task": _a_create_task,
    "find_email": _a_find_email,
    "find_people": _a_find_people,
    "refresh_insights": _a_refresh_insights,
    "webhook": _a_webhook,
    "notify": _a_notify,
}


def _host(url: str) -> str:
    return urlparse(url).netloc or url


def _int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def subject_data(s: Subject) -> dict[str, Any]:
    out: dict[str, Any] = dict(s.payload)
    if s.person:
        out["person"] = {
            "id": s.person.id,
            "name": s.person.full_name,
            "title": s.person.title,
            "seniority": _norm(s.person.seniority) or None,
            "company_domain": s.person.company_domain,
        }
    if s.company:
        out["company"] = {
            "id": s.company.id,
            "name": s.company.name,
            "domain": s.company.domain,
            "country": s.company.country,
            "size": s.company.size_range,
        }
    if s.email:
        out["email"] = {"address": s.email.address, "status": _norm(s.email.status)}
    return out


# ---------------------------------------------------------------------- running


def execute(
    ctx: Ctx, wf: Workflow, trigger: str, subjects: list[Subject], *, note: str = ""
) -> dict[str, Any]:
    """Run wf's actions on `subjects` (conditions already applied). Logs the run, returns it."""
    started = utcnow()
    steps: list[dict[str, Any]] = []
    total = failed = 0
    actions = [{**a, "_workflow": wf.name} for a in wf.actions]
    with workflows.running():
        for subject in subjects:
            for action in actions:
                st = run_action(ctx, subject, action)
                total += 1
                failed += st["status"] == "failed"
                if len(steps) < MAX_STEPS_LOGGED:
                    steps.append(st)
            _log_activity(ctx, wf, subject)
    if len(steps) < total:
        steps.append(
            {"action": None, "label": "More", "status": "skipped", "subject": "",
             "detail": f"{total - len(steps)} more steps are not listed."}
        )  # fmt: skip
    if not subjects:
        status, summary = "skipped", note or "No record matched the conditions."
    elif not total:
        status, summary = "skipped", "The workflow has no actions."
    elif failed == 0:
        status = "success"
        summary = f"{len(subjects)} record{'s' if len(subjects) != 1 else ''}, {total} steps done."
    elif failed == total:
        status, summary = "failed", f"All {total} steps failed."
    else:
        status, summary = "partial", f"{total - failed} of {total} steps done, {failed} failed."
    if note and subjects:
        summary = f"{summary} {note}"
    rid = ctx.repo.add_run(wf.id, trigger, status, summary, steps, started)
    return {"id": rid, "status": status, "summary": summary, "steps": steps}


def _log_activity(ctx: Ctx, wf: Workflow, subject: Subject) -> None:
    engine = getattr(ctx.store, "engine", None)
    if engine is None or not (subject.person or subject.company):
        return
    try:
        from leadcommons.store.pipeline import ActivityRepo

        ActivityRepo(engine).add(
            "workflow",
            f"Workflow “{wf.name}” ran.",
            person_id=subject.person.id if subject.person else None,
            company_id=subject.company.id if subject.company else None,
            meta={"workflow_id": wf.id},
        )
    except Exception:
        log.debug("could not log workflow activity", exc_info=True)


def dispatch(app: Any, event: str, payload: dict[str, Any]) -> None:
    """Handle one event: webhooks first (queued), then every enabled workflow listening."""
    ctx = make_ctx(app)
    listeners = ctx.repo.enabled_for_event(event)
    hooks = ctx.repo.webhooks_for_event(event)
    if not listeners and not hooks:
        return
    subjects = build_subjects(ctx, event, payload)
    if hooks and not ctx.demo:
        from leadcommons.workflows import webhooks

        data = subject_data(subjects[0]) if subjects else dict(payload)
        webhooks.fan_out(ctx, hooks, event, data)
    for wf in listeners:
        try:
            matched = [s for s in subjects if conditions_met(ctx, s, wf.conditions)]
            if matched:
                execute(ctx, wf, event, matched)
        except Exception:
            log.exception("workflow %s failed on %s", wf.id, event)


def sample_subjects(ctx: Ctx, wf: Workflow) -> tuple[list[Subject], str]:
    """Records for 'Run now': a list (manual trigger), a saved search (schedule), or the most
    recent people."""
    trig = wf.trigger
    if trig.get("type") == "schedule":
        from leadcommons.workflows import searches

        found = searches.run_query(
            ctx.search, ctx.store, str(trig.get("source") or "companies"),
            str(trig.get("query") or ""), MAX_SUBJECTS, seen=set(),
        )  # fmt: skip
        return [_record_subject(ctx, r) for r in found], "Ran the saved search."
    if trig.get("type") == "manual" and trig.get("list_id"):
        ids = list(ctx.members(str(trig["list_id"])))[:MAX_SUBJECTS]
        info = next((li for li in ctx.store.lists() if li.id == trig["list_id"]), None)
        make = company_subject if info and info.kind == "companies" else person_subject
        subs = [s for rid in ids if (s := make(ctx, rid, {})) is not None]
        return subs, "Ran on the list."
    from leadcommons.search import PeopleFilters

    people = ctx.store.search_people(PeopleFilters(), page=1, per_page=25).items
    subs = [s for p in people if (s := person_subject(ctx, p.id, {})) is not None]
    return subs, "Ran on your 25 most recent people."


def _record_subject(ctx: Ctx, record: Any) -> Subject:
    if isinstance(record, Person):
        s = person_subject(ctx, record.id, {})
        return s or Subject(person=record)
    return Subject(company=record)


def run_now(app: Any, wf_id: str) -> dict[str, Any]:
    """Manual run of one workflow (also for disabled ones). Conditions still apply."""
    ctx = make_ctx(app)
    wf = ctx.repo.get_workflow(wf_id)
    if wf is None:
        raise KeyError(wf_id)
    try:
        subjects, note = sample_subjects(ctx, wf)
    except Exception as exc:
        log.exception("sample subjects failed")
        rid = ctx.repo.add_run(
            wf.id, "manual", "failed", f"Could not choose records: {exc}"[:300], [], utcnow()
        )
        return {"id": rid, "status": "failed", "summary": str(exc), "steps": []}
    matched = [s for s in subjects if conditions_met(ctx, s, wf.conditions)]
    return execute(ctx, wf, "manual", matched, note=note)


# ---------------------------------------------------------------------- scheduling


def next_run(trigger: dict[str, Any], after: datetime | None = None) -> datetime | None:
    """Next UTC time a schedule trigger is due after `after`; None for other triggers."""
    if trigger.get("type") != "schedule":
        return None
    after = after or utcnow()
    hour = min(23, _int(trigger.get("hour"), 8))
    weekly = trigger.get("frequency") == "weekly"
    cand = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if weekly:
        cand += timedelta(days=(min(6, _int(trigger.get("weekday"), 0)) - cand.weekday()) % 7)
    if cand <= after:
        cand += timedelta(days=7 if weekly else 1)
    return cand


def run_scheduled(ctx: Ctx, wf: Workflow, now: datetime | None = None) -> dict[str, Any] | None:
    """One scheduled run: act on matches of the saved search not handled before."""
    from leadcommons.workflows import searches

    now = now or utcnow()
    trig = wf.trigger
    state = dict(wf.state)
    seen: list[str] = list(state.get("seen") or [])
    # advance the clock first so a slow or failing run is never repeated in a tight loop
    ctx.repo.save_state(wf.id, state, next_run(trig, now))
    if ctx.demo:
        ctx.repo.add_run(wf.id, "schedule", "skipped", DEMO_REASON, [], now)
        return None
    found = searches.run_query(
        ctx.search, ctx.store, str(trig.get("source") or "companies"),
        str(trig.get("query") or ""), MAX_SUBJECTS, seen=set(seen),
    )  # fmt: skip
    subjects = [_record_subject(ctx, r) for r in found]
    matched = [s for s in subjects if conditions_met(ctx, s, wf.conditions)]
    # every record we looked at counts as handled, matching or not
    seen.extend(r.id for r in found)
    state["seen"] = seen[-MAX_SEEN:]
    ctx.repo.save_state(wf.id, state, next_run(trig, now))
    note = f"{len(found)} new record{'s' if len(found) != 1 else ''} in the saved search."
    return execute(ctx, wf, "schedule", matched, note=note)


def run_due(app: Any, now: datetime | None = None) -> int:
    """Run every scheduled workflow that is due. Returns how many ran. Never raises."""
    n = 0
    try:
        ctx = make_ctx(app)
        for wf in ctx.repo.due_workflows(now or utcnow()):
            try:
                run_scheduled(ctx, wf, now)
                n += 1
            except Exception:
                log.exception("scheduled workflow %s failed", wf.id)
    except Exception:
        log.exception("scheduler pass failed")
    return n


# ---------------------------------------------------------------------- form parsing


def clean_workflow(
    form: Any,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Validate the builder form. Returns (name, trigger, conditions, actions, error)."""
    from leadcommons.models import EmailStatus

    name = str(form.get("name") or "").strip()[:160] or "Untitled workflow"
    ttype = str(form.get("trigger_type") or "event")
    trigger: dict[str, Any]
    if ttype == "schedule":
        source = "people" if form.get("source") == "people" else "companies"
        trigger = {
            "type": "schedule",
            "frequency": "weekly" if form.get("frequency") == "weekly" else "daily",
            "weekday": min(6, _int(form.get("weekday"), 0)),
            "hour": min(23, _int(form.get("hour"), 8)),
            "source": source,
            "query": str(form.get("query") or "").strip().lstrip("?")[:1500],
        }
    elif ttype == "manual":
        trigger = {"type": "manual", "list_id": str(form.get("manual_list") or "") or None}
    else:
        event = str(form.get("event") or "")
        if event not in workflows.EVENTS:
            return name, {}, [], [], "Choose what starts the workflow."
        trigger = {"type": "event", "event": event}

    conditions: list[dict[str, Any]] = []
    for i in range(MAX_ROWS):
        fld = str(form.get(f"cond_field_{i}") or "")
        op = str(form.get(f"cond_op_{i}") or "")
        val = str(form.get(f"cond_value_{i}") or "").strip()
        if fld not in FIELDS or op not in FIELDS[fld][1] or not val:
            continue
        if fld == "seniority" and val not in {s.value for s in Seniority}:
            continue
        if fld == "email_status" and val not in {s.value for s in EmailStatus}:
            continue
        if fld == "size" and val not in SIZE_RANGES:
            continue
        conditions.append({"field": fld, "op": op, "value": val[:120]})

    actions: list[dict[str, Any]] = []
    for i in range(MAX_ROWS):
        kind = str(form.get(f"action_type_{i}") or "")
        if kind not in ACTIONS:
            continue
        a: dict[str, Any] = {"type": kind}
        arg = str(form.get(f"action_arg_{i}") or "").strip()
        extra = str(form.get(f"action_extra_{i}") or "").strip()
        if kind == "add_to_list":
            if not arg:
                return name, trigger, conditions, [], "Pick a list for the “Add to a list” step."
            a["list_id"] = arg
        elif kind == "enroll":
            if not arg:
                return name, trigger, conditions, [], "Pick a sequence for the “Enrol” step."
            a["sequence_id"] = arg
        elif kind == "create_task":
            a.update(
                title=arg[:200] or "Follow up with {name}",
                due_days=_int(extra, 2),
                kind=str(form.get(f"action_kind_{i}") or "action"),
            )
        elif kind == "webhook":
            from leadcommons.workflows import webhooks

            err = webhooks.check_url(arg)
            if err:
                return name, trigger, conditions, [], f"Webhook step: {err}"
            a["url"] = arg
        elif kind == "notify":
            a["message"] = arg[:300] or "Workflow notification for {name}"
        actions.append(a)
    if not actions:
        return name, trigger, conditions, [], "Add at least one action."
    return name, trigger, conditions, actions, None
