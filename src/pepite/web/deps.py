"""FastAPI dependencies and the shared Jinja2 environment. SHARED CONTRACT."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request

from pepite.web.i18n import LocaleTemplates, install_filters

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# English, or the request's language (pepite.web.i18n).
templates = LocaleTemplates(directory=str(TEMPLATES_DIR))
install_filters(templates.env)


def get_settings(request: Request) -> Any:
    return request.app.state.settings


def get_search(request: Request) -> Any:  # pepite.search.CompanySearch
    return request.app.state.search


def get_store(request: Request) -> Any:  # pepite.store.Store
    return request.app.state.store


def get_jobs(request: Request) -> Any:  # pepite.web.jobs.JobRunner
    return request.app.state.jobs
