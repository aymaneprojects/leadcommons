# People sources

"Find people" asks, for one company at a time, the official public sources that say who runs it,
and merges them with the people on the company's own website. It runs on your machine when you
click Find people (or run a workflow); results go to your local database only. Packs never contain
people.

Code: `src/leadcommons/connectors/people/` (one module per source), merge in
`src/leadcommons/enrich/people.py`.

## Which source is asked

The company's registry ids decide. A source that does not apply is not called.

| Source | Asked when the company has | Licence | Needs |
| --- | --- | --- | --- |
| Wikidata | a Wikidata id, or LEI, SIREN, UK number, CIK, Norwegian org number, or a website that matches exactly one organisation item | CC0 1.0 | nothing |
| SEC EDGAR insider filings | a CIK (US filer) | public domain | `LEADCOMMONS_CONTACT_EMAIL` |
| Companies House officers | registry `GB-CH` and a company number | OGL v3.0 | free API key in `LEADCOMMONS_COMPANIES_HOUSE_KEY` |
| Brønnøysund roles | registry `NO-BRREG` and an organisation number | NLOD 2.0 | nothing |
| Company website | a domain | n/a (robots.txt respected) | nothing |

France (SIREN) is not covered yet: the officers register (RNE) needs a free INPI account and is
planned. A company found by SIREN can still get its leaders from Wikidata when it has an item.

A source that cannot run says why. In the jobs menu you see for example "Companies House:
Companies House officers need a free API key ..." and the other sources still run. A source that
fails (network, rate limit) is reported as an error and never hides the others. If the website
cannot be read but a register answered, you get the register results.

## What each source gives, and what we keep

**Wikidata** (SPARQL, `query.wikidata.org`): people with CEO (P169), founded by (P112),
chairperson (P488), director/manager (P1037) or board member (P3320) on the company item. Only
humans who are alive, with a statement that is not deprecated and has no end date in the past.
Kept: name, role, start date, link to the item. Wikidata is partial: many companies have no
people, others are out of date.

**SEC EDGAR** (Forms 3, 4 and 5): from the newest filings of the issuer (up to 60 inside 24
months) we read each `reportingOwner`: name, `isOfficer` + `officerTitle`, `isDirector`. The
latest statement per owner wins; owners who are neither officer nor director in it (including
pure 10 % holders and funds) are dropped. "Current" means reported in the last 24 months: an
insider who left without a filing may still appear. SEC fair use: the contact email is added to
the User-Agent, requests are spaced about 150 ms apart (under 10 per second). Without a contact
the source is skipped. Kept: name, title, link to the filing. Addresses in the filings are not
read.

**Companies House** (`/company/{number}/officers`): active appointments (no `resigned_on`) with
role director, secretary, LLP member or LLP designated member. Corporate officers are companies
and are skipped. Kept: name, role, appointment date, link to the public officers page. Not kept:
date of birth, correspondence address, nationality, country of residence, occupation. Get a key
at developer.company-information.service.gov.uk (register an application, type REST); the limit
is 600 requests per 5 minutes and we use one to three per company.

**Brønnøysund** (`data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller`, no key): people
holding daglig leder (CEO), styreleder (chair), nestleder, styremedlem, varamedlem, innehaver,
komplementar, deltaker and kontaktperson roles, not those who stepped down. Auditors and
accountants (companies) are skipped. Birth dates are in the response and are discarded.

**Company website**: schema.org Person data, team cards, legal notices (mentions légales,
Impressum), as before.

## Merge rules

- Same person: equal normalised name, or same surname with compatible first names
  ("Timothy D. Cook", "Tim Cook" and "T. Cook" are one person).
- Title: the official title wins over the website's. Among official sources: SEC, then
  Companies House, then Brønnøysund, then Wikidata. Roles of one person in one source are joined,
  most senior first ("Chief executive officer, Board member").
- Name shown: the website's spelling, else Wikidata's, else the shortest official one.
- Every source URL is kept in `Person.source_urls`, official pages first. The chips on the people
  table and the person page show "Wikidata", "SEC EDGAR", "Companies House", "Brønnøysund
  register" or the website host, with the full URL as a tooltip or link.
- Seniority and department come from `enrich/titles.py`, which knows the register role words in
  English (director, company secretary, LLP member, board member), Norwegian (daglig leder,
  styreleder, nestleder, styremedlem, varamedlem, innehaver, komplementar) and French (président,
  directeur général délégué, gérant, administrateur).
- Suppression: nothing is fetched for an opted-out domain, and a person with an opted-out email
  address on file is not returned.

## Legal basis, and what you must do

The registers publish this data so that people can know who runs a company; the licences above
allow reuse. Using it to contact someone for B2B prospecting is still processing personal data
under GDPR/UK GDPR, and **you are the controller**. LeadCommons gives you the tools; it is not
legal advice (have a lawyer review before large campaigns).

- **Legitimate interest** (Art. 6(1)(f)) is the usual basis for contacting a business role
  holder about something relevant to their role. Do a short balancing test and keep it.
- **Art. 14**: you collected the data from a source, not from the person, so you must tell them
  who you are, where you got their data (name the register, as the source chips do), why, and how
  to object, at the latest in your first message (and within one month). The sequences in
  Engage can carry that line; the unsubscribe link must work.
- **Opt-out**: add the address or the domain to the suppression list (person or company page,
  Settings, Privacy); it is honoured by Find people, Find email and Engage.
- Do not use these sources for personal addresses, only for business roles. LeadCommons does not
  collect personal phone numbers or home addresses and drops them from register responses.
- Keep it on your machine: people are stored in your local database, never in a pack.

## Testing and fixtures

`tests/test_people_sources.py`. The Wikidata fixtures (`tests/fixtures/people/wikidata_*`) are
real responses for Microsoft (Q2283). The SEC, Companies House and Brønnøysund fixtures copy the
real formats with invented companies and people, because those hosts are not reachable from the
development sandbox; they should be checked once against the live APIs (GitHub Actions runner or
a laptop).
