# Working rules for the data agents (Phase 1 of docs/dev/data-roadmap.md)

Read `docs/dev/data-roadmap.md` first (goal, rules per source, what is out of scope).

## Shared tree

Several agents edit this repository at the same time. Edit only the files you own. For shared
files (`src/leadcommons/cli.py`, `src/leadcommons/connectors/bulk/__init__.py`, `docs/data-sources.md`,
`src/leadcommons/web/jobs.py`) make small `Edit` calls, re-read before editing, never rewrite the
whole file, never undo someone else's change. Do not run `git commit`, `checkout`, `stash`,
`reset` or `clean`: the lead commits. Before you finish: `.venv/bin/pytest -q -p no:warnings`,
`.venv/bin/ruff check src tests tools packaging`, `.venv/bin/ruff format src tests tools packaging`.

## Network

This sandbox reaches only a few hosts (Wikidata SPARQL, recherche-entreprises.api.gouv.fr, PyPI,
npm). Register downloads (data.gouv.fr, GLEIF, Companies House, SEC, data.brreg.no, data.gov.au,
open.canada.ca) are blocked here but work on GitHub Actions runners. So:

- Read each source's official format documentation (WebFetch / WebSearch may work; if not, rely on
  what you know and say so) and write the parser against small fixture files in `tests/fixtures/bulk/`
  that copy the real header and a few realistic (fake) rows.
- Make every downloader resumable, streaming to disk, with clear logs (bytes, rows, seconds).
- The lead runs the real thing in GitHub Actions (`packs.yml`, test mode on `data/**` branches) and
  sends you the logs if something breaks.

## Contracts

**Source shards** (`src/leadcommons/connectors/bulk/__init__.py`): one `BulkSource` per module,
registered with `register(...)`; output Parquet with exactly `PACK_COLUMNS`
(`src/leadcommons/search.py`, schema v0.3), via `shard_select` + `write_shard`. File names:
`<source>.parquet`, or `<source>-<CC>.parquet` when a worldwide source splits by country.

**CLI** (`src/leadcommons/cli.py`, `pack` group):

- `leadcommons pack fetch-source <name> --out <shards dir> [--limit N] [--country CC ...] [--work <dir>]`
  (bulk registers agent)
- `leadcommons pack merge <shards dir> --out <packs dir> [--release packs-YYYY-MM]`
  (linking agent)
- `leadcommons pack quality <packs dir>` prints the quality summary (linking agent)

**Merged pack** (written by `pack merge`, read by the app):

```
packs-out/
  companies-<CC>-<YYYYMMDD>.parquet   one file per country, PACK_COLUMNS, stable ids
  manifest.json
  ATTRIBUTION.md
  QUALITY.md
  quality.json
```

`manifest.json`:

```json
{
  "schema": 3,
  "release": "packs-2026-12",
  "built_at": "2026-12-01T03:40:00+00:00",
  "countries": [
    {"country": "FR", "file": "companies-FR-20261201.parquet", "rows": 13250000,
     "bytes": 812345678, "sha256": "…", "sources": ["sirene", "gleif", "wikidata"],
     "with_domain": 412345}
  ],
  "files": {"attribution": "ATTRIBUTION.md", "quality": "QUALITY.md", "quality_json": "quality.json"}
}
```

Releases published before schema 3 have a single `companies-*.parquet` and no manifest; the app
must keep reading them.

**Stable company ids**: the merged id comes from the strongest key, in this order: LEI,
`registry:company_number`, SIREN, CIK, Wikidata id, domain, then name + country. Format
`co_<16 hex>` from a hash of that key, so the same company keeps its id month after month (saved
lists and deals point to it).
