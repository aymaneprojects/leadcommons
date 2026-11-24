"""Vercel build step: bundle a published company pack into data/packs.

Runs after dependencies are installed. Never fails the build: without a published pack the
sample pack shipped inside the package is used.

Releases since schema 3 hold one file per country plus a small all-countries demo file
(`files.demo` in manifest.json); the hosted demo bundles that one, because the function has a
size limit. Older releases hold a single companies-*.parquet.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def demo_pack_url(repo: str) -> str | None:
    """Download URL of the demo file of the newest release that has a manifest, if any."""
    import httpx

    from pepite.connectors.base import USER_AGENT
    from pepite.packs import MANIFEST_FILE, list_pack_releases

    for release in list_pack_releases(repo):
        if not release.has_manifest:
            continue
        resp = httpx.get(
            release.assets[MANIFEST_FILE],
            headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        name = str((resp.json().get("files") or {}).get("demo") or "")
        if name and Path(name).name == name and name in release.assets:
            return release.assets[name]
    return None


def main() -> int:
    out = ROOT / "data" / "packs"
    out.mkdir(parents=True, exist_ok=True)
    try:
        from pepite.packs import DEFAULT_PACKS_REPO, download_pack, latest_pack_url

        repo = os.environ.get("PEPITE_PACKS_REPO", DEFAULT_PACKS_REPO)
        url = demo_pack_url(repo) or latest_pack_url(repo)
        path = download_pack(url, out)
        print(f"Bundled company pack: {path}")
    except Exception as exc:  # noqa: BLE001 - the sample pack is a fine fallback
        print(f"No published pack bundled ({exc}); the sample pack will be used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
