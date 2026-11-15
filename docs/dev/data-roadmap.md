# Data roadmap: Apollo-level data from public, lawful sources

Goal: data that is as broad and as reliable as Apollo's for B2B prospecting, while keeping our
rules: public and lawful sources only, no Apollo data, no LinkedIn scraping, no personal mobile
numbers, opt-outs honoured everywhere, packs that contain companies only.

## Where we are (November 2026) and where we go

| Dimension | Apollo (public claims) | LeadCommons today | Target | How |
| --- | --- | --- | --- | --- |
| Companies | tens of millions | 13,625 (10 countries, 1,500 per source) | 30M+ active companies | bulk official registers, monthly |
| Company website / domain | most companies | 11,612 | 5M+ linked domains | registry ID bridges (Wikidata, GLEIF), OSM, web matching |
| Firmographics | size, revenue, industry | industry, size band (partly) | exact head count and revenue where filed | SIRENE, ABR, UK and SEC accounts (XBRL), INPI accounts |
| Company phone | yes | from websites, on demand | switchboard for millions | registers, OSM, websites |
| People | 200M+ prebuilt profiles | found on demand on company sites | on demand from 5+ lawful sources, never a published people database | officer registers, SEC filings, Wikidata, websites |
| Emails | verified, ~90% accuracy claimed | pattern guess + checks | 90%+ measured precision | domain pattern intelligence, provider-aware verification, bounce feedback |
| Personal mobiles | yes | no | **no** (not lawful for us) | n/a |
| Technographics | yes | homepage, on demand | precomputed for linked domains | CI crawl of homepages |
| Funding, jobs | yes | jobs on demand | US Form D funding, ATS job feeds | SEC Form D, public ATS APIs |
| Intent, visitors | yes | no | **no** (needs tracking or paid feeds) | n/a |
| Quality measurement | marketing claims | none | QUALITY.md per pack, email precision report | `leadcommons pack quality`, `leadcommons eval emails` |

## Rules for every source

1. Licence allows reuse (CC0, Licence Ouverte, OGL, NLOD, CC-BY, public domain). Record it in
   `sources_json` and ATTRIBUTION.md.
2. Packs (published on GitHub) hold companies and domain-level facts only. Names of people are
   never written to a pack; people are fetched on demand, on the user's machine, and can be
   suppressed.
3. Respect robots.txt, rate limits and each register's fair-use rules (SEC: declared contact,
   10 requests per second).
4. Every number shown to users is measured: coverage, fill rate and precision are published with
   each pack.

## Phase 1: companies at scale, measured (started)

- **Bulk registers** (`connectors/bulk/`, `.github/workflows/packs.yml`): GLEIF golden copy
  (2.9M legal entities, CC0), INSEE SIRENE stock (France, Licence Ouverte), UK Companies House
  basic company data (OGL), SEC EDGAR bulk submissions (US filers), Brønnøysund (Norway, NLOD),
  Australian Business Register bulk extract (CC-BY), Canada federal corporations (OGL-Canada),
  Wikidata organisations with websites (CC0). Active companies only by default. One Parquet file
  per country plus `manifest.json`.
- **Linking and dedupe** (`packs/linking.py`, `packs/merge.py`): strong IDs first (GLEIF
  registration authority numbers to SIREN / Companies House numbers; Wikidata P1616 SIREN,
  P2622 Companies House, P5531 CIK, P1278 LEI, P2333 Norway), then name + postcode + city.
  Websites from Wikidata P856 attached through those IDs.
- **Quality report** (`packs/quality.py`): rows, fill rate per column, share with a domain,
  duplicates removed, per source and country; written as QUALITY.md and `quality.json`.
- **App at scale**: per-country download from the manifest; search stays fast (p95 < 1.5 s) on
  tens of millions of rows on a laptop.
- **Email quality**: provider-aware verification (Google Workspace, Microsoft 365, catch-all
  detection, greylisting), calibrated confidence, and an evaluation harness with a gold set built
  from addresses published next to names on public pages.

## Phase 2: people and firmographics

- People on demand from officer registers (UK Companies House officers API with a free key,
  Norway roles API, France RNE with a free INPI account), SEC insider filings (Forms 3/4/5),
  Wikidata (CEO, founders, chair, board) and better team-page extraction (schema.org Person).
- Revenue and head count from public accounts: UK iXBRL accounts bulk, SEC XBRL company facts,
  INPI annual accounts. Funding rounds from SEC Form D. Job postings from public ATS feeds.
- Domain-level email patterns learned from public pages, shipped in packs (pattern only, no names).

## Phase 3: more countries and signals

- More open registers: Brazil CNPJ, Japan corporate numbers, Switzerland Zefix, Czech ARES,
  Finland PRH, Estonia, Denmark CVR, New Zealand; US state registers with open bulk data.
- Company switchboard numbers from registers and OpenStreetMap (ODbL: share-alike for the
  derived pack). Technographics precomputed for linked domains. News mentions from GDELT.
- Bounce and reply feedback loop (local, opt-in) to keep email confidence honest.

## Not planned, on purpose

Personal mobile numbers, LinkedIn scraping or automation, buying data, harvesting users'
mailboxes or contacts without explicit opt-in, email open/click tracking, website visitor
identification, intent data from paid networks.

## Legal note

Prospecting people in the EU relies on legitimate interest, an Article 14 information duty and an
easy opt-out. LeadCommons gives users the tools (sources shown, opt-out list, suppression), but the
user is the data controller. Have a lawyer review before large campaigns.
