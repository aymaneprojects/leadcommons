"""Vercel entrypoint (see [tool.vercel] in pyproject.toml). Not part of the Python package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pepite.web.serverless import app  # noqa: E402

__all__ = ["app"]
