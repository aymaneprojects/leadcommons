"""Command line interface: `leadcommons --help`.

Heavy modules are imported lazily inside each command so that `--help` stays fast
and works even if an optional dependency is missing.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(
    name="leadcommons",
    help="LeadCommons: free, open-source, local-first prospecting from public data.",
    no_args_is_help=True,
    add_completion=False,
)
pack_app = typer.Typer(help="Build, download and inspect company data packs.", no_args_is_help=True)
app.add_typer(pack_app, name="pack")

DEFAULT_PACKS_REPO = "aymaneprojects/leadcommons"


def _fail(message: str, hint: str | None = None) -> typer.Exit:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    if hint:
        typer.echo(hint, err=True)
    return typer.Exit(code=1)


def _guard[T](what: str, fn: Callable[[], T]) -> T:
    """Run fn(), turning expected failures into short, friendly messages."""
    try:
        return fn()
    except typer.Exit:
        raise
    except NotImplementedError:
        raise _fail(f"{what} is not available yet in this build.") from None
    except ModuleNotFoundError as exc:
        raise _fail(
            f"a required module is missing ({exc.name}).",
            "Reinstall LeadCommons, or run `uv sync` if you are working from source.",
        ) from None
    except KeyboardInterrupt:
        raise _fail("interrupted.") from None
    except Exception as exc:  # noqa: BLE001 - last-resort friendly message
        if os.environ.get("LEADCOMMONS_DEBUG"):
            raise
        raise _fail(
            f"{what} failed: {exc}", "Set LEADCOMMONS_DEBUG=1 for the full traceback."
        ) from None


def _settings() -> Any:
    from leadcommons.config import Settings

    return Settings()


@app.command()
def version() -> None:
    """Print the installed version."""
    from leadcommons import __version__

    typer.echo(f"leadcommons {__version__}")


@app.command()
def serve(
    host: Annotated[
        str | None,
        typer.Option(help="Interface to bind. Default 127.0.0.1 (local) or 0.0.0.0 (server mode)."),
    ] = None,
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    reload: Annotated[
        bool, typer.Option(help="Auto-reload on code changes (development).")
    ] = False,
) -> None:
    """Start the web app."""
    settings = _settings()
    if host is None:
        host = "0.0.0.0" if settings.mode == "server" else "127.0.0.1"  # noqa: S104
    if settings.mode == "server" and not (settings.password or settings.password_hash):
        raise _fail(
            "server mode needs a login.",
            "Set LEADCOMMONS_PASSWORD_HASH (make one with `leadcommons hash-password`).",
        )
    import uvicorn

    typer.echo(f"LeadCommons ({settings.mode} mode) on http://{host}:{port}")
    _guard(
        "the web server",
        lambda: uvicorn.run(
            "leadcommons.web.app:create_app", factory=True, host=host, port=port, reload=reload
        ),
    )


@pack_app.command("build")
def pack_build(
    country: Annotated[
        list[str] | None, typer.Option("--country", "-c", help="ISO country code (repeatable).")
    ] = None,
    source: Annotated[
        list[str] | None, typer.Option("--source", "-s", help="Source name (repeatable).")
    ] = None,
    limit: Annotated[int | None, typer.Option(help="Max rows fetched per source.")] = None,
    out: Annotated[
        Path | None, typer.Option(help="Output directory. Default: your data directory.")
    ] = None,
) -> None:
    """Build a company pack from open data (Wikidata, GLEIF)."""
    from leadcommons.packs import build_pack

    out_dir = out or _settings().data_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    countries = [c.upper() for c in country] if country else None
    sources = tuple(source) if source else ("wikidata", "gleif")
    path = _guard(
        "building the pack",
        lambda: build_pack(out_dir, sources=sources, countries=countries, limit_per_source=limit),
    )
    typer.echo(f"Pack written to {path}")


def _latest_pack_url(repo: str) -> str:
    import httpx

    api = f"https://api.github.com/repos/{repo}/releases"
    resp = httpx.get(
        api,
        params={"per_page": 30},
        headers={"Accept": "application/vnd.github+json"},
        timeout=30,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub answered HTTP {resp.status_code} for {api}")
    releases = [
        r
        for r in resp.json()
        if str(r.get("tag_name", "")).startswith("packs-") and not r.get("draft")
    ]
    releases.sort(key=lambda r: r.get("published_at") or r.get("created_at") or "", reverse=True)
    for release in releases:
        for asset in release.get("assets", []):
            if fnmatch.fnmatch(asset.get("name", ""), "companies-*.parquet"):
                return asset["browser_download_url"]
    raise RuntimeError(f"no 'packs-*' release with a companies-*.parquet file found in {repo}")


@pack_app.command("download")
def pack_download(
    url: Annotated[
        str, typer.Argument(help="Pack URL, or 'latest' for the newest published pack.")
    ] = "latest",
) -> None:
    """Download a published company pack into your data directory."""
    from leadcommons.packs import download_pack

    repo = os.environ.get("LEADCOMMONS_PACKS_REPO", DEFAULT_PACKS_REPO)
    data_dir = _settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    if url == "latest":
        typer.echo(f"Looking for the latest pack in {repo} ...")
        url = _guard("finding the latest pack", lambda: _latest_pack_url(repo))
    typer.echo(f"Downloading {url}")
    path = _guard("downloading the pack", lambda: download_pack(url, data_dir))
    typer.echo(f"Saved {path}")


@pack_app.command("info")
def pack_info() -> None:
    """List the packs currently loaded."""
    from leadcommons.packs import DuckDBCompanySearch

    settings = _settings()
    rows = _guard("reading packs", lambda: DuckDBCompanySearch(settings.data_dir).pack_info())
    if not rows:
        typer.echo("No packs found. Run `leadcommons pack download latest`.")
        return
    for row in rows:
        sources = row.get("sources") or []
        if not isinstance(sources, str):
            sources = ", ".join(str(s) for s in sources)
        typer.echo(
            f"{row.get('file')}  rows={row.get('rows')}  sources={sources}  "
            f"built={row.get('built_at')}"
        )


@app.command("find-people")
def find_people(
    domain: Annotated[str, typer.Argument(help="Company domain, e.g. example.com")],
) -> None:
    """List people found on a company's public web pages."""
    from leadcommons.enrich import find_people as _find

    people = _guard("finding people", lambda: _find(domain.strip().lower()))
    if not people:
        typer.echo(f"No people found on the public pages of {domain}.")
        return
    for p in people:
        title = f" - {p.title}" if p.title else ""
        typer.echo(f"{p.full_name}{title}")
        for src in p.source_urls[:1]:
            typer.echo(f"    {src}")


@app.command("find-email")
def find_email(
    name: Annotated[str, typer.Argument(help='Full name, e.g. "Ada Lovelace"')],
    domain: Annotated[str, typer.Argument(help="Company domain, e.g. example.com")],
    smtp: Annotated[
        bool, typer.Option("--smtp", help="Also probe the mail server (port 25 must be open).")
    ] = False,
) -> None:
    """Find and verify the most likely work email for a person."""
    from leadcommons.enrich import find_email as _find
    from leadcommons.models import Person, make_id

    domain = domain.strip().lower()
    person = Person(id=make_id("pe", domain, name), full_name=name.strip(), company_domain=domain)
    use_smtp = smtp or _settings().smtp_probe
    result = _guard("finding the email", lambda: _find(person, domain, smtp_probe=use_smtp))
    if result is None:
        typer.echo("No likely address found.")
        raise typer.Exit(code=1)
    typer.echo(f"{result.address}  status={result.status}  confidence={result.confidence}")
    if result.pattern:
        typer.echo(f"pattern: {result.pattern}")


@app.command()
def enrich(
    input_csv: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Input CSV.")],
    output_csv: Annotated[Path, typer.Argument(dir_okay=False, help="Where to write the result.")],
) -> None:
    """Enrich a CSV of companies/people with domains, contacts and emails."""
    from leadcommons.packs import DuckDBCompanySearch
    from leadcommons.pipeline import enrich_csv
    from leadcommons.store import Store

    settings = _settings()
    settings.ensure_dirs()

    def run() -> Any:
        store = Store(settings.database_url)
        search = DuckDBCompanySearch(settings.data_dir)

        def progress(done: int, total: int, *_: Any) -> None:
            typer.echo(f"\r{done}/{total} rows", nl=False, err=True)

        try:
            return enrich_csv(
                input_csv,
                output_csv,
                store=store,
                search=search,
                settings=settings,
                progress=progress,
            )
        finally:
            typer.echo("", err=True)

    _guard("enrichment", run)
    typer.echo(f"Wrote {output_csv}")


@app.command("hash-password")
def hash_password_cmd() -> None:
    """Make a password hash for LEADCOMMONS_PASSWORD_HASH (server mode)."""
    from leadcommons.web.auth import hash_password

    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    if not password:
        raise _fail("the password cannot be empty.")
    typer.echo(_guard("hashing", lambda: hash_password(password)))


@app.command()
def optout(
    target: Annotated[str, typer.Argument(help="Email address or domain to never contact.")],
) -> None:
    """Add an email or domain to the suppression list."""
    from leadcommons.store import Store

    target = target.strip().lower()
    if not target:
        raise _fail("give an email address or a domain.")
    settings = _settings()
    settings.ensure_dirs()
    _guard("saving the opt-out", lambda: Store(settings.database_url).add_suppression(target))
    typer.echo(f"{target} added to the suppression list.")
