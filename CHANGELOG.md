# Changelog

## 0.1.0 (2026-11-15)

First public release. LeadCommons is a free, open-source, local-first prospecting app built
on public data only.

### Find

- Search companies from open registers: Wikidata, GLEIF, INSEE SIRENE (France) and SEC EDGAR
  (United States), shipped as monthly Parquet packs.
- Find people and work emails from companies' own public pages (robots.txt respected), with
  email checks and confidence scores. Opt-outs are honoured everywhere.
- Filters for titles (simple or boolean), departments, levels, personas, location, industry,
  keywords and size; saved views; quick filters; an AI prompt that turns plain words into filters
  (rules offline, or your own OpenAI-compatible model).
- Company insights: technologies, job postings, business phones, trends and lookalikes.
- Lists, CSV import and export, and CSV enrichment.

### Engage and win

- Sequences sent from your own mailbox (SMTP, optional IMAP for replies and bounces), with
  sending windows, daily limits, a mandatory unsubscribe footer and no open tracking.
- Emails, tasks, call queue, meetings, conversations and a deals board with an activity timeline.

### Automate

- Workflows triggered by events (person found, email found, added to a list, email sent or
  replied, task completed), schedules and webhooks.
- An Assistant that searches, explains and drafts; JSON API with tokens; an MCP server for
  Claude, ChatGPT and other AI tools.

### Run it your way

- On your computer with a one-line installer (macOS, Linux, Windows), on your own server
  (Docker image on GHCR, Docker Compose with Caddy and password login), or in the public demo.

### Quality

- 1,171 automated tests; a button audit that clicks every control (624 on 41 pages); the
  status of every control from the reference screens in `docs/dev/ux-parity/`.
