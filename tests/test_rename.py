"""Installs from before the rename to Pépite keep their data and settings."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import pepite
from pepite.config import Settings


def test_an_existing_leadcommons_folder_and_database_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PEPITE_HOME", raising=False)
    monkeypatch.delenv("PEPITE_DATABASE_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    old = tmp_path / ".leadcommons"
    old.mkdir()
    (old / "leadcommons.db").write_bytes(b"")
    settings = Settings()
    assert settings.home == old
    assert settings.database_url == f"sqlite:///{old / 'leadcommons.db'}"


def test_a_new_install_uses_the_new_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("PEPITE_HOME", raising=False)
    monkeypatch.delenv("PEPITE_DATABASE_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    settings = Settings()
    assert settings.home == tmp_path / ".pepite"
    assert settings.database_url.endswith("/.pepite/pepite.db")


def test_old_environment_variables_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PEPITE_MODE", raising=False)
    monkeypatch.setenv("LEADCOMMONS_MODE", "demo")
    importlib.reload(pepite)
    assert Settings().mode == "demo"
