# Data sources

Pépite only uses public data that it is allowed to use.

The monthly packs are built from eight whole registers, downloaded in bulk (details below).

| Source | Countries | Licence | What we take |
| --- | --- | --- | --- |
| INSEE SIRENE | France | Licence Ouverte 2.0 (Etalab) | legal name, SIREN, address, activity code, size band, creation year |
| UK Companies House | United Kingdom | OGL v3.0 | name, number, registered office, category, SIC code, incorporation year |
| Brønnøysund Enhetsregisteret | Norway | NLOD 2.0 | name, organisation number, address, industry, employees, website |
| Australian Business Register | Australia | CC BY 3.0 AU | name, ABN, state, postcode, entity type |
| Corporations Canada | Canada | OGL Canada 2.0 | name, corporation number, registered office, incorporation year |
| SEC EDGAR bulk submissions | United States | US public domain | name, CIK, address, SIC industry, website, business phone (needs a contact address, see below) |
| GLEIF golden copy | worldwide | CC0 1.0 | legal name, address, legal form, creation year, LEI |
| Wikidata organisations | worldwide | CC0 1.0 | website, register numbers, head count, industry, headquarters city |
| Company websites | n/a | n/a | public legal notice and contact pages: register number, business phone; robots.txt respected, rate-limited ([sitescan.md](sitescan.md)) |

OpenStreetMap (ODbL 1.0, local businesses) is planned. Its attribution and share-alike terms would
apply to the derived database.

Each pack ships with an `ATTRIBUTION.md` listing sources and licences. Packs contain companies only, never people.

## What each bulk register keeps (monthly packs)

The monthly packs are built from whole official registers, downloaded in bulk
(`pepite pack sources` lists them; `pepite pack fetch-source <name> --out shards/`
builds one shard). Active companies only.

| Source (`name`) | Licence | Kept | Left out |
| --- | --- | --- | --- |
| GLEIF golden copy (`gleif_golden`), ~2.9M LEI records, worldwide | CC0 1.0 | entities with status ACTIVE and an ISSUED, LAPSED, PENDING_TRANSFER or PENDING_ARCHIVAL LEI: legal name, legal address (headquarters for US entities, whose legal address is usually a registered agent), legal form text, creation year, LEI, and the register number behind the registration authority code (`registry` + `company_number`, below) | funds, branches, sole proprietors, inactive entities, retired / merged / annulled LEIs |
| INSEE SIRENE stock (`sirene_stock`), France | Licence Ouverte 2.0 (Etalab) | active, fully diffusible legal units with their head office: name, SIREN, address, postal code, city, region, NAF section, workforce band, creation year, legal category | units with partial diffusion; sole traders (entrepreneurs individuels), except those with a trade name that does not contain the person's name (the person's name is never written) |
| UK Companies House basic company data (`companies_house`) | OGL v3.0 | companies with status Active registered in the UK: name, number, registered office, postcode, town, nation, company category, incorporation year, first SIC code | companies in liquidation, administration, receivership or with a proposal to strike off; overseas entities |
| SEC EDGAR bulk submissions (`edgar_bulk`) | US public domain | operating companies with a ticker or a 10-K / 10-Q / 20-F / 40-F in the last two years: name, CIK, business address and state, SIC industry, website, business phone (E.164) | people filing insider forms, funds and other non-operating filers, shells without recent reports |
| Brønnøysund Enhetsregisteret (`brreg`), Norway | NLOD 2.0 | registered entities not deleted, bankrupt or being wound up: name, organisation number (`NO-BRREG`), business (else postal) address, postal code, town, county, NACE section, employees and size band, founding year, organisational form, website (`hjemmeside`) and domain | bankruptcy and other estates (KBO, BO); sole proprietorships (ENK) whose name is not a trade name (an ENK name contains the owner's surname by law: kept only with a trade word and no given name or initial); other entities named after persons |
| Australian Business Register, ABN Bulk Extract (`abr`) | CC BY 3.0 AU | active, non-replaced ABNs of organisations: name (business name for trusts and person-named partnerships), ABN (`AU-ABN`), state, postcode, entity type, ABN year when after 2000 | individuals / sole traders, self-managed super funds, deceased estates, family investment trusts without a business name, partnerships and trusts named after persons |
| Corporations Canada, Federal Corporations (`canada_corps`) | OGL – Canada 2.0 | active federal corporations (CBCA and the other federal acts): name (English form when there are two), corporation number (`CA-CORP`, digits as written), registered office, city, province, postal code, anniversary (incorporation) year, governing act | inactive or dissolved corporations, and active ones with a dissolution pending or an intent to dissolve; provincial corporations are not in this dataset |
| Wikidata organisations bridge (`wikidata_orgs`), worldwide | CC0 1.0 | organisations with an official website and/or a register number (SIREN, Companies House, Norwegian organisation number, ABN / ACN, Canadian corporation number, SEC CIK, LEI): website and domain, the number of the item's own national register in `registry` + `company_number` (so the merge attaches the website to the register row), LEI / SIREN / CIK, Wikidata id, inception year, latest head count, industry, HQ city; one shard per country | humans, dissolved organisations (P576), unlabelled items, items without a country, SIREN items without a website |

GLEIF registration authority codes mapped to registers (how GLEIF records join national
registers): RA000189 INSEE SIRENE -> `FR-SIREN`; RA000585 / RA000586 / RA000587 Companies House
(England and Wales / Northern Ireland / Scotland) -> `GB-CH`; RA000472 / RA000473 Brønnøysund
(Foretaksregisteret / Enhetsregisteret) -> `NO-BRREG`; RA000013 Australian Business Register ->
`AU-ABN` (and RA000014 ASIC, whose ACN gives the company's ABN); RA000072 Corporations Canada ->
`CA-CORP`; RA000463 KvK -> `NL-KVK`; RA000170 CVR -> `DK-CVR`.

Attribution lines: "Source: INSEE, base SIRENE des entreprises"; "Contains public sector
information from Companies House licensed under the Open Government Licence v3.0"; "Contains data
under the Norwegian licence for Open Government data (NLOD) distributed by
Brønnøysundregistrene"; "ABN Bulk Extract, Australian Business Register (Australian Taxation
Office), licensed under CC BY 3.0 AU"; "Contains information licensed under the Open Government
Licence – Canada (Corporations Canada)".

The Wikidata bridge queries query.wikidata.org politely (one query at a time, at least 1 s apart,
a User-Agent with the project URL and the declared contact, Retry-After honoured, timed-out chunks
split into smaller ones and resumed from the work directory). The worldwide run lists ~300,000
items and takes about 70 minutes.

## SEC EDGAR contact address

The SEC asks every automated client to say who it is. Set `PEPITE_CONTACT_EMAIL` before
building a pack and Pépite adds it to its User-Agent; without it, EDGAR is skipped. The
monthly workflow reads it from the repository secret `EDGAR_CONTACT_EMAIL` (Settings > Secrets
and variables > Actions > New repository secret); GitHub masks secrets in the public logs. SEC
refuses the GitHub no-reply address used as a fallback. Requests stay
under the SEC's limit of 10 per second. The bulk source `edgar_bulk` (one download of
`submissions.zip`) refuses to run without a contact address.

## Build your own pack

Quick, small packs from the live APIs (needs the build extra: `pip install 'pepite[build]'`, or
`uv sync --extra dev` in a source checkout):

```sh
pepite pack build --country FR --country DE --out ./data/packs
```

Options: `--source wikidata --source gleif --source sirene --source edgar` (default: Wikidata and
GLEIF, plus the national register of the country where there is one), `--limit N` per source.
Packs are Parquet files; every `*.parquet` in the data directory is searched.

To rebuild what is published, take the whole registers: run `pepite pack fetch-source <name> --out shards/`
for each name from `pepite pack sources`, then `pepite pack merge shards/ --out packs/` (one file per
country, with `manifest.json` and `ATTRIBUTION.md`) and `pepite pack quality packs/`. The workflow
`.github/workflows/packs.yml` does exactly this every month. Copy the result into your data
folder and run `pepite pack index`.

## Published packs

A monthly workflow (the 5th of each month) publishes a GitHub Release tagged `packs-YYYY-MM`. It contains one `companies-<CC>-<date>.parquet` per country, a small all-countries file `companies-0-demo-<date>.parquet` (for the public demo), `manifest.json` (rows, size and sha256 per country), `ATTRIBUTION.md`, and the quality report (`QUALITY.md`, `quality.json`). The app downloads only the countries you pick (Settings > Data packs, or `pepite pack download --country FR`), checks each file's sha256 and prepares it for fast search ([how](dev/search-performance.md)). Older releases with a single `companies-*.parquet` still work with `pepite pack download latest`.

## Adding a source

Only open licences or explicit permission. See the connector guide in [CONTRIBUTING.md](../CONTRIBUTING.md).
