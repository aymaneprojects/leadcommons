# LeadCommonsBot: the site scan

LeadCommons links company websites to the companies that own them. Companies must print their
legal details on their own site (France: *mentions légales* with SIREN/RCS, UK: company number,
Germany: *Impressum*, Norway: org.nr, Australia: ABN...). Our bot reads those pages, finds a
valid register number and publishes one fact: **this domain belongs to that company**.

## For website owners

**User-Agent:** `LeadCommonsBot/<version> (+https://github.com/aymaneprojects/leadcommons/blob/main/docs/sitescan.md)`

**What it reads**, at most **4 pages per site** and **once per month**:

1. `robots.txt` (first, always)
2. the home page
3. the legal notice / imprint page (a link found on the home page, or a usual path such as
   `/mentions-legales`, `/impressum`, `/legal-notice`)
4. an about or contact page, only if the legal notice did not hold a register number

It stops as soon as it has found a register number on a legal page.

**How it behaves.** One request at a time per site, at least one second apart (your
`Crawl-delay`, up to 10 s, if larger). HTML only, at most 1 MB per page, 10 s timeouts. It follows
redirects only inside your own domain, never logs in, never submits forms, never fetches images,
scripts or files. A `5xx` answer on `robots.txt` makes it skip your site.

**What it keeps:** the register number (and the page it was on), the home page `<title>`, and
switchboard phone numbers of the company. **Never** names, e-mail addresses or phone numbers of
people. The pages themselves are not stored.

**How to opt out.** Add this to `https://your-site/robots.txt`; the bot reads it before every
visit and will never read another page of your site:

```
User-agent: LeadCommonsBot
Disallow: /
```

You can also disallow only some paths (`Disallow: /private/`). Entries you removed from our data
stay out of the next monthly build. For anything else open an issue on the repository.

## Running it

Pipeline: `sitescan hosts` (candidate domains) -> `sitescan run` (read the pages, write findings) ->
`sitescan shard` (a shard for the packs) -> `pack merge`.

```bash
# 1. Candidate domains. Common Crawl's URL index, per country TLD (runs in GitHub Actions:
#    .github/workflows/sitescan-hosts.yml publishes a release sitescan-hosts-YYYY-MM).
leadcommons sitescan hosts --tld fr --out hosts/
#    ...or the domains the packs already know (to verify them):
leadcommons sitescan hosts --packs packs/ --out hosts/

# 2. Scan. Resumable: run the same command again after an interruption.
leadcommons sitescan run --domains hosts/domains-fr.txt.gz --country FR --out findings/ --concurrency 60

# 3. Shard for the packs (domain, website, registry, company number, switchboard phone).
leadcommons sitescan shard findings/ --out shards/
```

`sitescan run` writes `findings-NNNNN.parquet` files (one row per domain: status, title, register
numbers as JSON, phones, pages read) to the output directory every 500 domains and skips the
domains already there. Findings status: `ok`, `no-site` (DNS or connection failure), `robots`
(disallowed, or `robots.txt` answered 5xx), `error` (other HTTP error, non-HTML, redirect to
another domain, timeout).

### On a small server

`deploy/sitescan/` holds a Dockerfile, a compose file and `run.sh`, which downloads the newest
`sitescan-hosts-*` release, scans, and builds the shard:

```bash
docker compose -f deploy/sitescan/docker-compose.yml build
TLDS="fr" PROCS=2 docker compose -f deploy/sitescan/docker-compose.yml run --rm sitescan
# monthly, from cron:  0 2 1 * *  cd /srv/leadcommons && TLDS=fr PROCS=2 docker compose ... run --rm sitescan
```

Findings are in the `sitescan-data` volume (`/data/findings/YYYY-MM/<tld>/part-K/`), the shard in
`/data/shards/YYYY-MM/sitescan.parquet`. Keep `PROCS` the same for the whole month: each process
owns one slice of the domains (`--part K/N`) and resumes only its own slice.

| Server | PROCS x CONCURRENCY | Throughput (estimate) | A TLD of 3 million domains |
| --- | --- | --- | --- |
| 2 vCPU, 4 GB | 2 x 60 | 10-12 domains/s, about 1 M/day | 3 days |
| 4 vCPU, 8 GB | 4 x 60 | 20-24 domains/s, about 2 M/day | 1.5 days |

How these were estimated: one Python process is CPU bound, not network bound. Parsing the pages
(three HTML passes per page) and finding numbers cost about 100 ms of CPU per domain for a 150 KB
home page plus one legal page (measured offline with a mock transport, 4 vCPU sandbox), so one core
does about 6 to 10 domains per second. The network side allows more: a live domain costs 3 to 5
requests of about 0.3 to 1 s plus 1 s politeness between them, about 5 s per domain, so 60 sites in
flight give about 12 domains/s per process. Dead domains are cheaper. Real figures depend on page
sizes, latency and the DNS resolver; the log prints the rate every 30 s. Memory is 300 to 600 MB
per process.

## How findings reach the packs

`sitescan shard` writes `sitescan.parquet` with exactly the pack columns, one row per
(domain, register number) for the registers the packs use (FR-SIREN, GB-CH, NO-BRREG, AU-ABN,
CA-CORP, US-SEC): `domain`, `website`, `registry`, `company_number` (FR also `siren`), `phone`
(first switchboard number), source `sitescan`, licence "n/a (public web pages, facts only)", and
the page where the number was read as the provenance URL. Copy it next to the other shards and run
the packs workflow (`leadcommons pack merge shards/ ...`): the merge joins it to the register's
company through the register key and attaches domain, website and phone (tier "web").

A site that shows several different companies of the same register (a group site, a franchise, an
agency) is ambiguous: no row is written for it. Numbers of registers we have no pack for are not
written either.
