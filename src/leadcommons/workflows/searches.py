"""Run a saved search (the query string of /companies or /people) outside a request."""

from __future__ import annotations

from typing import Any

from starlette.datastructures import QueryParams

MAX_PAGES = 6


class _Req:
    """Just enough of a Request for the page parsers: they only read query_params."""

    def __init__(self, query: str) -> None:
        self.query_params = QueryParams(query)


def run_query(
    search: Any, store: Any, source: str, query: str, limit: int, *, seen: set[str]
) -> list[Any]:
    """Up to `limit` companies or people matching `query` whose id is not in `seen`."""
    from leadcommons.web import pages

    query = "&".join(
        part
        for part in query.lstrip("?").split("&")
        if not part.startswith(("page=", "per=", "notice="))
    )
    out: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        q = f"{query}&per=100&page={page}" if query else f"per=100&page={page}"
        req: Any = _Req(q)
        if source == "people":
            params = pages.parse_people_params(req)
            res = pages.run_people(search, store, params, facets=False)
        else:
            params = pages.parse_company_params(req)
            res = pages.run_companies(search, store, params, facets=False)
        items = list(res.get("items") or [])
        if not items:
            break
        for item in items:
            if item.id not in seen:
                out.append(item)
                if len(out) >= limit:
                    return out
        if len(items) < 100:
            break
    return out
