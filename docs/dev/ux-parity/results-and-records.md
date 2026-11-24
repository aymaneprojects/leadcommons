# UX parity: results and records (toolbar, selection bar, dialogs, field editor, drawers, lists)

Status of every control listed in `docs/dev/ux-inventory.md` for screens B1, B2, B3, B4 and
11 to 20, in Pépite. **works** = does the job for real; **disabled** = visible,
`aria-disabled` or disabled inside a `.tipwrap`, with a tooltip and a screen-reader description
saying why and how to turn it on; **n/a** = not part of Pépite, with the reason.

Rows that belong to other parity files are listed once per screen as a range:

- sidebar rail, top bar, command palette, notifications, avatar, help button: the shell
  (`base.html`), see the shell parity file;
- filter side panel and All filters window (Total / Net new / Saved tabs, filter accordions,
  chips, Clear all, View all filters): the filters parity file;
- trial banners and credit pills: n/a everywhere (rule 3: Pépite is free, no plans, no
  credits).

Tests: `tests/test_search_parity_v3.py` (one or more tests per screen below).

## B1 - Find people results (`/people?...`)

| # | Control | Pépite | Status |
|---|---|---|---|
| B1-01..03 | Trial banner, benefits button, close | No trials or plans | n/a (rule 3) |
| B1-04..23 | Rail, top bar, palette, AI Assistant, bell, avatar, help | Shell | see shell parity |
| B1-20 | Credits pill | No credits | n/a (rule 3) |
| B1-24 | Page title | "Find people" | works |
| B1-25 | Import menu | Single contact (dialog, `POST /people/new`) and CSV (Data enrichment) | works |
| B1-26 | Default view | Saved-view menu (see screen 11) | works |
| B1-27 | Hide / Show filters with count | Toggles the side panel; badge = applied filters | works |
| B1-28 | Search people | Free text over name and title, live | works |
| B1-29 | Research with AI | "Research with AI" menu (see screens 18, 19) | works (menu); prompt items need a model, see 18 |
| B1-30 | Create workflow | Menu: from the current filters, your workflows; label folds to the icon under 1520px | works |
| B1-31 | Save as new search | Opens "New saved search" | works |
| B1-32 | Sort | Recently found, Name A to Z, Company A to Z | works |
| B1-33 | Search settings | Full-height drawer (see screen 12) | works |
| B1-34..44 | Total / Net new / Saved tabs, filter accordions, chips, Clear all, View all filters | Filter side panel | see filters parity |
| B1-45 | Select-all checkbox | Selects the page; indeterminate when partial | works |
| B1-46..49 | Name, Job title, Company, Email headers | Columns, order and choice from the field editor | works |
| B1-50 | Phone numbers header | "Phone" column: business line from the company's public pages (insights), empty when unknown | works |
| B1-51 | Add column | Opens the field editor (screen 13) | works |
| B1-52 | Row checkbox | Selection, drives the selection bar | works |
| B1-53 | Copy icon on a hovered cell | Not built in the table; the profile has a copy button for the email and exports give every value | n/a (copy lives on the profile) |
| B1-54 | Person name link | Opens the person drawer; expand opens the full page | works |
| B1-55 | Company link | Opens the company drawer (B4) | works |
| B1-56 | Access email | "Find email" button when no address is known (background job) | works; disabled in the demo (reason) |
| B1-57 | Access mobile | Personal mobile numbers are never collected; the Phone column shows the business line | n/a (rule 4) |
| B1-58 | Row-end actions | Email, sequence, save, more menu: shown on row hover or keyboard focus only | works |
| B1-59..62 | Previous, page select, next, range label | Pager | works |

## B2 - Results with rows selected

| # | Control | Pépite | Status |
|---|---|---|---|
| B2-01..03 | Trial banner | | n/a (rule 3) |
| B2-04..23 | Rail, top bar, help | Shell | see shell parity |
| B2-24 | Import | As B1-25 | works |
| B2-25 | Default view (left of the bar) | Stays visible beside the selection bar | works |
| B2-26 | Clear N selected | Clears the selection | works |
| B2-27 | Save (primary) | Opens the save window (screen 15) | works |
| B2-28 | Email | Compose for one selected person (engage dialog); asks to select one otherwise | works |
| B2-29 | Sequence menu | Add to a sequence (engage picker), your sequences | works |
| B2-30 | Workflow menu | Create a workflow from this search, your workflows | works |
| B2-31 | Start call session | "Log a call (one person)" (pipeline dialog); on Find companies: Create tasks | works |
| B2-32 | Add to list | Quick list picker, or a new list | works |
| B2-33 | Export | CSV of the selection; opted-out addresses left out | works |
| B2-34 | Research with AI | AI menu dropping from the bar, right-aligned (screen 18) | works (menu); prompt items need a model |
| B2-35 | Push to CRM/ATS | "Send to another tool": CSV for your CRM, set up an integration | works |
| B2-36 | More | Select all on page, clear, add to sequence, create tasks, find emails / find people, refresh insights, mark as do not contact | works; look-ups disabled in the demo (reason) |
| B2-37..47 | Filter panel | | see filters parity |
| B2-48..50 | Select-all, indeterminate state, highlighted rows | | works |
| B2-51..56 | Column headers, Add column | As B1 | works |
| B2-57..62 | Row controls | As B1-52..58 | works / n/a as in B1 |
| B2-63..66 | Pager | | works |

## B3 - Person page (`/people/<id>`)

| # | Control | Pépite | Status |
|---|---|---|---|
| B3-01..03 | Trial banner | | n/a (rule 3) |
| B3-04..23 | Rail, top bar, help | Shell | see shell parity |
| B3-24 | Breadcrumb | People > name | works |
| B3-25 | Avatar | Initials | works |
| B3-26 | Name | Heading | works |
| B3-27 | Company link | Opens the company page | works |
| B3-28 | Add to list | List picker or new list | works |
| B3-29 | Access email (primary) | "Find email" (background job) | works; disabled in the demo (reason) |
| B3-30 | Contact information header with collapse | Collapse button folds the card | works |
| B3-31 | Email with status icon | Real address, status dot and chip, confidence; "Opted out" when suppressed | works |
| B3-32 | Access email | Find email / Re-check, copy button (`data-copy-value`) | works; disabled in the demo (reason) |
| B3-33..34 | Masked mobile, credits, Access mobile | No personal numbers, no credits | n/a (rules 3 and 4) |
| B3-35 | No phone number available (business) | Business line from insights, or how to look for one; Log a call | works |
| B3-36 | Score with label | 0-100 with Strong / Fair / Low | works |
| B3-37 | Score model dropdown | "Scores": Email confidence or Profile completeness (remembered) | works |
| B3-38 | Show breakdown | Rows behind the score | works |
| B3-39 | Do Not Call banner | Do-not-contact banner inside the contact card; expands to mark or remove | works |
| B3-40 | Insights gear | Menu: refresh insights now, open the company page, what a refresh reads | works; refresh disabled in the demo (reason) |
| B3-41 | Insights collapse | Folds the card (remembered in this browser) | works |
| B3-42 | Overview tab | Jobs, technologies, funding, employees, business phone rows | works |
| B3-43 | News tab | Inline tab, needs a licensed news feed | disabled (reason in tooltip) |
| B3-44 | Technologies (count) | From the company website | works |
| B3-45 | Funding | Stock listings and revenue from Wikidata | works |
| B3-46 | Job postings | From the job board the site links to | works |
| B3-47 | Employee trends | Wikidata headcount chart | works |
| B3-48 | Website visitors tab | Needs a tracking script on your own site | disabled (reason in tooltip) |
| B3-49 | Insight rows | "No relevant ..." empty values in our words | works |
| B3-50 | Company with score badge | Name with record-completeness badge | works |
| B3-51 | Company card "..." | Open the company page, visit the website, create a task | works |
| B3-52 | Company card collapse | Folds the card | works |
| B3-53 | About tab | Company fields with sources; People at this company tab | works |
| B3-54 | Show more (description) | Open data has no company description to expand | n/a (no description in the packs) |

## B4 - Company drawer (`/companies/<id>` in the drawer; full page without htmx)

| # | Control | Pépite | Status |
|---|---|---|---|
| B4-01 | Breadcrumb | Companies > name | works |
| B4-02 | Logo | Initials avatar | works |
| B4-03 | Name + building icon | Opens the full company page | works |
| B4-04 | Link icon | Opens the company website | works |
| B4-05 | Add to list | List picker or new list | works |
| B4-06 | Save company (primary) | One click saves it; once saved: "Saved · N lists" menu with its lists and Add to another list | works |
| B4-07 | Expand | Full page | works |
| B4-08 | Close | Closes the drawer (also Escape) | works |
| B4-09..11 | Overview / People / Locations segmented tabs | Locations: head office (open data), address on its website, places it hires (insights); honest empty state | works |
| B4-12 | Company details collapse | Folds the card | works |
| B4-13 | Show more (description) | No description in open data | n/a (no description in the packs) |
| B4-14 | Industries chips | Industry label | works |
| B4-15 | Keywords chips | Technologies read on its website (insights); how-to when empty | works |
| B4-16 | Show all (count) | Expands the keyword chips | works |
| B4-17 | Founding year, employees, codes | Founding year, employees band, ISIC code, location, LEI, Wikidata | works |
| B4-18 | NAICS chips | ISIC Rev. 4 code chip (what the open packs carry) | works |
| B4-19 | Links | Website, Wikidata, social pages from insights | works |
| B4-20 | Insights gear | As B3-40 | works |
| B4-21 | Insights collapse | As B3-41 | works |
| B4-22 | Insight tabs | As B3-42..48 (News, Website visitors disabled with reasons) | works / disabled |
| B4-23 | Score | Record completeness in the details card | works |
| B4-24 | Score model dropdown | One company score only, nothing to choose | n/a (single score) |
| B4-25 | Show breakdown | Fields behind the score | works |
| B4-26 | Insight rows | As B3-49 | works |
| B4-27 | Suggested leads collapse | Folds the card | works |
| B4-28..30 | All / CXO / Director+ tabs | All / Leaders / Director and up, with counts, from people we hold | works |
| B4-31 | Show filters | The level tabs and the search box cover the few people held per company | n/a (tabs + search instead) |
| B4-32 | Search people | Filters the suggested leads | works |
| (extra) | Find more people | Starts the people search on its website | works; disabled in the demo or without a known website (reason) |

## 11 - Saved-view menu ("Default view")

| # | Control | Pépite | Status |
|---|---|---|---|
| 11-01..06 | Rail, top bar | Shell | see shell parity |
| 11-03 | Credits | | n/a (rule 3) |
| 11-07 | Import | As B1-25 | works |
| 11-08 | Default view trigger | Shows the view in use | works |
| 11-09 | Hide filters | | works |
| 11-10 | Toolbar controls | As B1-28..33 | works |
| 11-11 | Search box | Filters the rows | works |
| 11-12 | All | | works |
| 11-13 | Yours | Your saved searches | works |
| 11-14 | Starred | Starred searches | works |
| 11-15 | Assigned to you | One person per install, nothing to assign | n/a (single user) |
| 11-16 | Shared | Visible tab | disabled (no one to share with on a local install; copy the page address instead) |
| 11-17 | Default view (System) | Built-in, links to the results table | works |
| 11-18 | Row "..." menu | Saved: duplicate, rename, star, delete. Built-in: duplicate; rename, star and delete disabled with the reason | works |
| 11-19..20 | Built-in views | People in your lists, not in a list yet, with a valid email (companies: saved, not saved yet) | works |
| 11-21 | Create saved search | Opens "New saved search" | works |
| 11-22..23 | Filter panel | | see filters parity |
| 11-24 | Table behind | | works |
| 11-25..28 | Pager | | works |
| 11-29 | Help | Shell | see shell parity |

## 12 - Search settings drawer

| # | Control | Pépite | Status |
|---|---|---|---|
| 12-01..02 | Title, close | | works |
| 12-03..04 | Fields row with count | Opens the field editor | works |
| 12-05..06 | Applied filters > Filters with count | Lists every applied filter with remove buttons, Clear per group, Clear all | works |
| 12-07 | Enrichment mode heading | | works |
| 12-08 | Enrichment toggle | People: find emails as soon as people are saved. Companies: find people as soon as a company is saved. Stored on the server | works; disabled in the demo (reason) |
| 12-09 | Helper text | Own words | works |
| (extra) | Results per page, compact rows, show source chips | Kept from the old popover | works |
| 12-10 | Rail | Shell | see shell parity |
| 12-11 | Dimmed background | Scrim; a click on it closes the drawer | works |
| 12-12..15 | Pager | | works |
| 12-16 | Help | Shell | see shell parity |

## 13 - Field editor

| # | Control | Pépite | Status |
|---|---|---|---|
| 13-01..03 | Back, title, close | | works |
| 13-04 | Displayed fields count | | works |
| 13-05 | Name (locked) | Locked first, cannot be removed | works |
| 13-06..09 | Job title, Company, Emails, Phone numbers rows | Drag handle (mouse, or arrow keys on the handle), field dropdown (swap; two fields trade places), remove x | works |
| 13-10..11 | Qualify Contact / Qualify Account | AI answers come from Research with AI as a temporary column | n/a (use Research with AI) |
| 13-12 | Actions (locked) | Row actions column, locked last | works |
| 13-13 | Links | "Source" column: the pages where the record was found | works |
| 13-14 | People Auto-Score | Scores live on the profile; "Email status" column shows verification | n/a (score on the profile) |
| 13-15 | Location | | works |
| 13-16 | Number of employees | "Employees" | works |
| 13-17 | Industries | "Industry" | works |
| 13-18 | Keywords | Keywords are per-company insights, not a table field | n/a |
| 13-19 | Add fields to table (count) | "Add more fields" with count, searchable list | works |
| (extra) | Reset to default | | works |
| 13-20..26 | Rail, dimmed background, pager, help | | shell / works |

Order and choice persist per screen in `localStorage` (`lc-cols-people`, `lc-cols-companies`).

## 14 - Selection bar (icon buttons)

| # | Control | Pépite | Status |
|---|---|---|---|
| 14-01..06 | Rail, top bar | Shell | see shell parity |
| 14-07..09 | Import, Default view, Hide filters | | works |
| 14-10..20 | Clear, Save, Email, Sequence, Workflow, Call, Add to list, Export, AI, Push, More | As B2-26..36; icon buttons with short tips above them | works |
| 14-21..31 | Filter panel | | see filters parity |
| 14-32..34 | Indeterminate header box, headers, selected rows | | works |
| 14-35..39 | Pager, help | | works / shell |

## 15 - Save window

| # | Control | Pépite | Status |
|---|---|---|---|
| 15-01..02 | Title, close | "Save people" / "Save companies" | works |
| 15-03 | Enrichment header with info | "Look up missing details" with an on/off switch and an info tip | works; disabled in the demo (reason) |
| 15-04 | Source badge | "Public web" | works |
| 15-05..06 | Emails only / Emails and phone numbers | Companies: People only / People and phone numbers. Phones = company insights refresh | works; disabled in the demo (reason) |
| 15-07 | Enrichment card | Where we look, how an email is checked, phone numbers (no credit usage) | works |
| 15-08..09 | What is waterfall, Discover waterfall | One source only: the public pages we read | n/a |
| 15-10 | Add to list | Default list "Saved people", an existing list or a new one (companies: optional) | works |
| 15-11 | Add to sequence | Sequences from engage, enrolled with `engage.enroll` | works; disabled for companies (reason) |
| 15-12 | Contact ownership | "Stays on this computer" note (one user, local database) | n/a (replaced by the note) |
| 15-13 | Saving N of N selected | Live summary from `POST /partials/save-summary` | works |
| 15-14 | Verified share bar | Share with a valid email | works |
| 15-15..17 | Status checkboxes with counts | Valid, Catch-all, Not confirmed, Invalid, No email yet | works |
| 15-18 | Include catch-all | The Catch-all box | works |
| 15-19 | Credit estimate card | "What happens": to save, not in a list yet, steps, time estimate | works (no credits, rule 3) |
| 15-20 | Records to save / net new | | works |
| 15-21 | Email enrichment estimate | Email look-ups to start, phone searches | works |
| 15-22 | Export CSV checkbox | "Also download a CSV of what is saved" | works |
| 15-23 | CSV settings gear | CSV columns are fixed (named in the Export tip) | n/a |
| 15-24..25 | Cancel, Save | `POST /bulk/save` | works |
| 15-26 | Help | Shell | see shell parity |

## 16 - Import menu

| # | Control | Pépite | Status |
|---|---|---|---|
| 16-01..06 | Rail, top bar | Shell | see shell parity |
| 16-07 | Import | | works |
| 16-08 | Single contact | People: add one person by hand. Companies: "Single company" | works (people); disabled on companies (records come from the open packs) |
| 16-09 | CSV | Data enrichment page | works |
| 16-10..12 | Selection bar, toolbar | As 14 | works |
| 16-13..23 | Filter panel | | see filters parity |
| 16-24..29 | Table, pager, help | | works / shell |

## 17 - Export tooltip

| # | Control | Pépite | Status |
|---|---|---|---|
| 17-01..07 | Rail, top bar, Import | | shell / works |
| 17-08 | Tooltip "Export" | Short "Export CSV" tip above the icon; the column list is read by screen readers | works |
| 17-09..19 | Selection bar | As 14 | works |
| 17-20..37 | Toolbar, filter panel, table, pager, help | | works / see filters parity / shell |

## 18 - AI menu from the selection bar

| # | Control | Pépite | Status |
|---|---|---|---|
| 18-01..07 | Rail, top bar, Import | | shell / works |
| 18-08 | Run custom AI prompt | Runs your model on the selected rows, temporary column | works with `PEPITE_LLM_URL` + `PEPITE_LLM_MODEL`; otherwise disabled (reason); disabled in the demo |
| 18-09 | Generate AI formula | Same | as 18-08 |
| 18-10 | Use Assistant | "Ask the Assistant" (third, like the reference) | works |
| 18-11 | Start with a template | Four prompt templates | as 18-08 |
| 18-12 | Active trigger | Drops from the bar, aligned to the trigger's right edge | works |
| 18-13..41 | Selection bar, toolbar, filter panel, table, pager, help | As 14 | works / see filters parity / shell |

## 19 - AI menu from the toolbar

| # | Control | Pépite | Status |
|---|---|---|---|
| 19-01..07 | Rail, top bar, Import | | shell / works |
| 19-08..15 | Toolbar controls | As B1-26..33 | works |
| 19-16 | Tooltip "Research with AI" | Tip above the trigger | works |
| 19-17..20 | Menu items | As 18-08..11, same order; then "Or describe who you want, in plain words" turns text into filters (works offline) | works / disabled as 18 |
| 19-21..31 | Filter panel | | see filters parity |
| 19-32..38 | Table, pager, help | | works / shell |

## 20 - Lists, first visit (`/lists` on a fresh home)

| # | Control | Pépite | Status |
|---|---|---|---|
| 20-01..20 | Rail, top bar, help | Shell | see shell parity |
| 20-17 | Credits | | n/a (rule 3) |
| 20-21 | "My lists" title | No header button and no tabs before the first list | works |
| 20-22 | Create a people list | Opens the create dialog set to people | works |
| 20-23 | Create a company list | Opens the create dialog set to companies | works |
| 20-24 | Learn more about lists | "How lists work" expander on the page | works |

## Files outside this agent's set that were touched

- `templates/people.html` and `templates/companies.html`: one `phone` branch each in the cell
  loop (the Phone column needs a cell).
- `templates/_search_macros.html`: `ai_menu` (owned) and the disabled states of `row_actions`.
- `tests/test_pages.py`, `tests/test_pages_extra.py`, `tests/test_insights.py`: assertions
  updated to the new markup (saved-view rows, field editor data, banner, start screen).
