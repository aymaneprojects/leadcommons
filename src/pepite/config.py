"""Runtime settings from environment variables. SHARED CONTRACT."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _on_serverless() -> bool:
    """True on hosts with a read-only disk and no background threads (Vercel sets VERCEL=1)."""
    return os.environ.get("VERCEL") == "1"


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes"}


def _home() -> Path:
    default = Path("/tmp/pepite") if _on_serverless() else Path.home() / ".pepite"
    old = Path.home() / ".leadcommons"  # data of installs from before the rename to Pépite
    if not _on_serverless() and old.is_dir() and not default.exists():
        default = old
    return Path(os.environ.get("PEPITE_HOME", default)).expanduser()


def _database_file() -> Path:
    home = _home()
    old = home / "leadcommons.db"  # database of installs from before the rename
    return old if old.exists() and not (home / "pepite.db").exists() else home / "pepite.db"


@dataclass
class Settings:
    home: Path = field(default_factory=_home)
    # Directory holding company data packs (*.parquet). Falls back to the bundled sample pack.
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("PEPITE_DATA_DIR", _home() / "packs"))
    )
    # SQLAlchemy URL of the local user database (people, emails, lists). Postgres in server mode.
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "PEPITE_DATABASE_URL", f"sqlite:///{_database_file()}"
        )
    )
    # "local" (single user on a laptop, no login), "server" (login required) or "demo"
    # (public showcase: no login, enrichment jobs disabled so the host is never used to crawl).
    # Serverless hosts default to "demo": a public URL must never crawl for anonymous users.
    mode: str = field(
        default_factory=lambda: os.environ.get(
            "PEPITE_MODE", "demo" if _on_serverless() else "local"
        )
    )
    # Server mode: plain password or a hash made with `pepite hash-password`.
    password: str | None = field(default_factory=lambda: os.environ.get("PEPITE_PASSWORD"))
    password_hash: str | None = field(
        default_factory=lambda: os.environ.get("PEPITE_PASSWORD_HASH")
    )
    secret_key: str = field(
        default_factory=lambda: os.environ.get("PEPITE_SECRET_KEY") or secrets.token_hex(32)
    )
    # Live SMTP mailbox probe for email verification (off by default; port 25 is often blocked).
    smtp_probe: bool = field(default_factory=lambda: _flag("PEPITE_SMTP_PROBE"))
    # Run jobs inside the request instead of a thread pool (serverless hosts freeze threads).
    sync_jobs: bool = field(
        default_factory=lambda: _flag("PEPITE_SYNC_JOBS", default=_on_serverless())
    )
    max_workers: int = field(default_factory=lambda: int(os.environ.get("PEPITE_WORKERS", "4")))

    @property
    def auth_required(self) -> bool:
        return self.mode == "server" or bool(self.password or self.password_hash)

    @property
    def enrichment_enabled(self) -> bool:
        """Crawling company sites and checking emails; off in the public demo."""
        return self.mode != "demo"

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
