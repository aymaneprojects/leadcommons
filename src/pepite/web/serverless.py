"""ASGI app for serverless hosts such as Vercel.

On Vercel (VERCEL=1) settings default to a writable home in /tmp and jobs that run inside the
request. A company pack downloaded at build time into <repo>/data/packs is used when present;
otherwise the sample pack bundled in the package is.
"""

from __future__ import annotations

import os
from pathlib import Path

from pepite.config import Settings
from pepite.web.app import create_app

BUNDLED_PACKS = Path(__file__).resolve().parents[3] / "data" / "packs"


def build_settings() -> Settings:
    settings = Settings()
    if "PEPITE_DATA_DIR" not in os.environ and any(BUNDLED_PACKS.glob("*.parquet")):
        settings.data_dir = BUNDLED_PACKS
    return settings


app = create_app(build_settings())
