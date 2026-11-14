# Data sources

LeadCommons only uses public data that it is allowed to use.

| Source | Licence | What we take | Notes |
| --- | --- | --- | --- |
| Wikidata | CC0 1.0 | company name, website, country, industry, size, founding year | no attribution required, we give it anyway |
| GLEIF (LEI records) | CC0 1.0 | legal name, jurisdiction, address, LEI | company-level only |
| OpenStreetMap (planned) | ODbL 1.0 | local businesses | attribution and share-alike apply to the derived database |
| Company websites | n/a | public team, about, legal notice and contact pages | robots.txt respected, rate-limited, kept only in your local database |

Each pack ships with an `ATTRIBUTION.md` listing sources and licences. Packs contain companies only, never people.

## Build your own pack

```sh
leadcommons pack build --country FR --country DE --out ./data/packs
```

Options: `--source wikidata --source gleif`, `--limit N` per source. Packs are Parquet files; every `*.parquet` in the data directory is searched.

## Published packs

A monthly workflow publishes a GitHub Release tagged `packs-YYYY-MM` containing `companies-*.parquet` and `ATTRIBUTION.md`. `leadcommons pack download latest` fetches the newest one.

## Adding a source

Only open licences or explicit permission. See the connector guide in [CONTRIBUTING.md](../CONTRIBUTING.md).
