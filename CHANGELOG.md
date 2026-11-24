# Changelog

## Unreleased

- LeadCommons is now **Pépite** (`pepite` command, `PEPITE_*` settings), with a new logo. Existing
  installs keep their data: `~/.leadcommons`, `leadcommons.db` and `LEADCOMMONS_*` settings are
  still read.
- Company data per country: Settings > Data packs lists the countries of the newest release
  (companies, size, installed or update available) with Download, Update and Remove; downloads
  resume after an interruption and are checked (sha256). The desktop app downloads your own
  country at the first start. `pepite pack download --country FR`, `pack countries`.
- Search stays fast with millions of companies: each downloaded pack is prepared for search
  (a local DuckDB database). On 12 million companies the results page answers in under 0.7 s
  (p95) on a laptop-sized memory budget, instead of 3.7 s.

## 0.1.1 (2026-11-15)

- Desktop app, built like the big desktop apps: an Electron shell (own window, real menu bar in
  English or French, splash screen, native "Save as" for exports, system notifications when
  background jobs finish, badge on the Dock or taskbar, one instance at a time, window size kept,
  update notice) around the Pépite engine. Installers for Windows (.exe), macOS on Apple
  Silicon (.dmg) and Linux (.deb and .AppImage), built and tested by GitHub Actions.
- In the app, pages behave like an application: no browser reload or developer shortcuts, no
  stray text selection, links to other sites open in your browser.
- Settings > Data packs: download the newest company pack from the app; search switches to it
  when the download is done. A new download replaces the previous one, never your own packs. The
  desktop app does this by itself at the first start.
- `pepite desktop` opens a window from a command-line install.

## 0.1.0 (2026-11-15)

First public release. Pépite is a free, open-source, local-first prospecting app built
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
