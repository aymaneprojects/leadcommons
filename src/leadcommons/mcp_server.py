"""MCP server: lets Claude, ChatGPT and other MCP clients use LeadCommons.

Same Settings, Store and company packs as the web app, created lazily on first use.
Run it with `leadcommons mcp` (stdio) or `leadcommons mcp --http` (network).
"""

import csv
import functools
import hmac
import io
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from leadcommons import __version__
from leadcommons.models import Company, EmailStatus, Person, Seniority
from leadcommons.search import CompanyFilters, PeopleFilters

INSTRUCTIONS = """\
LeadCommons is a free, open-source, local-first prospecting tool: find companies, the
people who work there and their likely work emails, using only public, lawful data.

- Company data comes from open datasets (Wikidata, GLEIF; CC0). People and emails are
  found on a company's own public web pages and stay in the user's local database; they
  are never uploaded anywhere.
- Typical flow: search_companies -> get_company -> find_people (by company_id or domain)
  -> find_email (by person_id) -> add_to_list -> export_list (CSV).
- Ethics: no login-walled scraping, polite crawling (robots.txt respected). Always honour
  opt-outs: suppressed addresses and domains are never returned or exported. If a person
  asks not to be contacted, call opt_out. Use results for professional (B2B) outreach only.
- Email statuses: valid, catch_all, unknown, invalid. Treat anything but valid as a guess.
"""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
READ_WEB = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
WRITE_WEB = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True
)

MAX_PER_PAGE = 50
MAX_MEMBERS = 500


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _company_dict(c: Company, *, full: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": c.id,
        "name": c.name,
        "domain": c.domain,
        "country": c.country,
        "city": c.city,
        "industry": c.industry_label or c.industry,
        "size_range": c.size_range,
    }
    if full:
        out.update(
            legal_name=c.legal_name,
            region=c.region,
            founded_year=c.founded_year,
            registry_ids=c.registry_ids,
            links=c.links,
            sources=[
                {
                    "source": s.source,
                    "licence": s.licence,
                    "url": s.url,
                    "fetched_at": _iso(s.fetched_at),
                }
                for s in c.sources
            ],
        )
    return out


class _Backend:
    """Lazily created Settings / Store / company search, shared by all tools."""

    def __init__(self, settings: Any = None, search: Any = None, store: Any = None) -> None:
        self._settings = settings
        self._search = search
        self._store = store

    @property
    def settings(self) -> Any:
        if self._settings is None:
            from leadcommons.config import Settings

            self._settings = Settings()
            self._settings.ensure_dirs()
        return self._settings

    @property
    def search(self) -> Any:
        if self._search is None:
            from leadcommons.packs import DuckDBCompanySearch

            self._search = DuckDBCompanySearch(self.settings.data_dir)
        return self._search

    @property
    def store(self) -> Any:
        if self._store is None:
            from leadcommons.store import Store

            self._store = Store(self.settings.database_url)
        return self._store

    # ---- helpers
    def suppressed(self, value: str | None) -> bool:
        return bool(value) and self.store.is_suppressed(value)

    def person_dict(self, p: Person) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": p.id,
            "full_name": p.full_name,
            "title": p.title,
            "seniority": p.seniority.value if p.seniority else None,
            "department": p.department,
            "company_domain": p.company_domain,
            "company_id": p.company_id,
            "source_urls": list(p.source_urls),
            "last_seen": _iso(p.last_seen),
            "email": None,
        }
        best = self.store.best_email(p.id)
        if best and not (self.suppressed(best.address) or self.suppressed(p.company_domain)):
            out["email"] = {
                "address": best.address,
                "status": best.status.value,
                "confidence": best.confidence,
                "pattern": best.pattern,
            }
        return out


def _enum_list[E](values: list[str] | None, enum: type[E], label: str) -> list[E]:
    out = []
    for v in values or []:
        try:
            out.append(enum(v.strip().lower()))  # type: ignore[call-arg]
        except ValueError:
            allowed = ", ".join(str(m.value) for m in enum)  # type: ignore[attr-defined]
            raise ValueError(f"unknown {label} '{v}'. Allowed: {allowed}") from None
    return out


def _page_args(page: int, per_page: int) -> tuple[int, int]:
    return max(1, page), min(max(1, per_page), MAX_PER_PAGE)


def _clean_domain(domain: str) -> str:
    d = domain.strip().lower()
    for prefix in ("https://", "http://"):
        d = d.removeprefix(prefix)
    return d.split("/")[0].removeprefix("www.")


def _csv_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _friendly(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn any failure into a short ToolError message (no tracebacks)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except NotImplementedError:
            raise ToolError(f"{fn.__name__} is not available in this build yet.") from None
        except (ValueError, KeyError, LookupError) as exc:
            raise ToolError(str(exc).strip("'\"") or "invalid input") from None
        except Exception as exc:  # noqa: BLE001
            raise ToolError(f"{fn.__name__} failed: {exc}") from None

    return wrapper


def build_server(settings: Any = None, search: Any = None, store: Any = None) -> MCPServer:
    """Create the MCP server. Arguments are for tests; by default everything is lazy."""
    be = _Backend(settings, search, store)
    server = MCPServer("leadcommons", instructions=INSTRUCTIONS, version=__version__)

    def tool(annotations: ToolAnnotations, title: str):
        def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
            wrapped = _friendly(fn)
            server.add_tool(wrapped, name=fn.__name__, title=title, annotations=annotations)
            return wrapped

        return deco

    @tool(READ, "Search companies")
    def search_companies(
        q: str | None = None,
        countries: list[str] | None = None,
        industries: list[str] | None = None,
        size_ranges: list[str] | None = None,
        keywords: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """Search the open company data packs. `q` matches name or domain; `countries` are
        ISO alpha-2 codes (FR, DE); `industries` are ISIC codes (J, 62); `size_ranges` are
        like "11-50", "51-200"; max 50 results per page."""
        page, per_page = _page_args(page, per_page)
        filters = CompanyFilters(
            text=q,
            countries=[c.strip().upper() for c in countries or []],
            industries=industries or [],
            size_ranges=size_ranges or [],
            keywords=keywords or [],
        )
        res = be.search.search_companies(filters, page, per_page)
        return {
            "total": res.total,
            "page": res.page,
            "per_page": res.per_page,
            "companies": [_company_dict(c) for c in res.items],
        }

    @tool(READ, "Get a company")
    def get_company(company_id: str) -> dict[str, Any]:
        """Full record of one company, including registry ids, links and data sources."""
        c = be.search.get_company(company_id)
        if c is None:
            raise ToolError(f"No company with id '{company_id}'.")
        return _company_dict(c, full=True)

    @tool(WRITE_WEB, "Find people at a company")
    def find_people(company_id: str | None = None, domain: str | None = None) -> dict[str, Any]:
        """Read a company's public web pages (team, about, legal notice, contact) and list the
        people found with their titles. Saves them to the local database. Give a company_id
        or a domain. Respects robots.txt and takes several seconds."""
        company = None
        if company_id:
            company = be.search.get_company(company_id)
            if company is None:
                raise ToolError(f"No company with id '{company_id}'.")
            domain = domain or company.domain
        if not domain:
            raise ToolError("Give a company_id (with a known domain) or a domain.")
        domain = _clean_domain(domain)
        if be.suppressed(domain):
            raise ToolError(f"{domain} is on the suppression list (opt-out); not contacted.")
        from leadcommons import enrich

        people = enrich.find_people(domain)
        out = []
        for p in people:
            p.company_domain = p.company_domain or domain
            if company is not None:
                p.company_id = p.company_id or company.id
            be.store.save_person(p)
            out.append(be.person_dict(p))
        if company is not None:
            be.store.save_company(company)
        return {"domain": domain, "count": len(out), "people": out}

    @tool(READ, "Search saved people")
    def search_people(
        text: str | None = None,
        titles: list[str] | None = None,
        seniorities: list[str] | None = None,
        email_statuses: list[str] | None = None,
        company_domains: list[str] | None = None,
        company_countries: list[str] | None = None,
        page: int = 1,
        per_page: int = 25,
    ) -> dict[str, Any]:
        """Search people already found and saved locally. `seniorities`: owner, founder,
        c_suite, partner, vp, head, director, manager, senior, entry, intern.
        `email_statuses`: valid, catch_all, unknown, invalid."""
        page, per_page = _page_args(page, per_page)
        filters = PeopleFilters(
            text=text,
            titles=titles or [],
            seniorities=_enum_list(seniorities, Seniority, "seniority"),
            email_statuses=_enum_list(email_statuses, EmailStatus, "email status"),
            company=CompanyFilters(
                domains=[_clean_domain(d) for d in company_domains or []],
                countries=[c.strip().upper() for c in company_countries or []],
            ),
        )
        res = be.store.search_people(filters, page, per_page)
        return {
            "total": res.total,
            "page": res.page,
            "per_page": res.per_page,
            "people": [be.person_dict(p) for p in res.items],
        }

    @tool(WRITE_WEB, "Find a person's work email")
    def find_email(person_id: str) -> dict[str, Any]:
        """Find and verify the most likely work email of a saved person (syntax, MX,
        catch-all checks; no mailbox probing unless the user enabled it). Suppressed
        addresses are never returned or stored."""
        person = be.store.get_person(person_id)
        if person is None:
            raise ToolError(f"No saved person with id '{person_id}'. Use find_people first.")
        domain = person.company_domain
        if not domain:
            raise ToolError("This person has no company domain.")
        if be.suppressed(domain):
            raise ToolError(f"{domain} is on the suppression list (opt-out).")
        from leadcommons import enrich

        cand = enrich.find_email(person, domain, smtp_probe=be.settings.smtp_probe)
        if cand is None:
            return {"person_id": person_id, "found": False, "email": None}
        if be.suppressed(cand.address):
            return {
                "person_id": person_id,
                "found": False,
                "email": None,
                "note": "The best candidate is on the suppression list and was discarded.",
            }
        be.store.save_email(person_id, cand)
        return {
            "person_id": person_id,
            "found": True,
            "email": {
                "address": cand.address,
                "status": cand.status.value,
                "confidence": cand.confidence,
                "pattern": cand.pattern,
                "checks": cand.checks,
            },
        }

    @tool(READ, "List lists")
    def list_lists(kind: Literal["people", "companies"] | None = None) -> dict[str, Any]:
        """All saved lists with their size."""
        return {
            "lists": [
                {
                    "id": i.id,
                    "name": i.name,
                    "kind": i.kind,
                    "size": i.size,
                    "created_at": _iso(i.created_at),
                }
                for i in be.store.lists(kind)
            ]
        }

    @tool(WRITE, "Create a list")
    def create_list(name: str, kind: Literal["people", "companies"]) -> dict[str, Any]:
        """Create an empty list of people or companies."""
        name = name.strip()[:120]
        if not name:
            raise ToolError("Give the list a name.")
        return {"id": be.store.create_list(name, kind), "name": name, "kind": kind}

    def _list_info(list_id: str) -> Any:
        for info in be.store.lists():
            if info.id == list_id:
                return info
        raise ToolError(f"No list with id '{list_id}'.")

    @tool(WRITE, "Add records to a list")
    def add_to_list(list_id: str, record_ids: list[str]) -> dict[str, Any]:
        """Add company ids or person ids (matching the list's kind) to a list."""
        info = _list_info(list_id)
        ids = [r for r in dict.fromkeys(record_ids) if r]
        if info.kind == "companies":
            for cid in ids:
                c = be.search.get_company(cid)
                if c:
                    be.store.save_company(c)
        be.store.add_to_list(list_id, ids)
        return {"list_id": list_id, "added": len(ids), "size": _list_info(list_id).size}

    @tool(READ, "List members")
    def list_members(list_id: str) -> dict[str, Any]:
        """Records in a list (up to 500), with their details."""
        info = _list_info(list_id)
        ids = be.store.list_members(list_id)
        items: list[dict[str, Any]] = []
        for rid in ids[:MAX_MEMBERS]:
            if info.kind == "people":
                p = be.store.get_person(rid)
                if p:
                    items.append(be.person_dict(p))
            else:
                c = be.search.get_company(rid)
                if c:
                    items.append(_company_dict(c))
        return {
            "list_id": list_id,
            "name": info.name,
            "kind": info.kind,
            "total": len(ids),
            "items": items,
        }

    @tool(READ, "Export a list as CSV")
    def export_list(list_id: str) -> str:
        """The whole list as CSV text. Suppressed emails are blanked; cells that could run
        as spreadsheet formulas are neutralised."""
        info = _list_info(list_id)
        ids = be.store.list_members(list_id)
        buf = io.StringIO()
        w = csv.writer(buf)
        if info.kind == "people":
            w.writerow(
                [
                    "name",
                    "title",
                    "company_domain",
                    "email",
                    "email_status",
                    "confidence",
                    "source_urls",
                ]
            )
            for pid in ids:
                p = be.store.get_person(pid)
                if not p:
                    continue
                d = be.person_dict(p)
                best = be.store.best_email(pid)
                em = d["email"]
                status = "suppressed" if best and em is None else (em["status"] if em else "")
                w.writerow(
                    [
                        _csv_cell(v)
                        for v in (
                            p.full_name,
                            p.title,
                            p.company_domain,
                            em["address"] if em else "",
                            status,
                            em["confidence"] if em else "",
                            " | ".join(p.source_urls),
                        )
                    ]
                )
        else:
            w.writerow(
                [
                    "name",
                    "domain",
                    "country",
                    "city",
                    "industry",
                    "size_range",
                    "lei",
                    "wikidata_id",
                ]
            )
            for cid in ids:
                c = be.search.get_company(cid)
                if c:
                    w.writerow(
                        [
                            _csv_cell(v)
                            for v in (
                                c.name,
                                c.domain,
                                c.country,
                                c.city,
                                c.industry_label or c.industry,
                                c.size_range,
                                c.registry_ids.get("lei"),
                                c.registry_ids.get("wikidata"),
                            )
                        ]
                    )
        return buf.getvalue()

    @tool(WRITE, "Opt out an email or domain")
    def opt_out(email_or_domain: str) -> dict[str, Any]:
        """Add an email address or domain to the suppression list: it will never be
        returned or exported again."""
        value = email_or_domain.strip().lower()
        if not value:
            raise ToolError("Give an email address or a domain.")
        be.store.add_suppression(value)
        return {"suppressed": value}

    server._leadcommons_backend = be  # type: ignore[attr-defined]  # for tests
    return server


class BearerTokenMiddleware:
    """Pure ASGI middleware: HTTP requests need `Authorization: Bearer <token>`."""

    def __init__(self, app: Any, token: str) -> None:
        if not token:
            raise ValueError("a bearer token is required")
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        scheme, _, supplied = auth.partition(b" ")
        if scheme.lower() == b"bearer" and hmac.compare_digest(supplied.strip(), self._token):
            await self.app(scope, receive, send)
            return
        body = b'{"error":"unauthorized"}'
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b'Bearer realm="leadcommons"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def resolve_http_token(mode: str, host: str, token: str | None) -> str | None:
    """Token needed in server mode or on a non-loopback host; refuse to start without it."""
    token = (token or "").strip() or None
    if token is None and (mode == "server" or host not in LOOPBACK):
        raise ValueError(
            "the HTTP transport needs a bearer token here. Set LEADCOMMONS_MCP_TOKEN "
            "(for example: openssl rand -hex 32)."
        )
    return token


def build_http_app(server: MCPServer, token: str | None, host: str = "127.0.0.1") -> Any:
    """Streamable-HTTP ASGI app (endpoint /mcp), behind the bearer token when given."""
    app = server.streamable_http_app(host=host)
    return BearerTokenMiddleware(app, token) if token else app
