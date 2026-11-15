"""Settings > Data packs: the country picker (list, download, update, remove) and its jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.models import utcnow
from leadcommons.packs import DEFAULT_PACKS_REPO
from leadcommons.packs import installed as pack_installed
from leadcommons.store import Store
from leadcommons.web.app import create_app
from leadcommons.web.jobs import Job
from tests.fakes import FakeJobs, FakeSearch

API = f"https://api.github.com/repos/{DEFAULT_PACKS_REPO}/releases"
BASE = f"https://github.com/{DEFAULT_PACKS_REPO}/releases/download/packs-2026-12/"
FR_SHA = hashlib.sha256(b"fr").hexdigest()


def _manifest() -> dict:
    return {
        "schema": 3,
        "release": "packs-2026-12",
        "built_at": "2026-12-01T03:40:00+00:00",
        "countries": [
            {"country": "FR", "file": "companies-FR-20261201.parquet", "rows": 13250000,
             "bytes": 812345678, "sha256": FR_SHA, "sources": ["sirene"]},
            {"country": "GB", "file": "companies-GB-20261201.parquet", "rows": 5100000,
             "bytes": 301000000, "sha256": "b" * 64, "sources": ["companies_house"]},
            {"country": "NO", "file": "companies-NO-20261201.parquet", "rows": 1100000,
             "bytes": 61000000, "sha256": "c" * 64, "sources": ["brreg"]},
        ],
    }  # fmt: skip


def _mock_github() -> None:
    names = [c["file"] for c in _manifest()["countries"]] + ["manifest.json"]
    release = {
        "tag_name": "packs-2026-12",
        "published_at": "2026-12-01T04:00:00Z",
        "assets": [{"name": n, "browser_download_url": BASE + n} for n in names],
    }
    respx.get(API).mock(return_value=httpx.Response(200, json=[release]))
    respx.get(BASE + "manifest.json").mock(return_value=httpx.Response(200, json=_manifest()))


def _client(tmp_path: Path, **kw) -> tuple[TestClient, FakeJobs, Settings]:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/p.db", **kw
    )
    jobs = FakeJobs()
    app = create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=jobs
    )
    return TestClient(app, follow_redirects=False), jobs, settings


def _install(settings: Settings, cc: str, sha: str, release: str = "packs-2026-12") -> None:
    name = f"companies-{cc}-x.parquet"
    (settings.data_dir / name).write_bytes(b"PAR1")
    pack_installed.record(
        settings.data_dir,
        pack_installed.InstalledCountry(country=cc, file=name, release=release, sha256=sha),
    )


def test_page_never_waits_for_github(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    with respx.mock(assert_all_called=False) as mock:  # any network call would fail the test
        html = client.get("/settings/data-packs").text
        assert not mock.calls
    assert 'hx-get="/settings/data-packs/countries"' in html and "Loading the list" in html
    assert "Get the full company data" in html  # the one-click download stays


@respx.mock
def test_country_list_from_the_manifest(tmp_path: Path) -> None:
    _mock_github()
    client, _, settings = _client(tmp_path)
    _install(settings, "FR", FR_SHA)
    _install(settings, "GB", "0" * 64, release="packs-2026-11")
    html = client.get("/settings/data-packs/countries").text
    assert "packs-2026-12" in html
    assert "France" in html and "13,250,000" in html and "812.3 MB" in html
    assert "United Kingdom" in html and "Update available" in html
    assert "Norway" in html and "Not installed" in html
    assert html.count(">Installed<") == 1
    assert 'name="action" value="remove"' in html and 'value="install"' in html
    assert "Update all (1)" in html
    # the saved list is used by the full page from now on, without GitHub
    with respx.mock(assert_all_called=False) as mock:
        page = client.get("/settings/data-packs").text
        assert not mock.calls
    assert "Update available" in page and "Loading the list" not in page


def test_unreachable_github_says_so(tmp_path: Path) -> None:
    client, _, _ = _client(tmp_path)
    with respx.mock:
        respx.get(API).mock(side_effect=httpx.ConnectError("offline"))
        html = client.get("/settings/data-packs/countries?refresh=1").text
    assert "Could not reach GitHub" in html


@pytest.mark.parametrize(
    "form, payload",
    [
        ({"action": "install", "country": "fr"}, {"action": "install", "countries": ["FR"]}),
        ({"action": "remove", "country": "GB"}, {"action": "remove", "countries": ["GB"]}),
        ({"action": "update"}, {"action": "update"}),
        ({"action": "index"}, {"action": "index"}),
    ],
)
def test_actions_start_a_pack_job(tmp_path: Path, form: dict, payload: dict) -> None:
    client, jobs, _ = _client(tmp_path)
    r = client.post("/settings/data-packs/countries", data=form)
    assert r.status_code == 303 and "notice=" in r.headers["location"]
    (job,) = jobs.jobs.values()
    assert job.kind == "download_pack" and job.payload == payload


def test_bad_requests_start_nothing(tmp_path: Path) -> None:
    client, jobs, _ = _client(tmp_path)
    for form in ({"action": "install"}, {"action": "install", "country": "../x"},
                 {"action": "nuke", "country": "FR"}):  # fmt: skip
        client.post("/settings/data-packs/countries", data=form)
    assert not jobs.jobs
    r = client.post(
        "/settings/data-packs/countries",
        data={"action": "install", "country": "FR"},
        headers={"origin": "https://evil.example"},
    )
    assert r.status_code == 403 and not jobs.jobs


@respx.mock
def test_demo_mode_disables_downloads_with_the_reason(tmp_path: Path) -> None:
    _mock_github()
    client, jobs, _ = _client(tmp_path, mode="demo")
    html = client.get("/settings/data-packs/countries").text
    assert "turned off in the public demo" in html and "disabled" in html
    assert 'action="/settings/data-packs/countries"' not in html
    r = client.post("/settings/data-packs/countries", data={"action": "install", "country": "FR"})
    assert r.status_code == 303 and "public+demo" in r.headers["location"] and not jobs.jobs


@respx.mock
def test_running_job_disables_the_buttons_and_shows_progress(tmp_path: Path) -> None:
    _mock_github()
    client, jobs, _ = _client(tmp_path)
    jobs.jobs["j1"] = Job(
        id="j1", kind="download_pack", label="Download company data: France",
        payload={"action": "install", "countries": ["FR"]}, status="running",
        result={"phase": "Downloading FR", "progress": 406172839, "total": 812345678},
    )  # fmt: skip
    html = client.get("/settings/data-packs/countries").text
    assert "already running" in html and 'value="install"' not in html
    page = client.get("/settings/data-packs").text
    assert "Downloading FR: 50%" in page


def test_first_run_notice(tmp_path: Path) -> None:
    client, jobs, _ = _client(tmp_path)
    jobs.jobs["j1"] = Job(
        id="j1", kind="download_pack", label="Download company data for France",
        payload={"countries": ["FR"], "first_run": True}, status="done",
        result={"countries": ["FR"], "pack": "companies-FR-1.parquet", "companies": 3,
                "message": "Company data for FR is ready to search. Add more countries in "
                "Settings > Data packs."},
        finished_at=utcnow(),
    )  # fmt: skip
    html = client.get("/settings/data-packs").text
    assert "France was downloaded when LeadCommons first started" in html
    assert "Add more countries" in html
