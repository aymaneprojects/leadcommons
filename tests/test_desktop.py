"""The desktop launcher: local server on 127.0.0.1, single instance, first-run pack download."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from leadcommons import desktop
from leadcommons.config import Settings
from tests.fakes import FakeJobs


def _settings(tmp_path: Path) -> Settings:
    s = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/d.db"
    )
    s.ensure_dirs()
    return s


def test_free_port_is_bindable() -> None:
    port = desktop.free_port()
    assert 1024 < port < 65536


def test_state_file_round_trip(tmp_path: Path) -> None:
    desktop.write_state(tmp_path, 1234)
    assert json.loads((tmp_path / desktop.STATE_FILE).read_text())["port"] == 1234
    desktop.clear_state(tmp_path, 9999)  # someone else's port: keep the file
    assert (tmp_path / desktop.STATE_FILE).exists()
    desktop.clear_state(tmp_path, 1234)
    assert not (tmp_path / desktop.STATE_FILE).exists()


def test_running_instance_ignores_stale_or_broken_state(tmp_path: Path) -> None:
    assert desktop.running_instance(tmp_path) is None
    (tmp_path / desktop.STATE_FILE).write_text("not json")
    assert desktop.running_instance(tmp_path) is None
    desktop.write_state(tmp_path, desktop.free_port())  # nothing listens there
    assert desktop.running_instance(tmp_path) is None


def test_local_server_serves_on_loopback_and_is_found_again(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_DESKTOP_NO_DOWNLOAD", "1")
    server = desktop.LocalServer(_settings(tmp_path), desktop.free_port())
    url = server.start()
    try:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(url + "companies?all=1", timeout=10) as resp:  # noqa: S310
            assert resp.status == 200 and b"LeadCommons" in resp.read()
        desktop.write_state(tmp_path, server.port)
        assert desktop.running_instance(tmp_path) == url
    finally:
        server.stop()
    assert not desktop.is_alive(url)


def test_first_run_downloads_the_full_pack_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEADCOMMONS_DESKTOP_NO_DOWNLOAD", raising=False)
    settings = _settings(tmp_path)
    jobs = FakeJobs()
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, jobs=jobs))
    assert desktop.start_first_pack_download(app) is True
    assert [j.kind for j in jobs.jobs.values()] == ["download_pack"]
    (settings.data_dir / "companies-1.parquet").write_bytes(b"PAR1")
    assert desktop.start_first_pack_download(app) is False  # already has its own pack


def test_first_run_download_can_be_turned_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_DESKTOP_NO_DOWNLOAD", "1")
    jobs = FakeJobs()
    app = SimpleNamespace(state=SimpleNamespace(settings=_settings(tmp_path), jobs=jobs))
    assert desktop.start_first_pack_download(app) is False and not jobs.jobs


def test_headless_main_starts_and_stops(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("LEADCOMMONS_HOME", str(tmp_path))
    monkeypatch.setenv("LEADCOMMONS_DESKTOP_NO_DOWNLOAD", "1")
    assert desktop.main(["--headless", "--exit-after", "0.5"]) == 0
    assert "LeadCommons running at http://127.0.0.1:" in capsys.readouterr().out
    assert not (tmp_path / desktop.STATE_FILE).exists()
    assert (tmp_path / desktop.LOG_FILE).exists()


def test_open_window_falls_back_without_pywebview(tmp_path: Path, monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def no_webview(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("no pywebview")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_webview)
    assert desktop.open_window("http://127.0.0.1:1/", tmp_path) is False


@pytest.mark.parametrize("argv", [["--help"]])
def test_cli_help(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        desktop.main(argv)
    assert exc.value.code == 0
