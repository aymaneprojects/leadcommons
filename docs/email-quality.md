# Email quality: how we measure it, and where we are

The email finder guesses a person's work address from the company's public pages, checks it
and shows a status and a 0-100 score. This page reports how often it is right, measured with
`leadcommons eval emails` on a gold set. It gives the numbers before and after the November
2026 rework, and says what verification can and cannot tell for each mail provider.

## What the statuses and the score mean now

| Shown | Meaning | Measured |
| --- | --- | --- |
| **valid** | At least 90% likely to be right, **and** mailbox-level evidence: the mail server accepted the address and rejects made-up ones, or the person's full-name address is published on the company's own pages | 97.6% right on the held-out gold split (90% interval 96.0-99.0%) |
| **catch_all** | The server accepts any address, so it cannot confirm this one | |
| **invalid** | Bad syntax, no mail server for the domain, or the server refused this mailbox | |
| **unknown** | Everything else, including our best format-based guess when no mailbox check ran | |
| **score (0-100)** | Calibrated: out of 100 addresses scored 80, about 80 are right | calibration error 1.3 points with SMTP checks, 5.8 without (held-out split) |

Each found address also gets `checks.basis`, a plain sentence such as "{first}.{last} fits all
4 personal addresses on the site; Microsoft 365 accepted the mailbox, which alone does not
prove it is this person's: the status rests on the format evidence too". The profile shows it
next to the other checks.

**The address format alone never makes an address "valid".** Without a mailbox check, the best
format-based guesses (score 90-100) were right 93.1% of the time on the dev split but 87.9% on
the held-out split. Some people's addresses follow no format: nicknames, a namesake's digit,
a previous surname, a legacy handle. That ceiling is not reliably above 90%, so those guesses
stay "unknown" with an honest score, often 90 or more.

## Results

All numbers come from `leadcommons eval emails` on the gold fixture in `tests/fixtures/email_gold/`.
The fixture holds fake people at fake companies (see "The gold fixture" below). Each person's
own address is removed from the site evidence, so every row asks "find the address of someone
whose address is not published". The **dev** split (1,050 people, 300 companies) was used to
tune the model and fit the calibration. The **test** split (491 people, 150 companies) was held
out. "Coverage" is the share of all gold rows answered. Intervals resample whole companies,
because errors cluster by company.

### Held-out test split (491 people, 150 companies)

| | Baseline (commit 72b4035) | New |
| --- | --- | --- |
| **SMTP off (the default)** | | |
| Top-ranked guess: precision (coverage) | 69.7% (92.9%) | **77.4% (100%)** |
| True address among the first 3 guesses | 78.4% | **87.8%** |
| Shown as valid | none | none (by design, see above) |
| Calibration error of the score / Brier score | 6.8 points / 0.203 | **5.8 / 0.154** |
| **SMTP on (simulated mail servers)** | | |
| Shown address: precision (coverage) | 84.4% (85.1%) | **85.1% (100%)** |
| Shown as valid: precision | 95.7% (93.0-97.8%) | **97.6% (96.0-99.0%)** |
| Shown as valid: coverage | 52.1% | **60.1%** |
| Calibration error / Brier score | 5.0 points / 0.106 | **1.3 / 0.073** |

### Dev split (1,050 people, 300 companies; tuned on)

| | Baseline | New |
| --- | --- | --- |
| SMTP off: top-ranked guess, precision (coverage) | 66.7% (97.0%) | 79.6% (100%) |
| SMTP off: true address in the first 3 guesses | 80.3% | 88.8% |
| SMTP off: calibration error / Brier | 3.4 / 0.211 | 2.1 / 0.129 |
| SMTP on: valid precision (interval), coverage | 94.9% (93.3-96.5%), 54.4% | 97.8% (96.6-98.9%), 57.3% |
| SMTP on: calibration error / Brier | 5.1 / 0.112 | 1.8 / 0.065 |

### Per provider (held-out split, SMTP on)

| Provider | n | Top guess, old → new | Valid precision, old → new | Valid coverage, old → new |
| --- | --- | --- | --- | --- |
| Microsoft 365 | 142 | 70.7% → 83.8% | 93.8% → 99.0% | 57.0% → 67.6% |
| Google Workspace | 135 | 68.2% → 74.8% | 98.9% → 100% | 65.9% → 54.8% |
| Other / own server | 115 | 72.0% → 76.5% | 94.6% → 96.9% | 32.2% → 56.5% |
| Security gateway | 31 | 80.6% → 80.6% | 100% → 100% | 38.7% → 35.5% |
| Zoho | 31 | 58.3% → 71.0% | 100% → 90.5% | 29.0% → 67.7% |
| OVHcloud | 20 | 70.0% → 85.0% | 92.9% → 100% | 70.0% → 70.0% |
| IONOS | 17 | 47.1% → 47.1% | 85.7% → 85.7% | 82.4% → 82.4% |

Google coverage drops on purpose: an acceptance by Google Workspace or Microsoft 365 no longer
makes a bare guess "valid". Microsoft coverage still rises, because the finder now retries the
"one recipient per connection" throttling and ranks better. Small groups (IONOS, Zoho, OVH)
swing with a single company.

### Where the gains come from

- **Learning each company's house rules**, not only its format: umlauts written ae or a,
  hyphens kept or dropped, particles kept (vandenberg) or dropped (berg), which Spanish surname
  is used, family-first East Asian names, Cyrillic spelling. On the held-out split, top-guess
  precision rose for German-speaking companies (62.1% → 72.4%), Spanish-speaking ones
  (42.9% → 78.6%) and Chinese ones (75.0% → 85.0%). Russian names went from no guess to 65.7%.
  Arabic names stayed level (71.1% → 68.9%).
- **Probabilities instead of points**: pattern shares are a Dirichlet posterior predictive
  from the site's addresses, so a company with two formats keeps both and a consistent one
  converges. An explicit share of people follow no format at all.
- **Verification as evidence**: each SMTP answer is a likelihood ratio that depends on the
  provider and on how specific the format is. Candidates are combined in one posterior,
  then calibrated by isotonic regression on dev.

## What verification can and cannot tell, per provider

The SMTP probe runs only with `LEADCOMMONS_SMTP_PROBE=1` (or `--smtp`), never in demo mode,
and never sends DATA. It sends EHLO, `MAIL FROM:<>`, RCPT for up to 3 candidates (stopping at
the first accepted), then RCPT for a random, human-looking address to detect catch-all servers.
Sessions to one MX host are paced (1-3 s apart, all threads), commands time out after 6 s,
and the next MX host is tried when one is unreachable. 4xx answers (greylisting, "busy") are
retried after 20 s and 60 s; when a target is greylisted, the random address is asked in the
same session so both settle on the retry. Policy blocks (5.7.x) are not retried.

| Provider (MX) | What an RCPT answer means | How we use it |
| --- | --- | --- |
| Google Workspace (`aspmx.l.google.com`, `smtp.google.com`) | Unknown users get 550 5.1.1 unless the admin set a catch-all route. An accepted address can be an alias or group. Probing IPs are throttled quickly. | "Limited": acceptance is weak evidence (ratio 4) and makes an address valid only when the site's format evidence already rates it ≥ 0.5. A bare guess stays unknown (score ~86) and the basis says why. |
| Microsoft 365 (`*.mail.protection.outlook.com`, `*.mx.microsoft`) | Rejects unknown users (550 5.4.1) only with directory-based edge blocking. Hybrid tenants accept everything and bounce later; the random-address test catches that. Residential and cloud IPs are often blocked (5.7.6xx). | Same as Google. |
| Security gateways (Mimecast, Proofpoint, Barracuda, Cisco, Trend Micro, Sophos, ...) | Usually accept every recipient or reject for policy reasons; the real mailboxes sit behind them. | "Hidden": acceptance barely counts (ratio 1.5). A 5.1.1 rejection still counts against the address. |
| Zoho, Proton, OVHcloud, IONOS, Gandi, Fastmail, Yandex, Amazon WorkMail, Rackspace, Strato, Infomaniak, Titan | Reject unknown users at RCPT unless the domain has a catch-all (common at IONOS, OVH and Strato); the random-address test finds those. | "Reliable": acceptance with random rejected is strong evidence (ratio 30, 12.6 for {first}/{last}, which namesakes often own). |
| Own server (MX on the domain itself, or A-record fallback) | Usually rejects unknown users. Some greylist first-time senders (Postgrey's default 5-minute delay outlasts our retries, so the result stays "temp": run it again a few minutes later and it passes). | Reliable. |
| Any provider, catch-all | Accepts made-up addresses, so acceptance says nothing. | Ratio 1; status catch_all unless the address is published. |

Everywhere: a 550 "user unknown" makes the address **invalid**, since sending would bounce. An
accepted mailbox may still belong to a namesake, an alias or a former employee, so the address
can reach a mailbox and still not be the person's. The harness reports this separately as
"reaches the person".

Free-mail domains (gmail.com, gmx.de, ...) and disposable ones (mailinator, ...) are flagged in
`checks`. The finder never guesses addresses at them.

**Most users cannot probe:** port 25 is blocked on most home and cloud connections. For them,
the gains are the better ranking and the calibrated score.

## How it is measured

- **Harness:** `src/leadcommons/enrich/eval.py`, run with
  `leadcommons eval emails --gold <csv> [--smtp] [--world <json>] [--json out.json] [--details rows.csv] [--min-valid-precision 0.9] [--fit]`.
- **Gold CSV:** `full_name, domain, true_address` (other columns ignored).
- **World file** (`<gold>.world.json`, optional): per domain, the names and addresses the site
  shows (replayed instead of crawling). It can also hold MX hosts (simulated DNS) and a mail
  server behaviour (`reject_unknown`, `accept_all`, `block`, `greylist`, `throttle`) with the
  existing mailboxes (simulated SMTP). With `--smtp`, the real verification code then talks to
  a fake server. Without a world file, sites are crawled live, DNS is real, and SMTP is real
  only with `--smtp` from a host where port 25 is open.
- **Leave-one-out:** each person's own address is removed from the evidence.
- **Metrics:** precision and coverage for the top guess, the shown address and "valid".
  Calibration buckets, expected calibration error and Brier score. A company-resampled 90%
  interval for valid precision. Breakdowns per provider, segment and amount of site evidence.
- **Calibration refit:** `--fit` prints new knots for `score.CALIBRATION`. Refit only on a dev
  set, never on the set you report.

### Baseline

The baseline is the finder at commit 72b4035 (before this work), run by the same harness. A
20-line adapter called its `find_email` body with the replayed evidence. One fairness patch:
the fixture's companies live under `.example` (`firma.de.example`), so the baseline's
German-domain check was given the `.de` it would see on a real domain.

### The gold fixture

`tools/make_email_gold.py` generates it, deterministically from a seed:

- **People:** random combinations of common given and family names. The mix is English,
  French (accents, Jean-Pierre, de la, Le), German (umlauts, ß, von), Spanish (two surnames,
  sometimes only one shown), Dutch (van der), Nordic (ø, å, æ), romanised Arabic (Al-, El, Ben,
  "bin" patronymics), Chinese, Japanese and Korean (family-first, in capitals or not), Russian
  in Cyrillic, plus international staff at US and UK companies.
- **Companies:** names under the reserved `.example` TLD (RFC 2606). The middle label carries
  the country (`nordwind-data.de.example`), so no real domain or person appears.
- **Addresses:** a main format per company drawn from per-country shares, a second format at
  18% of companies, and house rules (umlauts, hyphens, particles, surnames, transliteration).
  Per company, 0-20% of people (about 7% on average) have an irregular address (nickname,
  middle name, other format, legacy handle). Namesakes get a digit. Aliases exist at Google and Microsoft tenants, and
  former staff keep mailboxes.
- **Sites:** 1-10 people shown, 85% of them with their address, plus some addresses without a
  name and some role mailboxes.
- **Mail servers:** provider shares per country and simulated RCPT behaviour per provider
  (catch-all, blocking, greylisting, throttling).

The generator renders addresses with its own code, never the finder's. All of these shares are
**assumptions, not measurements**. The fixture tests the finder against a realistic mix and
keeps it honest from release to release; it does not prove real-world precision. The SMTP
numbers in particular depend on the simulated server behaviour.

### Honest notes

- The held-out split was evaluated once before the rule "the format alone is never valid" was
  adopted. Back then, format-only "valid" was 87.6% precise on it (dev: 92.7%), and that miss
  is why the rule exists. Three later fixes came from unit tests, not from test-split numbers:
  namesakes listed twice now split a published address, an accepting server lifts the
  A-record discount, and the random address is asked early when a target is greylisted.
- The calibration is fitted on synthetic data. On real companies the format-only scores may
  run high or low; measure with a real gold set and refit.
- Nothing here measures bounces. The roadmap's opt-in bounce feedback would.

## Build a real gold set

Names and addresses that a company publishes side by side on its own site are the most honest
gold we can get lawfully:

```sh
# domains.txt: one company domain per line (pick a mix of countries, sizes and providers)
leadcommons eval build-gold domains.txt --out ~/.leadcommons/eval/gold.csv
leadcommons eval emails --gold ~/.leadcommons/eval/gold.csv          # replays the saved evidence
leadcommons eval emails --gold ~/.leadcommons/eval/gold.csv --smtp   # from a host with port 25 open
```

`build-gold` crawls each site politely: robots.txt, rate limit, the same crawler as the app. It
pairs a person's name with a personal address only when both sit in the same card or block, or
the address follows the name within two lines. It skips role mailboxes, blocks that name two
people, and addresses that share nothing with the name, such as an assistant's address next to
a director. It never filters by format, so irregular addresses stay in. It writes the gold CSV
and `gold.world.json` (the names and addresses the site showed, for leave-one-out replay
without crawling again).

**These files hold personal data of real people.** Keep them on your machine. Never commit,
publish or share them, delete them when you are done, and leave out any domain or person on
your opt-out list. Only the aggregate numbers belong in this document.

Expect bias: companies that publish addresses tend to be smaller and more consistent than
average. Compare per country and per provider, not just the headline.
