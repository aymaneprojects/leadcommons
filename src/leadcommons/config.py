"""Runtime settings from environment variables. SHARED CONTRACT."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _home() -> Path:
    return Path(os.environ.get("LEADCOMMONS_HOME", Path.home() / ".leadcommons")).expanduser()


@dataclass
class Settings:
    home: Path = field(default_factory=_home)
    # Directory holding company data packs (*.parquet). Falls back to the bundled sample pack.
    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("LEADCOMMONS_DATA_DIR", _home() / "packs"))
    )
    # SQLAlchemy URL of the local user database (people, emails, lists). Postgres in server mode.
    database_url: str = field(
        default_factory=lambda: os.environ.get(
            "LEADCOMMONS_DATABASE_URL", f"sqlite:///{_home() / 'leadcommons.db'}"
        )
    )
    # "local" (single user on a laptop, no login) or "server" (login required).
    mode: str = field(default_factory=lambda: os.environ.get("LEADCOMMONS_MODE", "local"))
    # Server mode: plain password or a hash made with `leadcommons hash-password`.
    password: str | None = field(default_factory=lambda: os.environ.get("LEADCOMMONS_PASSWORD"))
    password_hash: str | None = field(
        default_factory=lambda: os.environ.get("LEADCOMMONS_PASSWORD_HASH")
    )
    secret_key: str = field(
        default_factory=lambda: os.environ.get("LEADCOMMONS_SECRET_KEY") or secrets.token_hex(32)
    )
    # Live SMTP mailbox probe for email verification (off by default; port 25 is often blocked).
    smtp_probe: bool = field(
        default_factory=lambda: os.environ.get("LEADCOMMONS_SMTP_PROBE", "0") in {"1", "true"}
    )
    max_workers: int = field(
        default_factory=lambda: int(os.environ.get("LEADCOMMONS_WORKERS", "4"))
    )

    @property
    def auth_required(self) -> bool:
        return self.mode == "server" or bool(self.password or self.password_hash)

    def ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
