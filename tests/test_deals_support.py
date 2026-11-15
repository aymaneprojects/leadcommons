"""Shared fixtures for the Win deals tests (deals, meetings, calls, activity). Owner: pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.store import Store
from leadcommons.store.pipeline import Pipeline
from leadcommons.web.deps import STATIC_DIR
from leadcommons.web.pages import router as pages_router
from leadcommons.web.pages_extra import router as extra_router
from leadcommons.web.pages_pipeline import router as pipeline_router
from tests.fakes import FakeJobs, FakeSearch, sample_email, sample_person

HX = {"HX-Request": "true"}
SAME_ORIGIN = {"Origin": "http://testserver"}


def make_app(tmp_path: Path, **settings_kw: object) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(
        home=tmp_path,
        data_dir=tmp_path / "packs",
        database_url=f"sqlite:///{tmp_path / 'lc.db'}",
        **settings_kw,
    )
    app.state.search = FakeSearch()
    app.state.store = Store(app.state.settings.database_url)
    app.state.jobs = FakeJobs()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(extra_router)  # same order as create_app; base.html needs its globals
    app.include_router(pipeline_router)
    app.include_router(pages_router)
    return app


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return make_app(tmp_path)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, follow_redirects=False, headers=SAME_ORIGIN)


@pytest.fixture
def pl(app: FastAPI) -> Pipeline:
    return Pipeline(app.state.store.engine)


@pytest.fixture
def people(app: FastAPI) -> dict[str, object]:
    """Two saved people (with a company in the fake pack) for forms that pick one."""
    search, store = app.state.search, app.state.store
    atelier, nordwind = search.companies[0], search.companies[1]
    claire = sample_person(atelier, "Claire Martin", "CEO")
    jonas = sample_person(nordwind, "Jonas Weber", "Head of Sales")
    for p in (claire, jonas):
        store.save_person(p)
    store.save_email(claire.id, sample_email("claire.martin@ateliernuage.example"))
    return {"claire": claire, "jonas": jonas, "atelier": atelier, "nordwind": nordwind}


def test_support_module_builds_an_app(client: TestClient) -> None:
    assert client.get("/deals").status_code == 200
