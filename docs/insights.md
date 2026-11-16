# Company insights

Company insights show what can honestly be learned about a company from public sources. Open a
company, click **Refresh** in the Company insights card, and Pépite reads the company's own
website, public DNS, the job board the site links to, and Wikidata. Nothing is bought, guessed
or scraped from login-walled sites.

## Where the data comes from

| Tab | Source | What you get |
| --- | --- | --- |
| Overview | all of the below | one row each for jobs, technologies, funding, employees, phone |
| Technologies | homepage and a few company pages, response headers, cookie names, MX/SPF/TXT records | 290 rules over CMS, e-commerce, analytics, advertising pixels, tag managers, marketing automation, CRM forms, chat, payments, JS frameworks, CDN/hosting, web servers, A/B testing, consent managers, email providers and business tools. Every hit shows its evidence (for example `header server: cloudflare`). |
| Job postings | public embed APIs of Greenhouse, Lever, Ashby, Workable, Recruitee and SmartRecruiters | title, department, location, remote, URL, posted date, plus open positions per department |
| Funding | Wikidata (CC0) | stock exchange and ticker, revenue per year with currency, owner. Funding rounds are not in public data: the tab says "no public data" instead of guessing. |
| Employee trends | Wikidata P1128 with point in time | employee counts over time, 1 and 2 year growth, chart |
| Contact | company's own pages | business phone numbers (E.164 when the country is known), generic mailboxes (contact@, info@ ...), postal address from structured data, company social pages |

## Rules the crawler follows

- Same polite crawler as people search (`enrich/crawl.py`): User-Agent, robots.txt, one request
  per second per site, size limit, same-site redirects only. Up to six pages: homepage, careers,
  contact, legal notice and about pages. Team pages are not visited.
- Job boards and Wikidata are read through their documented JSON APIs with the polite HTTP client
  (back-off, `Retry-After`).
- No personal data in insights: numbers inside team or person cards and Person structured data
  are skipped, only role mailboxes are kept, only company profiles (not `linkedin.com/in/...`).
- Demo mode (`PEPITE_MODE=demo`) never refreshes: the API answers 403, jobs are refused and
  the button is disabled. Insights saved earlier still display.

## Using it

- Partial: `GET /partials/insights?company_id=<id>&tab=overview|technologies|jobs|funding|trends|contact`
  returns the card (tabs, "Never refreshed" / "Refreshed 3 h ago", Refresh button). Load it with
  `hx-get` in a drawer or page. While a refresh runs the card polls itself every two seconds.
- API: `GET /api/companies/{id}/insights` returns the stored insights (or `refreshed: false`);
  `POST /api/companies/{id}/insights/refresh` queues the job and returns `{"job_id": ...}`.
- Job kind `company_insights` with `{"company_id", "domain"}`.
- Python: `insights.get(store, company_id)`, `insights.refresh(store, company, settings=None)`
  (pass `settings` to enforce demo mode), `insights.lookalikes(search, company, limit)`.

## Lookalikes

`lookalikes` returns companies with the same ISIC industry and the same or a neighbouring size
bucket, same country first, then closer size, then companies with a known domain, then name. The
company itself is excluded. `DuckDBCompanySearch.lookalike_companies(company, limit)` runs it as one
SQL query; other backends are scanned (up to 2,000 candidates). `similar_people(candidates, person)`
ranks people by seniority, department and shared title words (via `enrich/titles.py`).

## Limits

- Technology detection reads server responses, not a browser: tools injected by JavaScript after
  load, or behind a consent banner, can be missed. Matches are signals, not certainty.
- Job boards: only the six systems above. A company on another ATS shows "No job board found".
- Wikidata covers mostly larger companies, and its numbers are reported figures that can be years
  old. Small companies usually show "no public data" for employees, revenue and listing.
- Phone numbers without a `+` need the company's country (from the pack) to become E.164.
- Technology rules are written for Pépite; see `insights/techrules.py` to add more.
