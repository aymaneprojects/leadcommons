"""Company data packs: Parquet files built from open data, queried with DuckDB.

Published packs live in GitHub releases tagged `packs-*`. Since schema 3 a release holds one
file per country plus `manifest.json` (docs/dev/data-agents.md): the app downloads only the
countries the user picks, resumes interrupted downloads, checks each file's sha256, builds the
local search index (packs/index.py) and keeps a record of what it installed
(packs/installed.py). Older releases with a single companies-*.parquet keep working.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import locale
import logging
import os
import re
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from pepite.connectors.base import USER_AGENT
from pepite.packs import index as pack_index
from pepite.packs import installed as pack_installed
from pepite.packs.duck import DuckDBCompanySearch
from pepite.packs.installed import InstalledCountry

log = logging.getLogger(__name__)

# Small real pack shipped inside the package so the app works on first run.
SAMPLE_PACK_DIR: Path = Path(__file__).parent / "sample"

Progress = Callable[[str, int, int], None]  # (what is happening, done, total)


def build_pack(
    out_dir: Path,
    sources: Sequence[str] = ("wikidata", "gleif"),
    countries: Sequence[str] | None = None,
    limit_per_source: int | None = None,
) -> Path:
    """Fetch from connectors, normalise, dedupe (LEI, Wikidata id, domain), write
    out_dir/companies-<YYYYMMDD>.parquet with PACK_COLUMNS plus an attribution file.
    Returns the parquet path."""
    from pepite.packs.builder import build_pack as _build

    return _build(out_dir, sources, countries, limit_per_source)


DEFAULT_PACKS_REPO = os.environ.get("PEPITE_PACKS_REPO", "aymaneprojects/leadcommons")
MANIFEST_FILE = "manifest.json"
CATALOG_FILE = ".pack-catalog.json"  # the last manifest seen, for offline use
CATALOG_TTL = timedelta(hours=6)
_PACK_LOCK = threading.RLock()  # one install, update, remove or index build at a time


# -- releases -------------------------------------------------------------------------------


@dataclass
class PackRelease:
    tag: str
    published_at: str
    assets: dict[str, str]  # file name -> download URL
    sizes: dict[str, int] = field(default_factory=dict)

    @property
    def has_manifest(self) -> bool:
        return MANIFEST_FILE in self.assets

    @property
    def legacy_url(self) -> str | None:
        """The single companies-*.parquet of a release made before schema 3."""
        for name in sorted(self.assets):
            if fnmatch.fnmatch(name, "companies-*.parquet"):
                return self.assets[name]
        return None


def list_pack_releases(repo: str = DEFAULT_PACKS_REPO) -> list[PackRelease]:
    """Published `packs-*` releases of repo that hold a pack, newest first."""
    api = f"https://api.github.com/repos/{repo}/releases"
    resp = httpx.get(
        api,
        params={"per_page": 30},
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub answered HTTP {resp.status_code} for {api}")
    out = []
    for r in resp.json():
        if not str(r.get("tag_name", "")).startswith("packs-") or r.get("draft"):
            continue
        assets = {
            str(a.get("name", "")): str(a.get("browser_download_url", ""))
            for a in r.get("assets", [])
            if a.get("name") and a.get("browser_download_url")
        }
        sizes = {str(a.get("name")): int(a.get("size") or 0) for a in r.get("assets", [])}
        rel = PackRelease(
            str(r["tag_name"]),
            str(r.get("published_at") or r.get("created_at") or ""),
            assets,
            sizes,
        )
        if rel.has_manifest or rel.legacy_url:
            out.append(rel)
    out.sort(key=lambda r: r.published_at, reverse=True)
    return out


def latest_release(repo: str = DEFAULT_PACKS_REPO) -> PackRelease:
    releases = list_pack_releases(repo)
    if not releases:
        raise RuntimeError(f"no 'packs-*' release with company data found in {repo}")
    return releases[0]


def latest_pack_url(repo: str = DEFAULT_PACKS_REPO) -> str:
    """URL of the companies-*.parquet file in the newest packs-* release of repo that has a
    single-file pack (releases made before schema 3)."""
    for release in list_pack_releases(repo):
        if not release.has_manifest and release.legacy_url:
            return release.legacy_url
    raise RuntimeError(f"no 'packs-*' release with a companies-*.parquet file found in {repo}")


_CC = re.compile(r"[A-Z]{2}")
_SHA = re.compile(r"[0-9a-f]{64}")


def parse_manifest(data: Any) -> dict[str, Any]:
    """A manifest.json (schema 3) with only well-formed country entries: a two-letter country,
    a plain *.parquet file name (never a path), a sha256, row and byte counts."""
    if not isinstance(data, dict) or int(data.get("schema") or 0) < 3:
        raise ValueError("not a pack manifest (schema 3 or later)")
    countries = []
    for c in data.get("countries") or []:
        if not isinstance(c, dict):
            continue
        cc = str(c.get("country") or "").strip().upper()
        name = str(c.get("file") or "")
        sha = str(c.get("sha256") or "").strip().lower()
        if not _CC.fullmatch(cc) or not _SHA.fullmatch(sha):
            continue
        if Path(name).name != name or name.startswith(".") or not name.endswith(".parquet"):
            continue
        try:
            rows, size = int(c.get("rows") or 0), int(c.get("bytes") or 0)
        except (TypeError, ValueError):
            continue
        countries.append(
            {
                "country": cc,
                "file": name,
                "rows": rows,
                "bytes": size,
                "sha256": sha,
                "sources": [str(s) for s in c.get("sources") or []],
                "with_domain": int(c["with_domain"])
                if str(c.get("with_domain", "")).isdigit()
                else None,
            }
        )
    return {
        "schema": int(data["schema"]),
        "release": str(data.get("release") or ""),
        "built_at": data.get("built_at"),
        "countries": countries,
        "files": data.get("files") if isinstance(data.get("files"), dict) else {},
    }


def fetch_manifest(release: PackRelease) -> dict[str, Any]:
    resp = httpx.get(
        release.assets[MANIFEST_FILE],
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
        timeout=30,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_manifest(resp.json())


# -- the catalog: what the newest release offers, per country ---------------------------------


@dataclass
class CountryOffer:
    country: str
    file: str
    url: str
    rows: int
    bytes: int
    sha256: str
    sources: list[str]
    with_domain: int | None
    release: str
    built_at: str | None
    installed: InstalledCountry | None = None

    @property
    def state(self) -> str:
        """ "installed" (this exact file), "update" (an older file) or "available"."""
        if self.installed is None:
            return "available"
        return "installed" if self.installed.sha256 == self.sha256 else "update"


@dataclass
class PackCatalog:
    repo: str
    release: str = ""
    built_at: str | None = None
    fetched_at: str | None = None
    countries: list[CountryOffer] = field(default_factory=list)
    legacy_url: str | None = None  # a release from before schema 3 (one file, no manifest)
    error: str | None = None  # set when GitHub could not be reached (offers may be stale)

    @property
    def legacy(self) -> bool:
        return not self.countries and self.legacy_url is not None

    def get(self, country: str) -> CountryOffer | None:
        cc = country.strip().upper()
        return next((c for c in self.countries if c.country == cc), None)

    @property
    def updates(self) -> list[CountryOffer]:
        return [c for c in self.countries if c.state == "update"]


def _catalog_from(data: dict[str, Any], data_dir: Path) -> PackCatalog:
    manifest = data.get("manifest") or {}
    urls = data.get("urls") or {}
    have = pack_installed.load(data_dir)
    release = str(manifest.get("release") or data.get("release") or "")
    offers = [
        CountryOffer(
            country=c["country"],
            file=c["file"],
            url=str(urls[c["file"]]),
            rows=int(c["rows"]),
            bytes=int(c["bytes"]),
            sha256=c["sha256"],
            sources=list(c.get("sources") or []),
            with_domain=c.get("with_domain"),
            release=release,
            built_at=manifest.get("built_at"),
            installed=have.get(c["country"]),
        )
        for c in manifest.get("countries") or []
        if c.get("file") in urls
    ]
    return PackCatalog(
        repo=str(data.get("repo") or ""),
        release=release,
        built_at=manifest.get("built_at"),
        fetched_at=data.get("fetched_at"),
        countries=sorted(offers, key=lambda o: (-o.rows, o.country)),
        legacy_url=data.get("legacy_url"),
    )


def _read_catalog_cache(data_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((Path(data_dir) / CATALOG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pack_catalog(
    data_dir: Path,
    repo: str = DEFAULT_PACKS_REPO,
    *,
    refresh: bool = False,
    offline: bool = False,
) -> PackCatalog:
    """What the newest packs-* release offers, with the installed state of each country.

    Uses the copy saved by the last check when it is recent (CATALOG_TTL) unless refresh;
    with offline it never touches the network. When GitHub cannot be reached the saved copy is
    used and `error` says why."""
    data_dir = Path(data_dir)
    cached = _read_catalog_cache(data_dir)
    if cached is not None and cached.get("repo") != repo:
        cached = None
    fresh = False
    if cached and not refresh:
        try:
            when = datetime.fromisoformat(str(cached.get("fetched_at")))
            fresh = datetime.now(UTC) - when < CATALOG_TTL
        except (TypeError, ValueError):
            fresh = False
    if offline or (cached and fresh and not refresh):
        if cached:
            return _catalog_from(cached, data_dir)
        return PackCatalog(repo=repo, error="The list of countries has not been loaded yet.")
    try:
        release = latest_release(repo)
        data: dict[str, Any] = {
            "repo": repo,
            "release": release.tag,
            "published_at": release.published_at,
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "urls": release.assets,
            "manifest": fetch_manifest(release) if release.has_manifest else None,
            "legacy_url": None if release.has_manifest else release.legacy_url,
        }
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
        log.warning("could not read the pack catalog of %s: %s", repo, exc)
        cat = _catalog_from(cached, data_dir) if cached else PackCatalog(repo=repo)
        cat.error = f"Could not reach GitHub ({type(exc).__name__}: {exc})"[:200]
        return cat
    with contextlib.suppress(OSError):
        data_dir.mkdir(parents=True, exist_ok=True)
        tmp = data_dir / f"{CATALOG_FILE}.part"
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, data_dir / CATALOG_FILE)
    return _catalog_from(data, data_dir)


# -- downloading ------------------------------------------------------------------------------


def _sha256_of(path: Path, h: Any | None = None) -> Any:
    h = h or hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h


def _is_parquet(path: Path) -> bool:
    with path.open("rb") as fh:
        head = fh.read(4)
        if path.stat().st_size < 8:
            return False
        fh.seek(-4, os.SEEK_END)
        return head == b"PAR1" and fh.read(4) == b"PAR1"


def download_file(
    url: str,
    dest: Path,
    *,
    size: int | None = None,
    sha256: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    retries: int = 3,
    chunk: int = 1 << 20,
) -> Path:
    """Download url to dest through dest.part, resuming a previous partial download (HTTP
    Range), then check the size and sha256 before the file takes its final name. A file that
    does not match is deleted and the call fails."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempt = 0
    last_report = 0.0
    while True:
        have = part.stat().st_size if part.exists() else 0
        if size and have > size:
            part.unlink()
            have = 0
        headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
        if have and (not size or have < size):
            headers["Range"] = f"bytes={have}-"
        try:
            if size and have == size:
                break  # complete already: only the checks remain
            timeout = httpx.Timeout(30.0, read=120.0)
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True, timeout=timeout
            ) as resp:
                if resp.status_code == 416 and have:
                    break  # the server says we have it all
                resp.raise_for_status()
                start = 0
                if resp.status_code == 206:
                    m = re.match(r"bytes (\d+)-", resp.headers.get("content-range", ""))
                    start = int(m.group(1)) if m else -1
                if start != have:  # a full answer (or a range we did not ask for): restart
                    have = 0
                total = size or (have + int(resp.headers.get("content-length") or 0))
                with part.open("ab" if have else "wb") as fh:
                    for data in resp.iter_bytes(chunk):
                        fh.write(data)
                        have += len(data)
                        now = time.monotonic()
                        if progress and now - last_report > 0.25:
                            last_report = now
                            progress(have, total)
            break
        except (httpx.TransportError, httpx.RemoteProtocolError) as exc:
            attempt += 1
            if attempt > retries:
                raise
            log.warning("download of %s interrupted (%s); resuming", dest.name, exc)
            time.sleep(min(10.0, 1.5**attempt))
    got = part.stat().st_size if part.exists() else 0
    if progress:
        progress(got, size or got)
    if size and got != size:
        part.unlink(missing_ok=True)
        raise ValueError(f"{dest.name}: got {got} bytes, expected {size}")
    if sha256:
        digest = _sha256_of(part).hexdigest()
        if digest != sha256.lower():
            part.unlink(missing_ok=True)
            raise ValueError(f"{dest.name}: checksum mismatch (sha256 {digest[:12]}…)")
    if dest.suffix == ".parquet" and not _is_parquet(part):
        part.unlink(missing_ok=True)
        raise ValueError(f"{url} is not a Parquet file (bad magic bytes)")
    os.replace(part, dest)
    return dest


def download_pack(url: str, out_dir: Path) -> Path:
    """Download a published pack (e.g. a GitHub Release asset) into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urlsplit(url).path).name or "companies-downloaded.parquet"
    if not name.endswith(".parquet"):
        name += ".parquet"
    dest = out_dir / name
    fd, tmp_name = tempfile.mkstemp(dir=out_dir, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as fh,
            httpx.stream(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=120.0,
            ) as resp,
        ):
            resp.raise_for_status()
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
        if not _is_parquet(tmp):
            raise ValueError(f"{url} is not a Parquet file (bad magic bytes)")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _forget_old_downloads(out_dir, keep=dest)
    return dest


DOWNLOADS_FILE = ".downloaded-packs.json"


def _read_downloads(out_dir: Path) -> list[str]:
    try:
        previous = json.loads((Path(out_dir) / DOWNLOADS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [Path(str(n)).name for n in previous] if isinstance(previous, list) else []


def _forget_old_downloads(out_dir: Path, keep: Path) -> None:
    """Delete packs this function downloaded before (a newer month replaces them).

    Downloaded and self-built packs have the same kind of name, so downloads are recorded
    in a small JSON file and only those are ever removed.
    """
    for name in _read_downloads(out_dir):
        old = out_dir / name  # never leave out_dir
        if old != keep and old.suffix == ".parquet":
            old.unlink(missing_ok=True)
            pack_index.remove_index(old, out_dir)
    (out_dir / DOWNLOADS_FILE).write_text(json.dumps([keep.name]), encoding="utf-8")


def _drop_country_from_old_downloads(data_dir: Path, country: str) -> None:
    """A single-file pack downloaded from an older release also holds this country: keep its
    other countries, drop this one (the country file replaces it)."""
    import duckdb

    if not _CC.fullmatch(country):
        return
    names = _read_downloads(data_dir)
    kept = []
    for name in names:
        f = Path(data_dir) / name
        if not f.is_file():
            continue
        kept.append(name)
        con = duckdb.connect()
        try:
            src = "read_parquet('" + str(f).replace("'", "''") + "')"
            mine, rest = con.execute(
                f"SELECT count(*) FILTER (WHERE country = '{country}'), "
                f"count(*) FILTER (WHERE country IS DISTINCT FROM '{country}') FROM {src}"
            ).fetchone()
            if not mine:
                continue
            if not rest:
                f.unlink(missing_ok=True)
                pack_index.remove_index(f, data_dir)
                kept.remove(name)
                continue
            kv = pack_index.kv_metadata(con, f)
            tmp = f.with_name(f".{f.name}.part")
            query = f"SELECT * FROM {src} WHERE country IS DISTINCT FROM '{country}'"
            target = "'" + str(tmp).replace("'", "''") + "'"
            meta = ", ".join(
                f"{k}: '{str(v).replace(chr(39), chr(39) * 2)}'"
                for k, v in kv.items()
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
            )
            try:
                con.execute(
                    f"COPY ({query}) TO {target} (FORMAT PARQUET, COMPRESSION ZSTD"
                    + (f", KV_METADATA {{{meta}}})" if meta else ")")
                )
            except duckdb.Error:
                con.execute(f"COPY ({query}) TO {target} (FORMAT PARQUET, COMPRESSION ZSTD)")
            os.replace(tmp, f)
            log.info("removed %s rows of %s from %s (now a country file)", mine, country, name)
        finally:
            con.close()
    if kept != names:
        (Path(data_dir) / DOWNLOADS_FILE).write_text(json.dumps(kept), encoding="utf-8")


def _country_progress(progress: Progress | None, cc: str) -> tuple[Any, Any]:
    def dl(done: int, total: int) -> None:
        if progress:
            progress(f"Downloading {cc}", done, total)

    def idx(msg: str) -> None:
        if progress:
            progress(f"Preparing {cc} for fast search ({msg})", 0, 0)

    return dl, idx


def install_countries(
    data_dir: Path,
    countries: Sequence[str],
    repo: str = DEFAULT_PACKS_REPO,
    *,
    progress: Progress | None = None,
    catalog: PackCatalog | None = None,
) -> list[InstalledCountry]:
    """Download the newest file of each country (skipping ones already current), build their
    search indexes, then replace the earlier download of the same country. Files the app did
    not download are never touched."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    wanted = list(dict.fromkeys(c.strip().upper() for c in countries if c and c.strip()))
    with _PACK_LOCK:
        cat = catalog or pack_catalog(data_dir, repo, refresh=True)
        if cat.error and not cat.countries:
            raise RuntimeError(cat.error)
        missing = [cc for cc in wanted if cat.get(cc) is None]
        if missing:
            raise ValueError(
                f"No company data for {', '.join(missing)} in {cat.release or 'the newest release'}"
            )
        done: list[InstalledCountry] = []
        for cc in wanted:
            offer = cat.get(cc)
            assert offer is not None
            prev = pack_installed.load(data_dir).get(cc)
            dest = data_dir / offer.file
            dl, idx = _country_progress(progress, cc)
            current = prev is not None and prev.sha256 == offer.sha256 and dest.is_file()
            if not current:
                download_file(offer.url, dest, size=offer.bytes or None, sha256=offer.sha256,
                              progress=dl)  # fmt: skip
            if not pack_index.has_fresh_index(dest, data_dir):
                pack_index.build_index(dest, data_dir, progress=idx)
            entry = InstalledCountry(
                country=cc,
                file=offer.file,
                release=offer.release,
                sha256=offer.sha256,
                rows=offer.rows,
                bytes=offer.bytes,
                built_at=offer.built_at,
                sources=offer.sources,
            )
            pack_installed.record(data_dir, entry, release=offer.release)
            if prev is not None and prev.file != offer.file:
                old = data_dir / prev.file
                with contextlib.suppress(OSError):
                    old.unlink(missing_ok=True)
                pack_index.remove_index(old, data_dir)
            _drop_country_from_old_downloads(data_dir, cc)
            done.append(entry)
        pack_index.ensure_index(data_dir, [])  # relink the countries, drop old indexes
        return done


def update_countries(
    data_dir: Path, repo: str = DEFAULT_PACKS_REPO, *, progress: Progress | None = None
) -> list[InstalledCountry]:
    """Install the newer file of every installed country that has one."""
    cat = pack_catalog(data_dir, repo, refresh=True)
    if cat.error and not cat.countries:
        raise RuntimeError(cat.error)
    todo = [c.country for c in cat.updates]
    return install_countries(data_dir, todo, repo, progress=progress, catalog=cat) if todo else []


def remove_country(data_dir: Path, country: str) -> bool:
    """Delete a downloaded country file, its index and its record. False when this country
    was not downloaded by the app."""
    data_dir = Path(data_dir)
    with _PACK_LOCK:
        entry = pack_installed.load(data_dir, existing_only=False).get(country.strip().upper())
        if entry is None:
            return False
        f = data_dir / entry.file
        f.unlink(missing_ok=True)  # fails loudly (record kept) if the file cannot be deleted
        pack_index.remove_index(f, data_dir)  # an index still open stays until the next start
        pack_installed.forget(data_dir, entry.country)
        pack_index.ensure_index(data_dir, [])  # relink the remaining countries
        return True


def install_latest_pack(out_dir: Path, repo: str = DEFAULT_PACKS_REPO) -> Path:
    """Find the newest published pack, download it into out_dir and drop older downloads.

    Releases with a manifest: update the installed countries, or install the user's own
    country (default_country) when none is installed yet."""
    return _install_latest(Path(out_dir), repo)


def _install_latest(
    out_dir: Path, repo: str = DEFAULT_PACKS_REPO, progress: Progress | None = None
) -> Path:
    cat = pack_catalog(out_dir, repo, refresh=True)
    if cat.legacy and cat.legacy_url:
        path = download_pack(cat.legacy_url, out_dir)
        pack_index.ensure_index(out_dir, [path])
        return path
    if cat.error and not cat.countries:
        raise RuntimeError(cat.error)
    targets = list(pack_installed.load(out_dir))
    if not targets:
        own = default_country()
        targets = [own] if own and cat.get(own) else []
    if not targets:
        raise RuntimeError(
            "The newest release has one file per country: pick the countries to download in "
            "Settings > Data packs (or run `pepite pack download --country FR`)."
        )
    done = install_countries(out_dir, targets, repo, progress=progress, catalog=cat)
    return out_dir / done[0].file


def run_pack_job(
    data_dir: Path,
    payload: dict[str, Any],
    progress: Progress | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """What the download_pack background job does, by payload:
    {} or {"action": "latest"}: newest pack (install_latest_pack);
    {"action": "install", "countries": [...]} (the default when countries are given);
    {"action": "update"}: newer files of the installed countries;
    {"action": "remove", "countries": [...]}; {"action": "index"}: build missing indexes.
    "first_run": true marks the desktop app's first start (a country that is not published
    is then reported, not an error)."""
    with _PACK_LOCK:
        return _run_pack_job(Path(data_dir), payload, progress, repo or DEFAULT_PACKS_REPO)


def _run_pack_job(
    data_dir: Path, payload: dict[str, Any], progress: Progress | None, repo: str
) -> dict[str, Any]:
    countries = [str(c).strip().upper() for c in payload.get("countries") or [] if str(c).strip()]
    action = str(payload.get("action") or ("install" if countries else ""))
    if action == "":
        return {"pack": install_latest_pack(data_dir).name}
    if action == "latest":
        return {"pack": _install_latest(data_dir, repo, progress).name}
    if action == "install":
        if payload.get("first_run"):
            cat = pack_catalog(data_dir, repo, refresh=True)
            if cat.legacy and cat.legacy_url:
                return {"pack": _install_latest(data_dir, repo, progress).name}
            if not [cc for cc in countries if cat.get(cc)]:
                where = ", ".join(countries) or "your country"
                return {
                    "message": f"No company data is published for {where} yet. Pick other "
                    "countries in Settings > Data packs."
                }
            countries = [cc for cc in countries if cat.get(cc)]
            done = install_countries(data_dir, countries, repo, progress=progress, catalog=cat)
        else:
            done = install_countries(data_dir, countries, repo, progress=progress)
        result: dict[str, Any] = {
            "pack": ", ".join(e.file for e in done),
            "countries": [e.country for e in done],
            "release": done[0].release if done else None,
        }
        if payload.get("first_run") and done:
            result["message"] = (
                f"Company data for {', '.join(result['countries'])} is ready to search. "
                "Add more countries in Settings > Data packs."
            )
        return result
    if action == "update":
        done = update_countries(data_dir, repo, progress=progress)
        return {
            "pack": ", ".join(e.file for e in done) or "up to date",
            "countries": [e.country for e in done],
        }
    if action == "remove":
        removed = [cc for cc in countries if remove_country(data_dir, cc)]
        return {"removed": removed, "pack": "removed " + (", ".join(removed) or "nothing")}
    if action == "index":

        def say(msg: str, i: int, n: int) -> None:
            if progress:
                progress(msg, i, n)

        built = pack_index.ensure_index(data_dir, progress=say)
        return {"indexed": [p.name for p in built], "pack": f"{len(built)} file(s) indexed"}
    raise ValueError(f"unknown pack action {action!r}")


def prepare_for_search(data_dir: Path, files: Sequence[Path] | None = None) -> list[Path]:
    """Build the missing search indexes of the packs in data_dir (or of `files`)."""
    with _PACK_LOCK:
        return pack_index.ensure_index(Path(data_dir), files)


def has_own_pack(data_dir: Path) -> bool:
    """True when data_dir holds at least one pack (otherwise search uses the small sample)."""
    return Path(data_dir).is_dir() and any(Path(data_dir).glob("*.parquet"))


# -- the user's country -----------------------------------------------------------------------

# A bare language code tells the country only for these (en, pt, es... are spoken widely).
_LANG_COUNTRY = {
    "fr": "FR", "de": "DE", "it": "IT", "nl": "NL", "nb": "NO", "nn": "NO", "no": "NO",
    "da": "DK", "sv": "SE", "fi": "FI", "pl": "PL", "cs": "CZ", "ja": "JP", "ko": "KR",
    "el": "GR", "hu": "HU", "ro": "RO", "bg": "BG", "hr": "HR", "sk": "SK", "sl": "SI",
    "et": "EE", "lv": "LV", "lt": "LT", "is": "IS", "uk": "UA", "tr": "TR", "he": "IL",
}  # fmt: skip


def country_from_locale(text: str | None) -> str | None:
    """ISO country of a locale such as "fr-FR", "en_GB.UTF-8", "de" or "FR"."""
    if not text:
        return None
    t = str(text).strip().split(".")[0].split("@")[0].replace("-", "_")
    parts = [p for p in t.split("_") if p]
    if not parts or parts[0].upper() in ("C", "POSIX"):
        return None
    for p in parts[1:]:
        if len(p) == 2 and p.isalpha():
            return p.upper()
    if len(parts) == 1 and len(parts[0]) == 2 and parts[0].isupper():
        return parts[0]  # already a country code
    return _LANG_COUNTRY.get(parts[0].lower())


def _windows_locale() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
            return buf.value
    except Exception:
        return None
    return None


def default_country() -> str | None:
    """The user's country: PEPITE_LOCALE (set by the desktop app from the system
    settings), then the usual locale variables, then the operating system's locale."""
    candidates = [
        os.environ.get("PEPITE_LOCALE"),
        os.environ.get("LC_ALL"),
        os.environ.get("LC_MESSAGES"),
        os.environ.get("LANG"),
        _windows_locale(),
    ]
    with contextlib.suppress(Exception):
        candidates.append(locale.getlocale()[0])
    for cand in candidates:
        cc = country_from_locale(cand)
        if cc:
            return cc
    return None


__all__ = [
    "DEFAULT_PACKS_REPO",
    "DOWNLOADS_FILE",
    "SAMPLE_PACK_DIR",
    "CountryOffer",
    "DuckDBCompanySearch",
    "InstalledCountry",
    "PackCatalog",
    "PackRelease",
    "build_pack",
    "country_from_locale",
    "default_country",
    "download_file",
    "download_pack",
    "has_own_pack",
    "install_countries",
    "install_latest_pack",
    "latest_pack_url",
    "latest_release",
    "list_pack_releases",
    "pack_catalog",
    "parse_manifest",
    "prepare_for_search",
    "remove_country",
    "run_pack_job",
    "update_countries",
]
