# ruff: noqa: B008, E501  (FastAPI Depends defaults; long inline markup)
"""Tools and automation: assistant, workflows, analytics, integrations, API tokens.

Owner: tools agent. Included after pages_extra/pages_engage/pages_pipeline and before pages.py,
so the fallback pages at the bottom only answer for sections nobody else provides.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from markupsafe import Markup, escape

from pepite import workflows
from pepite.ai.assistant import COMMANDS, STARTERS, Assistant
from pepite.config import Settings
from pepite.models import SIZE_RANGES, EmailStatus, Seniority
from pepite.store.tools import ToolsRepo, repo_for
from pepite.web.deps import get_store, templates
from pepite.web.pages import (
    COUNTRIES,
    csrf,
    done,
    form_values,
    is_htmx,
    load_state,
    render,
    set_state,
    toast_headers,
    with_notice,
)
from pepite.workflows import engine, stats, webhooks

router = APIRouter()

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MCP_DOC = "https://github.com/aymaneprojects/pepite/blob/main/docs/mcp.md"
API_DOCS = "/api/docs"


def repo(request: Request) -> ToolsRepo:
    return repo_for(request.app.state.store)


def _demo(request: Request) -> bool:
    return request.app.state.settings.mode == "demo"


def md(text: str) -> Markup:
    """Tiny safe markdown for assistant text: **bold**, `code`, bullet lines, line breaks."""
    out: list[str] = []
    in_list = False
    for line in str(text).split("\n"):
        s = str(escape(line))
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        if s.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{s}</p>" if s.strip() else "")
    if in_list:
        out.append("</ul>")
    return Markup("".join(out))


templates.env.filters["md"] = md
templates.env.globals.update(
    ev_labels=workflows.EVENT_LABELS,
    weekdays=WEEKDAYS,
    svg_line=stats.svg_line,
    svg_bars=stats.svg_bars,
)


def _state(request: Request) -> dict[str, Any]:
    return load_state(request.app.state.settings)


# ==== assistant


def make_assistant(request: Request) -> Assistant:
    st = request.app.state
    settings = st.settings
    return Assistant(
        st.store,
        st.search,
        st.jobs,
        settings,
        repo_for(st.store),
        state_get=lambda: load_state(settings),
        state_set=lambda **kw: set_state(settings, **kw),
    )


def _assistant_ctx(request: Request, cid: str | None) -> dict[str, Any]:
    r = repo(request)
    messages = r.messages(cid) if cid else []
    settings = request.app.state.settings
    return {
        "cid": cid,
        "messages": messages,
        "history": r.conversations(30),
        "commands": COMMANDS,
        "starters": STARTERS,
        "embed": request.query_params.get("embed") == "1",
        "mcp_dismissed": bool(_state(request).get("mcp_card_dismissed")),
        "llm_on": settings.mode != "demo"
        and bool(__import__("os").environ.get("PEPITE_LLM_URL")),
        "prefill": (request.query_params.get("prompt") or "")[:500],
        "mcp_doc": MCP_DOC,
        "enrich_on": settings.enrichment_enabled,
    }


@router.get("/assistant", response_class=HTMLResponse)
def assistant_new(request: Request) -> Response:
    return render(request, "assistant.html", active="assistant", **_assistant_ctx(request, None))


@router.get("/assistant/{cid}", response_class=HTMLResponse)
def assistant_chat(cid: str, request: Request) -> Response:
    if repo(request).get_conversation(cid) is None:
        return RedirectResponse("/assistant", status_code=303)
    return render(request, "assistant.html", active="assistant", **_assistant_ctx(request, cid))


def _fragment(
    request: Request, cid: str, messages: list[Any], headers: dict[str, str] | None = None
) -> Response:
    resp = templates.TemplateResponse(
        request,
        "_tools_messages.html",
        {
            "request": request,
            "messages": messages,
            "cid": cid,
            "settings": request.app.state.settings,
        },
    )
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    resp.headers["X-Conversation"] = cid
    return resp


@router.post("/assistant/send")
async def assistant_send(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    text = str(form.get("message") or "").strip()
    cid = str(form.get("conversation_id") or "")
    att_text = str(form.get("attachment_text") or "")[:20000]
    attachment = (
        {"name": str(form.get("attachment_name") or "file.txt").strip(), "text": att_text}
        if att_text.strip()
        else None
    )
    r = repo(request)
    if not text and not attachment:
        return done(
            request, "Type a message first.", f"/assistant/{cid}" if cid else "/assistant", "error"
        )
    if not cid or r.get_conversation(cid) is None:
        cid = r.create_conversation()
    asst = make_assistant(request)
    asst.converse(cid, text, attachment)
    if is_htmx(request):
        # the page appends the whole newest exchange: user message and answer
        msgs = r.messages(cid)
        return _fragment(request, cid, msgs[-2:])
    return RedirectResponse(f"/assistant/{cid}", status_code=303)


@router.post("/assistant/{cid}/choose")
async def assistant_choose(cid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    try:
        message_id = int(str(form.get("message_id") or ""))
    except ValueError:
        return done(request, "Pick one of the options.", f"/assistant/{cid}", "error")
    key = str(form.get("choice") or "")
    if not key:
        return done(request, "Pick one of the options first.", f"/assistant/{cid}", "error")
    asst = make_assistant(request)
    new, action = asst.choose(cid, message_id, key)
    if not new:
        return done(request, "That choice was already used.", f"/assistant/{cid}", "error")
    headers: dict[str, str] = {}
    if action.get("type") == "link" and action.get("href"):
        headers["HX-Redirect"] = str(action["href"])
        if not is_htmx(request):
            return RedirectResponse(str(action["href"]), status_code=303)
    elif action.get("type") == "dialog" and action.get("href"):
        headers["HX-Trigger"] = json.dumps({"lc-open": {"href": str(action["href"])}})
        if not is_htmx(request):
            return RedirectResponse(str(action["href"]), status_code=303)
    if is_htmx(request):
        # re-render the options card (now answered) followed by the new messages
        r = repo(request)
        all_msgs = r.messages(cid)
        src = next((m for m in all_msgs if m.id == message_id), None)
        resp = templates.TemplateResponse(
            request,
            "_tools_choice_result.html",
            {
                "request": request,
                "source": src,
                "messages": new,
                "cid": cid,
                "settings": request.app.state.settings,
            },
        )
        for k, v in headers.items():
            resp.headers[k] = v
        return resp
    return RedirectResponse(f"/assistant/{cid}", status_code=303)


@router.post("/assistant/feedback")
async def assistant_feedback(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    try:
        mid = int(str(form.get("message_id") or ""))
    except ValueError:
        return Response(status_code=400)
    rating = 1 if form.get("rating") == "up" else -1
    r = repo(request)
    if r.get_message(mid) is None:
        return Response(status_code=404)
    stored = r.set_feedback(mid, rating)
    if is_htmx(request):
        return templates.TemplateResponse(
            request, "_tools_feedback.html", {"request": request, "m": r.get_message(mid)}
        )
    return Response(json.dumps({"rating": stored}), media_type="application/json")


@router.post("/assistant/{cid}/delete")
def assistant_delete(cid: str, request: Request, _: None = Depends(csrf)) -> Response:
    repo(request).delete_conversation(cid)
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": "/assistant"})
    return RedirectResponse(with_notice("/assistant", "Conversation deleted."), status_code=303)


@router.post("/assistant-card/dismiss")
def mcp_card_dismiss(request: Request, _: None = Depends(csrf)) -> Response:
    set_state(request.app.state.settings, mcp_card_dismissed=True)
    if is_htmx(request):
        return Response("", status_code=200)
    return RedirectResponse("/assistant", status_code=303)


# ==== workflows

TEMPLATES = {
    "list-new-matches": {
        "name": "Add new matches of a search to a list",
        "trigger": {
            "type": "schedule",
            "frequency": "daily",
            "hour": 8,
            "source": "companies",
            "query": "",
        },
        "actions": [{"type": "add_to_list", "list_id": ""}],
    },
    "reply-notify": {
        "name": "Tell me when someone replies",
        "trigger": {"type": "event", "event": "email_replied"},
        "actions": [{"type": "notify", "message": "{name} replied. Open the conversation."}],
    },
    "email-task": {
        "name": "Create a task when an email is found",
        "trigger": {"type": "event", "event": "email_found"},
        "conditions": [{"field": "email_status", "op": "is", "value": "valid"}],
        "actions": [{"type": "create_task", "title": "Email {name} ({company})", "due_days": 1}],
    },
}


def _sequences(request: Request) -> list[dict[str, str]]:
    r = repo(request)
    db = stats.Db(r.engine)
    if not db.has("engage_sequences"):
        return []
    return [
        {"id": str(a), "name": str(b)}
        for a, b in db.rows(
            "SELECT id, name FROM engage_sequences WHERE status != 'archived' ORDER BY name"
        )
    ]


def _saved_searches(store: Any) -> list[dict[str, str]]:
    out = []
    try:
        for s in store.saved_searches():
            try:
                query = str(json.loads(s.filters_json).get("query") or "")
            except Exception:
                query = ""
            out.append({"name": s.name, "kind": s.kind, "query": query})
    except Exception:
        pass
    return out


def _builder_ctx(
    request: Request, draft: dict[str, Any], wf_id: str | None, error: str | None
) -> dict[str, Any]:
    store = request.app.state.store
    try:
        lists = [{"id": li.id, "name": li.name, "kind": li.kind} for li in store.lists()]
    except Exception:
        lists = []
    seniorities = [(s.value, s.value.replace("_", " ")) for s in Seniority]
    config = {
        "fields": {
            k: {"label": v[0], "ops": [[o, engine.OPERATORS[o]] for o in v[1]], "kind": v[2]}
            for k, v in engine.FIELDS.items()
        },
        "actions": {k: {"label": v[0], "needs": v[1]} for k, v in engine.ACTIONS.items()},
        "countries": sorted(COUNTRIES.items(), key=lambda kv: kv[1]),
        "sizes": list(SIZE_RANGES),
        "seniorities": seniorities,
        "statuses": [s.value for s in EmailStatus],
        "lists": lists,
        "sequences": _sequences(request),
        "taskKinds": list(engine.TASK_KINDS),
        "saved": _saved_searches(store),
        "draft": draft,
    }
    return {
        "wf_id": wf_id,
        "draft": draft,
        "error": error,
        "config_json": Markup(json.dumps(config).replace("</", "<\\/")),
        "events": list(workflows.EVENTS),
        "lists": lists,
        "demo": _demo(request),
    }


def _blank() -> dict[str, Any]:
    return {
        "name": "",
        "trigger": {"type": "event", "event": "person_found"},
        "conditions": [],
        "actions": [{"type": "add_to_list"}],
    }


@router.get("/workflows", response_class=HTMLResponse)
def workflows_list(request: Request) -> Response:
    r = repo(request)
    tab = "runs" if request.query_params.get("tab") == "runs" else "workflows"
    store = request.app.state.store
    try:
        list_names = {li.id: li.name for li in store.lists()}
    except Exception:
        list_names = {}
    items = r.workflows()
    names = {w.id: w.name for w in items}
    return render(
        request,
        "tools_workflows.html",
        active="workflows",
        tab=tab,
        items=items,
        runs=r.runs(None, 60) if tab == "runs" else [],
        names=names,
        list_names=list_names,
        templates_=TEMPLATES,
        demo=_demo(request),
        scheduler_on=_scheduler_on(request),
    )


def _scheduler_on(request: Request) -> bool:
    s = request.app.state.settings
    return s.mode != "demo" and not s.sync_jobs


def describe_trigger(t: dict[str, Any]) -> str:
    kind = t.get("type")
    if kind == "event":
        return workflows.EVENT_LABELS.get(str(t.get("event")), str(t.get("event")))
    if kind == "schedule":
        when = f"{t.get('frequency', 'daily')} at {int(t.get('hour', 8)):02d}:00 UTC"
        if t.get("frequency") == "weekly":
            when = f"every {WEEKDAYS[min(6, int(t.get('weekday', 0)))]} at {int(t.get('hour', 8)):02d}:00 UTC"
        return f"Saved {t.get('source', 'companies')} search, {when}"
    return "Run by hand"


def describe_action(a: dict[str, Any], list_names: dict[str, str]) -> str:
    label = engine.ACTIONS.get(str(a.get("type")), (str(a.get("type")),))[0]
    if a.get("type") == "add_to_list":
        return f"{label}: {list_names.get(str(a.get('list_id')), 'unknown list')}"
    return label


def describe_condition(c: dict[str, Any], list_names: dict[str, str]) -> str:
    f = engine.FIELDS.get(str(c.get("field")))
    label = f[0] if f else str(c.get("field"))
    val = (
        list_names.get(str(c["value"]), c["value"]) if c.get("field") == "list" else c.get("value")
    )
    return f"{label} {engine.OPERATORS.get(str(c.get('op')), c.get('op'))} {val}"


templates.env.globals.update(
    describe_trigger=describe_trigger,
    describe_action=describe_action,
    describe_condition=describe_condition,
)


@router.get("/workflows/new", response_class=HTMLResponse)
def workflow_new(
    request: Request, source: str = "", query: str = "", template: str = ""
) -> Response:
    draft = _blank()
    if template in TEMPLATES:
        t = TEMPLATES[template]
        draft = {
            "name": t["name"],
            "trigger": dict(t["trigger"]),
            "conditions": list(t.get("conditions", [])),
            "actions": [dict(a) for a in t["actions"]],
        }
    if source in ("companies", "people"):
        draft["trigger"] = {
            "type": "schedule",
            "frequency": "daily",
            "weekday": 0,
            "hour": 8,
            "source": source,
            "query": query.lstrip("?"),
        }
        draft["name"] = draft["name"] or f"New {source} matching my search"
        if not draft["actions"] or draft["actions"] == [{"type": "add_to_list"}]:
            draft["actions"] = [{"type": "add_to_list"}]
    return render(
        request,
        "tools_workflow_edit.html",
        active="workflows",
        **_builder_ctx(request, draft, None, None),
    )


def _draft_from_form(form: Any) -> dict[str, Any]:
    trig: dict[str, Any] = {"type": str(form.get("trigger_type") or "event")}
    if trig["type"] == "event":
        trig["event"] = str(form.get("event") or "")
    elif trig["type"] == "schedule":
        trig.update(
            frequency=str(form.get("frequency") or "daily"),
            weekday=int(form.get("weekday") or 0),
            hour=int(form.get("hour") or 8),
            source=str(form.get("source") or "companies"),
            query=str(form.get("query") or ""),
        )
    else:
        trig["list_id"] = str(form.get("manual_list") or "") or None
    conds = []
    acts = []
    for i in range(engine.MAX_ROWS):
        if form.get(f"cond_field_{i}"):
            conds.append(
                {
                    "field": form.get(f"cond_field_{i}"),
                    "op": form.get(f"cond_op_{i}"),
                    "value": form.get(f"cond_value_{i}"),
                }
            )
        if form.get(f"action_type_{i}"):
            t = str(form.get(f"action_type_{i}"))
            arg = str(form.get(f"action_arg_{i}") or "")
            a: dict[str, Any] = {"type": t}
            a.update(
                {
                    "add_to_list": {"list_id": arg},
                    "enroll": {"sequence_id": arg},
                    "webhook": {"url": arg},
                    "notify": {"message": arg},
                    "create_task": {
                        "title": arg,
                        "due_days": form.get(f"action_extra_{i}") or 2,
                        "kind": form.get(f"action_kind_{i}") or "action",
                    },
                }.get(t, {})
            )
            acts.append(a)
    return {
        "name": str(form.get("name") or ""),
        "trigger": trig,
        "conditions": conds,
        "actions": acts,
    }


@router.post("/workflows")
async def workflow_create(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    name, trigger, conds, acts, err = engine.clean_workflow(form)
    if err:
        return render(
            request,
            "tools_workflow_edit.html",
            status=400,
            active="workflows",
            **_builder_ctx(request, _draft_from_form(form), None, err),
        )
    enabled = form.get("enabled") != "0"
    wid = repo(request).create_workflow(
        name,
        trigger,
        conds,
        acts,
        enabled=enabled,
        next_run_at=engine.next_run(trigger) if enabled else None,
    )
    return RedirectResponse(with_notice(f"/workflows/{wid}", f"Created “{name}”."), status_code=303)


@router.get("/workflows/{wid}", response_class=HTMLResponse)
def workflow_edit(wid: str, request: Request) -> Response:
    wf = repo(request).get_workflow(wid)
    if wf is None:
        return RedirectResponse(
            with_notice("/workflows", "That workflow does not exist."), status_code=303
        )
    draft = {
        "name": wf.name,
        "trigger": wf.trigger,
        "conditions": wf.conditions,
        "actions": wf.actions,
    }
    ctx = _builder_ctx(request, draft, wid, None)
    return render(
        request,
        "tools_workflow_edit.html",
        active="workflows",
        wf=wf,
        runs=repo(request).runs(wid, 30),
        **ctx,
    )


@router.post("/workflows/{wid}")
async def workflow_update(wid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    wf = r.get_workflow(wid)
    if wf is None:
        return done(request, "That workflow does not exist.", "/workflows", "error")
    form = await form_values(request)
    name, trigger, conds, acts, err = engine.clean_workflow(form)
    if err:
        return render(
            request,
            "tools_workflow_edit.html",
            status=400,
            active="workflows",
            wf=wf,
            runs=r.runs(wid, 30),
            **_builder_ctx(request, _draft_from_form(form), wid, err),
        )
    r.update_workflow(
        wid,
        name=name,
        trigger=trigger,
        conditions=conds,
        actions=acts,
        next_run_at=engine.next_run(trigger) if wf.enabled else None,
    )
    return RedirectResponse(with_notice(f"/workflows/{wid}", "Workflow saved."), status_code=303)


@router.post("/workflows/{wid}/toggle")
async def workflow_toggle(wid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    wf = r.get_workflow(wid)
    if wf is None:
        return done(request, "That workflow does not exist.", "/workflows", "error")
    form = await form_values(request)
    want = form.get("enabled")
    enabled = (want == "1") if want in ("0", "1") else not wf.enabled
    r.set_enabled(wid, enabled, engine.next_run(wf.trigger) if enabled else None)
    msg = f"“{wf.name}” is {'on' if enabled else 'off'}."
    back = str(form.get("next") or "/workflows")
    if is_htmx(request):
        return Response(status_code=200, headers={**toast_headers(msg), "HX-Refresh": "true"})
    return RedirectResponse(
        with_notice(back if back.startswith("/") else "/workflows", msg), status_code=303
    )


@router.post("/workflows/{wid}/run")
def workflow_run(wid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    wf = r.get_workflow(wid)
    if wf is None:
        return done(request, "That workflow does not exist.", "/workflows", "error")
    res = engine.run_now(request.app, wid)
    kind = "error" if res["status"] == "failed" else "ok"
    msg = f"Ran “{wf.name}”: {res['summary']}"
    back = f"/workflows/{wid}#runs"
    if is_htmx(request):
        return Response(status_code=200, headers={**toast_headers(msg, kind), "HX-Refresh": "true"})
    return RedirectResponse(with_notice(back, msg), status_code=303)


@router.post("/workflows/{wid}/delete")
def workflow_delete(wid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    wf = r.get_workflow(wid)
    if wf is not None:
        r.delete_workflow(wid)
    msg = f"Deleted “{wf.name}”." if wf else "Already deleted."
    if is_htmx(request):
        return Response(status_code=200, headers={**toast_headers(msg), "HX-Refresh": "true"})
    return RedirectResponse(with_notice("/workflows", msg), status_code=303)


# ==== analytics


@router.get("/analytics", response_class=HTMLResponse)
def analytics(request: Request) -> Response:
    rng = stats.parse_range(request.query_params.get("range"))
    data = stats.collect(repo(request).engine, rng)
    return render(
        request,
        "tools_analytics.html",
        active="analytics",
        d=data,
        ranges=stats.RANGE_LABELS,
        rng=rng,
    )


# ==== integrations + tokens


def _settings_ctx(request: Request, section: str, **extra: Any) -> dict[str, Any]:
    from pepite.web.pages_extra import settings_ctx

    return settings_ctx(request, section, **extra)


@router.get("/settings/integrations", response_class=HTMLResponse)
def integrations(request: Request, show: str = "") -> Response:
    r = repo(request)
    hooks = r.webhooks()
    base = str(request.base_url).rstrip("/")
    return render(
        request,
        "tools_integrations.html",
        active="settings",
        **_settings_ctx(
            request,
            "integrations",
            hooks=hooks,
            deliveries=r.deliveries(None, 20),
            events=list(workflows.EVENTS),
            hook_names={h.id: h.url for h in hooks},
            demo=_demo(request),
            base=base,
            mcp_doc=MCP_DOC,
            api_docs=API_DOCS,
            show=show,
            tokens=len([t for t in r.tokens() if not t.revoked_at]),
        ),
    )


def _events_from(form: Any) -> list[str]:
    return [
        e for e in dict.fromkeys(str(x) for x in form.getlist("events")) if e in workflows.EVENTS
    ]


@router.post("/settings/integrations/webhooks")
async def webhook_create(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    url = str(form.get("url") or "").strip()
    err = webhooks.check_url(url)
    events = _events_from(form)
    back = "/settings/integrations"
    if err:
        return done(request, err, back, "error")
    if not events:
        return done(request, "Choose at least one event.", back, "error")
    repo(request).create_webhook(url, events)
    return done(request, "Webhook added. Use Send test to check it.", back)


@router.post("/settings/integrations/webhooks/{hid}")
async def webhook_update(hid: str, request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    back = "/settings/integrations"
    url = str(form.get("url") or "").strip()
    err = webhooks.check_url(url)
    events = _events_from(form)
    if err or not events:
        return done(request, err or "Choose at least one event.", back, "error")
    ok = repo(request).update_webhook(
        hid, url=url, events=events, enabled=form.get("enabled") == "1"
    )
    return done(
        request,
        "Webhook saved." if ok else "That webhook does not exist.",
        back,
        "ok" if ok else "error",
    )


@router.post("/settings/integrations/webhooks/{hid}/test")
def webhook_test(hid: str, request: Request, _: None = Depends(csrf)) -> Response:
    r = repo(request)
    hook = r.get_webhook(hid)
    back = "/settings/integrations"
    if hook is None:
        return done(request, "That webhook does not exist.", back, "error")
    if _demo(request):
        return done(request, "Webhooks are turned off in the public demo.", back, "error")
    ctx = engine.make_ctx(request.app)
    res = webhooks.deliver(ctx, hook, "ping", {"message": "Test from Pépite"})
    if res.ok:
        return done(
            request, f"The test arrived: HTTP {res.status_code} in {res.duration_ms} ms.", back
        )
    return done(request, f"The test failed: {res.error}", back, "error")


@router.post("/settings/integrations/webhooks/{hid}/rotate")
def webhook_rotate(hid: str, request: Request, _: None = Depends(csrf)) -> Response:
    repo(request).rotate_secret(hid)
    return done(
        request, "New signing secret created. Update your receiver.", "/settings/integrations"
    )


@router.post("/settings/integrations/webhooks/{hid}/delete")
def webhook_delete(hid: str, request: Request, _: None = Depends(csrf)) -> Response:
    repo(request).delete_webhook(hid)
    return done(request, "Webhook deleted.", "/settings/integrations")


@router.get("/settings/api", response_class=HTMLResponse)
def api_tokens(request: Request) -> Response:
    return render(
        request,
        "tools_tokens.html",
        active="settings",
        **_settings_ctx(
            request,
            "api",
            tokens=repo(request).tokens(),
            new_token=None,
            api_docs=API_DOCS,
            base=str(request.base_url).rstrip("/"),
            auth=request.app.state.settings.auth_required,
        ),
    )


@router.post("/settings/api")
async def api_token_create(request: Request, _: None = Depends(csrf)) -> Response:
    form = await form_values(request)
    name = str(form.get("name") or "").strip()[:120]
    if not name:
        return done(
            request, "Give the token a name so you can recognise it.", "/settings/api", "error"
        )
    plain, _rec = repo(request).create_token(name)
    set_state(request.app.state.settings, api_token_created=True)
    resp = render(
        request,
        "tools_tokens.html",
        active="settings",
        **_settings_ctx(
            request,
            "api",
            tokens=repo(request).tokens(),
            new_token=plain,
            new_name=name,
            api_docs=API_DOCS,
            base=str(request.base_url).rstrip("/"),
            auth=request.app.state.settings.auth_required,
        ),
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/settings/api/{tid}/revoke")
def api_token_revoke(tid: str, request: Request, _: None = Depends(csrf)) -> Response:
    ok = repo(request).revoke_token(tid)
    return done(
        request,
        "Token revoked. It no longer works." if ok else "Nothing to revoke.",
        "/settings/api",
        "ok" if ok else "error",
    )


# ==== fallbacks for other sections

FALLBACK = {
    "sequences": (
        "Sequences",
        "send",
        "Chain emails and tasks into a series that stops when people reply.",
    ),
    "emails": ("Emails", "mail", "Every email you send and every reply, next to the person."),
    "calls": ("Calls", "phone", "Log calls and follow-ups with the people you prospect."),
    "tasks": (
        "Tasks",
        "square-check-big",
        "Follow-ups and reminders tied to people and companies.",
    ),
    "meetings": ("Meetings", "calendar", "Meetings booked with the people you prospect."),
    "conversations": ("Conversations", "message-square", "Notes and replies, per person."),
    "deals": ("Deals", "handshake", "A light pipeline from first contact to a close."),
}


def _fallback(slug: str) -> None:
    title, ico, blurb = FALLBACK[slug]

    @router.get(f"/{slug}", response_class=HTMLResponse, name=f"fallback_{slug}")
    def page(request: Request) -> Response:
        return render(
            request,
            "soon.html",
            active=slug,
            title=title,
            ico=ico,
            soon=False,
            blurb=f"{blurb} This section is not installed in this build.",
        )


for _slug in FALLBACK:
    _fallback(_slug)


__all__ = ["Settings", "get_store", "router", "urlencode"]
