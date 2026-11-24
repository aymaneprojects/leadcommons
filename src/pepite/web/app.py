"""Application factory. SHARED CONTRACT. Owner: main.

Routers: pepite.web.pages.router (HTML, UI agent) and pepite.web.api.router
(JSON under /api, app-core agent). Both read app.state.{settings,search,store,jobs}.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pepite import __version__
from pepite.config import Settings
from pepite.web.deps import STATIC_DIR


def create_app(
    settings: Settings | None = None,
    *,
    search: Any = None,
    store: Any = None,
    jobs: Any = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()

    if store is None:
        from pepite.store import Store

        store = Store(settings.database_url)
    if search is None:
        from pepite.packs import DuckDBCompanySearch

        search = DuckDBCompanySearch(settings.data_dir, saved=store.get_company)
    if jobs is None:
        from pepite.web.jobs import JobRunner

        jobs = JobRunner(store, search, settings)

    # /api/docs is our own server-rendered page (pages_apiref): Swagger UI needs a CDN.
    app = FastAPI(title="Pépite", version=__version__, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.search = search
    app.state.store = store
    app.state.jobs = jobs
    if hasattr(jobs, "attach_state"):
        jobs.attach_state(app.state)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from pepite.web import (
        api,
        pages,
        pages_apiref,
        pages_engage,
        pages_extra,
        pages_insights,
        pages_pipeline,
        pages_tools,
    )
    from pepite.web.auth import install_auth

    install_auth(app, settings)
    app.include_router(pages_apiref.router)
    app.include_router(api.router, prefix="/api")
    # First match wins: real feature pages before the shell's placeholders, and the shell
    # (settings, onboarding, notifications) before the older handlers still in pages.py.
    app.include_router(pages_engage.router)
    app.include_router(pages_pipeline.router)
    app.include_router(pages_tools.router)
    app.include_router(pages_insights.router)
    app.include_router(pages_extra.router)
    app.include_router(pages.router)
    pages_engage.install_sender(app)  # background email sender; off in demo and with sync_jobs

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.on_event("shutdown")
    def _shutdown() -> None:
        shutdown = getattr(app.state.jobs, "shutdown", None)
        if callable(shutdown):
            shutdown()

    return app
