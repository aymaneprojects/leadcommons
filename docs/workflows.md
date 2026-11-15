# Workflows, the Assistant, analytics and integrations

## Workflows

A workflow is "when this happens, do that". Open **Workflows** in the sidebar, then **Create workflow**.

**1. When this happens (the trigger)**

| Trigger | Starts when |
| --- | --- |
| An event | `person_found`, `email_found`, `added_to_list`, `email_sent`, `email_replied`, `task_completed` or `deal_stage_changed` |
| A schedule on a saved search | every day or every week (hour in UTC), LeadCommons runs the search of the Companies or People page and acts on records it has not handled before (up to 100 per run) |
| By hand | you press **Run now**; it acts on the members of a list, or on your 25 most recent people |

`/workflows/new?source=companies&query=country%3DFR` pre-fills a saved-search trigger (the search pages link to it).

**2. Only if (conditions)**: all must hold for a record. Fields: country, industry (ISIC section letter), company size, company domain, job title, seniority, email status, list membership, deal stage.

**3. Then do (actions)**, in order, for each record. A failing step never stops the next ones.

| Action | Notes |
| --- | --- |
| Add to a list | the list must match the record (people or companies) |
| Enrol in a sequence | `engage.enroll`; skips people without a usable email, opted out or already active |
| Create a task | `engage.create_task`; the title can use `{name}`, `{company}`, `{title}`, `{email}` |
| Find the email address / Find people / Refresh company insights | background jobs |
| Send to a webhook URL | JSON `POST` |
| Add a notification | shown in the bell menu |

Every run is logged with the result of every step (open a workflow, then **Run history**). Workflows never trigger other workflows.

In the **public demo** nothing crawls, no webhook is sent and scheduled workflows do not run; the steps say so. Scheduled workflows run from a background thread of the server (not when `LEADCOMMONS_SYNC_JOBS` is set).

## For developers

`leadcommons.workflows.emit(event, **payload)` is the one call other features make. It never raises and returns at once when nothing listens. `create_app` binds the running app to the engine (`workflows.bind_app`), so events raised in job threads reach the same database as the pages.

## Assistant

`/assistant` is a local chat (conversations, messages and thumbs up/down are stored in your database). It understands plain requests with the offline rules in `leadcommons.ai` and, when `LEADCOMMONS_LLM_URL` and `LEADCOMMONS_LLM_MODEL` are set, an OpenAI-compatible model. It searches, shows the top results with links, and proposes the next step as single-choice cards: find people at the companies, save a list, add people to a sequence, automate the search. Give it your website and it reads the public homepage to say what you sell.

Commands: `/find`, `/people`, `/list`, `/site`, `/enrich`, `/help`. In the demo it never crawls and never calls an LLM.

## Analytics

`/analytics` shows counts read from your own database for the last 7, 30, 90 days or all time: people and emails found (by status), lists, saved companies and searches, sequence sends with reply, bounce and unsubscribe rates, completed tasks, deals by stage and value, meetings and workflow runs. A feature with no data shows "n/a"; nothing is estimated.

## Integrations

**Settings, Integrations** lists outgoing webhooks, how to connect Claude or ChatGPT over MCP ([mcp.md](mcp.md)) and the REST API (`/api/docs`).

Webhook requests are JSON with the headers `X-LeadCommons-Event` and `X-LeadCommons-Signature: sha256=<hex>`, the HMAC-SHA256 of the raw body keyed with the webhook's secret:

```python
import hashlib, hmac
expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
assert hmac.compare_digest(expected, request.headers["X-LeadCommons-Signature"])
```

**Settings, API tokens** creates tokens (shown once; only a hash is stored). Send one as `Authorization: Bearer lc_…` to `/api/*` and `/mcp` when the instance asks for a login.
