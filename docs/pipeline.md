# Win deals: deals, meetings, calls, conversations, activity

Code: `src/leadcommons/web/pages_pipeline.py` (routes), `src/leadcommons/store/pipeline.py`
(tables and repositories), templates `pipeline_*.html` / `_pipeline_*.html`, `static/css/pipeline.css`,
`static/js/pipeline.js`, icons in `static/icons/pipeline-sprite.svg` (Lucide, ISC licence).

Everything works without JavaScript (plain forms and redirects). HTMX and `pipeline.js` add dialogs,
the typeahead, drag and drop and in-place refreshes. In demo mode (`LEADCOMMONS_MODE=demo`) all of it
works on the temporary database and nothing external is called.

## Data

| Table | What |
| --- | --- |
| `activities` | the shared timeline (`ActivityRepo`, unchanged contract) |
| `deal_stages` | name, order, kind (`open`/`won`/`lost`), default probability, archived |
| `deals` | name, stage, company and contact (id plus a name snapshot), amount, currency, close date, probability, source, notes, position in column |
| `meetings` | title, start (wall-clock time, no zone), duration, person/company/deal, location, video link, agenda, outcome, status |
| `calls` | person/company/deal, number, outcome, duration, notes, follow-up task id |
| `pipeline_phones` | numbers typed by hand (the `Person` record has no phone field) |
| `pipeline_settings` | key/value, today only `booking_url` |

`Pipeline(store.engine)` bundles all repositories and exposes `.activity` (an `ActivityRepo`).
`ActivityRepo` kept `add` and `list_for`; it gained `search`, `get` and `delete` for the conversations page.

Default stages: Qualification 10 %, Discovery 25 %, Proposal 50 %, Negotiation 75 %, Won 100 %, Lost 0 %.
New open stages are inserted before the first closed stage. A stage with deals cannot be archived, and
the last open stage cannot be archived. A deal's probability follows its stage until it is set by hand.

## Stage changes

Every stage change (drag and drop, card menu, keyboard, stage buttons in the drawer, the stage select of
the table or the deal form) goes through `log_stage_change`: it writes an `ActivityRepo` entry of kind
`deal_stage` (summary `Deal: Old → New`, meta `stage`, `previous_stage`, ids) and calls
`workflows.emit("deal_stage_changed", deal_id=, stage=<name>, previous_stage=<name>)`. A failing
`emit` never breaks the request. Moving inside a column only reorders: nothing is logged.

## Endpoints

Pages: `/deals` (`view=board|table`, `q`, `stage`, `sort`, `dir`), `/deals/{id}` (drawer for HTMX, full page
otherwise), `/meetings` (`tab=upcoming|past`, `q`), `/calls` (`tab=list|history`, filters),
`/conversations` (`kind`, `q`, `thread=p:<person id>|c:<company id>`), `/settings/deal-stages`.

Partials (return a `<dialog data-autoopen>` or a fragment):

| GET | Notes |
| --- | --- |
| `/partials/deal-new?company_id=&person_id=&stage_id=` | with `POST /deals` |
| `/partials/note-new?person_id=&company_id=&deal_id=&reload=` | with `POST /notes` |
| `/partials/call-log?person_id=&company_id=&deal_id=&phone=` | with `POST /calls` |
| `/partials/meeting-new?person_id=&company_id=&deal_id=` | with `POST /meetings` |
| `/partials/meeting/{id}` | details, calendar links, outcome form |
| `/partials/activity?person_id=&company_id=&deal_id=&kinds=&limit=` | timeline fragment |
| `/partials/pick?kind=company\|person&q=` | typeahead results |

Other POSTs: `/deals/{id}` (edit), `/deals/{id}/move` (`stage_id`, optional `position`; answers JSON when
`Accept: application/json`, used by drag and drop), `/deals/{id}/delete`, `/meetings/{id}/outcome`,
`/meetings/{id}/delete`, `/meetings/booking-link`, `/calls/phone`, `/settings/deal-stages[/...]`.
Every POST checks Origin/Referer through `pages.csrf`.

### Using the dialogs from another page

Open them with a plain fetch: `<button data-pl-dialog="/partials/deal-new?company_id=...">`
(`pipeline.js` fetches the fragment, appends it to the page and calls `showModal()`), or with HTMX:
`hx-get="/partials/note-new?person_id=..." hx-target="body" hx-swap="beforeend"` (the dialog carries
`data-autoopen`, so `pipeline.js` opens it after the swap). The forms return to the page they were
opened from (HTMX sends `HX-Current-URL`) with a notice. To refresh a fragment after a change, listen for the
`pl-changed` event: `hx-trigger="load, pl-changed from:body"`.

A dialog containing an HTMX request is closed by `app.js`, so these dialogs use a plain `fetch` for
the typeahead.

## Meetings

Times are stored and shown as typed (wall-clock, no zone) and the `.ics` file uses floating times, which
calendars read as the user's local time. Links: `GET /meetings/{id}.ics`, Google
(`calendar.google.com/calendar/render?action=TEMPLATE`) and Outlook
(`outlook.live.com/calendar/0/deeplink/compose`). Outcome notes (held, no-show, cancelled) are written
to the timeline as `meeting` activities. The booking link is the user's own scheduling page; it is only
stored, shown and copied.

## Calls

There is no telephony provider: numbers are `tel:` links the browser hands to the user's phone or softphone.
The calling list shows saved people with a number, taken in this order: `person.phone` if the model ever
has one, a number typed by hand, the number of the last logged call, then the company's business phone from
`leadcommons.insights.get(store, company_id)` (read defensively: `NotImplementedError`, `None` and several
record shapes are handled). Logging a call writes a `calls` row and a `call` activity; the optional follow-up
task is created through `leadcommons.engage.create_task(kind="call")` and the call is still logged if that fails.

## Conversations

One timeline per person (or per company, for activity without a person) over the kinds `note`, `call`,
`meeting`, `email_sent`, `email_reply`, searchable and filterable by kind.
