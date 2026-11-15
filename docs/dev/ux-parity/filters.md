# UX parity: search filters (People and Companies)

Status of every filter control listed in `docs/dev/ux-inventory.md` for screens A4, A5, 05, 06,
08, 09, 10 and the filter rows of B1, in LeadCommons. **works** = does the job for real;
**disabled** = visible, `aria-disabled`, with a tooltip saying why (and how to turn it on when
that is possible); **n/a** = not part of LeadCommons, with the reason.

Rows that belong to other parity files are listed once per screen as a range:

- sidebar rail, top bar, command palette, notifications, avatar, help button: the shell
  (`base.html`), see `shell-and-settings.md`;
- page title, Import, Default view, Hide filters, search box, Research with AI, Create
  workflow, Save as new search, sort, Search settings, the results table and pagination: see
  `results-and-records.md`;
- upgrade buttons, credit pills and upsell strips: n/a everywhere (rule 3: LeadCommons is free,
  no plans, no credits).

Code: `web/search_filters.py` (parsing, matching, groups, chips, quick filters, suggestions),
`store/personas.py` (saved personas), `packs/duck.py` (places, exclusions and keyword options in
SQL), templates `_search_filters.html`, `_search_panel.html`, `_search_modal.html`,
`_search_landing.html`, `static/css/search-filters.css`, `static/js/search-filters.js`.
Routes: `GET /search/suggest`, `POST /personas`, `POST /personas/{id}/delete`, `GET /search/count`.
Tests: `tests/test_search_filters_v3.py`.

## A4 - Find people landing (`/people` with nothing asked)

The landing replaces the table while there is no filter, no search text, the Total tab and the
default sort. "Show all people" (`/people?all=1`) and the Total tab open the full table. A store
with no people keeps its own empty state ("No people yet", with a link to Companies).

| # | Control | LeadCommons | Status |
|---|---|---|---|
| A4-01..32 | Sidebar, top bar, help | Shell | see shell parity |
| A4-21, A4-29 | Upgrade button, credits pill | No plans, no credits | n/a (rule 3) |
| A4-33..42 | Title, Import, toolbar | Toolbar | see results parity |
| A4-43 | Total tab with count | All matching people; on the landing it opens the full table (`?all=1`) | works |
| A4-44 | Net new tab with count | People in no list yet | works |
| A4-45 | Saved tab with count | People saved to a list | works |
| A4-46 | Job titles accordion | Opens the Job titles filter (see 05) | works |
| A4-47 | Hover tooltip on the filter header | Native tooltip "Show or hide the job titles filter" on every group header | works |
| A4-48 | People lookalikes | People with the same seniority at companies like the one of a person you found | works |
| A4-49 | Company | Exact domain | works |
| A4-50 | Location | See 06 | works |
| A4-51 | Industry & keywords | See 08 | works |
| A4-52 | Clear all | Goes back to the landing; disabled with a tooltip while nothing is applied | works |
| A4-53 | View 60+ filters | "View all filters" opens the All filters window (see 09) | works |
| A4-54 | AI prompt | "Describe the people you want to reach": plain words become filters (`POST /api/ai/parse`), built-in rules offline, your own model with `LEADCOMMONS_LLM_URL` + `LEADCOMMONS_LLM_MODEL` (the note under the box says how); rules only in the public demo | works |
| A4-55..56 | Locations chips | The most common countries of your people's companies, with counts | works |
| A4-57..59 | Email status chips | Valid, Catch-all, Unknown (our verification statuses) | works |
| A4-60..62 | Job titles chips | The three most common titles among your people, with counts (Founder, Sales Manager, Marketing Director when no title is known) | works |
| A4-63..65 | Industry chips | The most common ISIC sections of your people's companies, with counts | works |
| A4-66..70 | "Unlock advanced filters" strip, gated labels, View plans | Replaced by a footer: number of people saved on this computer + "Show all people" | n/a (rule 3) |

## A5 - Find companies landing (`/companies` with nothing asked)

| # | Control | LeadCommons | Status |
|---|---|---|---|
| A5-01..32 | Sidebar, top bar, help | Shell | see shell parity |
| A5-21, A5-29 | Upgrade button, credits pill | No plans, no credits | n/a (rule 3) |
| A5-33..42 | Title, Import, toolbar | Toolbar | see results parity |
| A5-43..45 | Total / Net new / Saved tabs | As A4, over the company packs (Saved = saved to a list) | works |
| A5-46 | Company | Name or domain (toolbar search) and exact domain | works |
| A5-47 | Lookalikes | Companies with the same industry, size band and country | works |
| A5-48 | Account location | "Location": cities, regions and countries (see 06, without the Contact tab) | works |
| A5-49 | # Employees | See 10 | works |
| A5-50 | Industry & keywords | See 08 | works |
| A5-51 | Website visitors | Needs a tracking script on your own website | disabled (Soon) |
| A5-52 | Clear filters | "Clear all" | works |
| A5-53 | View 30+ filters | "View all filters" | works |
| A5-54 | AI prompt | "Describe the companies you are looking for", as A4-54 | works |
| A5-55..56 | Locations chips | The three countries with the most companies in your packs, with counts | works |
| A5-57..59 | Employee count chips | 1-10, 11-50, 51-200 (the size buckets of the packs), with counts | works |
| A5-60..62 | Industry chips | The three largest ISIC sections in your packs, with counts | works |
| A5-63..65 | Upsell strip, View plans | Footer with the number of companies in your packs + "Show all companies" | n/a (rule 3) |

## B1 - Find people results: filter rows (`/people?title=...`)

| # | Control | LeadCommons | Status |
|---|---|---|---|
| B1-34..36 | Total / Net new / Saved tabs | Counts follow the filters | works |
| B1-37 | Job titles header, badge "1" with x | Count of active values; x clears the whole group | works |
| B1-38 | "Include titles" chip with x | Under every closed group with active values: "Titles: founder ×" (also Not titles, Level, Department, Places, Not in, Industries, Keywords, ...), an orange dot before the group; x removes that one value | works |
| B1-39 | People lookalikes | As A4-48 | works |
| B1-40 | Company | As A4-49 | works |
| B1-41 | Location | See 06 | works |
| B1-42 | Industry & keywords | See 08 | works |
| B1-43 | Clear all with count | Count of all active values | works |
| B1-44 | View 60+ filters | "View all filters" | works |

## 05 - Job titles filter open

| # | Control | LeadCommons | Status |
|---|---|---|---|
| 05-01..06 | Rail, top bar | Shell | see shell parity |
| 05-03 | Credits pill | No credits | n/a (rule 3) |
| 05-07..15 | Import, toolbar | Toolbar | see results parity |
| 05-16..18 | Total / Net new / Saved tabs | As B1 | works |
| 05-19 | Header with badge and x, chevron up | Collapses the group; x clears it | works |
| 05-20 | Simple | Include / Exclude boxes | works |
| 05-21 | Advanced | One boolean expression, for example `head AND (sales OR growth) NOT intern`: AND, OR, NOT in capitals, brackets, "quoted phrases", `-word`; two words side by side must both appear. Read by a small parser (never run as code); a broken expression shows what is wrong and is ignored until fixed. Switching to Advanced turns the Simple titles into an expression | works |
| 05-22 | Include chip with x | Removes that title | works |
| 05-23 | Include box with clear x and chevron | Suggestions while you type: titles already in your people (with counts) then a built-in list of common titles; Enter or comma adds what you typed; Backspace removes the last chip; x clears the box | works |
| 05-24 | Exact-match hint | "Write a title in "quotes" to match only that exact title." (a quoted title must equal the whole job title) | works |
| 05-25 | Similar titles checkbox | "Also match similar titles": CEO also finds chief executive officer, founder also finds co-founder | works |
| 05-26 | Info icon | Tooltip with examples | works |
| 05-27 | Exclude box | Same box, values shown in red | works |
| 05-28 | Past job titles | LeadCommons keeps only the current title of each person | disabled (tooltip) |
| 05-29 | Management level dropdown | Checkbox list of seniority levels (owner, founder, C-suite, partner, VP, head, director, manager, senior, entry, intern) | works |
| 05-30 | Departments & job function dropdown | "Departments": the stored department of a person, else read from the job title with the keyword table `search_filters.DEPARTMENTS` (leadership, engineering, IT and security, data, product, design, sales, marketing, customer success, finance, people and HR, operations, legal, research) | works |
| 05-31 | Save as new persona | Shown while Job titles is open: a window names the current titles, level and departments (`POST /personas`); the persona then appears in the Persona filter (09-15), where it can be ticked or deleted. Disabled with a tooltip until a title, level or department is set | works |
| 05-32 | Clear all with count | As B1-43 | works |
| 05-33 | View 60+ filters | "View all filters" | works |
| 05-34..39 | Table header, rows, pagination | Results table | see results parity |
| 05-40 | Help | Shell | see shell parity |

## 06 - Location filter open

| # | Control | LeadCommons | Status |
|---|---|---|---|
| 06-01..15 | Rail, top bar, toolbar | Shell / toolbar | see shell and results parity |
| 06-16..18 | Total / Net new / Saved tabs | As B1 | works |
| 06-19..20 | Job titles header and chip row | As B1-37/38 | works |
| 06-21..22 | People lookalikes, Company | As A4 | works |
| 06-23 | Location header | Collapses the group | works |
| 06-24 | Contact tab | People records keep no personal location; LeadCommons only knows where each person's company is based | disabled (tooltip) |
| 06-25 | Account HQ tab | "Company HQ": the location of the person's company (selected) | works |
| 06-26 | Select region radio | "Region" (selected) | works |
| 06-27 | City / State / Country / ZIP box | "City, region or country": suggestions from the packs (countries by English name or code, regions and cities with their country and company count); a typed country name becomes that country, any other text matches a region or a city. Places are alternatives (any of them matches). Postal codes: the packs hold none | works (postal codes n/a: no postal codes in the open packs) |
| 06-28 | Exclude locations | Disclosure with the same box; excluding a country also leaves out records with no country | works |
| 06-29 | Select ZIP code radius | "Distance from a postal code": needs postal codes and map coordinates, which the open packs do not include yet | disabled (tooltip) |
| 06-30..31 | Clear all, View all filters | As B1 | works |
| 06-32..37 | Table, pagination | Results table | see results parity |
| 06-38 | Help | Shell | see shell parity |

## 08 - Industry & keywords filter open

| # | Control | LeadCommons | Status |
|---|---|---|---|
| 08-01..15 | Rail, top bar, toolbar | Shell / toolbar | see shell and results parity |
| 08-16..18 | Total / Net new / Saved tabs | As B1 | works |
| 08-19 | Location accordion | See 06 | works |
| 08-20 | Header, chevron up | Collapses the group | works |
| 08-21 | Search industries box | ISIC sections and divisions from the packs with company counts; typed text is matched to a section name | works |
| 08-22 | Advanced settings | Disclosure with "Exclude industries" (records with no industry are left out when you exclude one) | works |
| 08-23 | Company keywords header with ? | Collapsible; the ? tooltip says where keywords look | works |
| 08-24 | Include keywords checkbox | Shows or hides the keyword box; unticking clears the keywords | works |
| 08-25 | Keyword box "e.g. Cloud, AWS" | Free text, Enter or comma adds a keyword | works |
| 08-26 | Type of keywords with warning | "Keyword type": Name, domain and industry / Company name and domain / Industry label (the packs hold no company descriptions); the warning tooltip says keyword filters are slower on large packs | works |
| 08-27 | Include ALL | "Match all keywords" (otherwise any keyword is enough) | works |
| 08-28 | Exclude keywords | Checkbox that reveals a red keyword box | works |
| 08-29 | "Keywords filters may slow down your search" | Own wording: "Keyword filters can slow down a search on a large pack." | works |
| 08-30..31 | Clear all, View all filters | As B1 | works |
| 08-32..37 | Table, pagination | Results table | see results parity |
| 08-38 | Help | Shell | see shell parity |

## 09 - All filters window

Four columns: Pinned filters, Person info, Company info, Engagement. All four fit in the window.

| # | Control | LeadCommons | Status |
|---|---|---|---|
| 09-01 | Filters title | "Filters" | works |
| 09-02 | Close x | Closes the window (Esc and a click outside too) | works |
| 09-03 | Search filters | Hides the rows whose name does not match | works |
| 09-04 | Type: All | All / Person / Company / Engagement: shows only that column (Pinned stays) | works |
| 09-05 | "N records found" | Live count (`GET /search/count`) that follows every change in the window, including values picked in a box; "1 record found" in the singular | works |
| 09-06 | Apply filters | Applies the window and closes it | works |
| 09-07 | Pinned filters section | The groups shown in the side panel | works |
| 09-08 | Drag handles | Drag a pinned row by its handle, or focus the handle and use the arrow keys: the order is saved in the browser with the pins (`lc-pins-people` / `lc-pins-companies`) and the side panel follows it | works |
| 09-09..13 | Pinned rows: pin toggle + chevron | Job titles, People lookalikes, Company, Location, Industry & keywords pinned by default (Companies: Company, Lookalikes, Location, # Employees, Industry & keywords); the pin removes a row from the side panel or adds any supported group; the chevron opens it inline | works |
| 09-14 | Industry sub-controls inline | The same controls as 08 inside the window | works |
| 09-15 | Persona | Tick saved personas to keep the people who match them; trash button deletes one | works |
| 09-16 | Email status | Valid, Catch-all, Unknown, Invalid | works |
| 09-17 | Name | Name or job title | works |
| 09-18 | Education | Not in any open dataset we can use lawfully | disabled (No open data) |
| 09-19 | Awards & certifications | No open dataset lists them | disabled (No open data) |
| 09-20 | Enrichment type | Every person comes from a public page you asked LeadCommons to read or from your own import; there is no enrichment type to choose | n/a |
| 09-21 | Work URLs | Profile links are not collected | disabled (Not collected) |
| 09-22 | Time zone | People have no time zone on record; filter by country instead | disabled (No data) |
| 09-23 | Company lookalikes | As A5-47 | works |
| 09-24 | # Employees | See 10 | works |
| 09-25 | Market segments | The packs classify companies by ISIC industry only | disabled (No data) |
| 09-26 | SIC and NAICS | The public registers give ISIC codes; pick them in Industry & keywords | disabled (Use ISIC) |
| 09-27 | Buying intent | Paid third-party signals; no free source | disabled (No free source) |
| 09-28 | Technologies | Companies whose refreshed insights list the technology | works |
| 09-29 | # Employees by department | Not published in open data | disabled (No open data) |
| 09-30 | Headcount growth | Employee trends are not stored in a searchable way yet | disabled (Soon) |
| 09-31 | Sequence | People enrolled in the ticked sequences, or "In no sequence" | works |
| 09-32 | Workflow | Filtering by workflow runs is not built yet; each workflow lists its runs | disabled (Soon) |
| 09-33 | Email opened | LeadCommons never adds a tracking pixel, so opens are not recorded | disabled (Not tracked) |
| 09-34 | Last activity | Notes, calls, emails, tasks and meetings logged for the person: last 7 / 30 / 90 days, or none yet | works |
| 09-35 | Email sent | Emailed / never emailed (sent messages) | works |
| 09-36 | Email clicked | "Link clicked": links are sent as written, so clicks are not recorded | disabled (Not tracked) |
| 09-37 | Email replied | "Replied": a reply was received / no reply yet | works |
| 09-38 | Email meeting | "Meeting booked": has a meeting / none yet | works |
| 09-39 | Further columns off-screen | All four columns fit; nothing is clipped | n/a |
| 09-40 | Help | Shell | see shell parity |
| (extra) | Job postings, Funding, Revenue, Website visitors | Job postings works (refreshed insights); the others are disabled with their reason | works / disabled |

On the Companies screen the person and engagement filters are shown disabled with "People page"
(they filter people, not companies).

## 10 - All filters window with # Employees open

| # | Control | LeadCommons | Status |
|---|---|---|---|
| 10-01..05 | Title, close, search, type, count, Apply | As 09 | works |
| 10-06 | Pinned rows with drag handle, pin, chevron | As 09-08..13 | works |
| 10-07 | Person info rows | As 09-15..22 | works / disabled as listed in 09 |
| 10-08 | Company lookalikes | As A5-47 | works |
| 10-09 | # Employees header: pin + chevron up | Pins it to the side panel; collapses it | works |
| 10-10 | Predefined range radio | "Predefined ranges" (selected); the other choice, "Custom range", gives a min-max pair that matches every bucket it overlaps. Switching clears the other choice | works |
| 10-11..21 | Range checkboxes with counts | The eight buckets of the packs (1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10000, 10001+) with counts that follow the other filters (refreshed when the results load); all fit without scrolling | works |
| 10-22 | Engagement column | As 09-31..38 | works / disabled as listed in 09 |
| 10-23 | Help | Shell | see shell parity |
