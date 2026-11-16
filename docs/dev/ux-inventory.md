# Pépite UX inventory (reference: observed prospecting-app screenshots)

Inventory of every visible UI control across the 36 reference screenshots, used as the verification checklist. Personal data seen in the screenshots is replaced by placeholders such as <person name> and <company name>. IDs: screenshots p0 use A1-A5, p1 use B1-B4, p2/p3/p4 use their file number (05-32; file 07 was not provided). Rows are numbered continuously per screenshot (e.g. 05-01). Controls repeated across screens are intentionally listed again. Controls hidden under overlays, clipped at the viewport edge, or only inferred are flagged 'best reading' or 'clipped'.

## A1 - p0/01_1.webp - AI Assistant full-page welcome (onboarding chat with option cards, dark theme)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| A1-01 | Page header | Explore Apollo (arrow) | button | Exits the assistant onboarding and goes to the main app. |
| A1-02 | Message | Assistant message text (long, scrollable) | text region | AI response; scrollable chat transcript. |
| A1-03 | Message actions | Copy (two squares icon) | icon button | Copies the assistant message. |
| A1-04 | Message actions | Thumbs up | icon button | Positive feedback on the response. |
| A1-05 | Message actions | Thumbs down | icon button | Negative feedback on the response. |
| A1-06 | Message actions | Timestamp '7 minutes ago' | label | Message time. |
| A1-07 | Option card | 'Select one of the following' option card | card | Single-choice prompt card. |
| A1-08 | Option card | Radio: See companies I can sell to | radio option | Choose this next step. |
| A1-09 | Option card | Radio: See leads in my market | radio option | Choose this next step. |
| A1-10 | Option card | Radio: Set up my company profile | radio option | Choose this next step. |
| A1-11 | Option card | Radio: Prospect different company | radio option | Choose this next step. |
| A1-12 | Option card | Continue (disabled until option chosen) | button | Submits the chosen option. |
| A1-13 | Composer | Message composer 'Find leads, save your preferences, or type / for commands' | textarea input | Free-text prompt entry; typing '/' opens commands. |
| A1-14 | Composer | Slash '/' command trigger (hint in placeholder) | keyboard shortcut | Typing / opens the command list. |
| A1-15 | Composer | Plus (+) attach button | icon button | Attach files / add context. |
| A1-16 | Composer | Send (up-arrow blue circle) | icon button | Sends the message. |
| A1-17 | Composer | Microphone | icon button | Voice input / dictation. |
| A1-18 | Composer footer | 'AI Policy' link | link | Opens AI policy page. |
| A1-19 | Composer footer | Disclaimer 'Chats may be shared with third parties...' | text | Privacy notice. |
| A1-20 | Promo card | 'Bring Apollo into Claude or ChatGPT' card close x | icon button | Dismisses the MCP promo card. |
| A1-21 | Promo card | Claude (external-link icon) button | button / external link | Opens instructions to connect Apollo MCP to Claude. |
| A1-22 | Promo card | ChatGPT (external-link icon) button | button / external link | Opens instructions to connect Apollo MCP to ChatGPT. |

## A2 - p0/02_2.webp - Home 'Get started with Apollo' with collapsed icon rail

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| A2-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| A2-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| A2-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| A2-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| A2-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| A2-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| A2-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| A2-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| A2-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| A2-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| A2-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| A2-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| A2-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| A2-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| A2-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| A2-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| A2-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| A2-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| A2-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| A2-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| A2-21 | Home | Getting started (table icon + chevron) | dropdown (view switcher) | Switches Home dashboard view/layout. |
| A2-22 | Banner | 'Pick up where you left off with the Assistant' banner | banner | Resume previous assistant conversation. |
| A2-23 | Banner | Continue chat | button | Reopens the last assistant conversation. |
| A2-24 | Banner | Close x | icon button | Dismisses the banner. |
| A2-25 | Onboarding | Overall progress bar | progress indicator | Shows onboarding progress (earn up to 750 credits). |
| A2-26 | Onboarding | Section 'Start reaching the right prospects' header with 'Earn 300 credits' badge | accordion header | Expands/collapses this task group. |
| A2-27 | Onboarding | '0 of 6 completed' with caret | accordion toggle | Expand/collapse the six tasks. |
| A2-28 | Onboarding task | Checkbox: Save a contact | checkbox (read-only status) | Task completion state. |
| A2-29 | Onboarding task | Save a prospect (yellow) | button (CTA) | Goes to People search to save a contact. |
| A2-30 | Onboarding task | Checkbox: Save a mobile number | checkbox (status) | Task completion state. |
| A2-31 | Onboarding task | Find numbers | button | Goes to People search to reveal a mobile number. |
| A2-32 | Onboarding task | Checkbox: Create a list | checkbox (status) | Task completion state. |
| A2-33 | Onboarding task | Create a list | button | Opens create-list flow. |
| A2-34 | Onboarding task | Checkbox: Save target companies | checkbox (status) | Task completion state. |
| A2-35 | Onboarding task | Save a company | button | Goes to Companies search to save an account. |
| A2-36 | Onboarding task | Checkbox: Save your search (partially visible) | checkbox (status) | Task completion state. |
| A2-37 | Onboarding task | Set up alert (partially visible) | button | Creates a saved-search alert. |

## A3 - p0/03_3.webp - Home 'Get started with Apollo' with expanded labelled sidebar

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| A3-01 | Sidebar | Collapse sidebar chevron '<<' | icon button | Collapses the sidebar to the icon rail. |
| A3-02 | Sidebar | Brand logo (top-left) | icon link | Goes to Home. |
| A3-03 | Sidebar | Home | nav item | Opens the Home / Get started page. |
| A3-04 | Sidebar | Phone / dialer icon next to Home | icon button | Opens the softphone/dialer. |
| A3-05 | Sidebar | AI Assistant | nav item | Opens the AI Assistant page. |
| A3-06 | Sidebar | Prospect and enrich (group header, chevron up) | collapsible group | Expands/collapses People, Companies, Lists, Data enrichment. |
| A3-07 | Sidebar | People | nav item | Opens Find people (contact search). |
| A3-08 | Sidebar | Companies | nav item | Opens Find companies (account search). |
| A3-09 | Sidebar | Lists | nav item | Opens saved lists of people/companies. |
| A3-10 | Sidebar | Data enrichment | nav item | Opens enrichment tools (import and enrich, waterfall, etc.). |
| A3-11 | Sidebar | Engage (group header, chevron up) | collapsible group | Expands/collapses Sequences, Emails, Calls, Tasks. |
| A3-12 | Sidebar | Sequences | nav item | Opens multi-step outreach sequences. |
| A3-13 | Sidebar | Emails | nav item | Opens unified mailbox / sent emails. |
| A3-14 | Sidebar | Calls | nav item | Opens call log and dialer data. |
| A3-15 | Sidebar | Tasks | nav item | Opens the task list. |
| A3-16 | Sidebar | Win deals (group header, chevron up) | collapsible group | Expands/collapses Meetings, Conversations, Deals (and a clipped further item). |
| A3-17 | Sidebar | Meetings | nav item | Opens scheduled meetings / scheduler. |
| A3-18 | Sidebar | Conversations | nav item | Opens recorded call/meeting conversation intelligence. |
| A3-19 | Sidebar | Deals | nav item | Opens the deals pipeline. |
| A3-20 | Sidebar | Clipped further group (partially hidden under the scroll edge) | nav group | Another sidebar group (likely Tools & integrations) scrolls under the upgrade button; label not readable. |
| A3-21 | Sidebar | Upgrade to Professional (yellow crown button) | button (CTA) | Opens plan upgrade flow. |
| A3-22 | Sidebar | Onboarding hub card (title, progress bar, '0% Completed') | card / link | Opens Onboarding hub checklist; shows completion percentage. |
| A3-23 | Sidebar | Email setup and health | nav item | Opens mailbox/domain deliverability setup. |
| A3-24 | Sidebar | Admin Settings (chevron right) | nav item / flyout trigger | Opens quick settings flyout (and 'All settings'). |
| A3-25 | Page chrome | Help button (circled ? + 'Help') | floating button | Opens help center / support widget. |
| A3-26 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| A3-27 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| A3-28 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| A3-29 | Top bar | 'AI Assistant' button (logo icon + label) | button | Toggles the AI Assistant side panel / opens the assistant. |
| A3-30 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| A3-31 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| A3-32 | Home | Getting started (table icon + chevron) | dropdown (view switcher) | Switches Home dashboard view/layout. |
| A3-33 | Banner | 'Pick up where you left off with the Assistant' banner | banner | Resume previous assistant conversation. |
| A3-34 | Banner | Continue chat | button | Reopens the last assistant conversation. |
| A3-35 | Banner | Close x | icon button | Dismisses the banner. |
| A3-36 | Onboarding | Overall progress bar | progress indicator | Shows onboarding progress. |
| A3-37 | Onboarding | Section 'Start reaching the right prospects' + 'Earn 300 credits' badge | accordion header | Expands/collapses task group. |
| A3-38 | Onboarding | '0 of 6 completed' with caret | accordion toggle | Expand/collapse tasks. |
| A3-39 | Onboarding task | Checkbox: Save a contact | checkbox (status) | Task completion state. |
| A3-40 | Onboarding task | Save a prospect (yellow) | button (CTA) | Go to People search to save a contact. |
| A3-41 | Onboarding task | Checkbox: Save a mobile number | checkbox (status) | Task completion state. |
| A3-42 | Onboarding task | Find numbers | button | Go to People search to reveal a mobile. |
| A3-43 | Onboarding task | Checkbox: Create a list | checkbox (status) | Task completion state. |
| A3-44 | Onboarding task | Create a list | button | Opens create-list flow. |
| A3-45 | Onboarding task | Checkbox: Save target companies | checkbox (status) | Task completion state. |
| A3-46 | Onboarding task | Save a company | button | Go to Companies search to save an account. |
| A3-47 | Onboarding task | Checkbox: Save your search (partial) | checkbox (status) | Task completion state. |
| A3-48 | Onboarding task | Set up alert (partial) | button | Creates a saved-search alert. |

## A4 - p0/04_4.webp - Find people landing (no filters applied, AI prompt + Quick filters, hover tooltip on Job Titles)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| A4-01 | Sidebar | Collapse sidebar chevron '<<' | icon button | Collapses the sidebar to the icon rail. |
| A4-02 | Sidebar | Brand logo (top-left) | icon link | Goes to Home. |
| A4-03 | Sidebar | Home | nav item | Opens the Home / Get started page. |
| A4-04 | Sidebar | Phone / dialer icon next to Home | icon button | Opens the softphone/dialer. |
| A4-05 | Sidebar | AI Assistant | nav item | Opens the AI Assistant page. |
| A4-06 | Sidebar | Prospect and enrich (group header, chevron up) | collapsible group | Expands/collapses People, Companies, Lists, Data enrichment. |
| A4-07 | Sidebar | People | nav item | Opens Find people (contact search). |
| A4-08 | Sidebar | Companies | nav item | Opens Find companies (account search). |
| A4-09 | Sidebar | Lists | nav item | Opens saved lists of people/companies. |
| A4-10 | Sidebar | Data enrichment | nav item | Opens enrichment tools (import and enrich, waterfall, etc.). |
| A4-11 | Sidebar | Engage (group header, chevron up) | collapsible group | Expands/collapses Sequences, Emails, Calls, Tasks. |
| A4-12 | Sidebar | Sequences | nav item | Opens multi-step outreach sequences. |
| A4-13 | Sidebar | Emails | nav item | Opens unified mailbox / sent emails. |
| A4-14 | Sidebar | Calls | nav item | Opens call log and dialer data. |
| A4-15 | Sidebar | Tasks | nav item | Opens the task list. |
| A4-16 | Sidebar | Win deals (group header, chevron up) | collapsible group | Expands/collapses Meetings, Conversations, Deals (and a clipped further item). |
| A4-17 | Sidebar | Meetings | nav item | Opens scheduled meetings / scheduler. |
| A4-18 | Sidebar | Conversations | nav item | Opens recorded call/meeting conversation intelligence. |
| A4-19 | Sidebar | Deals | nav item | Opens the deals pipeline. |
| A4-20 | Sidebar | Clipped further group (partially hidden under the scroll edge) | nav group | Another sidebar group (likely Tools & integrations) scrolls under the upgrade button; label not readable. |
| A4-21 | Sidebar | Upgrade to Professional (yellow crown button) | button (CTA) | Opens plan upgrade flow. |
| A4-22 | Sidebar | Email setup and health | nav item | Opens mailbox/domain deliverability setup. |
| A4-23 | Sidebar | Admin Settings (chevron right) | nav item / flyout trigger | Opens quick settings flyout (and 'All settings'). |
| A4-24 | Page chrome | Help button (circled ? + 'Help') | floating button | Opens help center / support widget. |
| A4-25 | Sidebar | Onboarding hub card close 'x' | icon button | Dismisses the onboarding hub card from the sidebar. |
| A4-26 | Sidebar | Onboarding hub card (title, progress bar, '0% Completed') | card / link | Opens Onboarding hub checklist. |
| A4-27 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| A4-28 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| A4-29 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| A4-30 | Top bar | 'AI Assistant' button (logo icon + label) | button | Toggles the AI Assistant side panel / opens the assistant. |
| A4-31 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| A4-32 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| A4-33 | Page header | 'Find people' title | heading | Page title. |
| A4-34 | Page header | Import (dropdown button) | menu trigger | Opens import menu (Single contact, CSV). |
| A4-35 | Toolbar | Default view (table icon + chevron) | menu trigger | Opens saved-search/view picker. |
| A4-36 | Toolbar | Hide Filters / Show Filters (funnel icon; badge with applied count) | toggle button | Collapses/expands the left filter panel; badge shows count of active filters. |
| A4-37 | Toolbar | Search people box | input | Free-text search over people within current filters. |
| A4-38 | Toolbar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu (custom prompt, formula, assistant, template). |
| A4-39 | Toolbar | Create workflow (bolt icon + chevron) | menu trigger | Opens workflow/automation creation menu. |
| A4-40 | Toolbar | Save as new search | button | Saves current filters as a new saved search. |
| A4-41 | Toolbar | Relevance (sort icon + chevron) | menu trigger | Opens sort order options for results. |
| A4-42 | Toolbar | Search settings (gear) | button | Opens Search settings drawer (fields, applied filters, enrichment mode). |
| A4-43 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| A4-44 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| A4-45 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| A4-46 | Filter panel | Job Titles accordion (with hover tooltip 'Open Job Titles Filter') | accordion header + tooltip | Expands Job Titles filter. |
| A4-47 | Filter panel | Tooltip 'Open Job Titles Filter' | tooltip | Hint shown on hover of filter header. |
| A4-48 | Filter panel | People Lookalikes accordion | accordion header | Filter by similarity to chosen people. |
| A4-49 | Filter panel | Company accordion | accordion header | Filter by company attributes. |
| A4-50 | Filter panel | Location accordion | accordion header | Filter by location. |
| A4-51 | Filter panel | Industry & Keywords accordion | accordion header | Filter by industry/keywords. |
| A4-52 | Filter panel footer | Clear all | button | Clears all filters. |
| A4-53 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal. |
| A4-54 | AI prompt | 'Use Apollo AI to find the right prospects' input (placeholder example in natural language) | input | Natural-language prompt that converts into filters. |
| A4-55 | Quick filters | Locations: United States chip | chip | Applies Location filter. |
| A4-56 | Quick filters | Locations: Canada chip | chip | Applies Location filter. |
| A4-57 | Quick filters | Email Status: Verified chip | chip | Applies email-status filter. |
| A4-58 | Quick filters | Email Status: Unverified chip | chip | Applies email-status filter. |
| A4-59 | Quick filters | Email Status: Unavailable chip | chip | Applies email-status filter. |
| A4-60 | Quick filters | Job Titles: founder chip | chip | Applies job title filter. |
| A4-61 | Quick filters | Job Titles: sales manager chip | chip | Applies job title filter. |
| A4-62 | Quick filters | Job Titles: marketing director chip | chip | Applies job title filter. |
| A4-63 | Quick filters | Industry: Information Technology & Services chip | chip | Applies industry filter. |
| A4-64 | Quick filters | Industry: Marketing & Advertising chip | chip | Applies industry filter. |
| A4-65 | Quick filters | Industry: Retail chip | chip | Applies industry filter. |
| A4-66 | Upsell strip | Lock icon 'Unlock advanced filters' (Revenue, Funding, Company Lookalikes) | info / upsell | Lists plan-gated filters. |
| A4-67 | Upsell strip | Revenue (gated filter label) | label | Plan-gated filter. |
| A4-68 | Upsell strip | Funding (gated filter label) | label | Plan-gated filter. |
| A4-69 | Upsell strip | Company Lookalikes (gated filter label) | label | Plan-gated filter. |
| A4-70 | Upsell strip | View plans (yellow) | button (CTA) | Opens plan comparison. |

## A5 - p0/05_5.webp - Find companies landing (no filters applied, AI prompt + Quick filters)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| A5-01 | Sidebar | Collapse sidebar chevron '<<' | icon button | Collapses the sidebar to the icon rail. |
| A5-02 | Sidebar | Brand logo (top-left) | icon link | Goes to Home. |
| A5-03 | Sidebar | Home | nav item | Opens the Home / Get started page. |
| A5-04 | Sidebar | Phone / dialer icon next to Home | icon button | Opens the softphone/dialer. |
| A5-05 | Sidebar | AI Assistant | nav item | Opens the AI Assistant page. |
| A5-06 | Sidebar | Prospect and enrich (group header, chevron up) | collapsible group | Expands/collapses People, Companies, Lists, Data enrichment. |
| A5-07 | Sidebar | People | nav item | Opens Find people (contact search). |
| A5-08 | Sidebar | Companies | nav item | Opens Find companies (account search). |
| A5-09 | Sidebar | Lists | nav item | Opens saved lists of people/companies. |
| A5-10 | Sidebar | Data enrichment | nav item | Opens enrichment tools (import and enrich, waterfall, etc.). |
| A5-11 | Sidebar | Engage (group header, chevron up) | collapsible group | Expands/collapses Sequences, Emails, Calls, Tasks. |
| A5-12 | Sidebar | Sequences | nav item | Opens multi-step outreach sequences. |
| A5-13 | Sidebar | Emails | nav item | Opens unified mailbox / sent emails. |
| A5-14 | Sidebar | Calls | nav item | Opens call log and dialer data. |
| A5-15 | Sidebar | Tasks | nav item | Opens the task list. |
| A5-16 | Sidebar | Win deals (group header, chevron up) | collapsible group | Expands/collapses Meetings, Conversations, Deals (and a clipped further item). |
| A5-17 | Sidebar | Meetings | nav item | Opens scheduled meetings / scheduler. |
| A5-18 | Sidebar | Conversations | nav item | Opens recorded call/meeting conversation intelligence. |
| A5-19 | Sidebar | Deals | nav item | Opens the deals pipeline. |
| A5-20 | Sidebar | Clipped further group (partially hidden under the scroll edge) | nav group | Another sidebar group (likely Tools & integrations) scrolls under the upgrade button; label not readable. |
| A5-21 | Sidebar | Upgrade to Professional (yellow crown button) | button (CTA) | Opens plan upgrade flow. |
| A5-22 | Sidebar | Email setup and health | nav item | Opens mailbox/domain deliverability setup. |
| A5-23 | Sidebar | Admin Settings (chevron right) | nav item / flyout trigger | Opens quick settings flyout (and 'All settings'). |
| A5-24 | Page chrome | Help button (circled ? + 'Help') | floating button | Opens help center / support widget. |
| A5-25 | Sidebar | Onboarding hub card close 'x' | icon button | Dismisses the onboarding hub card from the sidebar. |
| A5-26 | Sidebar | Onboarding hub card (title, progress bar, '0% Completed') | card / link | Opens Onboarding hub checklist. |
| A5-27 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| A5-28 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| A5-29 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| A5-30 | Top bar | 'AI Assistant' button (logo icon + label) | button | Toggles the AI Assistant side panel / opens the assistant. |
| A5-31 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| A5-32 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| A5-33 | Page header | 'Find companies' title | heading | Page title. |
| A5-34 | Page header | Import (dropdown button) | menu trigger | Opens import menu. |
| A5-35 | Toolbar | Default view (table icon + chevron) | menu trigger | Opens saved-search/view picker. |
| A5-36 | Toolbar | Hide Filters (funnel) | toggle button | Collapses/expands the filter panel. |
| A5-37 | Toolbar | Search companies box | input | Free-text search over companies. |
| A5-38 | Toolbar | Research with AI (sparkle + chevron) | menu trigger | AI research menu. |
| A5-39 | Toolbar | Create workflow (bolt + chevron) | menu trigger | Workflow menu. |
| A5-40 | Toolbar | Save as new search | button | Saves current filters as a saved search. |
| A5-41 | Toolbar | Sort (sort icon + chevron) | menu trigger | Opens sort order options (no sort chosen yet). |
| A5-42 | Toolbar | Search settings (gear) | button | Opens Search settings drawer. |
| A5-43 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| A5-44 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| A5-45 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| A5-46 | Filter panel | Company accordion | accordion header | Filter by company name/domain. |
| A5-47 | Filter panel | Lookalikes accordion | accordion header | Filter by similar companies. |
| A5-48 | Filter panel | Account Location accordion | accordion header | Filter by HQ/location. |
| A5-49 | Filter panel | # Employees accordion | accordion header | Filter by headcount ranges. |
| A5-50 | Filter panel | Industry & Keywords accordion | accordion header | Filter by industry/keywords. |
| A5-51 | Filter panel | Website Visitors accordion | accordion header | Filter by companies visiting your website (needs visitor tracking). |
| A5-52 | Filter panel footer | Clear filters | button | Clears all filters. |
| A5-53 | Filter panel footer | View 30+ Filters | button | Opens full company filters modal. |
| A5-54 | AI prompt | 'Use Apollo AI to find the right prospects' input | input | Natural-language prompt that converts into filters. |
| A5-55 | Quick filters | Locations: United States chip | chip | Applies location filter. |
| A5-56 | Quick filters | Locations: Canada chip | chip | Applies location filter. |
| A5-57 | Quick filters | Employee Count: 1-10 chip | chip | Applies headcount filter. |
| A5-58 | Quick filters | Employee Count: 11-20 chip | chip | Applies headcount filter. |
| A5-59 | Quick filters | Employee Count: 21-50 chip | chip | Applies headcount filter. |
| A5-60 | Quick filters | Industry: Information Technology & Services chip | chip | Applies industry filter. |
| A5-61 | Quick filters | Industry: Marketing & Advertising chip | chip | Applies industry filter. |
| A5-62 | Quick filters | Industry: Retail chip | chip | Applies industry filter. |
| A5-63 | Upsell strip | Lock icon 'Unlock advanced filters' (Revenue, Funding, Company Lookalikes) | info / upsell | Lists plan-gated filters. |
| A5-64 | Upsell strip | Revenue / Funding / Company Lookalikes labels | labels | Plan-gated filters. |
| A5-65 | Upsell strip | View plans (yellow) | button (CTA) | Opens plan comparison. |

## B1 - p1/01_find_people_resultats.jpg - Find people results table (filter Job Titles=founder applied, collapsed icon rail, trial banner)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| B1-01 | Banner (top) | Trial banner 'You're on a Professional trial with 11 days left...' | banner | Informs of trial days left and downgrade to Free plan. |
| B1-02 | Banner (top) | 'See trial benefits' button | button | Opens trial benefits / plan comparison. |
| B1-03 | Banner (top) | Close 'x' on trial banner | icon button | Dismisses the banner. |
| B1-04 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| B1-05 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| B1-06 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| B1-07 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| B1-08 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| B1-09 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| B1-10 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| B1-11 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| B1-12 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| B1-13 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| B1-14 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| B1-15 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| B1-16 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| B1-17 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| B1-18 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| B1-19 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| B1-20 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| B1-21 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| B1-22 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| B1-23 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| B1-24 | Page header | 'Find people' title | heading | Page title. |
| B1-25 | Page header | Import (dropdown button) | menu trigger | Opens import menu (Single contact, CSV). |
| B1-26 | Toolbar | Default view (table icon + chevron) | menu trigger | Opens saved-search/view picker. |
| B1-27 | Toolbar | Hide Filters / Show Filters (funnel icon; badge with applied count) | toggle button | Collapses/expands the left filter panel; badge shows count of active filters. |
| B1-28 | Toolbar | Search people box | input | Free-text search over people within current filters. |
| B1-29 | Toolbar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu (custom prompt, formula, assistant, template). |
| B1-30 | Toolbar | Create workflow (bolt icon + chevron) | menu trigger | Opens workflow/automation creation menu. |
| B1-31 | Toolbar | Save as new search | button | Saves current filters as a new saved search. |
| B1-32 | Toolbar | Relevance (sort icon + chevron) | menu trigger | Opens sort order options for results. |
| B1-33 | Toolbar | Search settings (gear) | button | Opens Search settings drawer (fields, applied filters, enrichment mode). |
| B1-34 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| B1-35 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| B1-36 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| B1-37 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| B1-38 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| B1-39 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| B1-40 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| B1-41 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| B1-42 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| B1-43 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| B1-44 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| B1-45 | Table header | Select-all checkbox | checkbox | Selects/deselects all rows on the page. |
| B1-46 | Table header | NAME column header | column header (sortable/label) | Person name column. |
| B1-47 | Table header | JOB TITLE column header | column header | Job title column. |
| B1-48 | Table header | COMPANY column header | column header | Company column. |
| B1-49 | Table header | EMAILS column header | column header | Email column. |
| B1-50 | Table header | PHONE NUMBERS column header | column header | Phone numbers column. |
| B1-51 | Table header | Add column (+ Add column) | button | Opens column chooser to add fields to the table. |
| B1-52 | Table row | Row checkbox (shown per row; one hovered/focused) | checkbox | Selects the person. |
| B1-53 | Table row | Copy icon on row hover (next to job title) | icon button | Copies value of the cell. |
| B1-54 | Table row | <person name> (blurred) | link | Opens the person profile page. |
| B1-55 | Table row | <company name> with logo | link | Opens the company drawer. |
| B1-56 | Table row | Access email button (green mail icon) | button | Spends a credit to reveal/save the person's email. |
| B1-57 | Table row | Access Mobile button (phone icon) | button | Spends mobile credits to reveal a mobile number. |
| B1-58 | Table row | Row-end action arrow (right, play-style icon) | icon button | Row action (e.g. add to sequence / open actions); partly cut off. |
| B1-59 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| B1-60 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| B1-61 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| B1-62 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |

## B2 - p1/02_selection_3_lignes.jpg - Results with 3 rows selected and bulk action bar replacing the toolbar

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| B2-01 | Banner (top) | Trial banner 'You're on a Professional trial with 11 days left...' | banner | Informs of trial days left and downgrade to Free plan. |
| B2-02 | Banner (top) | 'See trial benefits' button | button | Opens trial benefits / plan comparison. |
| B2-03 | Banner (top) | Close 'x' on trial banner | icon button | Dismisses the banner. |
| B2-04 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| B2-05 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| B2-06 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| B2-07 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| B2-08 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| B2-09 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| B2-10 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| B2-11 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| B2-12 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| B2-13 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| B2-14 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| B2-15 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| B2-16 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| B2-17 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| B2-18 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| B2-19 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| B2-20 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| B2-21 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| B2-22 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| B2-23 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| B2-24 | Page header | Import (dropdown button) | menu trigger | Opens import menu. |
| B2-25 | Toolbar | Default view (clipped by selection bar) | menu trigger | Saved-search/view picker. |
| B2-26 | Selection bar | Clear 3 selected | button | Clears the current row selection. |
| B2-27 | Selection bar | Save (yellow, + icon) | button (primary) | Opens Save contacts dialog (enrich + save selected). |
| B2-28 | Selection bar | Email (envelope icon) | icon button | Compose/send email to selected contacts. |
| B2-29 | Selection bar | Sequence (paper-plane icon + chevron) | menu trigger | Add selected contacts to a sequence. |
| B2-30 | Selection bar | Workflow (bolt icon + chevron) | menu trigger | Run/ add a workflow on selected contacts. |
| B2-31 | Selection bar | Start call session (phone icon, disabled) | button (disabled) | Starts a dialer session with selected contacts; disabled when no phone numbers/dialer. |
| B2-32 | Selection bar | Add to list (list+ icon) | icon button | Adds selected contacts to a list. |
| B2-33 | Selection bar | Export (download icon) | icon button | Exports selected records to CSV. |
| B2-34 | Selection bar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu on selected rows. |
| B2-35 | Selection bar | Push to CRM/ATS (cloud-upload icon + chevron) | menu trigger | Pushes selected contacts to a connected CRM/ATS. |
| B2-36 | Selection bar | More (three dots) | icon button / menu trigger | Overflow menu of further bulk actions. |
| B2-37 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| B2-38 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| B2-39 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| B2-40 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| B2-41 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| B2-42 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| B2-43 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| B2-44 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| B2-45 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| B2-46 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| B2-47 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| B2-48 | Table header | Select-all checkbox | checkbox | Selects/deselects all rows on the page. |
| B2-49 | Table header | Select-all checkbox in indeterminate state | checkbox | Toggles selection of all rows. |
| B2-50 | Table row | Three selected rows with checked boxes (highlighted) | checkbox (checked) | Selected contacts for bulk actions. |
| B2-51 | Table header | NAME column header | column header (sortable/label) | Person name column. |
| B2-52 | Table header | JOB TITLE column header | column header | Job title column. |
| B2-53 | Table header | COMPANY column header | column header | Company column. |
| B2-54 | Table header | EMAILS column header | column header | Email column. |
| B2-55 | Table header | PHONE NUMBERS column header | column header | Phone numbers column. |
| B2-56 | Table header | Add column (+ Add column) | button | Opens column chooser to add fields to the table. |
| B2-57 | Table row | Row checkbox | checkbox | Selects this person for bulk actions. |
| B2-58 | Table row | <person name> (blurred) | link | Opens the person profile page. |
| B2-59 | Table row | <company name> with logo | link | Opens the company drawer. |
| B2-60 | Table row | Access email button (green mail icon) | button | Spends a credit to reveal/save the person's email. |
| B2-61 | Table row | Access Mobile button (phone icon) | button | Spends mobile credits to reveal a mobile number. |
| B2-62 | Table row | Row-end action arrow (right, play-style icon) | icon button | Row action (e.g. add to sequence / open actions); partly cut off. |
| B2-63 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| B2-64 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| B2-65 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| B2-66 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |

## B3 - p1/03_fiche_personne.jpg - Person profile page (contact information, scores, company insights, company card)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| B3-01 | Banner (top) | Trial banner 'You're on a Professional trial with 11 days left...' | banner | Informs of trial days left and downgrade to Free plan. |
| B3-02 | Banner (top) | 'See trial benefits' button | button | Opens trial benefits / plan comparison. |
| B3-03 | Banner (top) | Close 'x' on trial banner | icon button | Dismisses the banner. |
| B3-04 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| B3-05 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| B3-06 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| B3-07 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| B3-08 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| B3-09 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| B3-10 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| B3-11 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| B3-12 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| B3-13 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| B3-14 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| B3-15 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| B3-16 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| B3-17 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| B3-18 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| B3-19 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| B3-20 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| B3-21 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| B3-22 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| B3-23 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| B3-24 | Header | Breadcrumb 'People >' | link | Back to People search. |
| B3-25 | Header | Person avatar (initials) | avatar | Identity marker. |
| B3-26 | Header | <person name> with verified/edit marker | heading | Person name. |
| B3-27 | Header | <company name> link in title line | link | Opens company page/drawer. |
| B3-28 | Header | Add to list | button | Adds this person to a list. |
| B3-29 | Header | Access email (yellow, download icon) | button (primary) | Spends a credit to reveal/save email. |
| B3-30 | Contact information | Section header 'Contact information' with collapse caret | accordion header | Collapses the section. |
| B3-31 | Contact information | Masked email '****@****.com' with status icon | text + status badge | Shows email status/verification once revealed. |
| B3-32 | Contact information | Access email (+ icon) | button | Reveal/save email. |
| B3-33 | Contact information | Mobile row: masked number + 'credits' link | text + link | Shows masked mobile and credit cost. |
| B3-34 | Contact information | Access mobile (phone icon) | button | Reveal mobile number (credits). |
| B3-35 | Contact information | 'No phone number available' (Business) | text | Business line empty state. |
| B3-36 | Scores | Score '45 / 100' 'Good' badge | badge | People auto-score result. |
| B3-37 | Scores | 'People Auto-Score' dropdown | dropdown | Choose scoring model. |
| B3-38 | Scores | Show breakdown | link / toggle | Expands score breakdown. |
| B3-39 | Compliance | Do Not Call (DNC) is not enabled banner with caret | collapsible alert | Explains/enables DNC checking. |
| B3-40 | Company insights | 'Company insights' header gear icon | icon button | Configure insight settings. |
| B3-41 | Company insights | Collapse caret | icon button | Collapses the card. |
| B3-42 | Company insights | Tab: Overview | tab | Overview of relevant jobs/tech/funding. |
| B3-43 | Company insights | Tab: News | tab | Company news. |
| B3-44 | Company insights | Tab: Technologies (count badge) | tab | Technologies used. |
| B3-45 | Company insights | Tab: Funding | tab | Funding history. |
| B3-46 | Company insights | Tab: Job postings | tab | Open jobs. |
| B3-47 | Company insights | Tab: Employee trends | tab | Headcount trends. |
| B3-48 | Company insights | Tab: Website visitors | tab | Visitor intent on your site. |
| B3-49 | Company insights | Rows: Relevant Jobs / Relevant technology / Recent funding ('No relevant ...') | info rows | Summary per signal. |
| B3-50 | Company card | <company name> with score badge 'Good' + number | heading + badge | Company auto-score. |
| B3-51 | Company card | Three dots (more) | icon button / menu trigger | Company card overflow menu. |
| B3-52 | Company card | Collapse caret | icon button | Collapses the company card. |
| B3-53 | Company card | Tab: About | tab | Company description. |
| B3-54 | Company card | Show More | link | Expands the description. |

## B4 - p1/04_fiche_entreprise_panneau.jpg - Company slide-over drawer opened over the person profile

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| B4-01 | Drawer header | Breadcrumb 'Companies > <company name>' | link | Back to Companies / company name. |
| B4-02 | Drawer header | Company logo/avatar | avatar | Identity. |
| B4-03 | Drawer header | <company name> + briefcase icon | icon button | Opens company/account details (best reading). |
| B4-04 | Drawer header | Link icon (chain) | icon button | Copies/opens the company link. |
| B4-05 | Drawer header | Add to list | button | Adds this company to a list. |
| B4-06 | Drawer header | Save company (yellow) | button (primary) | Saves the company to the workspace. |
| B4-07 | Drawer header | Expand (diagonal arrows) | icon button | Opens full company page. |
| B4-08 | Drawer header | Close x | icon button | Closes the drawer. |
| B4-09 | Drawer tabs | Overview | tab (segmented) | Company overview. |
| B4-10 | Drawer tabs | People | tab (segmented) | People at this company. |
| B4-11 | Drawer tabs | Locations | tab (segmented) | Company locations. |
| B4-12 | Company details | 'Company details' collapse caret | accordion header | Collapses details. |
| B4-13 | Company details | Show More (description) | link | Expands description. |
| B4-14 | Company details | Industries chips (e.g. design, environmental services) | chip | Industry tags; likely clickable to filter. |
| B4-15 | Company details | Keywords chips | chip | Keyword tags. |
| B4-16 | Company details | Show all (count) with caret | link | Expands all keywords. |
| B4-17 | Company details | Founding year / Number of employees / NAICS codes values | display fields | Firmographic data. |
| B4-18 | Company details | NAICS code chips | chip | Industry code tags. |
| B4-19 | Company details | Links: website, briefcase/Apollo, Facebook, X icons | icon links | External profile links. |
| B4-20 | Insights | 'Company insights' gear icon | icon button | Configure insights. |
| B4-21 | Insights | Collapse caret | icon button | Collapses card. |
| B4-22 | Insights | Tabs: Overview, News, Technologies (count), Funding, Job postings, Employee trends, Website visitors | tabs | Switch insight views. |
| B4-23 | Insights | Score '52 / 100' Good badge | badge | Company score. |
| B4-24 | Insights | 'Companies Auto...' score dropdown | dropdown | Choose company scoring model. |
| B4-25 | Insights | Show breakdown | link / toggle | Expands score breakdown. |
| B4-26 | Insights | Rows: Relevant Jobs / Relevant technology / Personas / Recent funding | info rows | Per-signal summary. |
| B4-27 | Suggested leads | Collapse caret | icon button | Collapses card. |
| B4-28 | Suggested leads | Tab: All | tab | All suggested leads. |
| B4-29 | Suggested leads | Tab: CXO (count) | tab | C-level leads. |
| B4-30 | Suggested leads | Tab: Director+ (count) | tab | Director and above. |
| B4-31 | Suggested leads | Show Filters (funnel) | button | Opens filters for leads. |
| B4-32 | Suggested leads | Search people input (cut off) | input | Search within suggested leads. |

## 05 - p2/05_filtre_job_titles.jpg - Job Titles filter expanded (Simple/Advanced, include, exclude, similar titles, past titles, management level, departments, persona)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 05-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 05-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 05-03 | Top bar | 75 credits pill | button | Credit balance. |
| 05-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 05-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 05-06 | Top bar | User avatar | icon button | Account menu. |
| 05-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 05-08 | Toolbar | Default view (chevron) | menu trigger | Saved-search/view picker. |
| 05-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse/expand filter panel. |
| 05-10 | Toolbar | Search people | input | Search within results. |
| 05-11 | Toolbar | Research with AI (icon + chevron, label collapsed) | menu trigger | AI research menu. |
| 05-12 | Toolbar | Create workflow (bolt icon only) | icon button | Workflow menu. |
| 05-13 | Toolbar | Save as new search | button | Saves current filters. |
| 05-14 | Toolbar | Relevance (sort + chevron) | menu trigger | Sort order. |
| 05-15 | Toolbar | Search settings (gear icon only) | icon button | Opens Search settings drawer. |
| 05-16 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 05-17 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 05-18 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 05-19 | Filter: Job Titles | Job Titles accordion header (badge 1 with x, chevron up) | accordion header | Collapse the filter; x clears it. |
| 05-20 | Filter: Job Titles | Simple tab | segmented control | Simple title entry mode. |
| 05-21 | Filter: Job Titles | Advanced tab | segmented control | Advanced mode (boolean/combinations). |
| 05-22 | Filter: Job Titles | Include: chip <job title> with x | chip | Titles to include; x removes. |
| 05-23 | Filter: Job Titles | Include: multi-select input with clear x and chevron | combobox input | Type/select job titles to include. |
| 05-24 | Filter: Job Titles | Hint 'Use quotation marks to return exact matches' | helper text | Documentation of exact-match syntax. |
| 05-25 | Filter: Job Titles | Checkbox 'Include people with similar titles' (checked) | checkbox | Expands match to similar titles. |
| 05-26 | Filter: Job Titles | Info icon next to similar titles | icon / tooltip | Explains similar-title matching. |
| 05-27 | Filter: Job Titles | Exclude: 'Enter titles to exclude' input + chevron | combobox input | Titles to exclude. |
| 05-28 | Filter: Job Titles | Past job titles (chevron) | collapsible link | Reveals past-title filter. |
| 05-29 | Filter: Job Titles | Management Level dropdown | dropdown multi-select | Filter by seniority. |
| 05-30 | Filter: Job Titles | Departments & Job Function dropdown | dropdown multi-select | Filter by department/function. |
| 05-31 | Filter panel footer | Save As New Persona (person icon) | button | Saves current person filters as a reusable persona. |
| 05-32 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 05-33 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 05-34 | Table | Header: select-all checkbox, NAME, JOB TITLE, COMPANY, + Add column | header controls | Same as B1 table header. |
| 05-35 | Table | Rows with checkbox, name link, company link, Access email / Access mobile buttons (partly cut off) | row controls | Same as B1 row. |
| 05-36 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 05-37 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 05-38 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 05-39 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 05-40 | Page chrome | Help button | floating button | Opens help. |

## 06 - p2/06_filtre_location.jpg - Location filter expanded (Contact / Account HQ tabs, region, ZIP radius, exclude)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 06-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 06-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 06-03 | Top bar | 75 credits pill | button | Credit balance. |
| 06-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 06-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 06-06 | Top bar | User avatar | icon button | Account menu. |
| 06-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 06-08 | Toolbar | Default view (chevron) | menu trigger | Saved-search/view picker. |
| 06-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse/expand filter panel. |
| 06-10 | Toolbar | Search people | input | Search within results. |
| 06-11 | Toolbar | Research with AI (icon + chevron, label collapsed) | menu trigger | AI research menu. |
| 06-12 | Toolbar | Create workflow (bolt icon only) | icon button | Workflow menu. |
| 06-13 | Toolbar | Save as new search | button | Saves current filters. |
| 06-14 | Toolbar | Relevance (sort + chevron) | menu trigger | Sort order. |
| 06-15 | Toolbar | Search settings (gear icon only) | icon button | Opens Search settings drawer. |
| 06-16 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 06-17 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 06-18 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 06-19 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 06-20 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 06-21 | Filter panel | People Lookalikes accordion | accordion header | Lookalike filter. |
| 06-22 | Filter panel | Company accordion | accordion header | Company filter. |
| 06-23 | Filter: Location | Location accordion header (chevron up) | accordion header | Collapses the Location filter. |
| 06-24 | Filter: Location | Contact tab | tab | Location of the person. |
| 06-25 | Filter: Location | Account HQ tab | tab | Location of the company headquarters. |
| 06-26 | Filter: Location | Radio 'Select region' (selected) | radio | Use region-based location. |
| 06-27 | Filter: Location | Input 'City / State / Country / ZIP' - 'Enter locations...' + chevron | combobox input | Type to add locations. |
| 06-28 | Filter: Location | Exclude locations (chevron) | collapsible link | Reveals exclusion input. |
| 06-29 | Filter: Location | Radio 'Select ZIP code radius' | radio | Search by distance from a ZIP code. |
| 06-30 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 06-31 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 06-32 | Table | Header: select-all checkbox, NAME, JOB TITLE, COMPANY, + Add column | header controls | Same as B1 table header. |
| 06-33 | Table | Rows with checkbox, name link, company link, Access email / Access mobile buttons (partly cut off) | row controls | Same as B1 row. |
| 06-34 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 06-35 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 06-36 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 06-37 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 06-38 | Page chrome | Help button | floating button | Opens help. |

## 08 - p2/08_filtre_industry_keywords.jpg - Industry & Keywords filter expanded (industries, advanced settings, company keywords)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 08-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 08-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 08-03 | Top bar | 75 credits pill | button | Credit balance. |
| 08-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 08-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 08-06 | Top bar | User avatar | icon button | Account menu. |
| 08-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 08-08 | Toolbar | Default view (chevron) | menu trigger | Saved-search/view picker. |
| 08-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse/expand filter panel. |
| 08-10 | Toolbar | Search people | input | Search within results. |
| 08-11 | Toolbar | Research with AI (icon + chevron, label collapsed) | menu trigger | AI research menu. |
| 08-12 | Toolbar | Create workflow (bolt icon only) | icon button | Workflow menu. |
| 08-13 | Toolbar | Save as new search | button | Saves current filters. |
| 08-14 | Toolbar | Relevance (sort + chevron) | menu trigger | Sort order. |
| 08-15 | Toolbar | Search settings (gear icon only) | icon button | Opens Search settings drawer. |
| 08-16 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 08-17 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 08-18 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 08-19 | Filter panel | Location accordion (collapsed) | accordion header | Location filter. |
| 08-20 | Filter: Industry & Keywords | Header (chevron up) | accordion header | Collapses the filter. |
| 08-21 | Filter: Industry & Keywords | Search industries... multi-select | combobox input | Pick industries. |
| 08-22 | Filter: Industry & Keywords | Advanced settings (chevron) | collapsible link | Reveals extra industry options. |
| 08-23 | Filter: Industry & Keywords | Company Keywords header with ? icon and chevron | collapsible + tooltip | Keyword sub-section; ? explains matching. |
| 08-24 | Filter: Industry & Keywords | Checkbox 'Include keywords' (checked) | checkbox | Enables include-keywords. |
| 08-25 | Filter: Industry & Keywords | Input 'e.g. Cloud, AWS' + chevron | combobox input | Type keywords. |
| 08-26 | Filter: Industry & Keywords | Type of Keywords (chevron) with warning triangle | dropdown | Choose keyword source (name, description, tags); warning notes slower search. |
| 08-27 | Filter: Industry & Keywords | Checkbox 'Include ALL' | checkbox | Requires all keywords to match. |
| 08-28 | Filter: Industry & Keywords | Checkbox 'Exclude keywords' | checkbox | Enables exclusion keywords. |
| 08-29 | Filter: Industry & Keywords | Note 'Keywords filters may slow down your search.' | helper text | Performance warning. |
| 08-30 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 08-31 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 08-32 | Table | Header: select-all checkbox, NAME, JOB TITLE, COMPANY, + Add column | header controls | Same as B1 table header. |
| 08-33 | Table | Rows with checkbox, name link, company link, Access email / Access mobile buttons (partly cut off) | row controls | Same as B1 row. |
| 08-34 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 08-35 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 08-36 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 08-37 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 08-38 | Page chrome | Help button | floating button | Opens help. |

## 09 - p2/09_panneau_60_filtres.jpg - Full 'Filters' modal (60+ filters): pinned filters, Person Info, Company Info, Engagement columns

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 09-01 | Modal header | Filters title | heading | Modal title. |
| 09-02 | Modal header | Close x | icon button | Closes modal. |
| 09-03 | Modal toolbar | Search filters input (focused) | input | Searches filter names. |
| 09-04 | Modal toolbar | Type: All (briefcase icon + chevron) | dropdown | Filters list by record type (All/Person/Company etc.). |
| 09-05 | Modal toolbar | '2,528,832 records found' label | label | Live count of matches. |
| 09-06 | Modal toolbar | Apply Filters (yellow) | button (primary) | Applies filters and closes modal. |
| 09-07 | Pinned Filters | Section 'Pinned Filters' | section | Filters pinned to the main side panel. |
| 09-08 | Pinned Filters | Drag handle (x5 rows) | drag handle | Reorders pinned filters. |
| 09-09 | Pinned Filters | Job Titles row: pin toggle + chevron | pin toggle / accordion | Unpin/expand. |
| 09-10 | Pinned Filters | People Lookalikes row: pin + chevron | pin toggle / accordion | Unpin/expand. |
| 09-11 | Pinned Filters | Company row: pin + chevron | pin toggle / accordion | Unpin/expand. |
| 09-12 | Pinned Filters | Location row: pin + chevron | pin toggle / accordion | Unpin/expand. |
| 09-13 | Pinned Filters | Industry & Keywords row: pin + chevron up (expanded) | pin toggle / accordion | Unpin/collapse. |
| 09-14 | Pinned Filters | Industry sub-controls: Search industries, Advanced settings, Company Keywords (?) , Include keywords checkbox, keyword input | inputs/checkboxes | Same controls as screenshot 08, inline. |
| 09-15 | Person Info | Persona (chevron) | accordion filter | Filter by saved persona. |
| 09-16 | Person Info | Email Status (chevron) | accordion filter | Verified/unverified/unavailable. |
| 09-17 | Person Info | Name (chevron) | accordion filter | Search by person name. |
| 09-18 | Person Info | Education (chevron) | accordion filter | Filter by school/degree. |
| 09-19 | Person Info | Awards & Ce... (Beta badge, chevron) | accordion filter | Awards & certifications. |
| 09-20 | Person Info | Enrichment Type (chevron) | accordion filter | Enrichment source type. |
| 09-21 | Person Info | Work URLs (chevron) | accordion filter | Filter by work profile URLs. |
| 09-22 | Person Info | Time Zone (chevron) | accordion filter | Filter by time zone. |
| 09-23 | Company Info | Company Lookalikes (chevron) | accordion filter | Similar companies (gated). |
| 09-24 | Company Info | # Employees (chevron) | accordion filter | Headcount ranges. |
| 09-25 | Company Info | Market Segments (chevron) | accordion filter | Market segment taxonomy. |
| 09-26 | Company Info | SIC and NAICS (chevron) | accordion filter | Industry codes. |
| 09-27 | Company Info | Buying Intent (chevron) | accordion filter | Intent topics (third-party intent data). |
| 09-28 | Company Info | Technologies (chevron) | accordion filter | Tech stack used. |
| 09-29 | Company Info | # Employees by Dept. (chevron) | accordion filter | Headcount by department. |
| 09-30 | Company Info | Headcount Growth (chevron) | accordion filter | Growth over time. |
| 09-31 | Engagement Activity | Sequence (clipped) | accordion filter | Sequence membership. |
| 09-32 | Engagement Activity | Workflow (clipped) | accordion filter | Workflow membership. |
| 09-33 | Engagement Activity | Email Op... (clipped, likely Email Opened) | accordion filter | Email opened status. |
| 09-34 | Engagement Activity | Last Acti... (clipped, likely Last Activity) | accordion filter | Last activity date. |
| 09-35 | Engagement Activity | Email Sen... (clipped, likely Email Sent) | accordion filter | Email sent status. |
| 09-36 | Engagement Activity | Email Clic... (clipped, likely Email Clicked) | accordion filter | Email clicked status. |
| 09-37 | Engagement Activity | Email Rep... (clipped, likely Email Replied) | accordion filter | Email replied status. |
| 09-38 | Engagement Activity | Email Me... (clipped, likely Email Meeting) | accordion filter | Meeting booked via email. |
| 09-39 | Modal | Scroll area / further columns off-screen | scroll | Additional filter columns (Engagement, more company filters) exist beyond viewport. |
| 09-40 | Page chrome | Help button | floating button | Opens help. |

## 10 - p2/10_filtre_employees.jpg - Filters modal with '# Employees' expanded (predefined ranges with counts)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 10-01 | Modal header | Filters title / Close x | heading / icon button | Modal title and close. |
| 10-02 | Modal toolbar | Search filters input | input | Searches filter names. |
| 10-03 | Modal toolbar | Type: All dropdown | dropdown | Filters list by record type. |
| 10-04 | Modal toolbar | '2,528,832 records found' | label | Live count. |
| 10-05 | Modal toolbar | Apply Filters (yellow) | button (primary) | Applies filters. |
| 10-06 | Pinned Filters | Five pinned rows with drag handle, pin toggle, chevron (Job Titles, People Lookalikes, Company, Location, Industry & Keywords) | rows | Pinned filters. |
| 10-07 | Person Info | Persona, Email Status, Name, Education, Awards & Ce... (Beta), Enrichment Type, Work URLs, Time Zone | accordion filters | Same as 09. |
| 10-08 | Company Info | Company Lookalikes (chevron) | accordion filter | Gated filter. |
| 10-09 | # Employees | # Employees header: pin icon + chevron up | pin toggle / accordion | Pin to panel / collapse. |
| 10-10 | # Employees | Radio 'Predefined Range' (selected) | radio | Choose predefined ranges (vs custom). |
| 10-11 | # Employees | Checkbox 1-10 (2.0M) | checkbox | Include this headcount range; count shown. |
| 10-12 | # Employees | Checkbox 11-20 (209.7K) | checkbox | Same. |
| 10-13 | # Employees | Checkbox 21-50 (162.1K) | checkbox | Same. |
| 10-14 | # Employees | Checkbox 51-100 (67.1K) | checkbox | Same. |
| 10-15 | # Employees | Checkbox 101-200 (34.4K) | checkbox | Same. |
| 10-16 | # Employees | Checkbox 201-500 (26.3K) | checkbox | Same. |
| 10-17 | # Employees | Checkbox 501-1000 (12.7K) | checkbox | Same. |
| 10-18 | # Employees | Checkbox 1001-2000 (8.5K) | checkbox | Same. |
| 10-19 | # Employees | Checkbox 2001-5000 (10.9K) | checkbox | Same. |
| 10-20 | # Employees | Checkbox 5001-10000 (3.9K) | checkbox | Same. |
| 10-21 | # Employees | Further ranges off-screen (10001+) | checkbox (clipped) | Additional ranges beyond viewport. |
| 10-22 | Engagement Activity | Sequence, Workflow, Email Op..., Last Acti..., Email Sen..., Email Clic..., Email Rep..., Email Me... (clipped) | accordion filters | Same as 09. |
| 10-23 | Page chrome | Help button | floating button | Opens help. |

## 11 - p2/11_menu_default_view.jpg - Default view dropdown (saved searches picker)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 11-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 11-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 11-03 | Top bar | 75 credits pill | button | Credit balance. |
| 11-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 11-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 11-06 | Top bar | User avatar | icon button | Account menu. |
| 11-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 11-08 | Toolbar | Default view (chevron up, open) | menu trigger | Opens/closes the saved-search picker. |
| 11-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse filters. |
| 11-10 | Toolbar | Search people / Research with AI / Create workflow / Save as new search / Relevance / Search settings | toolbar controls | Same as 05 (partially hidden behind menu). |
| 11-11 | Saved-search menu | Search input (focused) | input | Search saved searches. |
| 11-12 | Saved-search menu | Tab: All searches | tab | All saved searches. |
| 11-13 | Saved-search menu | Tab: Your searches | tab | Searches I created. |
| 11-14 | Saved-search menu | Tab: Starred | tab | Starred searches. |
| 11-15 | Saved-search menu | Tab: Assigned to you | tab | Searches assigned to me. |
| 11-16 | Saved-search menu | Tab: Shared | tab | Searches shared with workspace. |
| 11-17 | Saved-search menu | Item: Default view (System badge) - highlighted | menu item | Loads the default search. |
| 11-18 | Saved-search menu | Row actions three dots on Default view | icon button / menu trigger | Per-search actions (rename, star, share, delete - best reading). |
| 11-19 | Saved-search menu | Item: My enrichable people (System badge) | menu item | Loads system view of enrichable contacts owned by me. |
| 11-20 | Saved-search menu | Item: All enrichable people (System badge) | menu item | Loads system view of all enrichable contacts. |
| 11-21 | Saved-search menu | Create saved search (yellow) | button (primary) | Creates a new saved search. |
| 11-22 | Filter panel | Total/Net New/Saved tabs and filter accordions (partially hidden) | tabs / accordions | Same as 05. |
| 11-23 | Filter panel footer | Clear all (1) / View 60+ Filters | buttons | Same as 05. |
| 11-24 | Table | Table header and rows (behind menu) | table | Same as B1. |
| 11-25 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 11-26 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 11-27 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 11-28 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 11-29 | Page chrome | Help button | floating button | Opens help. |

## 12 - p2/12_search_settings.jpg - Search settings drawer (Fields, Applied filters, Enrichment mode)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 12-01 | Drawer header | Search settings title | heading | Drawer title. |
| 12-02 | Drawer header | Close x | icon button | Closes the drawer. |
| 12-03 | Drawer | Section label 'Fields' | label | Group heading. |
| 12-04 | Drawer | Fields row (list icon, count 14, chevron right) - focused | drill-in row | Opens the Fields (column chooser) sub-view. |
| 12-05 | Drawer | Section label 'Applied filters' | label | Group heading. |
| 12-06 | Drawer | Filters row (icon, count 1, chevron right) | drill-in row | Opens list of applied filters. |
| 12-07 | Drawer | Enrichment mode label with 'New' badge | label + badge | Feature heading. |
| 12-08 | Drawer | Toggle 'Enable enrichment mode' (on) | toggle switch | Highlights records with enrichable data. |
| 12-09 | Drawer | Helper text under toggle | text | Explains enrichment mode. |
| 12-10 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 12-11 | Background | Filter panel (Total/Net New/Saved, Job Titles chip, accordions, Clear all, View 60+ Filters), toolbar and table dimmed | background | Same controls as 05, inert while drawer open. |
| 12-12 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 12-13 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 12-14 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 12-15 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 12-16 | Page chrome | Help button | floating button | Opens help. |

## 13 - p2/13_choix_colonnes.jpg - Search settings > Fields: displayed columns editor

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 13-01 | Drawer header | Back arrow | icon button | Back to Search settings. |
| 13-02 | Drawer header | Fields title | heading | Sub-view title. |
| 13-03 | Drawer header | Close x | icon button | Closes the drawer. |
| 13-04 | Fields | 'Displayed fields 14' label | label | Count of visible columns. |
| 13-05 | Fields | Name row (greyed, locked) - drag handle, chevron, x (disabled) | row (locked) | Mandatory column; cannot be removed. |
| 13-06 | Fields | Job title row - drag handle, chevron, remove x | row | Reorder (drag), configure (chevron), remove (x). |
| 13-07 | Fields | Company row - drag handle, chevron, remove x | row | Same. |
| 13-08 | Fields | Emails row - drag handle, chevron, remove x | row | Same. |
| 13-09 | Fields | Phone numbers row - drag handle, chevron, remove x | row | Same. |
| 13-10 | Fields | Qualify Contact row - drag handle, chevron, remove x | row | AI qualification column. |
| 13-11 | Fields | Company - Qualify Account row - drag handle, chevron, remove x | row | AI account qualification column. |
| 13-12 | Fields | Actions row (greyed, locked) | row (locked) | Row-actions column; fixed. |
| 13-13 | Fields | Links row - drag handle, chevron, remove x | row | Social/website links column. |
| 13-14 | Fields | People Auto-Score row - drag handle, chevron, remove x | row | Lead score column. |
| 13-15 | Fields | Location row - drag handle, chevron, remove x | row | Location column. |
| 13-16 | Fields | Company - Number of employees row - drag handle, chevron, remove x | row | Headcount column. |
| 13-17 | Fields | Company - Industries row - drag handle, chevron, remove x | row | Industries column. |
| 13-18 | Fields | Company - Keywords row - drag handle, chevron, remove x | row | Keywords column. |
| 13-19 | Fields | Add fields to table (count 45, chevron right) | drill-in button | Opens catalogue of 45 additional fields to add. |
| 13-20 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 13-21 | Background | Dimmed filter panel, toolbar and table | background | Inert while drawer open. |
| 13-22 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 13-23 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 13-24 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 13-25 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 13-26 | Page chrome | Help button | floating button | Opens help. |

## 14 - p2/14_barre_actions_3_lignes.jpg - Bulk action bar (icon-only) with 3 rows selected

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 14-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 14-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 14-03 | Top bar | 75 credits pill | button | Credit balance. |
| 14-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 14-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 14-06 | Top bar | User avatar | icon button | Account menu. |
| 14-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 14-08 | Toolbar | Default view (chevron) | menu trigger | Saved-search picker. |
| 14-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse filters. |
| 14-10 | Selection bar | Clear 3 selected | button | Clears the current row selection. |
| 14-11 | Selection bar | Save (yellow, + icon) | button (primary) | Opens Save contacts dialog (enrich + save selected). |
| 14-12 | Selection bar | Email (envelope icon) | icon button | Compose/send email to selected contacts. |
| 14-13 | Selection bar | Sequence (paper-plane icon + chevron) | menu trigger | Add selected contacts to a sequence. |
| 14-14 | Selection bar | Workflow (bolt icon + chevron) | menu trigger | Run/ add a workflow on selected contacts. |
| 14-15 | Selection bar | Start call session (phone icon, disabled) | button (disabled) | Starts a dialer session with selected contacts; disabled when no phone numbers/dialer. |
| 14-16 | Selection bar | Add to list (list+ icon) | icon button | Adds selected contacts to a list. |
| 14-17 | Selection bar | Export (download icon) | icon button | Exports selected records to CSV. |
| 14-18 | Selection bar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu on selected rows. |
| 14-19 | Selection bar | Push to CRM/ATS (cloud-upload icon + chevron) | menu trigger | Pushes selected contacts to a connected CRM/ATS. |
| 14-20 | Selection bar | More (three dots) | icon button / menu trigger | Overflow menu of further bulk actions. |
| 14-21 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 14-22 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 14-23 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 14-24 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 14-25 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 14-26 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| 14-27 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| 14-28 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| 14-29 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| 14-30 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 14-31 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 14-32 | Table header | Select-all checkbox (indeterminate) | checkbox | Select/deselect all. |
| 14-33 | Table header | NAME, JOB TITLE, COMPANY, + Add column | header controls | Same as B1. |
| 14-34 | Table row | Three selected rows (checkbox, name, company) | rows | Selected contacts. |
| 14-35 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 14-36 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 14-37 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 14-38 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 14-39 | Page chrome | Help button | floating button | Opens help. |

## 15 - p2/15_fenetre_save_to_list.jpg - 'Save contacts' dialog (enrichment, email status, add to list/sequence, credit estimate, export CSV)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 15-01 | Dialog header | Save contacts title | heading | Dialog title. |
| 15-02 | Dialog header | Close x | icon button | Closes dialog. |
| 15-03 | Add Enrichment | Header with collapse chevron + info icon 'Add Enrichment' | collapsible header | Collapses the enrichment panel; i explains. |
| 15-04 | Add Enrichment | 'Apollo-source' badge (asterisk icon) | badge | Indicates data source Apollo. |
| 15-05 | Add Enrichment | Segmented 'Emails only' (selected) | segmented control | Enrich emails only. |
| 15-06 | Add Enrichment | Segmented 'Emails & Phone Numbers' | segmented control | Enrich emails and mobile numbers (extra credits). |
| 15-07 | Add Enrichment | Card 'Email enrichment': Data source Apollo, Credit usage '1 / verified email' | info card | Shows cost per verified email. |
| 15-08 | Add Enrichment | 'What is Waterfall?' (chevron) | collapsible link | Explains waterfall enrichment across providers. |
| 15-09 | Add Enrichment | Discover Waterfall (icon) | button / link | Opens waterfall enrichment setup. |
| 15-10 | Destinations | Add to list (+) tile | card button | Choose a list to add contacts to. |
| 15-11 | Destinations | Add to sequence (+) tile | card button | Choose a sequence to enroll contacts. |
| 15-12 | Contact ownership | 'Contact ownership' heading with info icon + text | info | New contacts assigned to you; existing keep owner. |
| 15-13 | Summary | 'Saving 3 out of 3 selected records' with collapse caret | collapsible header | Expand breakdown. |
| 15-14 | Summary | Progress bar 'Verified emails 100%' | progress indicator | Share of records with verified email. |
| 15-15 | Email status | Checkbox Verified (3) - checked | checkbox | Include verified emails. |
| 15-16 | Email status | Checkbox Unverified (0) | checkbox | Include unverified emails. |
| 15-17 | Email status | Checkbox Unavailable (0) | checkbox | Include records without email. |
| 15-18 | Email status | Checkbox 'Include catch-all emails' (checked) | checkbox | Include catch-all domains. |
| 15-19 | Credit estimate | 'Credit estimate' card with info icon | info card | Estimated credit spend. |
| 15-20 | Credit estimate | Records to save 3 / Net new records 3 | stats | Counts used for credit calc. |
| 15-21 | Credit estimate | Emails: Apollo enrichment 0-3 credits | stat row | Estimated email credits. |
| 15-22 | Footer | Checkbox 'Export selected records to CSV' | checkbox | Also download CSV of saved records. |
| 15-23 | Footer | Gear icon next to export | icon button | CSV export settings (fields). |
| 15-24 | Footer | Cancel | button | Closes without saving. |
| 15-25 | Footer | Save (yellow) | button (primary) | Saves/enriches contacts. |
| 15-26 | Page chrome | Help button | floating button | Opens help. |

## 16 - p2/16_menu_import.jpg - Import dropdown open (with selection bar visible)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 16-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 16-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 16-03 | Top bar | 75 credits pill | button | Credit balance. |
| 16-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 16-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 16-06 | Top bar | User avatar | icon button | Account menu. |
| 16-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 16-08 | Import menu | Item: Single contact | menu item | Add one contact manually. |
| 16-09 | Import menu | Item: CSV | menu item | Import contacts from CSV file. |
| 16-10 | Selection bar | Clear 3 selected | button | Clears the current row selection. |
| 16-11 | Selection bar | Clear 3 selected / Save / Email / Sequence / Workflow / Call / Add to list / Export / Research with AI / Push to CRM/ATS / More (...) | bulk bar | Same as 14. |
| 16-12 | Toolbar | Default view / Hide Filters (1) | toolbar controls | Same as 14. |
| 16-13 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 16-14 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 16-15 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 16-16 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 16-17 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 16-18 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| 16-19 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| 16-20 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| 16-21 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| 16-22 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 16-23 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 16-24 | Table | Header controls and three selected rows | table | Same as 14. |
| 16-25 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 16-26 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 16-27 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 16-28 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 16-29 | Page chrome | Help button | floating button | Opens help. |

## 17 - p2/17_tooltip_export.jpg - Tooltip 'Export' on bulk-bar download icon

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 17-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 17-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 17-03 | Top bar | 75 credits pill | button | Credit balance. |
| 17-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 17-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 17-06 | Top bar | User avatar | icon button | Account menu. |
| 17-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 17-08 | Tooltip | 'Export' tooltip over download icon | tooltip | Describes icon: export selected records. |
| 17-09 | Selection bar | Clear 3 selected | button | Clears the current row selection. |
| 17-10 | Selection bar | Save (yellow, + icon) | button (primary) | Opens Save contacts dialog (enrich + save selected). |
| 17-11 | Selection bar | Email (envelope icon) | icon button | Compose/send email to selected contacts. |
| 17-12 | Selection bar | Sequence (paper-plane icon + chevron) | menu trigger | Add selected contacts to a sequence. |
| 17-13 | Selection bar | Workflow (bolt icon + chevron) | menu trigger | Run/ add a workflow on selected contacts. |
| 17-14 | Selection bar | Start call session (phone icon, disabled) | button (disabled) | Starts a dialer session with selected contacts; disabled when no phone numbers/dialer. |
| 17-15 | Selection bar | Add to list (list+ icon) | icon button | Adds selected contacts to a list. |
| 17-16 | Selection bar | Export (download icon) | icon button | Exports selected records to CSV. |
| 17-17 | Selection bar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu on selected rows. |
| 17-18 | Selection bar | Push to CRM/ATS (cloud-upload icon + chevron) | menu trigger | Pushes selected contacts to a connected CRM/ATS. |
| 17-19 | Selection bar | More (three dots) | icon button / menu trigger | Overflow menu of further bulk actions. |
| 17-20 | Toolbar | Default view / Hide Filters (1) | toolbar controls | Same as 14. |
| 17-21 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 17-22 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 17-23 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 17-24 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 17-25 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 17-26 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| 17-27 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| 17-28 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| 17-29 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| 17-30 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 17-31 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 17-32 | Table | Header controls and three selected rows | table | Same as 14. |
| 17-33 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 17-34 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 17-35 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 17-36 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 17-37 | Page chrome | Help button | floating button | Opens help. |

## 18 - p3/18_menu_ai_selection.jpg - 'Research with AI' menu opened from bulk action bar

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 18-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 18-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 18-03 | Top bar | 75 credits pill | button | Credit balance. |
| 18-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 18-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 18-06 | Top bar | User avatar | icon button | Account menu. |
| 18-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 18-08 | AI menu | Run custom AI prompt (sparkle icon) | menu item | Runs a custom prompt per selected row, writes result in a column. |
| 18-09 | AI menu | Generate AI formula (fx icon) | menu item | Generates a formula column from natural language. |
| 18-10 | AI menu | Use Apollo Assistant (logo icon) | menu item | Hands selection to the Assistant. |
| 18-11 | AI menu | Start with a template (dots icon) | menu item | Opens prebuilt AI prompt templates. |
| 18-12 | Selection bar | Research with AI (active, purple, chevron) | menu trigger | The open trigger. |
| 18-13 | Selection bar | Clear 3 selected | button | Clears the current row selection. |
| 18-14 | Selection bar | Save (yellow, + icon) | button (primary) | Opens Save contacts dialog (enrich + save selected). |
| 18-15 | Selection bar | Email (envelope icon) | icon button | Compose/send email to selected contacts. |
| 18-16 | Selection bar | Sequence (paper-plane icon + chevron) | menu trigger | Add selected contacts to a sequence. |
| 18-17 | Selection bar | Workflow (bolt icon + chevron) | menu trigger | Run/ add a workflow on selected contacts. |
| 18-18 | Selection bar | Start call session (phone icon, disabled) | button (disabled) | Starts a dialer session with selected contacts; disabled when no phone numbers/dialer. |
| 18-19 | Selection bar | Add to list (list+ icon) | icon button | Adds selected contacts to a list. |
| 18-20 | Selection bar | Export (download icon) | icon button | Exports selected records to CSV. |
| 18-21 | Selection bar | Research with AI (sparkle icon + chevron) | menu trigger | Opens AI research menu on selected rows. |
| 18-22 | Selection bar | Push to CRM/ATS (cloud-upload icon + chevron) | menu trigger | Pushes selected contacts to a connected CRM/ATS. |
| 18-23 | Selection bar | More (three dots) | icon button / menu trigger | Overflow menu of further bulk actions. |
| 18-24 | Toolbar | Default view / Hide Filters (1) | toolbar controls | Same as 14. |
| 18-25 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 18-26 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 18-27 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 18-28 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 18-29 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 18-30 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| 18-31 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| 18-32 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| 18-33 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| 18-34 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 18-35 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 18-36 | Table | Header controls and three selected rows | table | Same as 14. |
| 18-37 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 18-38 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 18-39 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 18-40 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 18-41 | Page chrome | Help button | floating button | Opens help. |

## 19 - p3/19_menu_research_with_ai.jpg - 'Research with AI' menu opened from the toolbar (nothing selected) with tooltip

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 19-01 | Sidebar (rail) | Collapsed icon rail (expand '>>', Home, Assistant, Prospect, Engage, Win deals, 3 more groups, Onboarding hub, Email setup, Admin settings) | nav icons | Same rail as B1. |
| 19-02 | Top bar | Global search 'Search or ask a question in Apollo' + Cmd K | input / shortcut | Command palette. |
| 19-03 | Top bar | 75 credits pill | button | Credit balance. |
| 19-04 | Top bar | AI Assistant icon button | icon button | Toggles assistant. |
| 19-05 | Top bar | Notification bell with red dot | icon button | Notifications. |
| 19-06 | Top bar | User avatar | icon button | Account menu. |
| 19-07 | Page header | Import (dropdown) | menu trigger | Import menu. |
| 19-08 | Toolbar | Default view (chevron) | menu trigger | Saved-search/view picker. |
| 19-09 | Toolbar | Hide Filters (badge 1) | toggle button | Collapse/expand filter panel. |
| 19-10 | Toolbar | Search people | input | Search within results. |
| 19-11 | Toolbar | Research with AI (icon + chevron, label collapsed) | menu trigger | AI research menu. |
| 19-12 | Toolbar | Create workflow (bolt icon only) | icon button | Workflow menu. |
| 19-13 | Toolbar | Save as new search | button | Saves current filters. |
| 19-14 | Toolbar | Relevance (sort + chevron) | menu trigger | Sort order. |
| 19-15 | Toolbar | Search settings (gear icon only) | icon button | Opens Search settings drawer. |
| 19-16 | Tooltip | 'Research with AI' tooltip | tooltip | Label of the sparkle button. |
| 19-17 | AI menu | Run custom AI prompt | menu item | Runs a custom prompt per row. |
| 19-18 | AI menu | Generate AI formula | menu item | Creates AI formula column. |
| 19-19 | AI menu | Use Apollo Assistant | menu item | Hands off to Assistant. |
| 19-20 | AI menu | Start with a template | menu item | Opens AI templates. |
| 19-21 | Filter panel | Total tab (with count) | segmented control tab | Shows all records matching filters. |
| 19-22 | Filter panel | Net New tab (with count) | segmented control tab | Shows only records not yet saved/known to the workspace. |
| 19-23 | Filter panel | Saved tab (with count) | segmented control tab | Shows only records already saved to the workspace. |
| 19-24 | Filter panel | Job Titles filter header (badge '1' with x; chevron) | accordion header | Expands the Job Titles filter; badge shows active count; x clears it. |
| 19-25 | Filter panel | 'Include titles' chip (e.g. <job title>) with x | chip | Active title filter; x removes it. |
| 19-26 | Filter panel | People Lookalikes (accordion) | accordion header | Filter by similarity to chosen people. |
| 19-27 | Filter panel | Company (accordion) | accordion header | Filter by company name/domain/etc. |
| 19-28 | Filter panel | Location (accordion) | accordion header | Filter by contact or account location. |
| 19-29 | Filter panel | Industry & Keywords (accordion) | accordion header | Filter by industry and company keywords. |
| 19-30 | Filter panel footer | Clear all (with count badge) | button | Clears all applied filters. |
| 19-31 | Filter panel footer | View 60+ Filters | button | Opens the full filters modal (60+ filters). |
| 19-32 | Table | Header: select-all checkbox, NAME, JOB TITLE, COMPANY, + Add column | header controls | Same as B1 table header. |
| 19-33 | Table | Rows with checkbox, name link, company link, Access email / Access mobile buttons (partly cut off) | row controls | Same as B1 row. |
| 19-34 | Pagination | Previous page chevron '<' | icon button | Go to previous page. |
| 19-35 | Pagination | Page number dropdown ('1') | dropdown | Jump to a page number. |
| 19-36 | Pagination | Next page chevron '>' | icon button | Go to next page. |
| 19-37 | Pagination | '1 - 25 of 2.529M' range label | label | Shows result range and total. |
| 19-38 | Page chrome | Help button | floating button | Opens help. |

## 20 - p3/20_page_lists_vide.jpg - Lists page, empty state ('My lists')

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 20-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 20-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 20-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 20-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 20-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 20-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 20-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 20-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 20-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 20-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 20-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 20-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 20-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 20-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 20-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 20-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 20-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 20-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 20-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 20-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 20-21 | Page header | 'My lists' title | heading | Page title. |
| 20-22 | Empty state | Create a people list | button | Creates a people list. |
| 20-23 | Empty state | Create a company list | button | Creates a company list. |
| 20-24 | Empty state | Learn more about lists (info icon) | link | Opens help docs on lists. |

## 21 - p4/21_sequences_vide.jpg - Sequences page, empty state

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 21-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 21-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 21-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 21-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 21-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 21-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 21-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 21-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 21-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 21-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 21-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 21-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 21-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 21-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 21-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 21-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 21-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 21-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 21-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 21-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 21-21 | Page header | Sequences title | heading | Page title. |
| 21-22 | Page header | Create sequence (yellow) | button (primary) | Starts sequence creation. |
| 21-23 | Tabs | All Sequences | tab | List of sequences. |
| 21-24 | Tabs | Analytics | tab | Sequence analytics. |
| 21-25 | Tabs | Diagnostics | tab | Sequence diagnostics/health. |
| 21-26 | Banner | Bounce Guard info banner close x | icon button | Dismisses banner. |
| 21-27 | Banner | Link 'Settings > Email setup and health > Sending policies' | link | Opens sending policies settings. |
| 21-28 | Banner | Warning bar 'You have no mailboxes linked...' | alert banner | Prompts mailbox connection. |
| 21-29 | Banner | Link mailbox | link | Opens mailbox connection flow. |
| 21-30 | Empty state | Create your first sequence hero (paper-plane icon) | hero card | Explains sequences. |
| 21-31 | Empty state | Create with AI (sparkle) | button | AI-generated sequence. |
| 21-32 | Empty state | Create manually | button | Opens blank sequence builder. |
| 21-33 | Empty state | Create manually dropdown chevron | menu trigger | Other creation options (templates etc.). |
| 21-34 | Feature cards | 'Built step by step' card | info card | Explains ordered steps. |
| 21-35 | Feature cards | 'Sends on your schedule' card | info card | Explains delays and sending windows. |
| 21-36 | Feature cards | 'Pauses when they reply' card | info card | Explains auto-pause on reply/meeting. |

## 22 - p4/22_emails_vide.jpg - Emails page, empty state / upsell

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 22-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 22-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 22-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 22-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 22-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 22-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 22-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 22-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 22-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 22-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 22-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 22-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 22-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 22-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 22-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 22-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 22-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 22-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 22-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 22-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 22-21 | Page header | Emails title | heading | Page title. |
| 22-22 | Upsell card | 'Want to supercharge your emails?' progress bar | progress indicator | Setup progress. |
| 22-23 | Upsell card | Checklist: Add up to 30 mailboxes / Access all of your emails / Track deals and sequences / View sentiments at a glance | info list | Benefits list. |
| 22-24 | Empty state | Add a mailbox (yellow) | button (primary) | Starts mailbox connection. |

## 23 - p4/23_tasks_vide.jpg - Tasks page, empty state

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 23-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 23-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 23-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 23-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 23-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 23-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 23-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 23-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 23-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 23-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 23-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 23-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 23-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 23-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 23-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 23-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 23-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 23-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 23-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 23-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 23-21 | Page header | Tasks title | heading | Page title. |
| 23-22 | Page header | Start call session (disabled) | button (disabled) | Starts a call session on call tasks. |
| 23-23 | Page header | Create task (yellow +) | button (primary) | Creates a task. |
| 23-24 | Banner | 'Call recording now has rules' banner (phone icon) | banner | Compliance announcement. |
| 23-25 | Banner | New call recording rules (yellow) | button | Opens call recording rules settings. |
| 23-26 | Banner | Close x | icon button | Dismisses banner. |
| 23-27 | Tabs | All tasks (0) | tab | All tasks. |
| 23-28 | Tabs | Call tasks (0) | tab | Call tasks. |
| 23-29 | Tabs | Email tasks (0) | tab | Email tasks. |
| 23-30 | Tabs | LinkedIn tasks (0) | tab | LinkedIn tasks. |
| 23-31 | Tabs | Overdue tasks (0) | tab | Overdue tasks. |
| 23-32 | Tabs | All your tasks | tab | Everything assigned to me. |
| 23-33 | Tabs | Views (table icon + chevron) | menu trigger | Saved views of tasks. |
| 23-34 | Toolbar | Show Filters (badge 1) | toggle button | Opens task filters. |
| 23-35 | Toolbar | Search tasks | input | Search tasks. |
| 23-36 | Toolbar | Sort (badge 1, chevron) | menu trigger | Sort tasks. |
| 23-37 | Toolbar | View options (gear) | button | Choose displayed columns/density. |
| 23-38 | Empty state | Learn more about tasks (? icon) | link | Opens help docs. |
| 23-39 | Empty state | View all team tasks | button | Shows tasks of all teammates. |
| 23-40 | Empty state | New task (yellow) | button (primary) | Creates a task. |

## 24 - p4/24_palette_ctrl_k.jpg - Command palette opened with Cmd/Ctrl+K over the Tasks page

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 24-01 | Palette | Search input 'Search or ask a question in Apollo' | input | Search records/pages or ask AI. |
| 24-02 | Palette | 'AI suggestions' label | label | Group heading. |
| 24-03 | Palette | Suggestion: Build your target audience (arrow) | list item | Starts that AI flow. |
| 24-04 | Palette | Suggestion: Build your TAM (arrow, highlighted) | list item | Starts that AI flow. |
| 24-05 | Palette | Suggestion: Craft outbound sequence (arrow) | list item | Starts that AI flow. |
| 24-06 | Palette footer | Up/Down arrows 'Navigate' | keyboard shortcut | Move selection. |
| 24-07 | Palette footer | esc 'Close' | keyboard shortcut | Closes palette. |
| 24-08 | Palette footer | Enter 'Select' | keyboard shortcut | Runs highlighted suggestion. |
| 24-09 | Background | Top-bar credits, assistant, bell, avatar; Tasks page controls (inert) | background | Same as 23. |

## 25 - p4/25_notifications.jpg - Notifications panel (Activities / Notifications tabs)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 25-01 | Panel | Tab: Activities | tab | Activity feed. |
| 25-02 | Panel | Tab: Notifications (selected) | tab | Notification list. |
| 25-03 | Panel | Close x (top right of panel) | icon button | Closes panel. |
| 25-04 | Panel | 'Older' group label | label | Time group. |
| 25-05 | Notification | 'There are no CRM integrations connected yet.' card (timestamp) | notification card | System notice. |
| 25-06 | Notification | Dismiss x on card | icon button | Removes the notification. |
| 25-07 | Notification | Go To Settings | button | Opens Integrations settings. |
| 25-08 | Top bar | Bell icon (active) | icon button | Toggles the panel. |
| 25-09 | Background | Top-bar credits, assistant, avatar; Tasks page | background | Same as 23. |

## 26 - p4/26_menu_settings.jpg - Admin Settings quick flyout (Team & Workspace setup)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 26-01 | Flyout | 'Team & Workspace setup' title with '0% Completed' progress | progress header | Team setup checklist progress. |
| 26-02 | Flyout | Users and teams | menu item | Opens Users & teams settings. |
| 26-03 | Flyout | System activity | menu item | Opens system activity log. |
| 26-04 | Flyout | Security | menu item | Opens security settings. |
| 26-05 | Flyout | Plan overview | menu item | Opens plan overview. |
| 26-06 | Flyout | Integrations | menu item | Opens integrations settings. |
| 26-07 | Flyout | All settings | menu item | Opens the full Settings area. |
| 26-08 | Sidebar (rail) | Admin Settings gear (active) | icon button | Opens/closes this flyout. |
| 26-09 | Background | Collapsed rail, top bar, Tasks page controls | background | Same as 23. |

## 27 - p4/27_settings_overview.jpg - Settings overview (Workspace settings)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 27-01 | Settings sidebar | Back (arrow) | button | Returns to main app. |
| 27-02 | Settings sidebar | Collapse '<<' | icon button | Collapses settings sidebar. |
| 27-03 | Settings sidebar | Search settings input | input | Search settings pages. |
| 27-04 | Settings sidebar | Settings overview (selected) | nav item | Overview page. |
| 27-05 | Settings sidebar | Personal (chevron) | collapsible group | Profile, preferences. |
| 27-06 | Settings sidebar | Plan & billing (chevron) | collapsible group | Plan, add-ons, billing. |
| 27-07 | Settings sidebar | Credits & activity (chevron) | collapsible group | Credit usage, data requests, AI usage. |
| 27-08 | Settings sidebar | Users & teams (chevron) | collapsible group | Users, teams, permissions. |
| 27-09 | Settings sidebar | Integrations (chevron) | collapsible group | CRM, MCP, API keys. |
| 27-10 | Settings sidebar | Outbound (chevron) | collapsible group | Email/dialer outbound settings. |
| 27-11 | Settings sidebar | Data management (chevron) | collapsible group | Data settings. |
| 27-12 | Settings sidebar | Ideal customer profile (chevron) | collapsible group | ICP definitions. |
| 27-13 | Settings sidebar | Meetings & conversations (chevron) | collapsible group | Scheduling and call recording settings. |
| 27-14 | Settings sidebar | Add teammates (button, bottom) | button | Invites teammates. |
| 27-15 | Top bar | Global search + Cmd K | input / shortcut | Command palette. |
| 27-16 | Top bar | 75 credits, assistant icon, bell, avatar | top bar controls | Same as top bar. |
| 27-17 | Card | Plan & billing: 'Professional' badge | badge | Current plan. |
| 27-18 | Card | Plan overview link | link | Opens plan overview. |
| 27-19 | Card | Product Add-ons link | link | Opens add-ons. |
| 27-20 | Card | Billing link | link | Opens billing. |
| 27-21 | Card | Credits & activity: Upgrade plan (button) | button | Opens upgrade flow. |
| 27-22 | Card | Credit usage link | link | Opens credit usage. |
| 27-23 | Card | Data requests link | link | Opens data requests log. |
| 27-24 | Card | AI word usage link | link | Opens AI usage. |
| 27-25 | Card | Users & teams: Add teammates (button) | button | Invites teammates. |
| 27-26 | Card | Users link | link | User list. |
| 27-27 | Card | Workspace details link | link | Workspace profile. |
| 27-28 | Card | Teams link with 'Upgrade' badge | link + badge | Gated feature. |
| 27-29 | Card | Team sharing & defaults link | link | Sharing defaults. |
| 27-30 | Card | Permission profiles link with 'Upgrade' badge | link + badge | Gated feature. |
| 27-31 | Card | System activity log link | link | Audit log. |
| 27-32 | Card | Territories link with 'Upgrade' badge | link + badge | Gated feature. |
| 27-33 | Card | Security link | link | Security settings. |
| 27-34 | Card | License settings link | link | License/seats. |
| 27-35 | Card | Support access link | link | Grant support access. |
| 27-36 | Card | Integrations: All integrations link | link | Integration catalog. |
| 27-37 | Card | MCP link | link | Apollo MCP setup. |
| 27-38 | Card | API Keys link | link | API key management. |
| 27-39 | Page chrome | Help button | floating button | Opens help. |

## 28 - p4/28_menu_outbound_settings.jpg - Settings overview with 'Outbound' group expanded

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 28-01 | Settings sidebar | Back, collapse '<<', Search settings, Settings overview, Personal, Plan & billing, Credits & activity, Users & teams, Integrations | nav | Same as 27. |
| 28-02 | Settings sidebar | Outbound (chevron up, expanded) | collapsible group | Collapses Outbound subpages. |
| 28-03 | Settings sidebar | Overview (Outbound) | nav item | Outbound overview. |
| 28-04 | Settings sidebar | Domains & Mailboxes | nav item | Mailbox/domain management. |
| 28-05 | Settings sidebar | Sending policies | nav item | Sending limits and bounce guard. |
| 28-06 | Settings sidebar | Sequences | nav item | Sequence defaults. |
| 28-07 | Settings sidebar | Tracking | nav item | Open/click tracking settings. |
| 28-08 | Settings sidebar | Dialer | nav item | Dialer/phone settings. |
| 28-09 | Settings sidebar | Prospecting configuration | nav item | Prospecting defaults. |
| 28-10 | Settings sidebar | Snippets | nav item | Reusable email snippets. |
| 28-11 | Settings sidebar | Data management, Ideal customer profile, Meetings & conversations (chevrons) | collapsible groups | Same as 27. |
| 28-12 | Settings sidebar | Add teammates | button | Invites teammates. |
| 28-13 | Content | All overview cards and links as in 27 | cards/links | Same as 27. |
| 28-14 | Top bar | Global search + Cmd K, 75 credits, assistant icon, bell, avatar | top bar controls | Same as 27. |
| 28-15 | Page chrome | Help button | floating button | Opens help. |

## 29 - p4/29_email_setup_health.jpg - Email setup and health > Domains & Mailboxes (empty)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 29-01 | Settings sidebar | Same nav as 28 with 'Domains & Mailboxes' selected | nav | Same as 28. |
| 29-02 | Page header | 'Email setup and health' title | heading | Page title. |
| 29-03 | Page header | Badge 'Workspace deliverability: Excellent' (shield) | badge | Overall deliverability status. |
| 29-04 | Page header | Run a check (icon) | button | Runs deliverability diagnostics. |
| 29-05 | Page header | Add email & domain (yellow, caret) | menu trigger (primary) | Add mailbox or domain. |
| 29-06 | Tabs | Overview | tab | Health overview. |
| 29-07 | Tabs | Domains & Mailboxes (selected) | tab | List of domains and mailboxes. |
| 29-08 | Tabs | Sending policies | tab | Sending limits. |
| 29-09 | Empty state | 'No domains or mailboxes yet' card | card | Empty state. |
| 29-10 | Empty state | Add email & domain (yellow, caret) | menu trigger | Add mailbox or domain. |
| 29-11 | Assistant offer | 'Let <AI helper name> get you set up' text | info | Offers AI-guided setup. |
| 29-12 | Assistant offer | Set up with AI (purple) | button | Starts AI-guided mailbox setup. |
| 29-13 | Footer | 'What happens next' text with 'guide to domains and mailboxes' link | link | Opens help guide. |
| 29-14 | Top bar | Global search + Cmd K, 75 credits, assistant icon, bell, avatar | top bar controls | Same as 27. |
| 29-15 | Page chrome | Help button | floating button | Opens help. |

## 30 - p4/30_onboarding_hub_haut.jpg - Home onboarding hub (top, task list)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 30-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 30-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 30-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 30-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 30-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 30-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 30-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 30-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 30-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 30-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 30-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 30-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 30-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 30-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 30-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 30-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 30-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 30-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 30-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 30-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 30-21 | Home | Getting started (table icon + chevron) | dropdown (view switcher) | Switches Home view. |
| 30-22 | Onboarding | Title 'Get started with Apollo' + subtitle | heading | Intro. |
| 30-23 | Onboarding | Overall progress bar | progress indicator | Shows overall progress. |
| 30-24 | Onboarding | Section 'Start reaching the right prospects' with 'Earn 300 credits' badge | accordion header | Expands/collapses. |
| 30-25 | Onboarding | '0 of 6 completed' with caret | accordion toggle | Expand/collapse tasks. |
| 30-26 | Onboarding task | Save a contact - Save a prospect (yellow) | checkbox + button | Task and CTA. |
| 30-27 | Onboarding task | Save a mobile number - Find numbers | checkbox + button | Task and CTA. |
| 30-28 | Onboarding task | Create a list - Create a list | checkbox + button | Task and CTA. |
| 30-29 | Onboarding task | Save target companies - Save a company | checkbox + button | Task and CTA. |
| 30-30 | Onboarding task | Save your search - Set up alert | checkbox + button | Create saved-search alert. |
| 30-31 | Onboarding task | Download the Chrome extension - Install extension | checkbox + button | Opens Chrome extension store page. |
| 30-32 | Onboarding | Section 'Get your inbox ready to send' with 'Earn 150 credits' badge and '0 of 3 completed' caret | accordion header | Expand/collapse. |

## 31 - p4/31_onboarding_hub_bas.jpg - Home onboarding hub (scrolled, remaining sections and resources)

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 31-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 31-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 31-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 31-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 31-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 31-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 31-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 31-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 31-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 31-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 31-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 31-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 31-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 31-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 31-15 | Top bar | Global search box 'Search or ask a question in Apollo' | input / command trigger | Opens the command palette (search records or ask the AI a question). |
| 31-16 | Top bar | Keyboard hint badge 'Cmd K' (shortcut) | keyboard shortcut | Cmd/Ctrl+K focuses/opens the global search palette. |
| 31-17 | Top bar | '75 credits' pill | button (badge) | Shows remaining credit balance; click opens credit usage / buy credits. |
| 31-18 | Top bar | AI Assistant button (icon only, white tile) | icon button | Toggles the AI Assistant side panel (label collapsed at narrower width). |
| 31-19 | Top bar | Notification bell (with red dot when unread) | icon button | Opens the notifications / activities panel. |
| 31-20 | Top bar | User avatar with initials | icon button / menu trigger | Opens the account/profile menu. |
| 31-21 | Home | Getting started dropdown (floating) | dropdown | Switches Home view. |
| 31-22 | Onboarding task | Set up alert (clipped), Install extension | buttons | As in 30. |
| 31-23 | Onboarding | Get your inbox ready to send (Earn 150 credits) - '0 of 3 completed' caret | accordion header | Expand/collapse. |
| 31-24 | Onboarding | Scale your outreach (Earn 150 credits) - '0 of 3 completed' caret | accordion header | Expand/collapse. |
| 31-25 | Onboarding | Turn activity into meetings (Earn 150 credits) - '0 of 3 completed' caret | accordion header | Expand/collapse. |
| 31-26 | Resources | Heading 'More resources to help you master Apollo' | heading | Resources section. |
| 31-27 | Resources | Card: Learn with Apollo Academy | card link | Opens Academy training. |
| 31-28 | Resources | Card: Watch a webinar | card link | Opens webinars. |
| 31-29 | Resources | Card: Go deeper with help docs | card link | Opens help center. |

## 32 - p4/32_assistant_vide.jpg - AI Assistant side panel (empty state) opened beside Home

| # | Area | Control | Type | What it does in Apollo |
|---|---|---|---|---|
| 32-01 | Sidebar (collapsed rail) | Expand sidebar chevron '>>' (top) | icon button | Expands the rail into the full labelled sidebar. |
| 32-02 | Sidebar (collapsed rail) | Brand logo (top) | icon link | Goes to Home. |
| 32-03 | Sidebar (collapsed rail) | Home (house icon) | nav icon | Opens Home / Get started page. |
| 32-04 | Sidebar (collapsed rail) | AI Assistant (logo icon) | nav icon | Opens the AI Assistant full page. |
| 32-05 | Sidebar (collapsed rail) | Prospect and enrich (magnifier icon) | nav icon | Opens People search (group: People, Companies, Lists, Data enrichment). |
| 32-06 | Sidebar (collapsed rail) | Engage (paper-plane icon) | nav icon | Opens Sequences / Emails / Calls / Tasks group. |
| 32-07 | Sidebar (collapsed rail) | Win deals (dollar icon) | nav icon | Opens Meetings / Conversations / Deals group. |
| 32-08 | Sidebar (collapsed rail) | Lightbulb-style icon (4th group, unlabeled) | nav icon | Likely Analytics / insights group (label not visible in rail; best reading). |
| 32-09 | Sidebar (collapsed rail) | Arrow-into-door icon (5th group, unlabeled) | nav icon | Likely Tools / integrations or Inbound/Visitors group (label not visible; best reading). |
| 32-10 | Sidebar (collapsed rail) | People-group icon (6th group, unlabeled) | nav icon | Likely Team / workspace group (best reading). |
| 32-11 | Sidebar (collapsed rail) | Onboarding hub icon (list/document, bottom) | icon button | Opens the Onboarding hub checklist. |
| 32-12 | Sidebar (collapsed rail) | Email setup and health icon (diamond/nodes, bottom) | icon button | Opens Email setup and health. |
| 32-13 | Sidebar (collapsed rail) | Admin Settings (gear, bottom) | icon button / menu trigger | Opens the quick settings flyout menu. |
| 32-14 | Page chrome | Help button (circled ? + 'Help', bottom right) | floating button | Opens help center / support widget. |
| 32-15 | Top bar | Global search (compact) + Cmd K | input / shortcut | Command palette. |
| 32-16 | Top bar | 75 credits, assistant icon, bell, avatar | top bar controls | Same as top bar. |
| 32-17 | Home (squeezed) | Getting started dropdown / Set up alert / Install extension / accordions | controls | Same as 30/31 in narrower layout. |
| 32-18 | Assistant panel | Close x (top right) | icon button | Closes assistant panel. |
| 32-19 | Assistant panel | Greeting 'Hi, I'm your AI sales assistant...' with logo | text | Empty state; composer not visible in crop. |

## Controls that need data or services we may not have

| Group | Controls affected | Why it is hard and honest alternatives for Pépite |
|---|---|---|
| Phone / mobile numbers | Access Mobile, Mobile credits, Phone numbers column, 'Find numbers', Emails & Phone Numbers enrichment, Start call session, phone icon next to Home, DNC banner, dialer settings | Needs a phone-number provider (paid data), call compliance (DNC lists) and telephony. Honest alternative: manual phone field per contact, optional click-to-call via tel: link, and no purchased mobile data. |
| Credits / billing | 75 credits pill, Earn credits badges, Credit estimate, 1 credit per verified email, Credit usage, Upgrade plan / Upgrade to Professional, View plans, trial banner, Data requests | Pépite has no paid data to meter. Alternatives: remove credits entirely, or keep a local 'enrichment actions' counter if an external provider key (BYO) is used; omit plans/trial banners. |
| Email discovery & verification | Access email, Email Status filter (Verified/Unverified/Unavailable), catch-all handling, Waterfall enrichment, Data enrichment page, Verified emails % | Needs an email-finder/verifier service. Alternatives: user-supplied emails, optional BYO-key verification plugin, or syntax/MX check only. |
| Prospect database | Total/Net New counts (2.5M), 60+ filters, People/Company Lookalikes, Technologies, Market Segments, SIC/NAICS, Headcount Growth, Time Zone, Education, Awards, Persona | Apollo owns a licensed contact database. Pépite only has the user's own data: offer filters on fields the user imports; omit database-wide counts and lookalikes. |
| Buying intent | Buying Intent filter | Requires third-party intent data. Omit or replace with user-defined tags/scores. |
| Website visitor tracking | Website Visitors filter, 'Website visitors' tab in Company insights | Needs a tracking pixel and IP-to-company resolution. Omit or provide a basic first-party script later. |
| Funding / revenue / technologies / jobs / news | Funding, Revenue filters, Funding tab, Recent funding, Technologies tab, Job postings, News, Employee trends, Relevant Jobs/technology | Third-party firmographic feeds. Alternatives: free-text/manual fields, optional import from CSV. |
| Auto-scoring | People Auto-Score, Companies Auto-Score, Score breakdown, Qualify Contact/Account columns | Apollo uses ML plus its data. Alternative: simple rule-based score on user-defined criteria. |
| AI features | Apollo AI prompt-to-filter box, Research with AI menu (custom prompt, formula, assistant, templates), AI Assistant chat, Create with AI, Set up with AI, AI word usage, AI suggestions in palette | Needs an LLM provider. Alternatives: BYO API key, or hide AI controls when none configured. |
| CRM / ATS integrations | Push to CRM/ATS, 'There are no CRM integrations connected yet' notification, Integrations settings, MCP, API Keys | Needs OAuth apps per CRM. Alternatives: CSV export and a REST API/webhooks first; add CRMs per demand. |
| Email sending & mailboxes | Sequences, Emails inbox, Link mailbox, Add a mailbox, Domains & Mailboxes, Sending policies, Bounce Guard, Run a check, Workspace deliverability, warmup | Needs SMTP/IMAP or Gmail/Microsoft OAuth, deliverability monitoring and warmup. Alternatives: connect via SMTP/IMAP, simple daily caps, defer warmup/health. |
| Dialer, recordings, conversations | Start call session, Call tasks, Calls, call recording rules, Conversations, Meetings | Needs telephony, transcription and storage. Alternatives: log calls manually; link out to calendar for meetings. |
| Chrome extension | Install extension | Needs a separate browser extension. Alternative: bookmarklet or defer. |
| Teams, permissions, territories, SSO, system activity | Teams, Permission profiles, Territories, Security, System activity log, Users, License, Support access | Multi-tenant admin features; may be simplified to roles for a self-hosted single workspace. |
| Marketing content / academy | Learn with Apollo Academy, Watch a webinar, help docs, Help widget, onboarding earn-credits | Needs own docs. Alternative: link to Pépite docs only. |
