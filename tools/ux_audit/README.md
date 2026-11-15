# UX audit: click every control

Checks that every visible button, link, menu, tab and checkbox does something.

```bash
LEADCOMMONS_HOME=/tmp/lc-audit python tools/ux_audit/seed.py          # fake people + a list
LEADCOMMONS_HOME=/tmp/lc-audit leadcommons serve --port 8170 &
node tools/ux_audit/audit.cjs --base http://127.0.0.1:8170 --out /tmp/lc-audit/report
```

It discovers pages from the app's own links (one per URL pattern, so detail pages are
visited once), then for every control loads the page in a fresh browser context, clicks it
and records the outcome:

| Outcome | Meaning |
| --- | --- |
| navigated, download, dialog, menu, updated, ui-change | the control works |
| same-page | a link to the page you are on (active navigation item) |
| disabled-with-reason | disabled, with a tooltip explaining why |
| **no-effect** | nothing happened: a dead control |
| **http-error** | a request it made answered 4xx/5xx |
| **blocked-by-overlay** | something covers it and swallows the click |
| **click-error / probe-error / page-error** | could not be clicked or the page failed |
| **disabled-no-reason** | disabled without telling the user why |

Items of a menu are clicked too (the menu is opened first). Console errors raised by a
click are attached to it. Controls in the sidebar and top bar are tested once. The report is
`report.md` (problems first) and `report.json`; the exit code is 1 when there are problems.

Run it against a throwaway home: it really clicks Delete, Save and Send buttons.
