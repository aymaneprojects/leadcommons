"""API reference page, rendered on the server from the app's own OpenAPI schema.

FastAPI's default /docs loads Swagger UI from a CDN, which breaks offline and on locked-down
servers. This page needs nothing but the running app.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from pepite.web.pages import render

router = APIRouter(include_in_schema=False)

METHODS = ("get", "post", "put", "patch", "delete")


def _ref_name(schema: dict[str, Any]) -> str:
    ref = schema.get("$ref", "")
    return ref.rsplit("/", 1)[-1] if ref else ""


def type_label(schema: dict[str, Any] | None) -> str:
    """A short human label for a JSON schema: 'string', 'integer[]', 'Company', 'string | null'."""
    if not schema:
        return ""
    if name := _ref_name(schema):
        return name
    if "anyOf" in schema or "oneOf" in schema:
        parts = [type_label(s) for s in schema.get("anyOf") or schema.get("oneOf") or []]
        return " | ".join(p for p in parts if p)
    kind = schema.get("type", "")
    if kind == "array":
        return f"{type_label(schema.get('items')) or 'any'}[]"
    if kind == "null":
        return "null"
    if schema.get("enum"):
        return " | ".join(repr(v) for v in schema["enum"][:6])
    return str(kind or "object")


def _body_schema(op: dict[str, Any]) -> dict[str, Any] | None:
    content = (op.get("requestBody") or {}).get("content") or {}
    for media in ("application/json", "multipart/form-data", "application/x-www-form-urlencoded"):
        if media in content:
            return {"media": media, "type": type_label(content[media].get("schema"))}
    return None


def _response_type(op: dict[str, Any]) -> str:
    for code in ("200", "201"):
        content = ((op.get("responses") or {}).get(code) or {}).get("content") or {}
        if "application/json" in content:
            return type_label(content["application/json"].get("schema"))
        if content:
            return next(iter(content))
    return ""


def build_reference(spec: dict[str, Any], prefix: str = "/api/") -> dict[str, Any]:
    """Group the JSON API operations (paths under `prefix`) by tag, or by first path segment."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not path.startswith(prefix):
            continue  # HTML pages also appear in the schema; they are not the API
        for method in METHODS:
            op = item.get(method)
            if not op:
                continue
            segments = [s for s in path.strip("/").split("/") if s and s != "api"]
            tags = op.get("tags") or [segments[0] if segments else "api"]
            tag = str(tags[0])
            params = [
                {
                    "name": p.get("name", ""),
                    "where": p.get("in", ""),
                    "required": bool(p.get("required")),
                    "type": type_label(p.get("schema")),
                    "about": p.get("description", ""),
                }
                for p in (op.get("parameters") or [])
            ]
            groups.setdefault(tag, []).append(
                {
                    "id": f"{method}-{path}".replace("/", "-").replace("{", "").replace("}", ""),
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary") or "",
                    "about": op.get("description") or "",
                    "params": params,
                    "body": _body_schema(op),
                    "returns": _response_type(op),
                }
            )
    schemas = []
    for name, schema in sorted(((spec.get("components") or {}).get("schemas") or {}).items()):
        required = set(schema.get("required") or [])
        fields = [
            {"name": k, "type": type_label(v), "required": k in required}
            for k, v in (schema.get("properties") or {}).items()
        ]
        schemas.append({"name": name, "fields": fields, "about": schema.get("description", "")})
    return {
        "title": (spec.get("info") or {}).get("title", "API"),
        "version": (spec.get("info") or {}).get("version", ""),
        "groups": sorted(groups.items()),
        "schemas": schemas,
        "count": sum(len(v) for v in groups.values()),
    }


@router.get("/api/docs")
def api_reference(request: Request) -> Response:
    ref = build_reference(request.app.openapi())
    base = str(request.base_url).rstrip("/")
    return render(request, "api_reference.html", active="settings", ref=ref, base=base)
