from __future__ import annotations

import sys
import types

import pytest
from typer.testing import CliRunner

from pepite.cli import app

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("serve", "pack", "find-people", "find-email", "enrich", "hash-password", "optout"):
        assert cmd in result.output


def test_pack_help():
    result = runner.invoke(app, ["pack", "--help"])
    assert result.exit_code == 0
    assert "build" in result.output and "download" in result.output and "info" in result.output


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.startswith("pepite ")


def _fake(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, name, mod)


def test_hash_password(monkeypatch):
    _fake(monkeypatch, "pepite.web.auth", hash_password=lambda p: f"hashed:{p}")
    result = runner.invoke(app, ["hash-password"], input="s3cret\ns3cret\n")
    assert result.exit_code == 0
    assert "hashed:s3cret" in result.output


def test_hash_password_mismatch(monkeypatch):
    _fake(monkeypatch, "pepite.web.auth", hash_password=lambda p: "x")
    result = runner.invoke(app, ["hash-password"], input="a\nb\na\nb\na\nb\n")
    assert result.exit_code != 0


def test_optout(monkeypatch, tmp_path):
    seen = []

    class FakeStore:
        def __init__(self, url=None):
            pass

        def add_suppression(self, target, reason="opt-out"):
            seen.append(target)

    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    _fake(monkeypatch, "pepite.store", Store=FakeStore)
    result = runner.invoke(app, ["optout", "Spam@Example.com"])
    assert result.exit_code == 0
    assert seen == ["spam@example.com"]


def test_pack_info_empty(monkeypatch, tmp_path):
    class FakeSearch:
        def __init__(self, data_dir=None):
            pass

        def pack_info(self):
            return []

    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    _fake(monkeypatch, "pepite.packs", DuckDBCompanySearch=FakeSearch)
    result = runner.invoke(app, ["pack", "info"])
    assert result.exit_code == 0
    assert "No packs" in result.output


def test_pack_download_latest(monkeypatch, tmp_path):
    import pepite.cli as cli

    got = {}
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setattr(
        cli, "_latest_pack_url", lambda repo: f"https://x/{repo}/companies-1.parquet"
    )

    def fake_download(url, out_dir):
        got["url"] = url
        return out_dir / "companies-1.parquet"

    _fake(monkeypatch, "pepite.packs", download_pack=fake_download)
    result = runner.invoke(app, ["pack", "download"])
    assert result.exit_code == 0
    assert got["url"].endswith("companies-1.parquet")


def test_find_people(monkeypatch):
    from pepite.models import Person

    people = [Person(id="1", full_name="Ada Lovelace", title="CEO", source_urls=["https://a/team"])]
    _fake(monkeypatch, "pepite.enrich", find_people=lambda d: people)
    result = runner.invoke(app, ["find-people", "example.com"])
    assert result.exit_code == 0
    assert "Ada Lovelace - CEO" in result.output


def test_find_email_and_not_implemented(monkeypatch):
    from pepite.models import EmailCandidate, EmailStatus

    cand = EmailCandidate("ada@example.com", EmailStatus.VALID, 90, "{first}")
    _fake(monkeypatch, "pepite.enrich", find_email=lambda p, d, smtp_probe=False: cand)
    result = runner.invoke(app, ["find-email", "Ada Lovelace", "example.com"])
    assert result.exit_code == 0
    assert "ada@example.com" in result.output

    def boom(*a, **k):
        raise NotImplementedError

    _fake(monkeypatch, "pepite.enrich", find_email=boom)
    result = runner.invoke(app, ["find-email", "Ada Lovelace", "example.com"])
    assert result.exit_code == 1


def test_serve_calls_uvicorn(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setenv("PEPITE_MODE", "local")
    monkeypatch.delenv("PEPITE_PASSWORD", raising=False)
    monkeypatch.delenv("PEPITE_PASSWORD_HASH", raising=False)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.update(args=a, kwargs=k))
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert calls["args"][0] == "pepite.web.app:create_app"
    assert calls["kwargs"]["factory"] is True
    assert calls["kwargs"]["host"] == "127.0.0.1"


def test_serve_server_mode_host_and_login_check(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    monkeypatch.setenv("PEPITE_MODE", "server")
    monkeypatch.delenv("PEPITE_PASSWORD", raising=False)
    monkeypatch.delenv("PEPITE_PASSWORD_HASH", raising=False)
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.update(kwargs=k))
    assert runner.invoke(app, ["serve"]).exit_code == 1
    monkeypatch.setenv("PEPITE_PASSWORD", "pw")
    assert runner.invoke(app, ["serve"]).exit_code == 0
    assert calls["kwargs"]["host"] == "0.0.0.0"


@pytest.mark.parametrize("args", [["pack", "info"]])
def test_stub_error_is_friendly(monkeypatch, tmp_path, args):
    class Stub:
        def __init__(self, data_dir=None):
            raise NotImplementedError

    monkeypatch.setenv("PEPITE_HOME", str(tmp_path))
    _fake(monkeypatch, "pepite.packs", DuckDBCompanySearch=Stub)
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "not available yet" in result.output
