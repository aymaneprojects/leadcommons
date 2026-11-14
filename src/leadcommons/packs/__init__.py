"""Company data packs: Parquet files built from open data, queried with DuckDB."""

from __future__ import annotations

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
    return dest


__all__ = ["SAMPLE_PACK_DIR", "DuckDBCompanySearch", "build_pack", "download_pack"]
