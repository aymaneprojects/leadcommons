"""Serverless (Vercel) and public-demo behaviour."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from leadcommons.config import Settings
from leadcommons.web.jobs import DEMO_DISABLED, JobRunner
from tests.fakes import FakeSearch, FakeStore, sample_companies, sample_person


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("VERCEL", "LEADCOMMONS_HOME", "LEADCOMMONS_SYNC_JOBS", "LEADCOMMONS_MODE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_vercel_defaults_to_tmp_home_and_sync_jobs(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("VERCEL", "1")
    s = Settings()
    assert s.home == Path("/tmp/leadcommons")
    assert s.database_url.startswith("sqlite:////tmp/leadcommons/")
    assert s.sync_jobs is True


def test_local_defaults_unchanged(clean_env: pytest.MonkeyPatch) -> None:
    s = Settings()
    assert s.home == Path.home() / ".leadcommons"
    assert s.sync_jobs is False
    assert s.enrichment_enabled is True


def test_sync_jobs_can_be_forced_off_on_vercel(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("VERCEL", "1")
    clean_env.setenv("LEADCOMMONS_SYNC_JOBS", "0")
    assert Settings().sync_jobs is False


def _runner(tmp_path: Path, **overrides: object) -> tuple[JobRunner, FakeStore]:
    settings = Settings(home=tmp_path, data_dir=tmp_path / "packs", database_url="sqlite://")
    for key, value in overrides.items():
        setattr(settings, key, value)
    store = FakeStore()
    return JobRunner(store, FakeSearch(), settings), store


def test_demo_mode_refuses_enrichment_without_crawling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from leadcommons import enrich

    def boom(*_a: object, **_k: object) -> list:
        raise AssertionError("demo mode must not crawl")

    monkeypatch.setattr(enrich, "find_people", boom)
    runner, _ = _runner(tmp_path, mode="demo")
    job = runner.get(runner.submit("find_people", {"domain": "example.com"}, "Find people"))
    assert job is not None and job.status == "failed" and job.error == DEMO_DISABLED
    assert runner.running_count() == 0
    runner.shutdown()


def test_sync_jobs_finish_before_submit_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from leadcommons import enrich

    company = sample_companies()[0]
    monkeypatch.setattr(enrich, "find_people", lambda domain: [sample_person(company)])
    runner, store = _runner(tmp_path, sync_jobs=True)
    job = runner.get(runner.submit("find_people", {"domain": company.domain}, "Find people"))
    assert job is not None and job.status == "done" and job.result == {"people": 1}
    assert len(store.people) == 1
    runner.shutdown()


def test_serverless_app_uses_bundled_pack_when_present(
    clean_env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clean_env.setenv("LEADCOMMONS_HOME", str(tmp_path))
    clean_env.delenv("LEADCOMMONS_DATA_DIR", raising=False)
    serverless = importlib.import_module("leadcommons.web.serverless")
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    clean_env.setattr(serverless, "BUNDLED_PACKS", bundled)
    assert serverless.build_settings().data_dir == tmp_path / "packs"  # empty: not used
    (bundled / "companies-x.parquet").write_bytes(b"PAR1")
    assert serverless.build_settings().data_dir == bundled
