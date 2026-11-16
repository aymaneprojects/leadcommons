# UX parity: every control, screen by screen

The founder's 36 reference screenshots are listed control by control in
[`../ux-inventory.md`](../ux-inventory.md) (1,216 controls, ids `A1-01` … `32-xx`). These
files record what each control does in Pépite.

| Area | Screens | File |
| --- | --- | --- |
| Shell, home, assistant, palette, notifications, settings | A1–A3, 24–32 | [shell-and-settings.md](shell-and-settings.md) |
| Search filters, landing, all-filters window | A4, A5, B1 (filters), 05, 06, 08–10 | [filters.md](filters.md) |
| Results table, toolbar, selection bar, dialogs, drawers, person page, lists | B1–B4, 11–20 | [results-and-records.md](results-and-records.md) |
| Sequences, emails, tasks, mailboxes | 21–23 | [engage.md](engage.md) |

Status words:

- **works**: clicking it does the real thing (checked by tests, and by the button audit in
  `tools/ux_audit/audit.cjs`).
- **disabled (reason)**: visible, `aria-disabled`, and its tooltip says why and how to turn it
  on (no LLM configured, public demo, no data for it yet).
- **n/a (reason)**: left out on purpose. Usually it is a paid-product feature (credits, plans,
  trials, upgrades, teams), a feed we cannot use for free (news, intent), or tracking we do
  not do (open pixels, link rewriting).

Rows are often grouped (`A4-01..32`), so counts below are rows, not single controls.

| File | works | disabled | n/a |
| --- | --- | --- | --- |
| shell-and-settings.md | 254 | 2 | 27 |
| filters.md | 103 | 16 | 23 |
| results-and-records.md | 166 | 3 | 37 |
| engage.md | 50 | 0 | 8 |

## How to check it yourself

```bash
PEPITE_HOME=/tmp/lc-audit python tools/ux_audit/seed.py
PEPITE_HOME=/tmp/lc-audit pepite serve --port 8170 &
node tools/ux_audit/audit.cjs --base http://127.0.0.1:8170 --out /tmp/lc-audit/report
```

`tools/ux_audit/parity.cjs` puts the app in the state of each reference screenshot and
captures it, for a side-by-side check.
