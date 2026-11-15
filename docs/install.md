# Install

## Desktop app

The easiest way: download the app for Windows, Mac (Apple Silicon) or Linux from the
[latest release](https://github.com/aymaneprojects/leadcommons/releases/latest). See
[desktop.md](desktop.md). The rest of this page is for the command line, Docker and servers.

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

It installs the latest release (or `main` if there is none), then tries to download the latest company pack. Options (environment variables):

- `LEADCOMMONS_REPO`: `owner/name` of a fork.
- `LEADCOMMONS_VERSION`: a tag, branch or commit to install.

Start the app:

```sh
leadcommons serve
```

Open <http://127.0.0.1:8000>. Data lives in `~/.leadcommons` (override with `LEADCOMMONS_HOME`).

Update by running the installer again. Uninstall with `uv tool uninstall leadcommons`.

## Docker (local)

```sh
git clone https://github.com/aymaneprojects/leadcommons.git
cd leadcommons
mkdir -p data
docker compose up --build
```

The container runs as user id 10001. On Linux, give it write access to the data folder: `sudo chown 10001:10001 data`.

The port is bound to `127.0.0.1` only. Download real data inside the container:

```sh
docker compose exec leadcommons leadcommons pack download latest
```

## Own server or GitHub Actions

See [server.md](server.md) and [deploy-with-actions.md](deploy-with-actions.md).

## From source

```sh
git clone https://github.com/aymaneprojects/leadcommons.git
cd leadcommons
uv sync --extra dev
uv run leadcommons serve
```

## Configuration

| Variable | Meaning | Default |
| --- | --- | --- |
| `LEADCOMMONS_HOME` | where data lives | `~/.leadcommons` |
| `LEADCOMMONS_DATA_DIR` | folder with `*.parquet` packs | `$LEADCOMMONS_HOME/packs` |
| `LEADCOMMONS_DATABASE_URL` | SQLAlchemy URL | SQLite in the home folder |
| `LEADCOMMONS_MODE` | `local` or `server` | `local` |
| `LEADCOMMONS_PASSWORD_HASH` | login hash (`leadcommons hash-password`) | none |
| `LEADCOMMONS_PASSWORD` | plain login password (prefer the hash) | none |
| `LEADCOMMONS_SECRET_KEY` | session signing key | random per start |
| `LEADCOMMONS_COOKIE_SECURE` | `1` to send cookies over HTTPS only | off |
| `LEADCOMMONS_SMTP_PROBE` | `1` to enable live mailbox probing | off |
| `LEADCOMMONS_WORKERS` | background workers | 4 |
| `LEADCOMMONS_PACKS_REPO` | repo that `pack download latest` reads | `aymaneprojects/leadcommons` |
| `LEADCOMMONS_DUCKDB_MEMORY` | memory the company search may use (e.g. `1GB`) | 1/16 of RAM, 256 MB to 1 GB |
| `LEADCOMMONS_DUCKDB_BUILD_MEMORY` | memory for preparing a downloaded pack for search | 1/6 of RAM, 512 MB to 2 GB |
| `LEADCOMMONS_DUCKDB_THREADS` | CPU threads for company search | number of cores, at most 8 |
| `LEADCOMMONS_LOCALE` | your locale, e.g. `fr-FR`: the country downloaded first | system locale |
