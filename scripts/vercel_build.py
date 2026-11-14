"""Vercel build step: bundle the newest published company pack into data/packs.

Runs after dependencies are installed. Never fails the build: without a published pack the
sample pack shipped inside the package is used.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    out = ROOT / "data" / "packs"
    out.mkdir(parents=True, exist_ok=True)
    try:
        from leadcommons.cli import DEFAULT_PACKS_REPO, _latest_pack_url
        from leadcommons.packs import download_pack

        repo = os.environ.get("LEADCOMMONS_PACKS_REPO", DEFAULT_PACKS_REPO)
        path = download_pack(_latest_pack_url(repo), out)
        print(f"Bundled company pack: {path}")
    except Exception as exc:  # noqa: BLE001 - the sample pack is a fine fallback
        print(f"No published pack bundled ({exc}); the sample pack will be used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
