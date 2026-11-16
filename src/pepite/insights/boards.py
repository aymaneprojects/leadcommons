"""Open positions from the public job-board APIs that applicant-tracking systems offer for
embedding (Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters).

The board is found from links on the company's own pages (careers page, footer, iframes).
Only these documented read-only endpoints are used; nothing is scraped from the boards.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from pepite.connectors._http import PoliteClient
from pepite.insights.model import JobPosting, Jobs

log = logging.getLogger(__name__)

MAX_POSTINGS = 500
MIN_INTERVAL = 0.5  # seconds between two API calls
MAX_BOARDS = 2
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
_REMOTE = re.compile(r"\bremote\b|\bhome[- ]?office\b|\btélétravail\b|\bteletrabajo\b", re.I)
_SKIP_SUB = {"www", "api", "careers", "app", "support", "help", "blog", "status"}


@dataclass(frozen=True)
class Board:
    ats: str
    token: str
    url: str
    region: str = ""  # "eu" for the EU data centres of Greenhouse and Lever


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def board_from_url(url: str) -> Board | None:
    """The ATS board a URL points to, or None."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    seg = _segments(parts.path)
    first = seg[0] if seg else ""
    if host.endswith("greenhouse.io") and host.split(".")[0] in {"boards", "job-boards"}:
        token = first
        if first == "embed":
            token = (parse_qs(parts.query).get("for") or [""])[0]
        if _TOKEN.match(token) and token not in {"embed", "v1"}:
            region = "eu" if ".eu." in host else ""
            return Board("greenhouse", token, f"https://{host}/{token}", region)
    elif host in {"jobs.lever.co", "jobs.eu.lever.co"} and _TOKEN.match(first):
        return Board("lever", first, f"https://{host}/{first}", "eu" if ".eu." in host else "")
    elif host == "jobs.ashbyhq.com" and _TOKEN.match(first):
        return Board("ashby", first, f"https://jobs.ashbyhq.com/{first}")
    elif host == "apply.workable.com" and _TOKEN.match(first) and first not in {"api", "j"}:
        return Board("workable", first, f"https://apply.workable.com/{first}/")
    elif host.endswith(".recruitee.com"):
        sub = host.removesuffix(".recruitee.com")
        if "." not in sub and sub not in _SKIP_SUB and _TOKEN.match(sub):
            return Board("recruitee", sub, f"https://{sub}.recruitee.com/")
    elif host in {"careers.smartrecruiters.com", "jobs.smartrecruiters.com"} and _TOKEN.match(
        first
    ):
        return Board("smartrecruiters", first, f"https://careers.smartrecruiters.com/{first}")
    return None


def find_boards(links: list[str]) -> list[Board]:
    """Distinct boards in order of first appearance."""
    out: dict[tuple[str, str], Board] = {}
    for link in links:
        b = board_from_url(link)
        if b and (b.ats, b.token.lower()) not in out:
            out[(b.ats, b.token.lower())] = b
    return list(out.values())


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return " ".join(value.split())
    return None


def _join(*parts: Any) -> str | None:
    bits = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
    return ", ".join(dict.fromkeys(bits)) or None


def _remote(flag: Any, *texts: str | None) -> bool | None:
    if isinstance(flag, bool):
        if flag:
            return True
    elif isinstance(flag, str) and flag.lower() == "remote":
        return True
    if any(t and _REMOTE.search(t) for t in texts):
        return True
    return False if flag is not None else None


def _ms_date(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _greenhouse(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in (data or {}).get("jobs", []):
        loc = _text((j.get("location") or {}).get("name"))
        depts = [d.get("name") for d in j.get("departments") or [] if d.get("name")]
        title = _text(j.get("title"))
        if title:
            out.append(
                JobPosting(
                    title,
                    "greenhouse",
                    j.get("absolute_url"),
                    depts[0] if depts else None,
                    loc,
                    _remote(None, loc, title),
                    _text(j.get("first_published") or j.get("updated_at")),
                )
            )
    return out


def _lever(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        loc = _text(cat.get("location")) or _text(", ".join(cat.get("allLocations") or []))
        title = _text(j.get("text"))
        if title:
            out.append(
                JobPosting(
                    title,
                    "lever",
                    j.get("hostedUrl"),
                    _text(cat.get("department")) or _text(cat.get("team")),
                    loc,
                    _remote(j.get("workplaceType"), loc, title),
                    _ms_date(j.get("createdAt")),
                )
            )
    return out


def _ashby(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in (data or {}).get("jobs", []):
        title = _text(j.get("title"))
        if title and j.get("isListed", True):
            loc = _text(j.get("location"))
            out.append(
                JobPosting(
                    title,
                    "ashby",
                    j.get("jobUrl"),
                    _text(j.get("department")) or _text(j.get("team")),
                    loc,
                    _remote(j.get("isRemote"), loc),
                    _text(j.get("publishedAt")),
                )
            )
    return out


def _workable(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in (data or {}).get("jobs", []):
        title = _text(j.get("title"))
        if title:
            loc = _join(j.get("city"), j.get("state"), j.get("country"))
            out.append(
                JobPosting(
                    title,
                    "workable",
                    j.get("url") or j.get("shortlink"),
                    _text(j.get("department")),
                    loc,
                    _remote(j.get("telecommuting"), loc),
                    _text(j.get("published_on") or j.get("created_at")),
                )
            )
    return out


def _recruitee(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in (data or {}).get("offers", []):
        title = _text(j.get("title"))
        if title and j.get("status", "published") == "published":
            loc = _text(j.get("location")) or _join(j.get("city"), j.get("country"))
            out.append(
                JobPosting(
                    title,
                    "recruitee",
                    j.get("careers_url"),
                    _text(j.get("department")),
                    loc,
                    _remote(j.get("remote"), loc, title),
                    _text(j.get("published_at") or j.get("created_at")),
                )
            )
    return out


def _smartrecruiters(data: Any, board: Board) -> list[JobPosting]:
    out = []
    for j in (data or {}).get("content", []):
        title = _text(j.get("name"))
        if title:
            loc = j.get("location") or {}
            full = _text(loc.get("fullLocation")) or _join(loc.get("city"), loc.get("country"))
            out.append(
                JobPosting(
                    title,
                    "smartrecruiters",
                    f"https://jobs.smartrecruiters.com/{board.token}/{j.get('id')}"
                    if j.get("id")
                    else None,
                    _text((j.get("department") or {}).get("label")),
                    full,
                    _remote(loc.get("remote"), full),
                    _text(j.get("releasedDate")),
                )
            )
    return out


def _api(
    board: Board,
) -> tuple[str, dict[str, str | int], Callable[[Any, Board], list[JobPosting]]]:
    t = board.token
    if board.ats == "greenhouse":
        return (
            f"https://boards-api.greenhouse.io/v1/boards/{t}/jobs",
            {"content": "true"},
            _greenhouse,
        )
    if board.ats == "lever":
        host = "api.eu.lever.co" if board.region == "eu" else "api.lever.co"
        return f"https://{host}/v0/postings/{t}", {"mode": "json"}, _lever
    if board.ats == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{t}", {}, _ashby
    if board.ats == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{t}", {}, _workable
    if board.ats == "recruitee":
        return f"https://{t}.recruitee.com/api/offers/", {}, _recruitee
    return (
        f"https://api.smartrecruiters.com/v1/companies/{t}/postings",
        {"limit": 100},
        _smartrecruiters,
    )


def fetch_board(board: Board, client: PoliteClient) -> list[JobPosting]:
    """Postings of one board; raises httpx errors when the API cannot be read."""
    url, params, parse = _api(board)
    data = client.get_json(url, params, "application/json")
    return parse(data, board)[:MAX_POSTINGS]


def collect_jobs(links: list[str], client: PoliteClient | None = None) -> Jobs:
    """Find the ATS board among `links` and read its open positions."""
    boards = find_boards(links)
    jobs = Jobs()
    if not boards:
        return jobs
    client = client or PoliteClient(
        min_interval=MIN_INTERVAL, max_retries=1, timeout=15.0, max_wait=5.0, max_total_wait=10.0
    )
    for board in boards[:MAX_BOARDS]:
        jobs.ats, jobs.board, jobs.board_url = board.ats, board.token, board.url
        try:
            jobs.postings = fetch_board(board, client)
            jobs.error = None
            return jobs
        except (httpx.HTTPError, ValueError) as exc:
            log.info("job board %s/%s unreadable: %s", board.ats, board.token, exc)
            jobs.error = f"The {board.ats} board '{board.token}' could not be read."
    return jobs
