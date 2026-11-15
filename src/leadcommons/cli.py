"""Command line interface: `leadcommons --help`.

Heavy modules are imported lazily inside each command so that `--help` stays fast
and works even if an optional dependency is missing.
"""

from __future__ import annotations

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


@app.command()
def desktop(
    browser: Annotated[
        bool, typer.Option("--browser", help="Open in your browser instead of an app window.")
    ] = False,
) -> None:
    """Open LeadCommons in its own window (needs: pip install 'leadcommons[desktop]')."""
    from leadcommons.desktop import main as desktop_main

    raise typer.Exit(desktop_main(["--browser"] if browser else []))


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
    import logging
    import os
    import sys

    from leadcommons.packs import build_pack

    # Progress goes to stderr so CI logs show it (DEBUG with LEADCOMMONS_DEBUG=1).
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if os.environ.get("LEADCOMMONS_DEBUG") == "1" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
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
    from leadcommons.packs import latest_pack_url

    return latest_pack_url(repo)


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


@app.command()
def worker(
    interval: Annotated[
        float, typer.Option(help="Seconds between two passes of the email sender.")
    ] = 60.0,
    once: Annotated[bool, typer.Option("--once", help="Run a single pass, then exit.")] = False,
) -> None:
    """Send due sequence emails, create due tasks and check for replies, in a loop.

    Use it when the web app runs elsewhere (a Docker worker service). Set
    LEADCOMMONS_ENGAGE_SCHEDULER=0 on the web service to leave sending to the worker only;
    both are safe together because a database lease lets one pass run at a time."""
    import logging

    from leadcommons.store import Store

    settings = _settings()
    if settings.mode == "demo":
        raise _fail("the public demo never sends email.", "Set LEADCOMMONS_MODE=local or server.")
    settings.ensure_dirs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(settings.database_url)
    typer.echo(f"LeadCommons worker ({settings.mode} mode): one pass every {interval:g}s")
    from leadcommons.engage.scheduler import loop_forever

    _guard("the worker", lambda: loop_forever(store, settings, max(5.0, interval), once))


@app.command("mcp")
def mcp_cmd(
    http: Annotated[
        bool, typer.Option("--http", help="Serve over HTTP (endpoint /mcp) instead of stdio.")
    ] = False,
    host: Annotated[
        str | None, typer.Option(help="HTTP only. Default 127.0.0.1 (local) or 0.0.0.0 (server).")
    ] = None,
    port: Annotated[int, typer.Option(help="HTTP only. Port to listen on.")] = 8001,
) -> None:
    """Run the MCP server so Claude, ChatGPT and other MCP clients can use LeadCommons."""
    settings = _settings()
    if not http:
        from leadcommons.mcp_server import build_server

        _guard("the MCP server", lambda: build_server().run("stdio"))
        return
    from leadcommons.mcp_server import build_http_app, build_server, resolve_http_token

    if host is None:
        host = "0.0.0.0" if settings.mode == "server" else "127.0.0.1"  # noqa: S104
    try:
        token = resolve_http_token(settings.mode, host, os.environ.get("LEADCOMMONS_MCP_TOKEN"))
    except ValueError as exc:
        raise _fail(str(exc)) from None
    import uvicorn

    asgi = build_http_app(build_server(), token, host)
    typer.echo(
        f"LeadCommons MCP on http://{host}:{port}/mcp "
        f"({'bearer token required' if token else 'no token, loopback only'})",
        err=True,
    )
    _guard("the MCP server", lambda: uvicorn.run(asgi, host=host, port=port))
