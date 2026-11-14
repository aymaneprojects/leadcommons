"""Application factory. SHARED CONTRACT. Owner: main.

Routers: leadcommons.web.pages.router (HTML, UI agent) and leadcommons.web.api.router
(JSON under /api, app-core agent). Both read app.state.{settings,search,store,jobs}.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from leadcommons import __version__
from leadcommons.config import Settings
from leadcommons.web.deps import STATIC_DIR


def create_app(
    settings: Settings | None = None,
    *,
    search: Any = None,
    store: Any = None,
    jobs: Any = None,
) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()

    if search is None:
        from leadcommons.packs import DuckDBCompanySearch

        search = DuckDBCompanySearch(settings.data_dir)
    if store is None:
        from leadcommons.store import Store

        store = Store(settings.database_url)
    if jobs is None:
        from leadcommons.web.jobs import JobRunner

        jobs = JobRunner(store, search, settings)

    app = FastAPI(title="LeadCommons", version=__version__, docs_url="/api/docs")
    app.state.settings = settings
    app.state.search = search
    app.state.store = store
    app.state.jobs = jobs

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from leadcommons.web import api, pages
    from leadcommons.web.auth import install_auth

    install_auth(app, settings)
    app.include_router(api.router, prefix="/api")
    app.include_router(pages.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.on_event("shutdown")
    def _shutdown() -> None:
        shutdown = getattr(app.state.jobs, "shutdown", None)
        if callable(shutdown):
            shutdown()

    return app
