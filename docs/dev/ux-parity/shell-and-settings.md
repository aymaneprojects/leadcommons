# Shell and settings: control status

Status of every control in the reference screens for the app shell, home, assistant, command palette, notifications and settings. Source of the rows: `docs/dev/ux-inventory.md`. Proof: `tests/test_shell_parity_v3.py`.

Legend: **works**, **disabled (reason)**, **not applicable (reason)**.


## A1 - AI Assistant full-page welcome (onboarding chat with option cards, dark theme)

| Row | Control | Status in LeadCommons |
|---|---|---|
| A1-01 | Explore Apollo (arrow) | works (button 'Back to the app', goes Home) |
| A1-02 | Assistant message text (long, scrollable) | works |
| A1-03 | Copy (two squares icon) | works (copies the answer) |
| A1-04 | Thumbs up | works |
| A1-05 | Thumbs down | works |
| A1-06 | Timestamp '7 minutes ago' | works |
| A1-07 | 'Select one of the following' option card | works (welcome option card; later choices are stored with the chat) |
| A1-08 | Radio: See companies I can sell to | works |
| A1-09 | Radio: See leads in my market | works |
| A1-10 | Radio: Set up my company profile | works |
| A1-11 | Radio: Prospect different company | works |
| A1-12 | Continue (disabled until option chosen) | works |
| A1-13 | Message composer 'Find leads, save your preferences, or type / for commands' | works |
| A1-14 | Slash '/' command trigger (hint in placeholder) | works (type / for the command menu; the / button opens it too) |
| A1-15 | Plus (+) attach button | works (+ attaches a CSV, offered to the enrichment flow, or a text file kept as context) |
| A1-16 | Send (up-arrow blue circle) | works |
| A1-17 | Microphone | works where the browser has speech recognition (Chrome, Edge, Safari); otherwise disabled with the reason in the tooltip |
| A1-18 | 'AI Policy' link | works (link to the privacy notes) |
| A1-19 | Disclaimer 'Chats may be shared with third parties...' | works (own wording: chats stay on this machine) |
| A1-20 | 'Bring Apollo into Claude or ChatGPT' card close x | works (dismisses the tip card, remembered) |
| A1-21 | Claude (external-link icon) button | works (opens the MCP guide for Claude) |
| A1-22 | ChatGPT (external-link icon) button | works (opens the MCP guide for ChatGPT) |

## A2 - Home 'Get started with Apollo' with collapsed icon rail

| Row | Control | Status in LeadCommons |
|---|---|---|
| A2-01 | Expand sidebar chevron '>>' (top) | works |
| A2-02 | Brand logo (top) | works |
| A2-03 | Home (house icon) | works |
| A2-04 | AI Assistant (logo icon) | works |
| A2-05 | Prospect and enrich (magnifier icon) | works |
| A2-06 | Engage (paper-plane icon) | works |
| A2-07 | Win deals (dollar icon) | works |
| A2-08 | Lightbulb-style icon (4th group, unlabeled) | works (Tools and automation group) |
| A2-09 | Arrow-into-door icon (5th group, unlabeled) | not applicable (LeadCommons has four groups; there is no Inbound or Team group) |
| A2-10 | People-group icon (6th group, unlabeled) | not applicable (LeadCommons has four groups; there is no Inbound or Team group) |
| A2-11 | Onboarding hub icon (list/document, bottom) | works |
| A2-12 | Email setup and health icon (diamond/nodes, bottom) | works |
| A2-13 | Admin Settings (gear, bottom) | works (Admin settings row opens the quick settings flyout) |
| A2-14 | Help button (circled ? + 'Help', bottom right) | works (Help menu: docs, shortcuts, Assistant, report a problem) |
| A2-15 | Global search box 'Search or ask a question in Apollo' | works |
| A2-16 | Keyboard hint badge 'Cmd K' (shortcut) | works |
| A2-17 | '75 credits' pill | not applicable (no credits in LeadCommons; the top bar shows the background jobs pill instead) |
| A2-18 | AI Assistant button (icon only, white tile) | works |
| A2-19 | Notification bell (with red dot when unread) | works |
| A2-20 | User avatar with initials | works |
| A2-21 | Getting started (table icon + chevron) | works |
| A2-22 | 'Pick up where you left off with the Assistant' banner | works |
| A2-23 | Continue chat | works |
| A2-24 | Close x | works |
| A2-25 | Overall progress bar | works |
| A2-26 | Section 'Start reaching the right prospects' header with 'Earn 300 credits' badge | works (accordion header); the 'Earn credits' badge is not applicable (no credits) |
| A2-27 | '0 of 6 completed' with caret | works |
| A2-28 | Checkbox: Save a contact | works |
| A2-29 | Save a prospect (yellow) | works |
| A2-30 | Checkbox: Save a mobile number | works (our task: find a company phone number) |
| A2-31 | Find numbers | works (our task list differs; same behaviour) |
| A2-32 | Checkbox: Create a list | works |
| A2-33 | Create a list | works |
| A2-34 | Checkbox: Save target companies | works |
| A2-35 | Save a company | works |
| A2-36 | Checkbox: Save your search (partially visible) | works |
| A2-37 | Set up alert (partially visible) | works |

## A3 - Home 'Get started with Apollo' with expanded labelled sidebar

| Row | Control | Status in LeadCommons |
|---|---|---|
| A3-01 | Collapse sidebar chevron '<<' | works |
| A3-02 | Brand logo (top-left) | works |
| A3-03 | Home | works |
| A3-04 | Phone / dialer icon next to Home | works (opens the Calls page where calls are logged by hand; there is no softphone) |
| A3-05 | AI Assistant | works |
| A3-06 | Prospect and enrich (group header, chevron up) | works |
| A3-07 | People | works |
| A3-08 | Companies | works |
| A3-09 | Lists | works |
| A3-10 | Data enrichment | works |
| A3-11 | Engage (group header, chevron up) | works |
| A3-12 | Sequences | works |
| A3-13 | Emails | works |
| A3-14 | Calls | works |
| A3-15 | Tasks | works |
| A3-16 | Win deals (group header, chevron up) | works |
| A3-17 | Meetings | works |
| A3-18 | Conversations | works |
| A3-19 | Deals | works |
| A3-20 | Clipped further group (partially hidden under the scroll edge) | works (Tools and automation group) |
| A3-21 | Upgrade to Professional (yellow crown button) | not applicable (free and open source, no plans or upgrades) |
| A3-22 | Onboarding hub card (title, progress bar, '0% Completed') | works |
| A3-23 | Email setup and health | works |
| A3-24 | Admin Settings (chevron right) | works |
| A3-25 | Help button (circled ? + 'Help') | works |
| A3-26 | Global search box 'Search or ask a question in Apollo' | works |
| A3-27 | Keyboard hint badge 'Cmd K' (shortcut) | works |
| A3-28 | '75 credits' pill | not applicable (no credits in LeadCommons; the top bar shows the background jobs pill instead) |
| A3-29 | 'AI Assistant' button (logo icon + label) | works |
| A3-30 | Notification bell (with red dot when unread) | works |
| A3-31 | User avatar with initials | works |
| A3-32 | Getting started (table icon + chevron) | works |
| A3-33 | 'Pick up where you left off with the Assistant' banner | works |
| A3-34 | Continue chat | works |
| A3-35 | Close x | works |
| A3-36 | Overall progress bar | works |
| A3-37 | Section 'Start reaching the right prospects' + 'Earn 300 credits' badge | works (accordion header); the 'Earn credits' badge is not applicable (no credits) |
| A3-38 | '0 of 6 completed' with caret | works |
| A3-39 | Checkbox: Save a contact | works |
| A3-40 | Save a prospect (yellow) | works |
| A3-41 | Checkbox: Save a mobile number | works |
| A3-42 | Find numbers | works |
| A3-43 | Checkbox: Create a list | works |
| A3-44 | Create a list | works |
| A3-45 | Checkbox: Save target companies | works |
| A3-46 | Save a company | works |
| A3-47 | Checkbox: Save your search (partial) | works |
| A3-48 | Set up alert (partial) | works |

## 24 - Command palette opened with Cmd/Ctrl+K over the Tasks page

| Row | Control | Status in LeadCommons |
|---|---|---|
| 24-01 | Search input 'Search or ask a question in Apollo' | works |
| 24-02 | 'AI suggestions' label | works |
| 24-03 | Suggestion: Build your target audience (arrow) | works (Build your target audience: opens the Assistant with a prompt) |
| 24-04 | Suggestion: Build your TAM (arrow, highlighted) | works (our wording: Size up my market) |
| 24-05 | Suggestion: Craft outbound sequence (arrow) | works (our wording: Draft an outbound sequence) |
| 24-06 | Up/Down arrows 'Navigate' | works |
| 24-07 | esc 'Close' | works |
| 24-08 | Enter 'Select' | works |
| 24-09 | Top-bar credits, assistant, bell, avatar; Tasks page controls (inert) | not applicable (background page) |

## 25 - Notifications panel (Activities / Notifications tabs)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 25-01 | Tab: Activities | works |
| 25-02 | Tab: Notifications (selected) | works |
| 25-03 | Close x (top right of panel) | works |
| 25-04 | 'Older' group label | works |
| 25-05 | 'There are no CRM integrations connected yet.' card (timestamp) | works (our notice: no webhook or integration yet) |
| 25-06 | Dismiss x on card | works |
| 25-07 | Go To Settings | works ('Go to Integrations') |
| 25-08 | Bell icon (active) | works |
| 25-09 | Top-bar credits, assistant, avatar; Tasks page | not applicable (background page) |

## 26 - Admin Settings quick flyout (Team & Workspace setup)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 26-01 | 'Team & Workspace setup' title with '0% Completed' progress | works |
| 26-02 | Users and teams | not applicable (single user; the Invite teammates button explains sharing) |
| 26-03 | System activity | not applicable (no audit log; the Activities tab of the notifications panel lists what happened) |
| 26-04 | Security | not applicable (no separate security page; password status is on Account) |
| 26-05 | Plan overview | not applicable (free and open source, no plans or upgrades) |
| 26-06 | Integrations | works |
| 26-07 | All settings | works |
| 26-08 | Admin Settings gear (active) | works |
| 26-09 | Collapsed rail, top bar, Tasks page controls | not applicable (background page) |

## 27 - Settings overview (Workspace settings)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 27-01 | Back (arrow) | works |
| 27-02 | Collapse '<<' | works |
| 27-03 | Search settings input | works |
| 27-04 | Settings overview (selected) | works |
| 27-05 | Personal (chevron) | works |
| 27-06 | Plan & billing (chevron) | not applicable (free and open source, no plans or upgrades) |
| 27-07 | Credits & activity (chevron) | not applicable (no credits in LeadCommons; the top bar shows the background jobs pill instead) |
| 27-08 | Users & teams (chevron) | not applicable (single user; see Invite teammates) |
| 27-09 | Integrations (chevron) | works |
| 27-10 | Outbound (chevron) | works |
| 27-11 | Data management (chevron) | works |
| 27-12 | Ideal customer profile (chevron) | not applicable (no ICP settings; saved searches play that role) |
| 27-13 | Meetings & conversations (chevron) | not applicable (nothing to configure for meetings; deal stages live under Deals) |
| 27-14 | Add teammates (button, bottom) | disabled in local mode (single user on this computer; run it on a server to share, with a link to the self-hosting guide); in server mode it opens a how-to-share dialog (server address, password login, API tokens page) |
| 27-15 | Global search + Cmd K | works |
| 27-16 | 75 credits, assistant icon, bell, avatar | works (assistant, bell, avatar); the credits pill is not applicable (no credits) |
| 27-17 | Plan & billing: 'Professional' badge | works |
| 27-18 | Plan overview link | works |
| 27-19 | Product Add-ons link | works |
| 27-20 | Billing link | works |
| 27-21 | Credits & activity: Upgrade plan (button) | works |
| 27-22 | Credit usage link | works |
| 27-23 | Data requests link | works |
| 27-24 | AI word usage link | works |
| 27-25 | Users & teams: Add teammates (button) | works |
| 27-26 | Users link | works |
| 27-27 | Workspace details link | works |
| 27-28 | Teams link with 'Upgrade' badge | works |
| 27-29 | Team sharing & defaults link | works |
| 27-30 | Permission profiles link with 'Upgrade' badge | works |
| 27-31 | System activity log link | works |
| 27-32 | Territories link with 'Upgrade' badge | works |
| 27-33 | Security link | works |
| 27-34 | License settings link | works |
| 27-35 | Support access link | works |
| 27-36 | Integrations: All integrations link | works |
| 27-37 | MCP link | works |
| 27-38 | API Keys link | works |
| 27-39 | Help button | works |

## 28 - Settings overview with 'Outbound' group expanded

| Row | Control | Status in LeadCommons |
|---|---|---|
| 28-01 | Back, collapse '<<', Search settings, Settings overview, Personal, Plan & billing, Credits & activity, Users & teams, Integrations | works |
| 28-02 | Outbound (chevron up, expanded) | works |
| 28-03 | Overview (Outbound) | works (Email setup and health, Overview tab) |
| 28-04 | Domains & Mailboxes | works (Domains & mailboxes tab) |
| 28-05 | Sending policies | works (Sending policies tab, read from the sending rules) |
| 28-06 | Sequences | not applicable (sequence defaults live in Sending rules) |
| 28-07 | Tracking | not applicable (LeadCommons does not track opens or clicks) |
| 28-08 | Dialer | not applicable (no dialer, no telephony) |
| 28-09 | Prospecting configuration | not applicable (saved searches cover prospecting defaults) |
| 28-10 | Snippets | not applicable (no snippets feature yet) |
| 28-11 | Data management, Ideal customer profile, Meetings & conversations (chevrons) | works (Data management, Deals, Integrations groups; no ICP or meetings group) |
| 28-12 | Add teammates | disabled in local mode with the reason; works in server mode (see 27-14) |
| 28-13 | All overview cards and links as in 27 | works |
| 28-14 | Global search + Cmd K, 75 credits, assistant icon, bell, avatar | works; the credits pill is not applicable |
| 28-15 | Help button | works |

## 29 - Email setup and health > Domains & Mailboxes (empty)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 29-01 | Same nav as 28 with 'Domains & Mailboxes' selected | works |
| 29-02 | 'Email setup and health' title | works |
| 29-03 | Badge 'Workspace deliverability: Excellent' (shield) | works (pill computed from the saved DNS checks: not set up, not checked yet, needs work, good) |
| 29-04 | Run a check (icon) | works (re-runs MX, SPF, DKIM, DMARC for every sending domain; disabled until a mailbox exists) |
| 29-05 | Add email & domain (yellow, caret) | works (menu: connect a mailbox, check any domain) |
| 29-06 | Overview | works |
| 29-07 | Domains & Mailboxes (selected) | works |
| 29-08 | Sending policies | works |
| 29-09 | 'No domains or mailboxes yet' card | works (empty panel with skeleton rows) |
| 29-10 | Add email & domain (yellow, caret) | works |
| 29-11 | 'Let <AI helper name> get you set up' text | works (own wording) |
| 29-12 | Set up with AI (purple) | works with a language model configured (opens the Assistant with a setup prompt); otherwise disabled with the hint; off in the demo |
| 29-13 | 'What happens next' text with 'guide to domains and mailboxes' link | works (link to the mailbox guide) |
| 29-14 | Global search + Cmd K, 75 credits, assistant icon, bell, avatar | works |
| 29-15 | Help button | works |

## 30 - Home onboarding hub (top, task list)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 30-01 | Expand sidebar chevron '>>' (top) | works |
| 30-02 | Brand logo (top) | works |
| 30-03 | Home (house icon) | works |
| 30-04 | AI Assistant (logo icon) | works |
| 30-05 | Prospect and enrich (magnifier icon) | works |
| 30-06 | Engage (paper-plane icon) | works |
| 30-07 | Win deals (dollar icon) | works |
| 30-08 | Lightbulb-style icon (4th group, unlabeled) | works |
| 30-09 | Arrow-into-door icon (5th group, unlabeled) | works |
| 30-10 | People-group icon (6th group, unlabeled) | works |
| 30-11 | Onboarding hub icon (list/document, bottom) | works |
| 30-12 | Email setup and health icon (diamond/nodes, bottom) | works |
| 30-13 | Admin Settings (gear, bottom) | works |
| 30-14 | Help button (circled ? + 'Help', bottom right) | works |
| 30-15 | Global search box 'Search or ask a question in Apollo' | works |
| 30-16 | Keyboard hint badge 'Cmd K' (shortcut) | works |
| 30-17 | '75 credits' pill | not applicable (no credits in LeadCommons; the top bar shows the background jobs pill instead) |
| 30-18 | AI Assistant button (icon only, white tile) | works |
| 30-19 | Notification bell (with red dot when unread) | works |
| 30-20 | User avatar with initials | works |
| 30-21 | Getting started (table icon + chevron) | works |
| 30-22 | Title 'Get started with Apollo' + subtitle | works |
| 30-23 | Overall progress bar | works |
| 30-24 | Section 'Start reaching the right prospects' with 'Earn 300 credits' badge | works (accordion header); the 'Earn credits' badge is not applicable (no credits) |
| 30-25 | '0 of 6 completed' with caret | works |
| 30-26 | Save a contact - Save a prospect (yellow) | works |
| 30-27 | Save a mobile number - Find numbers | works |
| 30-28 | Create a list - Create a list | works |
| 30-29 | Save target companies - Save a company | works |
| 30-30 | Save your search - Set up alert | works |
| 30-31 | Download the Chrome extension - Install extension | not applicable (no browser extension; LeadCommons reads public pages itself) |
| 30-32 | Section 'Get your inbox ready to send' with 'Earn 150 credits' badge and '0 of 3 completed' caret | works (accordion header) |

## 31 - Home onboarding hub (scrolled, remaining sections and resources)

| Row | Control | Status in LeadCommons |
|---|---|---|
| 31-01 | Expand sidebar chevron '>>' (top) | works |
| 31-02 | Brand logo (top) | works |
| 31-03 | Home (house icon) | works |
| 31-04 | AI Assistant (logo icon) | works |
| 31-05 | Prospect and enrich (magnifier icon) | works |
| 31-06 | Engage (paper-plane icon) | works |
| 31-07 | Win deals (dollar icon) | works |
| 31-08 | Lightbulb-style icon (4th group, unlabeled) | works |
| 31-09 | Arrow-into-door icon (5th group, unlabeled) | works |
| 31-10 | People-group icon (6th group, unlabeled) | works |
| 31-11 | Onboarding hub icon (list/document, bottom) | works |
| 31-12 | Email setup and health icon (diamond/nodes, bottom) | works |
| 31-13 | Admin Settings (gear, bottom) | works |
| 31-14 | Help button (circled ? + 'Help', bottom right) | works |
| 31-15 | Global search box 'Search or ask a question in Apollo' | works |
| 31-16 | Keyboard hint badge 'Cmd K' (shortcut) | works |
| 31-17 | '75 credits' pill | not applicable (no credits in LeadCommons; the top bar shows the background jobs pill instead) |
| 31-18 | AI Assistant button (icon only, white tile) | works |
| 31-19 | Notification bell (with red dot when unread) | works |
| 31-20 | User avatar with initials | works |
| 31-21 | Getting started dropdown (floating) | works |
| 31-22 | Set up alert (clipped), Install extension | works ('Set up alert' is our saved-search task); 'Install extension' is not applicable (no browser extension) |
| 31-23 | Get your inbox ready to send (Earn 150 credits) - '0 of 3 completed' caret | works |
| 31-24 | Scale your outreach (Earn 150 credits) - '0 of 3 completed' caret | works |
| 31-25 | Turn activity into meetings (Earn 150 credits) - '0 of 3 completed' caret | works |
| 31-26 | Heading 'More resources to help you master Apollo' | works |
| 31-27 | Card: Learn with Apollo Academy | not applicable (no Academy; the first card links to the documentation) |
| 31-28 | Card: Watch a webinar | not applicable (no webinars; the second card opens the Assistant) |
| 31-29 | Card: Go deeper with help docs | works (third card opens an issue on GitHub; docs card above) |

## 32 - AI Assistant side panel (empty state) opened beside Home

| Row | Control | Status in LeadCommons |
|---|---|---|
| 32-01 | Expand sidebar chevron '>>' (top) | works |
| 32-02 | Brand logo (top) | works |
| 32-03 | Home (house icon) | works |
| 32-04 | AI Assistant (logo icon) | works |
| 32-05 | Prospect and enrich (magnifier icon) | works |
| 32-06 | Engage (paper-plane icon) | works |
| 32-07 | Win deals (dollar icon) | works |
| 32-08 | Lightbulb-style icon (4th group, unlabeled) | works |
| 32-09 | Arrow-into-door icon (5th group, unlabeled) | works |
| 32-10 | People-group icon (6th group, unlabeled) | works |
| 32-11 | Onboarding hub icon (list/document, bottom) | works |
| 32-12 | Email setup and health icon (diamond/nodes, bottom) | works |
| 32-13 | Admin Settings (gear, bottom) | works |
| 32-14 | Help button (circled ? + 'Help', bottom right) | works |
| 32-15 | Global search (compact) + Cmd K | works |
| 32-16 | 75 credits, assistant icon, bell, avatar | works; the credits pill is not applicable |
| 32-17 | Getting started dropdown / Set up alert / Install extension / accordions | works |
| 32-18 | Close x (top right) | works (X button and Esc) |
| 32-19 | Greeting 'Hi, I'm your AI sales assistant...' with logo | works (own wording: 'Hi, I'm your sales assistant') |
