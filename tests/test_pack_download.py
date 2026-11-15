"""Downloading the newest company pack from the app (Settings > Data packs) and the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import respx
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.packs import (
    DOWNLOADS_FILE,
    SAMPLE_PACK_DIR,
    download_pack,
    has_own_pack,
    install_latest_pack,
)
from leadcommons.store import Store
from leadcommons.web.app import create_app
from leadcommons.web.jobs import JobRunner
from tests.fakes import FakeJobs, FakeSearch

SAMPLE = next(Path(SAMPLE_PACK_DIR).glob("*.parquet")).read_bytes()
REPO = "acme/packs"
API = f"https://api.github.com/repos/{REPO}/releases"


def _release(tag: str, asset: str) -> dict:
    url = f"https://github.com/{REPO}/releases/download/{tag}/{asset}"
    return {
        "tag_name": tag,
        "published_at": tag,
        "assets": [{"name": asset, "browser_download_url": url}],
    }


@respx.mock
def test_install_latest_pack_replaces_only_earlier_downloads(tmp_path: Path) -> None:
    own = tmp_path / "companies-20260101.parquet"  # built by the user: must stay
    own.write_bytes(SAMPLE)
    respx.get(API).mock(
        return_value=httpx.Response(
            200, json=[_release("packs-2026-10", "companies-20261001.parquet")]
        )
    )
    respx.get(url__regex=r".*/companies-20261001\.parquet").mock(
        return_value=httpx.Response(200, content=SAMPLE)
    )
    first = install_latest_pack(tmp_path, REPO)
    assert first.name == "companies-20261001.parquet" and first.exists()

    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=[
                _release("v0.1.0", "leadcommons-0.1.0.tar.gz"),  # app releases are ignored
                _release("packs-2026-11", "companies-20261101.parquet"),
                _release("packs-2026-10", "companies-20261001.parquet"),
            ],
        )
    )
    respx.get(url__regex=r".*/companies-20261101\.parquet").mock(
        return_value=httpx.Response(200, content=SAMPLE)
    )
    second = install_latest_pack(tmp_path, REPO)
    assert second.name == "companies-20261101.parquet"
    assert not first.exists() and own.exists()
    assert json.loads((tmp_path / DOWNLOADS_FILE).read_text()) == [second.name]


def test_marker_never_deletes_outside_the_data_dir(tmp_path: Path) -> None:
    outside = tmp_path / "keep.parquet"
    outside.write_bytes(SAMPLE)
    data = tmp_path / "packs"
    data.mkdir()
    (data / DOWNLOADS_FILE).write_text(json.dumps(["../keep.parquet"]))
    with respx.mock:
        respx.get("https://x.test/companies-1.parquet").mock(
            return_value=httpx.Response(200, content=SAMPLE)
        )
        download_pack("https://x.test/companies-1.parquet", data)
    assert outside.exists()


def test_has_own_pack(tmp_path: Path) -> None:
    assert not has_own_pack(tmp_path / "missing")
    assert not has_own_pack(tmp_path)
    (tmp_path / "companies-1.parquet").write_bytes(SAMPLE)
    assert has_own_pack(tmp_path)


def test_download_job_switches_search_everywhere(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/d.db"
    )

    def fake_install(out_dir: Path, repo: str = REPO) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "companies-20261101.parquet"
        dest.write_bytes(SAMPLE)
        return dest

    monkeypatch.setattr("leadcommons.packs.install_latest_pack", fake_install)
    store = Store(settings.database_url)
    runner = JobRunner(
        store,
        FakeSearch(),
        SimpleNamespace(**{**vars(settings), "sync_jobs": True, "max_workers": 1}),
    )
    state = SimpleNamespace(search=None)
    runner.attach_state(state)
    job = runner.get(runner.submit("download_pack", {}, "Download"))
    assert job is not None and job.status == "done", job and job.error
    assert job.result["pack"] == "companies-20261101.parquet" and job.result["companies"] > 0
    assert state.search is runner.search and state.search.files[0].parent == settings.data_dir


def _client(tmp_path: Path, **kw) -> tuple[TestClient, FakeJobs]:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/p.db", **kw
    )
    jobs = FakeJobs()
    app = create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=jobs
    )
    return TestClient(app, follow_redirects=False), jobs


def test_data_packs_page_offers_the_full_pack(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    html = client.get("/settings/data-packs").text
    assert "Get the full company data" in html and "built-in sample" in html
    assert 'action="/settings/data-packs/download"' in html and "Download the latest data" in html


def test_download_button_starts_a_job(tmp_path: Path) -> None:
    client, jobs = _client(tmp_path)
    r = client.post("/settings/data-packs/download")
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    assert [j.kind for j in jobs.jobs.values()] == ["download_pack"]
    html = client.get("/settings/data-packs").text
    assert "Downloading…" in html and "already running" in html


def test_download_is_off_in_the_demo(tmp_path: Path) -> None:
    client, jobs = _client(tmp_path, mode="demo")
    html = client.get("/settings/data-packs").text
    assert "turned off in the public demo" in html
    r = client.post("/settings/data-packs/download")
    assert r.status_code == 303 and not jobs.jobs
