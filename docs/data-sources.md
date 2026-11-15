# Data sources

LeadCommons only uses public data that it is allowed to use.

| Source | Licence | What we take | Notes |
| --- | --- | --- | --- |
| Wikidata | CC0 1.0 | company name, website, country, industry, size, founding year | no attribution required, we give it anyway |
| GLEIF (LEI records) | CC0 1.0 | legal name, jurisdiction, address, LEI | company-level only |
| INSEE SIRENE (France) | Licence Ouverte 2.0 (Etalab) | legal name, SIREN, address, activity code, size band | attribution: "Source: INSEE, base SIRENE" |
| SEC EDGAR (United States) | US public data | name, CIK, ticker, state, SIC industry | needs a contact address, see below |
| OpenStreetMap (planned) | ODbL 1.0 | local businesses | attribution and share-alike apply to the derived database |
| Company websites | n/a | public team, about, legal notice and contact pages | robots.txt respected, rate-limited, kept only in your local database |

Each pack ships with an `ATTRIBUTION.md` listing sources and licences. Packs contain companies only, never people.

## SEC EDGAR contact address

The SEC asks every automated client to say who it is. Set `LEADCOMMONS_CONTACT_EMAIL` before
building a pack and LeadCommons adds it to its User-Agent; without it, EDGAR is skipped. The
monthly workflow uses the project's GitHub no-reply address, or the repository variable
`EDGAR_CONTACT_EMAIL` when it is set. Requests stay
under the SEC's limit of 10 per second.

## Build your own pack

```sh
leadcommons pack build --country FR --country DE --out ./data/packs
```

Options: `--source wikidata --source gleif --source sirene --source edgar`, `--limit N` per source. Packs are Parquet files; every `*.parquet` in the data directory is searched.

## Published packs

A monthly workflow publishes a GitHub Release tagged `packs-YYYY-MM` containing `companies-*.parquet` and `ATTRIBUTION.md`. `leadcommons pack download latest` fetches the newest one.

## Adding a source

Only open licences or explicit permission. See the connector guide in [CONTRIBUTING.md](../CONTRIBUTING.md).
