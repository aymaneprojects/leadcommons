"""Company data packs: Parquet files built from open data, queried with DuckDB."""

from __future__ import annotations

import fnmatch
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from leadcommons.connectors.base import USER_AGENT
from leadcommons.packs.duck import DuckDBCompanySearch

# Small real pack shipped inside the package so the app works on first run.
SAMPLE_PACK_DIR: Path = Path(__file__).parent / "sample"


def build_pack(
    out_dir: Path,
    sources: Sequence[str] = ("wikidata", "gleif"),
    countries: Sequence[str] | None = None,
    limit_per_source: int | None = None,
) -> Path:
    """Fetch from connectors, normalise, dedupe (LEI, Wikidata id, domain), write
    out_dir/companies-<YYYYMMDD>.parquet with PACK_COLUMNS plus an attribution file.
    Returns the parquet path."""
    from leadcommons.packs.builder import build_pack as _build

    return _build(out_dir, sources, countries, limit_per_source)


DEFAULT_PACKS_REPO = os.environ.get("LEADCOMMONS_PACKS_REPO", "aymaneprojects/leadcommons")


def latest_pack_url(repo: str = DEFAULT_PACKS_REPO) -> str:
    """URL of the companies-*.parquet file in the newest packs-* release of repo."""
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
        with tmp.open("rb") as fh:
            head = fh.read(4)
            tail = b""
            if tmp.stat().st_size >= 8:
                fh.seek(-4, os.SEEK_END)
                tail = fh.read(4)
        if head != b"PAR1" or tail != b"PAR1":
            raise ValueError(f"{url} is not a Parquet file (bad magic bytes)")
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _forget_old_downloads(out_dir, keep=dest)
    return dest


DOWNLOADS_FILE = ".downloaded-packs.json"


def _forget_old_downloads(out_dir: Path, keep: Path) -> None:
    """Delete packs this function downloaded before (a newer month replaces them).

    Downloaded and self-built packs have the same kind of name, so downloads are recorded
    in a small JSON file and only those are ever removed.
    """
    marker = out_dir / DOWNLOADS_FILE
    try:
        previous = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = []
    for name in previous if isinstance(previous, list) else []:
        old = out_dir / Path(str(name)).name  # never leave out_dir
        if old != keep and old.suffix == ".parquet":
            old.unlink(missing_ok=True)
    marker.write_text(json.dumps([keep.name]), encoding="utf-8")


def install_latest_pack(out_dir: Path, repo: str = DEFAULT_PACKS_REPO) -> Path:
    """Find the newest published pack, download it into out_dir and drop older downloads."""
    return download_pack(latest_pack_url(repo), out_dir)


def has_own_pack(data_dir: Path) -> bool:
    """True when data_dir holds at least one pack (otherwise search uses the small sample)."""
    return Path(data_dir).is_dir() and any(Path(data_dir).glob("*.parquet"))


__all__ = [
    "DEFAULT_PACKS_REPO",
    "SAMPLE_PACK_DIR",
    "DuckDBCompanySearch",
    "build_pack",
    "download_pack",
    "has_own_pack",
    "install_latest_pack",
    "latest_pack_url",
]
