# Contributing

Thanks for helping. Pépite is built for people who cannot pay for sales tools, so keep it free, local-first and lawful.

## Development setup

```sh
git clone https://github.com/aymaneprojects/leadcommons.git pepite
cd pepite
uv sync --extra dev
uv run pepite serve --reload
```

## Checks

```sh
uv run ruff check .      # lint (add --fix for safe fixes)
uv run ruff format .     # format
uv run pytest -q         # tests
uv run actionlint        # GitHub Actions workflows
```

CI runs the same on Python 3.12 and 3.13 and builds the Docker image. Tests must not hit the network; use `respx` or the fakes in `tests/fakes.py`.

## Rules for data

- Only public, lawful data with a licence that allows reuse. State the licence in the connector's `Provenance`.
- No scraping behind logins, no captcha bypass, no paid-data resale.
- Respect robots.txt and rate-limit; use the shared `USER_AGENT`.
- Personal data (people, emails) is never committed, synced or uploaded.

## Writing a connector

There are two kinds. The monthly packs come from **bulk sources**; the small **connectors** serve
`pepite pack build` and the live lookups.

**A bulk source** turns one whole official register into a shard of companies. Read the contract at
the top of `src/pepite/connectors/bulk/__init__.py`, then:

1. Add `src/pepite/connectors/bulk/<name>.py` that builds the shard and registers a `BulkSource`
   (`name`, `title`, `licence` as an SPDX-like id, `licence_url`, `homepage`, `countries`,
   `attribution`). Modules in that folder are found automatically: `pepite pack sources` lists it.
2. Keep active companies only, fill the linking keys the register has, and never write the name of
   a natural person (packs hold companies only).
3. Add tests with small hand-written files (see `tests/test_bulk_sources_a.py`; no live calls).
4. Add the source to the tables in `docs/data-sources.md` and the README. The monthly workflow
   builds every registered source by itself.

**A connector** (Wikidata, GLEIF, SIRENE and EDGAR for `pepite pack build`) is in
`src/pepite/connectors/<name>.py`. See `src/pepite/connectors/base.py` for the interface: a `name`,
a `licence` and a method that yields `Company` objects with a `Provenance` entry. Use `httpx` with
the shared user agent and polite delays, support `countries` and `limit`, register it in
`src/pepite/connectors/__init__.py` so `pepite pack build --source <name>` finds it, and test it
with recorded or hand-written fixtures.

## Releasing

1. Set the new version in `pyproject.toml`, `src/pepite/__init__.py`, `desktop/electron/package.json` and `desktop/electron/package-lock.json` (both `version` fields at the top).
2. In `CHANGELOG.md`, rename `## Unreleased` to `## X.Y.Z (date)`.
3. Merge to `main` once CI is green.
4. Actions, **Release**, Run workflow on `main`: enter `version` (for example `0.2.0`, no `v`). Tick `dry_run` first to check, test and build without publishing.

The run checks that the four files and the changelog agree, runs the tests, builds the wheel and sdist, creates the tag `vX.Y.Z` and a GitHub release (notes from the changelog), pushes the Docker image, and starts the **Desktop apps** build, which attaches the Windows, macOS and Linux installers to that release.

## Pull requests

Small, focused changes with tests. Fill in the PR template. By contributing you agree your work is licensed under AGPL-3.0-or-later.
