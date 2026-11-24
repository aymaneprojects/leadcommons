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

- `PEPITE_REPO`: `owner/name` of a fork.
- `PEPITE_VERSION`: a tag, branch or commit to install.

Start the app:

```sh
pepite serve
```

Open <http://127.0.0.1:8000>. Data lives in `~/.pepite` (override with `PEPITE_HOME`).

Update by running the installer again. Uninstall with `uv tool uninstall pepite`.

## Docker (local)

```sh
git clone https://github.com/aymaneprojects/leadcommons.git pepite
cd pepite
docker compose up --build
```

Your data lives in the Docker volume `pepite-data` (kept when you stop or rebuild the container).
To keep it in a folder instead, replace `pepite-data:/data` in `docker-compose.yml` with `./data:/data`; the container runs as user id 10001, so on Linux run `sudo chown 10001:10001 data`.

The port is bound to `127.0.0.1` only. Download real data inside the container:

```sh
docker compose exec pepite pepite pack download latest
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

| Variable | Meaning | Default |
| --- | --- | --- |
| `PEPITE_HOME` | where data lives | `~/.pepite` |
| `PEPITE_DATA_DIR` | folder with `*.parquet` packs | `$PEPITE_HOME/packs` |
| `PEPITE_DATABASE_URL` | SQLAlchemy URL | SQLite in the home folder |
| `PEPITE_MODE` | `local` or `server` | `local` |
| `PEPITE_PASSWORD_HASH` | login hash (`pepite hash-password`) | none |
| `PEPITE_PASSWORD` | plain login password (prefer the hash) | none |
| `PEPITE_SECRET_KEY` | session signing key | random per start |
| `PEPITE_COOKIE_SECURE` | `1` to send cookies over HTTPS only | off |
| `PEPITE_SMTP_PROBE` | `1` to enable live mailbox probing | off |
| `PEPITE_WORKERS` | background workers | 4 |
| `PEPITE_PACKS_REPO` | repo that `pack download latest` reads | `aymaneprojects/leadcommons` |
| `PEPITE_DUCKDB_MEMORY` | memory the company search may use (e.g. `1GB`) | 1/16 of RAM, 256 MB to 1 GB |
| `PEPITE_DUCKDB_BUILD_MEMORY` | memory for preparing a downloaded pack for search | 1/6 of RAM, 512 MB to 2 GB |
| `PEPITE_DUCKDB_THREADS` | CPU threads for company search | number of cores, at most 8 |
| `PEPITE_LOCALE` | your locale, e.g. `fr-FR`: the country downloaded first | system locale |
