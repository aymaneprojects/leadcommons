"""Command line interface: `pepite --help`.

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
    name="pepite",
    help="Pépite: free, open-source, local-first prospecting from public data.",
    no_args_is_help=True,
    add_completion=False,
)
pack_app = typer.Typer(help="Build, download and inspect company data packs.", no_args_is_help=True)
app.add_typer(pack_app, name="pack")
eval_app = typer.Typer(help="Measure data quality against a gold set.", no_args_is_help=True)
app.add_typer(eval_app, name="eval")
sitescan_app = typer.Typer(
    help="Link company websites by reading the register numbers on their public pages.",
    no_args_is_help=True,
)
app.add_typer(sitescan_app, name="sitescan")

DEFAULT_PACKS_REPO = "aymaneprojects/pepite"


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
            "Reinstall Pépite, or run `uv sync` if you are working from source.",
        ) from None
    except KeyboardInterrupt:
        raise _fail("interrupted.") from None
    except Exception as exc:  # noqa: BLE001 - last-resort friendly message
        if os.environ.get("PEPITE_DEBUG"):
            raise
        raise _fail(
            f"{what} failed: {exc}", "Set PEPITE_DEBUG=1 for the full traceback."
        ) from None


def _settings() -> Any:
    from pepite.config import Settings

    return Settings()


@app.command()
def version() -> None:
    """Print the installed version."""
    from pepite import __version__

    typer.echo(f"pepite {__version__}")


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
            "Set PEPITE_PASSWORD_HASH (make one with `pepite hash-password`).",
        )
    import uvicorn

    typer.echo(f"Pépite ({settings.mode} mode) on http://{host}:{port}")
    _guard(
        "the web server",
        lambda: uvicorn.run(
            "pepite.web.app:create_app", factory=True, host=host, port=port, reload=reload
        ),
    )


@app.command()
def desktop(
    browser: Annotated[
        bool, typer.Option("--browser", help="Open in your browser instead of an app window.")
    ] = False,
) -> None:
    """Open Pépite in its own window (needs: pip install 'pepite[desktop]')."""
    from pepite.desktop import main as desktop_main

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

    from pepite.packs import build_pack

    # Progress goes to stderr so CI logs show it (DEBUG with PEPITE_DEBUG=1).
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if os.environ.get("PEPITE_DEBUG") == "1" else logging.INFO,
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


def _latest_pack_url(repo: str) -> str | None:
    """URL of the newest single-file pack; None when the newest release has one file per
    country (manifest.json)."""
    from pepite.packs import latest_release

    release = latest_release(repo)
    return None if release.has_manifest else release.legacy_url


def _print_progress(phase: str, done: int, total: int) -> None:
    pct = f" {100 * done // total}%" if total else ""
    typer.echo(f"\r{phase}{pct}      ", nl=False, err=True)


@pack_app.command("download")
def pack_download(
    url: Annotated[
        str, typer.Argument(help="Pack URL, or 'latest' for the newest published pack.")
    ] = "latest",
    country: Annotated[
        list[str] | None,
        typer.Option("--country", "-c", help="Download this country (repeatable), e.g. FR."),
    ] = None,
    update: Annotated[
        bool, typer.Option("--update", help="Update the countries already downloaded.")
    ] = False,
    remove: Annotated[
        list[str] | None, typer.Option("--remove", help="Delete a downloaded country.")
    ] = None,
) -> None:
    """Download published company data into your data directory (one file per country)."""
    from pepite.packs import download_pack

    repo = os.environ.get("PEPITE_PACKS_REPO", DEFAULT_PACKS_REPO)
    data_dir = _settings().data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    if country or update or remove:
        from pepite.packs import run_pack_job

        payload: dict[str, Any] = (
            {"action": "remove", "countries": remove}
            if remove
            else {"action": "update"}
            if update
            else {"action": "install", "countries": country}
        )
        res = _guard(
            "updating the company data",
            lambda: run_pack_job(data_dir, payload, _print_progress, repo),
        )
        typer.echo("", err=True)
        typer.echo(f"Done: {res.get('pack')}")
        return
    if url == "latest":
        typer.echo(f"Looking for the latest pack in {repo} ...")
        found = _guard("finding the latest pack", lambda: _latest_pack_url(repo))
        if found is None:  # one file per country: update what is installed, else your country
            from pepite.packs import run_pack_job

            res = _guard("downloading the pack", lambda: run_pack_job(
                data_dir, {"action": "latest"}, _print_progress, repo))  # fmt: skip
            typer.echo("", err=True)
            typer.echo(f"Saved {res.get('pack')}")
            return
        url = found
    typer.echo(f"Downloading {url}")
    path = _guard("downloading the pack", lambda: download_pack(url, data_dir))
    try:
        from pepite.packs import prepare_for_search
    except ImportError:
        prepare_for_search = None
    if prepare_for_search is not None:
        _guard("preparing the pack for search", lambda: prepare_for_search(data_dir, [path]))
    typer.echo(f"Saved {path}")


@pack_app.command("countries")
def pack_countries() -> None:
    """List the countries of the newest published data and which ones are downloaded."""
    from pepite.packs import pack_catalog

    repo = os.environ.get("PEPITE_PACKS_REPO", DEFAULT_PACKS_REPO)
    cat = _guard("reading the list", lambda: pack_catalog(_settings().data_dir, repo, refresh=True))
    if cat.error:
        typer.echo(cat.error, err=True)
    if cat.legacy:
        typer.echo(f"{cat.release}: one file for all countries (`pepite pack download`).")
    for c in cat.countries:
        typer.echo(f"{c.country}  {c.rows:>12,} companies  {c.bytes / 1e6:>9,.1f} MB  {c.state}")


@pack_app.command("index")
def pack_index() -> None:
    """Prepare the packs in your data directory for fast search (done after each download)."""
    from pepite.packs import prepare_for_search

    built = _guard("indexing", lambda: prepare_for_search(_settings().data_dir))
    typer.echo(f"{len(built)} file(s) indexed" if built else "Every pack is ready for search.")


@pack_app.command("info")
def pack_info() -> None:
    """List the packs currently loaded."""
    from pepite.packs import DuckDBCompanySearch

    settings = _settings()
    rows = _guard("reading packs", lambda: DuckDBCompanySearch(settings.data_dir).pack_info())
    if not rows:
        typer.echo("No packs found. Run `pepite pack download latest`.")
        return
    for row in rows:
        sources = row.get("sources") or []
        if not isinstance(sources, str):
            sources = ", ".join(str(s) for s in sources)
        typer.echo(
            f"{row.get('file')}  rows={row.get('rows')}  sources={sources}  "
            f"built={row.get('built_at')}"
        )


def _log_progress_to_stderr() -> None:
    """INFO logs on stderr so CI shows progress (DEBUG with PEPITE_DEBUG=1)."""
    import logging
    import sys

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if os.environ.get("PEPITE_DEBUG") == "1" else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@pack_app.command("sources")
def pack_sources(
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON (for CI matrices).")] = False,
) -> None:
    """List the bulk register sources `pack fetch-source` can build."""
    import json

    from pepite.connectors.bulk import load_all

    sources = sorted(_guard("loading the bulk sources", load_all).values(), key=lambda s: s.name)
    if as_json:
        rows = [
            {
                "name": s.name,
                "title": s.title,
                "licence": s.licence,
                "licence_url": s.licence_url,
                "homepage": s.homepage,
                "countries": sorted(s.countries) if s.countries is not None else None,
            }
            for s in sources
        ]
        typer.echo(json.dumps(rows))
        return
    for s in sources:
        countries = ",".join(sorted(s.countries)) if s.countries is not None else "worldwide"
        typer.echo(f"{s.name:<16} {s.licence:<14} {countries:<12} {s.title}")


@pack_app.command("fetch-source")
def pack_fetch_source(
    name: Annotated[str, typer.Argument(help="Bulk source, see `pepite pack sources`.")],
    out: Annotated[Path, typer.Option(help="Directory the shard is written to.")],
    limit: Annotated[int | None, typer.Option(help="Max rows in the shard (test runs).")] = None,
    country: Annotated[
        list[str] | None,
        typer.Option("--country", "-c", help="Keep only this ISO country (repeatable)."),
    ] = None,
    work: Annotated[
        Path | None,
        typer.Option(help="Download and scratch directory (default: <out>/.work), removed after."),
    ] = None,
) -> None:
    """Download one official register whole and write its shard (Parquet, pack columns)."""
    from pepite.connectors.bulk import BuildOptions
    from pepite.packs.bulk import run_source

    _log_progress_to_stderr()
    out.mkdir(parents=True, exist_ok=True)
    options = BuildOptions(
        work_dir=work or out / ".work",
        out_dir=out,
        limit=limit,
        countries=frozenset(c.strip().upper() for c in country) if country else None,
        contact=os.environ.get("PEPITE_CONTACT_EMAIL", "").strip() or None,
    )
    stats = _guard(f"fetching {name}", lambda: run_source(name, options))
    if work is None and options.work_dir.is_dir() and not any(options.work_dir.iterdir()):
        options.work_dir.rmdir()
    if not stats.files:
        typer.echo(f"{name}: nothing to do ({stats.notes})")
        return
    for path in stats.files:
        typer.echo(f"{path}  {path.stat().st_size / 1_048_576:.1f} MB")
    typer.echo(f"{name}: {stats.rows} rows written, {stats.skipped} dropped by the source rules")


@pack_app.command("merge")
def pack_merge(
    shards: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Directory of source shards.")
    ],
    out: Annotated[Path, typer.Option(help="Directory the per-country packs are written to.")],
    release: Annotated[
        str | None, typer.Option(help="Release tag, e.g. packs-2026-12 (default: this month).")
    ] = None,
    work: Annotated[
        Path | None,
        typer.Option(help="Scratch directory (default: inside --out), removed after."),
    ] = None,
    keep_unregistered: Annotated[
        bool,
        typer.Option(
            "--keep-unregistered",
            help="Keep companies whose register number is missing from that register's shard "
            "(use with --limit'ed test shards).",
        ),
    ] = False,
) -> None:
    """Link and merge the shards into one Parquet file per country, with manifest.json,
    ATTRIBUTION.md and the quality report."""
    from pepite.packs.merge import merge_shards
    from pepite.packs.quality import summary_text

    _log_progress_to_stderr()
    result = _guard(
        "merging the shards",
        lambda: merge_shards(
            shards, out, release=release, work_dir=work, keep_unregistered=keep_unregistered
        ),
    )
    typer.echo(summary_text(result.quality))
    typer.echo(f"Packs written to {out}")


@pack_app.command("quality")
def pack_quality(
    packs: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="Directory of merged packs.")
    ],
    as_json: Annotated[bool, typer.Option("--json", help="Print the full report as JSON.")] = False,
) -> None:
    """Measure merged packs, rewrite QUALITY.md and quality.json, print the summary.
    Exits with 1 when a health check fails."""
    import json

    from pepite.packs.quality import summary_text, write_quality

    report = _guard("measuring the packs", lambda: write_quality(packs))
    typer.echo(json.dumps(report, indent=2) if as_json else summary_text(report))
    if report["status"] == "fail":
        raise typer.Exit(code=1)


@sitescan_app.command("hosts")
def sitescan_hosts(
    out: Annotated[Path, typer.Option(help="Directory for domains-<tld>.txt.gz.")],
    tld: Annotated[
        list[str] | None,
        typer.Option("--tld", help="Top-level domain, e.g. fr (repeatable; default: all packs)."),
    ] = None,
    crawl: Annotated[
        str | None, typer.Option(help="Common Crawl id (default: the newest crawl).")
    ] = None,
    max_files: Annotated[
        int | None, typer.Option(help="Read only the first N index files (test runs).")
    ] = None,
    workers: Annotated[int, typer.Option(help="Index files read in parallel.")] = 4,
    packs: Annotated[
        Path | None,
        typer.Option(help="Instead of Common Crawl: list the domains of this packs directory."),
    ] = None,
) -> None:
    """List candidate domains: Common Crawl's URL index per TLD, or the domains already in packs."""
    from pepite.sitescan import hosts

    _log_progress_to_stderr()
    out.mkdir(parents=True, exist_ok=True)
    if packs is not None:
        import gzip

        target = out / "domains-packs.txt.gz"
        n = 0
        with gzip.open(target, "wt", encoding="utf-8") as fh:
            for domain, _ in _guard("reading the packs", lambda: list(hosts.packs_domains(packs))):
                fh.write(domain + "\n")
                n += 1
        typer.echo(f"{target}: {n} domains from the packs")
        return
    failed = 0
    for one in tld or list(hosts.DEFAULT_TLDS):
        stats = _guard(
            f"listing .{one}",
            lambda one=one: hosts.build_domains(
                one, out, crawl=crawl, max_files=max_files, workers=workers
            ),
        )
        typer.echo(
            f".{one}: {stats['domains']} domains from {stats['files_done']}/{stats['files']} "
            f"files of {stats['crawl']}" + (" (partial)" if stats["partial"] else "")
        )
        failed += stats["files_done"] == 0
    if failed:
        raise typer.Exit(1)


@sitescan_app.command("run")
def sitescan_run(
    domains: Annotated[
        Path,
        typer.Option(
            exists=True, dir_okay=False, help="Text file (.txt or .gz), one domain per line."
        ),
    ],
    out: Annotated[Path, typer.Option(help="Findings directory (findings-*.parquet, resumable).")],
    country: Annotated[
        str | None, typer.Option(help="Country of every domain (default: from the TLD).")
    ] = None,
    concurrency: Annotated[int, typer.Option(help="Sites read at the same time.")] = 100,
    delay: Annotated[float, typer.Option(help="Seconds between two requests to one site.")] = 1.0,
    max_pages: Annotated[int, typer.Option(help="Pages read per site (at most).")] = 4,
    limit: Annotated[int | None, typer.Option(help="Scan only the first N domains.")] = None,
    part: Annotated[
        str | None,
        typer.Option(
            help="K/N: scan slice K of N (0-based) into <out>/part-K. One Python process uses "
            "one core, so run N processes on N cores."
        ),
    ] = None,
) -> None:
    """Read the home and legal pages of each domain and keep the register numbers found."""
    import itertools
    import json
    import zlib

    from pepite.sitescan import fetch, hosts

    _log_progress_to_stderr()
    code = country.strip().upper() if country else None
    feed = hosts.read_domains_file(domains, code)
    if part:
        try:
            k, n = (int(x) for x in part.split("/"))
            if not 0 <= k < n:
                raise ValueError
        except ValueError:
            raise _fail("--part must look like 0/4 (slice K of N, K < N)") from None
        feed = (d for d in feed if zlib.crc32(d[0].encode()) % n == k)
        out = out / f"part-{k}"
    if limit:
        feed = itertools.islice(feed, limit)
    stats = _guard(
        "scanning",
        lambda: fetch.run_scan(
            feed,
            out,
            concurrency=concurrency,
            per_host_delay=delay,
            max_pages=max_pages,
        ),
    )
    typer.echo(json.dumps(stats, indent=2))


@sitescan_app.command("shard")
def sitescan_shard(
    findings: Annotated[
        Path, typer.Argument(exists=True, help="Findings directory written by `sitescan run`.")
    ],
    out: Annotated[Path, typer.Option(help="Shards directory (sitescan.parquet is written here).")],
) -> None:
    """Turn findings into a bulk-source shard for `pack merge` (domain, website, phone)."""
    import json

    from pepite.sitescan.shard import build_shard

    _log_progress_to_stderr()
    stats = _guard("building the shard", lambda: build_shard(findings, out))
    typer.echo(json.dumps(stats, indent=2))


@app.command("find-people")
def find_people(
    domain: Annotated[str, typer.Argument(help="Company domain, e.g. example.com")],
) -> None:
    """List people found on a company's public web pages."""
    from pepite.enrich import find_people as _find

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
    from pepite.enrich import find_email as _find
    from pepite.models import Person, make_id

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
    if result.checks.get("basis"):
        typer.echo(f"why: {result.checks['basis']}")


@eval_app.command("emails")
def eval_emails(
    gold: Annotated[
        Path,
        typer.Option(
            exists=True, dir_okay=False, help="Gold CSV: full_name, domain, true_address."
        ),
    ],
    smtp: Annotated[
        bool,
        typer.Option(
            "--smtp", help="Also probe mail servers (simulated with a world file, else live)."
        ),
    ] = False,
    world: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            help="Site evidence to replay. Default: <gold>.world.json when it exists, else the "
            "live web.",
        ),
    ] = None,
    json_out: Annotated[
        Path | None, typer.Option("--json", dir_okay=False, help="Also write the report as JSON.")
    ] = None,
    details: Annotated[
        Path | None, typer.Option(dir_okay=False, help="Write one CSV line per gold row.")
    ] = None,
    min_valid_precision: Annotated[
        float | None,
        typer.Option(help="Exit with code 1 if the precision of 'valid' is below this (0-1)."),
    ] = None,
    fit: Annotated[
        bool, typer.Option("--fit", help="Print calibration knots fitted on this gold set.")
    ] = False,
) -> None:
    """Measure email finding on a gold set: precision, coverage, calibration, per provider."""
    import json

    from pepite.enrich import eval as ev

    rows = _guard("reading the gold set", lambda: ev.load_gold(gold))
    if not rows:
        raise _fail(f"no usable rows in {gold}.")
    world_file = world or (ev.world_path_for(gold) if ev.world_path_for(gold).exists() else None)
    loaded = (
        _guard("reading the world file", lambda: ev.load_world(world_file)) if world_file else None
    )
    if loaded is None:
        typer.echo(f"No world file: crawling {len({r.domain for r in rows})} sites live.", err=True)

    def progress(done: int, total: int) -> None:
        if loaded is None:
            typer.echo(f"\r{done}/{total} people", nl=False, err=True)

    report = _guard(
        "the evaluation",
        lambda: ev.evaluate(rows, world=loaded, smtp=smtp, gold_name=gold.name, progress=progress),
    )
    if loaded is None:
        typer.echo("", err=True)
    typer.echo(ev.format_report(report))
    if json_out:
        json_out.write_text(json.dumps(report.to_dict(), indent=1, ensure_ascii=False) + "\n")
    if details:
        ev.write_details(report, details)
    if fit:
        knots = ev.fit_calibration(report.rows)
        typer.echo(f"\nCalibration knots (score.CALIBRATION): {knots}")
    precision = report.overall.valid_precision
    if (
        min_valid_precision is not None
        and precision is not None
        and precision < min_valid_precision
    ):
        raise _fail(f"precision of 'valid' {precision:.3f} is below {min_valid_precision}.")


@eval_app.command("build-gold")
def eval_build_gold(
    domains_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Company domains, one per line.")
    ],
    out: Annotated[
        Path | None,
        typer.Option(dir_okay=False, help="Gold CSV to write. Default: <home>/eval/gold.csv."),
    ] = None,
    max_pages: Annotated[int, typer.Option(help="Pages crawled per site.")] = 15,
) -> None:
    """Build a gold set from names and addresses published side by side on company sites.

    The result holds personal data of real people: keep it on your machine, never commit it."""
    from pepite.enrich.eval import build_gold

    target = out or _settings().home / "eval" / "gold.csv"
    domains = domains_file.read_text(encoding="utf-8").splitlines()

    def progress(domain: str, pairs: int) -> None:
        typer.echo(f"{domain}: {pairs} people with a published address", err=True)

    total, world_file = _guard(
        "building the gold set",
        lambda: build_gold(domains, target, max_pages=max_pages, progress=progress),
    )
    typer.echo(f"Wrote {total} rows to {target} and the site evidence to {world_file}.")
    typer.echo("These files contain personal data: keep them local, do not commit or share them.")


@app.command()
def enrich(
    input_csv: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Input CSV.")],
    output_csv: Annotated[Path, typer.Argument(dir_okay=False, help="Where to write the result.")],
) -> None:
    """Enrich a CSV of companies/people with domains, contacts and emails."""
    from pepite.packs import DuckDBCompanySearch
    from pepite.pipeline import enrich_csv
    from pepite.store import Store

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
    """Make a password hash for PEPITE_PASSWORD_HASH (server mode)."""
    from pepite.web.auth import hash_password

    password = typer.prompt("Password", hide_input=True, confirmation_prompt=True)
    if not password:
        raise _fail("the password cannot be empty.")
    typer.echo(_guard("hashing", lambda: hash_password(password)))


@app.command()
def optout(
    target: Annotated[str, typer.Argument(help="Email address or domain to never contact.")],
) -> None:
    """Add an email or domain to the suppression list."""
    from pepite.store import Store

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
    PEPITE_ENGAGE_SCHEDULER=0 on the web service to leave sending to the worker only;
    both are safe together because a database lease lets one pass run at a time."""
    import logging

    from pepite.store import Store

    settings = _settings()
    if settings.mode == "demo":
        raise _fail("the public demo never sends email.", "Set PEPITE_MODE=local or server.")
    settings.ensure_dirs()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(settings.database_url)
    typer.echo(f"Pépite worker ({settings.mode} mode): one pass every {interval:g}s")
    from pepite.engage.scheduler import loop_forever

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
    """Run the MCP server so Claude, ChatGPT and other MCP clients can use Pépite."""
    settings = _settings()
    if not http:
        from pepite.mcp_server import build_server

        _guard("the MCP server", lambda: build_server().run("stdio"))
        return
    from pepite.mcp_server import build_http_app, build_server, resolve_http_token

    if host is None:
        host = "0.0.0.0" if settings.mode == "server" else "127.0.0.1"  # noqa: S104
    try:
        token = resolve_http_token(settings.mode, host, os.environ.get("PEPITE_MCP_TOKEN"))
    except ValueError as exc:
        raise _fail(str(exc)) from None
    import uvicorn

    asgi = build_http_app(build_server(), token, host)
    typer.echo(
        f"Pépite MCP on http://{host}:{port}/mcp "
        f"({'bearer token required' if token else 'no token, loopback only'})",
        err=True,
    )
    _guard("the MCP server", lambda: uvicorn.run(asgi, host=host, port=port))
