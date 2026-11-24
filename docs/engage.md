# Engage: mailboxes, sequences, emails and tasks

Engage sends prospecting emails from **your own mailbox**, runs multi-step sequences, detects
replies and keeps a task list for the steps only you can do (calls, LinkedIn, manual emails).
Everything stays in your local database. Nothing is sent in the public demo.

## Mailboxes (Settings, Outbound email, Mailboxes)

Connect any mailbox that speaks SMTP. Pick your provider to fill in the servers, then enter
your password. Most providers want an **app password** (Gmail and iCloud need 2-step
verification first; Microsoft 365 may need an administrator to allow SMTP sign-in).

- **Encryption.** Passwords are encrypted with Fernet before they are saved. The key is a
  random file, `secret.key`, in `PEPITE_HOME`, created with mode 0600 the first time it is
  needed and never logged. Back it up with the database: without it, stored passwords cannot
  be read and must be typed again.
- **Test connection** signs in and quits. It never sends an email.
- **Daily limit** (default 50) caps what one mailbox sends per day, counted from midnight in
  the mailbox's time zone. Start with 30 to 50 a day on a new mailbox.
- **Sending window**: the hours and weekdays automatic emails may go out, in the mailbox's
  time zone. A sequence can use its own window instead.
- **Signature** is added under every email from that mailbox.
- **Reply detection (IMAP, optional).** Pépite reads the headers of new messages in the
  watched folder (read-only, nothing is marked as read) and matches `In-Reply-To` and
  `References` against the Message-IDs it sent. A match marks the contact as replied and stops
  the sequence for them. Delivery reports (bounces) are matched the same way.

## Sending rules (Settings, Outbound email, Sending rules)

- **Unsubscribe footer** (always on): your text, your postal address and a personal
  unsubscribe link. Every email also carries a `List-Unsubscribe` header (link and mailto) and,
  over HTTPS, `List-Unsubscribe-Post` for one-click unsubscribe in mail clients.
- **Address of this app**: unsubscribe links point to `<address>/u/<token>`. Recipients must
  be able to open it, so set your public HTTPS address when you run Pépite on a server.
  On a laptop, people can still reply "unsubscribe", which reply detection understands.
- **Timing**: default wait between steps, minimum minutes between two emails of one mailbox
  (emails are spaced out), how often replies are checked, and your own time zone (for task due
  dates and scheduled emails).
- **Replies and bounces**: stop on reply (default for new sequences), and what a bounce does:
  stop the sequence and add the address to the do-not-contact list (recommended), or only stop.
- **Tracking**: there is no open-tracking pixel and no link rewriting, ever.

## Sequences

A sequence is an ordered list of steps, each with a wait in days after the previous step:

| Step | What happens |
| --- | --- |
| Automatic email | Sent from the sequence's mailbox inside its window and daily limit. An empty subject replies in the same thread (`Re:` + `In-Reply-To`). |
| Manual email | Creates an email task with the draft; you review and send it from Tasks. |
| Phone call | Creates a call task. Pépite never dials. |
| LinkedIn task | Creates a LinkedIn task. You do it yourself: nothing is automated on LinkedIn. |
| Action item | Creates a to-do. |

Task steps wait until you complete or skip the task, then the sequence moves on.

Variables: `{{first_name}}`, `{{last_name}}`, `{{full_name}}`, `{{company}}`, `{{title}}` and
`{{sender_name}}`. Add a fallback after a bar: `Hi {{first_name|there}}`. The editor shows a
live preview for any enrolled contact and warns about empty or unknown variables.

**Enrolling** (from People: select rows, then "Add to a sequence"; or "Add contacts" on the
sequence) skips people on the do-not-contact list, people whose best email is missing or
invalid, and people already in the sequence. The toast says how many were added and why
others were skipped. Contacts have a status: active, paused, finished, replied, bounced or
unsubscribed. From the Contacts tab you can pause, resume, mark as replied or remove them.

## The sender

`pepite.engage.scheduler.run_due(store, settings, now)` does one pass:

1. sends one-off emails whose scheduled time has come;
2. runs due sequence steps: re-checks the do-not-contact list, respects the window, the daily
   limit and the spacing, sends through the mailbox (with signature, footer and Message-ID),
   or creates the task for a task step, then schedules the next step;
3. checks IMAP mailboxes for replies and bounces when their interval is due.

Every sent email is logged on the activity timeline (`email_sent`) and announced to workflows
(`email_sent`); replies give `email_reply` / `email_replied`; completed tasks give `task_done`
/ `task_completed`. A database lease lets only one pass run at a time, so several processes
can run the sender safely.

Where it runs:

- `pepite serve` (local and server modes) starts a background thread that runs a pass
  every 60 seconds. It is off in demo mode, with `PEPITE_SYNC_JOBS=1`, and with
  `PEPITE_ENGAGE_SCHEDULER=0`.
- `pepite worker` runs passes in a loop (`--interval 60`, `--once` for one pass). The
  server Docker Compose file has a `worker` service; it must share `PEPITE_HOME` (the
  `app-data` volume) with the app so it can read the encryption key.
- "Send due emails now" in Sending rules (and "Send due now" in Emails) runs one pass.

## Emails

Tabs: Scheduled (upcoming sequence emails with the time they will actually go out, and one-off
emails you scheduled), Sent, Replies, Failed and bounced. Compose sends one email now or at a
date and time, with the same checks and footer as sequences. Failed emails can be retried;
scheduled ones cancelled.

## Tasks

Views: all open, due today, overdue, upcoming, completed, with filters by type and priority.
Complete a task in one click, or with a note; skip it; reopen or delete it. Email tasks open
the composer with their draft; LinkedIn tasks open a LinkedIn search for the person; call tasks
open the call log. `pepite.engage.create_task(store, title, person_id=, company_id=,
deal_id=, due=, kind=)` creates tasks from workflows and the assistant.

## Unsubscribe page

`/u/<token>` is public. The token is signed (itsdangerous, with a key derived from
`secret.key`) and valid for three years. Opening the link shows a confirmation, so link
scanners cannot unsubscribe anyone; confirming (or a one-click POST from a mail client) adds
the address to the do-not-contact list, stops every sequence for it and cancels scheduled
emails. Bad links show a friendly page that points to `/opt-out`.
