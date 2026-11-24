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

Connectors turn one open data source into `Company` records. See `src/pepite/connectors/base.py` for the interface.

1. Add `src/pepite/connectors/<name>.py` implementing the base connector class: a `name`, a `licence` (SPDX id), and a method that yields `Company` objects with a `Provenance` entry.
2. Use `httpx` with the shared user agent and polite delays; paginate and support `countries` and `limit`.
3. Register it so `pepite pack build --source <name>` finds it.
4. Add tests with recorded or hand-written fixtures (no live calls).
5. Document the source and licence in `docs/data-sources.md` and the pack attribution.

## Pull requests

Small, focused changes with tests. Fill in the PR template. By contributing you agree your work is licensed under AGPL-3.0-or-later.
