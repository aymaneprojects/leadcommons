"""FastAPI dependencies and the shared Jinja2 environment. SHARED CONTRACT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_settings(request: Request) -> Any:
    return request.app.state.settings


def get_search(request: Request) -> Any:  # leadcommons.search.CompanySearch
    return request.app.state.search


def get_store(request: Request) -> Any:  # leadcommons.store.Store
    return request.app.state.store


def get_jobs(request: Request) -> Any:  # leadcommons.web.jobs.JobRunner
    return request.app.state.jobs
