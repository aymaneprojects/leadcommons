# Install

## Desktop app

The easiest way: download the app for Windows, Mac (Apple Silicon) or Linux from the
[latest release](https://github.com/aymaneprojects/leadcommons/releases/latest). See
[desktop.md](desktop.md). The rest of this page is for the command line, Docker and servers.
After installing, [getting-started.md](getting-started.md) shows the first hour step by step.

## Requirements

- One-line installer: `curl` (Linux/macOS) or PowerShell (Windows). It installs [uv](https://docs.astral.sh/uv/) and Python 3.13 for you.
- Docker options: Docker Engine or Docker Desktop with Compose v2.

## One-line installer

```sh
curl -fsSL https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/scripts/install.sh | sh
```

```powershell
irm https://raw.githubusercontent.com/aymaneprojects/leadcommons/main/scripts/install.ps1 | iex
```

It installs the latest release (or `main` if there is none), then downloads the company data of
your country (guessed from your system language). If that fails, the small bundled sample is used;
add countries later in Settings > Data packs or with `pepite pack download --country FR`.
Options (environment variables):

- `PEPITE_REPO`: `owner/name` of a fork.
- `PEPITE_VERSION`: a tag, branch or commit to install.
- `PEPITE_PACKS_REPO`: repository the company data comes from (default: `PEPITE_REPO`).

Start the app:

```sh
pepite serve
```

Open <http://127.0.0.1:8000>. Data lives in `~/.pepite` (override with `PEPITE_HOME`). If you
used the app before it was renamed, an existing `~/.leadcommons` folder is kept and used instead
(the old `LEADCOMMONS_*` settings are still read too).

Update by running the installer again. Uninstall with `uv tool uninstall pepite`.

## Docker (local)

```sh
git clone https://github.com/aymaneprojects/leadcommons.git pepite
cd pepite
mkdir -p data
docker compose up --build
```

The container runs as user id 10001. On Linux, give it write access to the data folder: `sudo chown 10001:10001 data`.

The port is bound to `127.0.0.1` only. Data lives in the `data` folder. Download the companies
of a country inside the container (a container has no system language to guess from, so name
the country):

```sh
docker compose exec pepite pepite pack download --country FR
```

## Own server or GitHub Actions

See [server.md](server.md) and [deploy-with-actions.md](deploy-with-actions.md).

## From source

```sh
git clone https://github.com/aymaneprojects/leadcommons.git pepite
cd pepite
uv sync --extra dev
uv run pepite serve
```

## Configuration

Settings are environment variables. In the desktop app, the defaults are what you want.

| Variable | Meaning | Default |
| --- | --- | --- |
| `PEPITE_HOME` | where data lives (database, packs, mailbox key) | `~/.pepite`, or an existing `~/.leadcommons` |
| `PEPITE_DATA_DIR` | folder with `*.parquet` packs | `$PEPITE_HOME/packs` |
| `PEPITE_DATABASE_URL` | SQLAlchemy URL (Postgres in server mode) | SQLite file `pepite.db` in the home folder |
| `PEPITE_MODE` | `local` (one user, no login), `server` (login required) or `demo` (public showcase: nothing crawls or sends) | `local`; `demo` on Vercel |
| `PEPITE_PASSWORD_HASH` | login hash (`pepite hash-password`) | none |
| `PEPITE_PASSWORD` | plain login password (prefer the hash) | none |
| `PEPITE_SECRET_KEY` | session signing key | random per start |
| `PEPITE_COOKIE_SECURE` | `1` to send cookies over HTTPS only | off (on in the server Compose file) |
| `PEPITE_SMTP_PROBE` | `1` to enable live mailbox probing when finding emails | off |
| `PEPITE_WORKERS` | background workers | 4 |
| `PEPITE_SYNC_JOBS` | `1` to run jobs inside the request (no threads) | off; on in Vercel |
| `PEPITE_ENGAGE_SCHEDULER` | `0` so this app does not send sequence emails (a `pepite worker` does) | on |
| `PEPITE_PACKS_REPO` | repo that company data is downloaded from | `aymaneprojects/leadcommons` |
| `PEPITE_LOCALE` | your locale, e.g. `fr-FR`: the country downloaded first | system locale |
| `PEPITE_DUCKDB_MEMORY` | memory the company search may use (e.g. `1GB`) | 1/16 of RAM, 256 MB to 1 GB |
| `PEPITE_DUCKDB_BUILD_MEMORY` | memory for preparing a downloaded pack for search | 1/6 of RAM, 512 MB to 2 GB |
| `PEPITE_DUCKDB_THREADS` | CPU threads for company search | number of cores, at most 8 |
| `PEPITE_LLM_URL` | base URL of an OpenAI-compatible server (Pépite calls `<url>/chat/completions`) to understand searches and write drafts | none (built-in rules, offline) |
| `PEPITE_LLM_MODEL` | model name to send; both URL and model are needed | none |
| `PEPITE_LLM_API_KEY` | key for that server, if it needs one | none |
| `PEPITE_MCP_TOKEN` | bearer token of the MCP HTTP server ([mcp.md](mcp.md)) | none |
| `PEPITE_CONTACT_EMAIL` | contact address added to the User-Agent for SEC EDGAR (building packs, people from SEC filings) | none |
| `PEPITE_COMPANIES_HOUSE_KEY` | free API key for UK officers ([people-sources.md](people-sources.md)) | none |
| `PEPITE_DEBUG` | `1` to print the full traceback of a failing command | off |
