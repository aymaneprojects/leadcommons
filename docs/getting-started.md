# Getting started: your first hour with Pépite

This page takes you from nothing to your first email sequence. You do not need to be a
developer. Each step says where to click, using the names you see on screen.

Pépite runs on your computer. Your leads stay on it.

## 1. Install

Pick one. Full details are in [install.md](install.md).

- **Desktop app (easiest).** Download the file for Windows, Mac or Linux from the
  [latest release](https://github.com/aymaneprojects/leadcommons/releases/latest). Open it like
  any other app. Your system may warn you that the app is not signed: [desktop.md](desktop.md)
  shows what to click.
- **One line in a terminal.** On Linux or Mac:
  `curl -fsSL https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/scripts/install.sh | sh`.
  On Windows, use the PowerShell line in [install.md](install.md). Then run `pepite serve`.
- **Docker or your own server.** Run `docker compose up --build` in the project folder, or follow
  [server.md](server.md) for a shared server with a login.

## 2. First start

- **Desktop app:** open Pépite. It opens its own window.
- **Terminal install:** run `pepite serve`, then open <http://127.0.0.1:8000> in your browser.

The home page, "Your first steps with Pépite", lists what to do next. A small sample of companies
is built in, so you can search right away. You still need to download your country's companies.

## 3. Download the companies of your country

The desktop app starts this for you, for the country of your computer. Watch it in the jobs menu
at the top (it shows "1 running"). To pick other countries:

1. Open **Settings**, then **Data packs**.
2. In the **Countries** table, click **Download** next to a country.
3. Wait until the status says **Installed**. Big countries take a few minutes.

From a terminal, run `pepite pack download --country FR` (use your own country code). Run
`pepite pack countries` to see the list. Each month, click **Update my countries** on the same
page, or run `pepite pack download --update`.

## 4. Search for companies

Open **Companies** in the sidebar.

- **With filters.** Use the filter panel: **Location**, **# Employees**, **Industry & keywords**
  and more. Click **Apply filters**. The numbers at the top (Total, Net new, Saved) update.
- **With a plain sentence.** Type who you want in the box "Describe the companies you are looking
  for", for example `Logistics companies in France with 50 to 200 employees`, then click
  **Search**. The filters fill in for you. This works offline. To use your own AI model, see the
  settings `PEPITE_LLM_URL` and `PEPITE_LLM_MODEL` in [install.md](install.md).

## 5. Open a company and save it

Click a company name. A panel opens with its details and where each fact comes from. Click
**Save company** to keep it on your computer.

To save many at once, tick the boxes in the results, then click **Save** in the bar that appears.
A window asks what to do:

- **Look for people and contact details** reads the company's public website. You can choose
  **People only** or **People and phone numbers**. Phone numbers are business lines only.
- **Add to list** puts the companies in a list.

Click **Save**. Progress shows in the jobs menu, and you can keep working.

## 6. Make a list and export a CSV

1. Tick some rows and click **Add to list** in the bar. Pick a list or choose **New list…**.
2. Open **Lists** in the sidebar to see all your lists. Click **Create list** to make an empty one.
3. To export, tick rows and click **Export CSV**, or click **Export CSV** next to a list on the
   **Lists** page.

People on your do-not-contact list are never exported.

## 7. Find people at a company

1. Open the company and click **Find more people**. Or tick companies in the results, open the
   **More actions** menu and choose **Find people**.
2. Pépite reads the company's public pages and official registers. It does not use LinkedIn.
3. Open **People** in the sidebar to see who was found, with their job title and source.

Not every company has people on its website. Some give none. This is normal.

## 8. Find and check an email

1. On **People**, open **More actions** on a person's row and choose **Find email**. It reads
   **Re-check email** if one was already found. You can also click **Find email** on the
   person's page.
2. Pépite learns the address format of the company, then asks the mail server. The email gets a
   status: **Valid**, **Catch-all**, **Unknown** or **Invalid**, and a score from 0 to 100.
3. Only send to **Valid** addresses when you can. A **Catch-all** server accepts any address, so
   Pépite cannot be sure. [email-quality.md](email-quality.md) explains how far you can trust
   the score.

From a terminal: `pepite find-email "Ada Lovelace" example.com`.

## 9. Connect a mailbox

You send emails from your own mailbox. Nothing goes through a third party.

1. Open **Settings**, then **Mailboxes** (the page is called "Outbound email"). Click
   **Connect mailbox**.
2. Pick your email provider in the list. The server fields fill in.
3. Enter your name, your email address and your password. Most providers need an
   **app password**, not your usual one. [engage.md](engage.md) explains how.
4. Keep **Emails per day** low at first (30 to 50). Optionally fill in **Reply detection (IMAP)**
   so Pépite stops the sequence when someone replies.
5. Click **Connect mailbox**.

Then open the **Sending rules** tab. Write your postal address in **Postal address**. If you
run Pépite on a server, write its web address in **Public address**. Every email gets an
unsubscribe link, and **Save sending rules** keeps your choices.

## 10. Build and start a sequence

A sequence is a series of emails (and tasks) sent over days.

1. Open **Sequences** and click **Create sequence**. Choose a template such as
   **Three-touch introduction**, or start with **Blank sequence**. You can also try
   **Draft with AI**.
2. Click **Add a step** to add or change steps. Each step has a wait in days. Use
   `{{first_name|there}}` to fill in a name.
3. Click **Add contacts**, or tick people on **People** and choose **Add to a sequence**.
   Pépite skips people without a valid email and people on the do-not-contact list.
4. Click **Activate**.

Pépite sends the emails while it is running. Keep the app open, or run the server. In the
desktop app, closing it stops the sending. See [engage.md](engage.md) for sending windows and
the separate sender (`pepite worker`).

## 11. Tasks and deals

**Tasks** holds the steps only you can do, like calls. Click **New task** to add one. **Deals**
is a board of your sales opportunities: click **New deal**, then drag it from stage to stage.
Both are in [pipeline.md](pipeline.md) and [engage.md](engage.md).

## 12. Opt-out and the do-not-contact list

Respect "no". Pépite checks the list everywhere: search, find email, export, sequences.

- To add someone yourself, open **Settings**, then **Privacy and opt-out**, type an email or a
  domain and click **Suppress**. From a terminal: `pepite optout someone@example.com`.
- On a company or person page, use **Mark everyone at this company** or **Mark this person as do
  not contact**.
- People can also ask you to stop. Every email has an unsubscribe link, and the public page
  `/opt-out` ("Ask us to stop") works for anyone.

You stay responsible for the law that applies to you. See [privacy.md](privacy.md).

## 13. Where your data lives, and how to back it up

Everything is in one folder: `.pepite` in your home folder. In the desktop app, choose
**Help**, then **Open the data folder**.

| What | Where |
| --- | --- |
| People, lists, sequences, deals, settings | `pepite.db` |
| Downloaded companies | the `packs` folder |
| The key that protects your mailbox passwords | `secret.key` |

Installed before the rename to Pépite? Your old `.leadcommons` folder is still used.

**To back up:** close Pépite, then copy the whole folder. Keep `secret.key` with `pepite.db`.
Without the key, you must type your mailbox passwords again. You can re-download the `packs`
folder at any time, so it is optional.

On a server with Docker, back up the database as shown in [server.md](server.md).

## 14. Update

- **Desktop app:** it tells you when a new version exists. You can also choose **Help**, then
  **Check for updates…**. Download the new installer and run it. Your data stays.
- **Terminal install:** run the install line again.
- **Docker on your computer:** pull the new code, then run `docker compose up --build`.
- **Server:** run `docker compose pull`, then `docker compose up -d`.
- **Company data:** every month, **Settings**, **Data packs**, **Update my countries**.

## What next

- Searching and filters: [README](../README.md) has screenshots.
- Automations and webhooks: [workflows.md](workflows.md).
- Use Pépite from Claude or ChatGPT: [mcp.md](mcp.md).
- Where the data comes from: [data-sources.md](data-sources.md).
