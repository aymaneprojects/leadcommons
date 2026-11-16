# UX parity: Engage (sequences, emails, tasks, mailboxes)

Status of every control listed in `docs/dev/ux-inventory.md` for screens 21, 22, 23 and the
mailbox part of 29, in Pépite. **works** = does the job for real; **disabled** = visible,
`aria-disabled`, with a tooltip saying why; **n/a** = not part of this screen in Pépite.

Rows 01-20 of screens 21-23 (sidebar rail, top bar, help button) belong to the shell and are
covered in the shell's parity file; they are listed once here for completeness.

## 21 - Sequences (`/sequences`)

| # | Control | Pépite | Status |
|---|---|---|---|
| 21-01..21-20 | Sidebar rail, top bar, help | Shell (`base.html`) | see shell parity |
| 21-17 | Credits pill | No credits: Pépite is free | n/a (rule 3) |
| 21-21 | Page title | "Sequences" | works |
| 21-22 | Create sequence | Opens the create dialog (name + template); chevron menu: Draft with AI, every template | works |
| 21-23 | All sequences tab | Table: name + step icons, status switch, steps, enrolled, active, replied, finished, created, row menu | works |
| 21-24 | Analytics tab | Emails sent, replies, bounces, unsubscribes, active contacts, per-sequence reply rate (no open tracking) | works |
| 21-25 | Diagnostics tab | "Health": mailbox login/IMAP state, footer address, public link address, empty sequences, unknown variables | works |
| 21-26 | Info banner close | Dismisses the "sequences step aside" tip (stored) | works |
| 21-27 | Link to sending policies | "Settings > Sending rules" | works |
| 21-28 | No-mailbox warning bar | Shown while no mailbox exists; demo reason in the public demo; mailbox error when a login fails | works |
| 21-29 | Link mailbox | "Connect mailbox" to `/settings/mailboxes` | works |
| 21-30 | Hero card | "Start your first sequence" | works |
| 21-31 | Create with AI | "Draft with AI": your own model drafts the steps from a goal (`POST /sequences/ai`) | works with `PEPITE_LLM_URL` + `PEPITE_LLM_MODEL`; otherwise disabled (tooltip says how to turn it on); disabled in the demo |
| 21-32 | Create manually | "Build from scratch": create dialog | works |
| 21-33 | Create manually chevron | Menu of templates (blank, three-touch, email then call, LinkedIn first); each creates a draft and opens it | works |
| 21-34..36 | Feature cards | Own wording: Plan each step / Goes out when you choose / Stops when they reply | works (info) |

Beyond the empty state (no reference image, built from the brief): sequence editor (steps with
wait days, automatic email, manual email, call, LinkedIn, action item; reorder, edit, delete;
variable chips; live preview for an enrolled contact), contacts tab (status filters, pause,
resume, mark as replied, write an email, remove), settings tab (mailbox, own window, stop on
reply), rename, activate/pause, duplicate, archive, delete. All work; sending is off in demo.

## 22 - Emails (`/emails`)

| # | Control | Pépite | Status |
|---|---|---|---|
| 22-01..22-20 | Sidebar rail, top bar, help | Shell | see shell parity |
| 22-17 | Credits pill | No credits | n/a (rule 3) |
| 22-21 | Page title | "Emails" | works |
| 22-22 | Progress card | "Get your outreach going" card with progress bar | works (info) |
| 22-23 | Benefits checklist | Own mailbox, one-off and sequences, replies per contact, opt-outs honored | works (info) |
| 22-24 | Add a mailbox | "Connect a mailbox" to the mailbox form; secondary "See how it works" to `/settings/mailboxes` | works; disabled in the demo (reason shown) |

With a mailbox (no reference image): tabs Scheduled / Sent / Replies / Failed and bounced with
counts, search, per-mailbox "sent today / limit" meter, Compose (now or scheduled), open an
email, cancel a scheduled email, retry a failed one, mark as replied, "Send due now". All work;
Compose's send button is disabled in the demo with the reason.

## 23 - Tasks (`/tasks`)

| # | Control | Pépite | Status |
|---|---|---|---|
| 23-01..23-20 | Sidebar rail, top bar, help | Shell | see shell parity |
| 23-17 | Credits pill | No credits | n/a (rule 3) |
| 23-21 | Page title | "Tasks" | works |
| 23-22 | Start call session | "Start call queue": today's call tasks, highest priority first | works; disabled with a reason when no call is due today |
| 23-23 | Create task | Task dialog (type, title, due date and time in your time zone, priority, notes) | works |
| 23-24 | Compliance banner | Own tip: calls and LinkedIn are always done by hand | works (info) |
| 23-25 | Banner button | "How tasks work" (docs/engage.md#tasks) | works |
| 23-26 | Banner close | Dismisses the tip (stored) | works |
| 23-27 | All tasks tab | "All tasks" with count | works |
| 23-28 | Call tasks tab | "Calls" | works |
| 23-29 | Email tasks tab | "Emails" | works |
| 23-30 | LinkedIn tasks tab | "LinkedIn" | works |
| 23-31 | Overdue tasks tab | "Overdue" (red count) | works |
| 23-32 | All your tasks | Views picker: Open tasks / Due today / Upcoming / Completed / Every task; "Everyone's tasks" | works; "Everyone's tasks" disabled: single user, so they are your tasks |
| 23-33 | Views menu | Same picker | works |
| 23-34 | Show filters | Priority filter chips (type is the tabs) | works |
| 23-35 | Search tasks | "Find a task" (title and notes) | works |
| 23-36 | Sort | Due date / Priority / Newest | works |
| 23-37 | View options | Compact rows, columns (contact, due, priority, sequence), show completed | works (layout kept per viewer in the browser) |
| 23-38 | Learn more about tasks | "How tasks work" | works |
| 23-39 | View all team tasks | "See every task" (all statuses) | works; "See everyone's tasks" disabled when there is nothing yet (single user) |
| 23-40 | New task | Task dialog | works |

Row actions (no reference image): complete in one click, complete with a note, skip, reopen,
delete; email tasks open the composer with the draft (sending completes the task); LinkedIn
tasks open a LinkedIn people search in a new tab (never automated); call tasks open the call
log. Completing a sequence task moves the sequence on.

## 29 - Mailbox setup (`/settings/mailboxes`, `/settings/mailboxes/new`, `/settings/sending`)

| # | Control | Pépite | Status |
|---|---|---|---|
| 29-01 | Settings sidebar | Shell settings menu (Outbound group: Mailboxes, Sending, Email verification & health) | see shell parity |
| 29-02 | Page title | "Outbound email" | works |
| 29-03 | Deliverability badge | Real state: Ready to send / Needs a connection test / A mailbox needs attention / No mailbox yet / demo | works |
| 29-04 | Run a check | "Test all": signs in to every mailbox (SMTP, and IMAP when set) and quits; never sends | works; disabled in the demo and with no mailbox |
| 29-05 | Add email & domain | "Connect mailbox" menu: provider presets (Gmail, Microsoft 365, Fastmail, Zoho, iCloud, Proton Bridge, OVHcloud, other) | works |
| 29-06 | Overview tab | Mailbox list shows status, sent today, window, SMTP and IMAP state | works (merged into Mailboxes) |
| 29-07 | Domains & Mailboxes tab | "Mailboxes" | works |
| 29-08 | Sending policies tab | "Sending rules": footer + postal address, public link address, default wait, spacing, reply check interval, time zone, stop on reply, bounce action, tracking off | works |
| 29-09 | Empty card | "No mailboxes yet" with illustration and placeholder rows | works |
| 29-10 | Add email & domain (card) | Same provider menu | works |
| 29-11 | AI helper offer | "Not sure which servers to use?" help row | works (info) |
| 29-12 | Set up with AI | "Choose a provider": presets fill servers, ports and security | works (no AI needed) |
| 29-13 | What happens next + guide link | "After you connect" + guide link (docs/engage.md) | works |
| 29-14, 29-15 | Top bar, help | Shell | see shell parity |
| - | Mailbox form | Sender, SMTP (password encrypted at rest), optional IMAP, daily limit, window (hours, days, time zone), signature | works; passwords never stored in the demo |
| - | Per mailbox: Test connection, Check replies, Edit, Remove | | works; tests and reply checks disabled in the demo |
| - | Domain DNS check tab | Links to `/settings/email-health` (shell) | works |
