"""The API reference page is rendered on the server and loads nothing from a CDN."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from leadcommons.config import Settings
from leadcommons.store import Store
from leadcommons.web.app import create_app
from leadcommons.web.pages_apiref import build_reference, type_label
from tests.fakes import FakeJobs, FakeSearch


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        home=tmp_path, data_dir=tmp_path / "packs", database_url=f"sqlite:///{tmp_path}/p.db"
    )
    app = create_app(
        settings=settings, search=FakeSearch(), store=Store(settings.database_url), jobs=FakeJobs()
    )
    return TestClient(app, follow_redirects=False)


def test_api_docs_page_lists_endpoints_without_a_cdn(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    r = client.get("/api/docs")
    assert r.status_code == 200
    html = r.text
    assert "API reference" in html and "/api/companies" in html
    assert "swagger" not in html.lower() and "cdn.jsdelivr" not in html
    assert 'href="/openapi.json"' in html
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/redoc").status_code == 404


def test_type_label_reads_refs_unions_and_arrays() -> None:
    assert type_label({"$ref": "#/components/schemas/Company"}) == "Company"
    assert type_label({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "string | null"
    assert type_label({"type": "array", "items": {"type": "integer"}}) == "integer[]"
    assert type_label(None) == ""


def test_build_reference_groups_by_tag_and_falls_back_to_path() -> None:
    spec = {
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/api/things": {"get": {"tags": ["things"], "summary": "List things"}},
            "/api/other/{id}": {
                "delete": {"parameters": [{"name": "id", "in": "path", "required": True}]}
            },
        },
        "components": {"schemas": {"Thing": {"properties": {"a": {"type": "string"}}}}},
    }
    ref = build_reference(spec)
    groups = dict(ref["groups"])
    assert ref["count"] == 2 and set(groups) == {"things", "other"}
    assert groups["other"][0]["params"][0]["required"] is True
    assert ref["schemas"][0]["name"] == "Thing"
