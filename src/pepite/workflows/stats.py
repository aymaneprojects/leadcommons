# ruff: noqa: E501
"""Real numbers for the Analytics page, read straight from the local database.

Every figure is a COUNT or SUM over the stores' own tables; a table that does not exist (a
feature not installed) simply yields None and the page shows "not available". Nothing is
estimated or invented. Owner: tools agent.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from markupsafe import Markup, escape
from sqlalchemy import Engine, inspect, text

from pepite.models import utcnow

log = logging.getLogger(__name__)

RANGES = {"7": 7, "30": 30, "90": 90, "all": 0}
RANGE_LABELS = {"7": "Last 7 days", "30": "Last 30 days", "90": "Last 90 days", "all": "All time"}


def parse_range(value: str | None) -> str:
    return value if value in RANGES else "30"


def since_for(rng: str, now: datetime | None = None) -> datetime | None:
    days = RANGES.get(rng, 30)
    if not days:
        return None
    return ((now or utcnow()) - timedelta(days=days)).replace(tzinfo=None)


class Db:
    """Tiny read helper: tolerant of missing tables and columns."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        try:
            self.tables = set(inspect(engine).get_table_names())
        except Exception:
            self.tables = set()

    def has(self, *names: str) -> bool:
        return all(n in self.tables for n in names)

    def rows(self, sql: str, **params: Any) -> list[Any]:
        try:
            with self.engine.connect() as conn:
                return list(conn.execute(text(sql), params).all())
        except Exception:
            log.debug("analytics query failed: %s", sql, exc_info=True)
            return []

    def one(self, sql: str, **params: Any) -> Any | None:
        rows = self.rows(sql, **params)
        return rows[0][0] if rows else None

    def count(self, table: str, where: str = "", **params: Any) -> int | None:
        if not self.has(table):
            return None
        val = self.one(
            f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else ""), **params
        )
        return int(val) if val is not None else None


def _rng(col: str, since: datetime | None) -> str:
    return f"{col} >= :since" if since else "1=1"


def _day(value: Any) -> str:
    return str(value)[:10]


def daily_series(
    db: Db, table: str, col: str, since: datetime | None, extra: str = ""
) -> list[tuple[str, int]]:
    """Rows per calendar day over the range (zero days filled in; last 90 days for all time)."""
    if not db.has(table):
        return []
    start = since or (utcnow() - timedelta(days=89)).replace(tzinfo=None)
    where = f"{col} >= :since" + (f" AND {extra}" if extra else "")
    got = {
        _day(r[0]): int(r[1])
        for r in db.rows(
            f"SELECT substr(CAST({col} AS TEXT), 1, 10) AS d, COUNT(*) FROM {table} "
            f"WHERE {where} GROUP BY d",
            since=start,
        )
    }
    days = (utcnow().replace(tzinfo=None).date() - start.date()).days
    return [
        (
            (start.date() + timedelta(days=i)).isoformat(),
            got.get((start.date() + timedelta(days=i)).isoformat(), 0),
        )
        for i in range(days + 1)
    ]


def collect(engine: Engine, rng: str = "30", now: datetime | None = None) -> dict[str, Any]:
    """All Analytics figures for a date range."""
    db = Db(engine)
    since = since_for(rng, now)
    p: dict[str, Any] = {"since": since} if since else {}
    out: dict[str, Any] = {"range": rng, "range_label": RANGE_LABELS[rng]}

    # ---- prospecting
    out["people_found"] = db.count("people", _rng("first_seen", since), **p)
    out["people_total"] = db.count("people")
    out["people_series"] = daily_series(db, "people", "first_seen", since)
    out["lists_total"] = db.count("lists")
    out["lists_new"] = db.count("lists", _rng("created_at", since), **p)
    out["companies_saved"] = db.count("companies", _rng("saved_at", since), **p)
    out["companies_saved_total"] = db.count("companies")
    out["searches_saved"] = db.count("saved_searches", _rng("created_at", since), **p)
    out["searches_total"] = db.count("saved_searches")

    statuses: dict[str, int] = {}
    if db.has("emails"):
        for status, n in db.rows(
            "SELECT status, COUNT(*) FROM emails WHERE "
            + _rng("checked_at", since)
            + " GROUP BY status",
            **p,
        ):
            statuses[str(status)] = int(n)
    out["email_status"] = statuses if db.has("emails") else None
    out["emails_found"] = sum(statuses.values()) if db.has("emails") else None

    # ---- engage
    seq: dict[str, Any] | None = None
    if db.has("engage_messages"):
        sent = db.count(
            "engage_messages",
            "direction = 'out' AND status IN ('sent', 'bounced') AND " + _rng("sent_at", since),
            **p,
        )
        bounced = db.count(
            "engage_messages",
            "direction = 'out' AND status = 'bounced' AND " + _rng("sent_at", since),
            **p,
        )
        contacted = db.one(
            "SELECT COUNT(DISTINCT person_id) FROM engage_messages WHERE direction = 'out' AND "
            "status IN ('sent', 'bounced') AND " + _rng("sent_at", since),
            **p,
        )
        replied = unsub = None
        if db.has("engage_enrollments"):
            replied = db.count(
                "engage_enrollments", "status = 'replied' AND " + _rng("updated_at", since), **p
            )
            unsub = db.count(
                "engage_enrollments",
                "status = 'unsubscribed' AND " + _rng("updated_at", since),
                **p,
            )
        contacted = int(contacted or 0)
        sent = sent or 0
        seq = {
            "sent": sent,
            "contacted": contacted,
            "replied": replied,
            "bounced": bounced,
            "unsubscribed": unsub,
            "reply_rate": _pct(replied, contacted),
            "bounce_rate": _pct(bounced, sent),
            "unsub_rate": _pct(unsub, contacted),
            "series": daily_series(
                db,
                "engage_messages",
                "sent_at",
                since,
                "direction = 'out' AND status IN ('sent','bounced')",
            ),
        }
    out["sequences"] = seq
    out["sequences_total"] = db.count("engage_sequences")
    out["sequences_active"] = db.count("engage_sequences", "status = 'active'")
    if db.has("engage_tasks"):
        out["tasks_done"] = db.count(
            "engage_tasks", "completed_at IS NOT NULL AND " + _rng("completed_at", since), **p
        )
        out["tasks_open"] = db.count("engage_tasks", "completed_at IS NULL AND status = 'open'")
    else:
        out["tasks_done"] = out["tasks_open"] = None

    # ---- pipeline
    deals: list[dict[str, Any]] | None = None
    if db.has("deals", "deal_stages"):
        deals = []
        for sid, name, kind, n, value in db.rows(
            "SELECT s.id, s.name, s.kind, COUNT(d.id), COALESCE(SUM(d.amount), 0) FROM deal_stages s "
            "LEFT JOIN deals d ON d.stage_id = s.id WHERE s.archived = :no OR d.id IS NOT NULL "
            "GROUP BY s.id, s.name, s.kind, s.position ORDER BY s.position",
            no=False,
        ):
            deals.append(
                {"id": sid, "name": name, "kind": kind, "count": int(n), "value": float(value or 0)}
            )
    out["deals"] = deals
    tone = {"won": "ok", "lost": "red"}
    out["deals_bars"] = [(x["name"], x["count"], tone.get(x["kind"], "")) for x in deals or []]
    out["deals_value_bars"] = [
        (x["name"], x["value"], tone.get(x["kind"], "")) for x in deals or []
    ]
    out["deals_total"] = sum(d["count"] for d in deals) if deals is not None else None
    out["deals_value"] = (
        sum(d["value"] for d in deals if d["kind"] != "lost") if deals is not None else None
    )
    out["deals_won_value"] = (
        sum(d["value"] for d in deals if d["kind"] == "won") if deals is not None else None
    )
    out["deals_new"] = db.count("deals", _rng("created_at", since), **p)
    out["currency"] = (
        db.one("SELECT currency FROM deals GROUP BY currency ORDER BY COUNT(*) DESC LIMIT 1")
        or "EUR"
    )
    out["meetings"] = db.count("meetings", _rng("start", since), **p)
    out["meetings_held"] = db.count("meetings", "status = 'held' AND " + _rng("start", since), **p)
    out["meetings_upcoming"] = db.count(
        "meetings", "start >= :now AND status = 'scheduled'", now=utcnow().replace(tzinfo=None)
    )
    out["calls"] = db.count("calls", _rng("created_at", since), **p)

    # ---- automation
    out["workflow_runs"] = db.count("workflow_runs", _rng("started_at", since), **p)
    out["workflow_failed"] = db.count(
        "workflow_runs", "status IN ('failed', 'partial') AND " + _rng("started_at", since), **p
    )
    out["workflows_enabled"] = db.count("workflows", "enabled = :yes", yes=True)
    return out


def _pct(part: int | None, whole: int | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 1)


# ---------------------------------------------------------------------- inline SVG charts

_W, _H = 640, 160


def svg_line(series: list[tuple[str, int]], label: str) -> Markup:
    """Area line chart of one daily series. Empty or all-zero series draw a flat baseline."""
    if not series:
        return Markup("")
    n = len(series)
    top = max(max(v for _, v in series), 1)
    pad_l, pad_r, pad_t, pad_b = 34, 8, 10, 22
    iw, ih = _W - pad_l - pad_r, _H - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (iw * i / (n - 1) if n > 1 else iw / 2)

    def y(v: int) -> float:
        return pad_t + ih - ih * v / top

    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, (_, v) in enumerate(series))
    area = f"{x(0):.1f},{pad_t + ih:.1f} {pts} {x(n - 1):.1f},{pad_t + ih:.1f}"
    ticks = []
    for frac in (0, 0.5, 1):
        v = round(top * frac)
        ty = y(v)
        ticks.append(
            f'<line x1="{pad_l}" x2="{_W - pad_r}" y1="{ty:.1f}" y2="{ty:.1f}" class="gl"/>'
            f'<text x="{pad_l - 6}" y="{ty + 4:.1f}" text-anchor="end">{v}</text>'
        )
    labels = []
    for i in sorted({0, n // 2, n - 1}):
        labels.append(
            f'<text x="{x(i):.1f}" y="{_H - 6}" text-anchor="middle">{escape(series[i][0][5:])}</text>'
        )
    total = sum(v for _, v in series)
    dots = "".join(
        f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.4" class="dot"><title>{escape(d)}: {v}</title></circle>'
        for i, (d, v) in enumerate(series)
        if n <= 31 and v
    )
    return Markup(
        f'<svg class="chart" viewBox="0 0 {_W} {_H}" role="img" aria-label="{escape(label)}, '
        f'{total} in total">{"".join(ticks)}<polygon points="{area}" class="area"/>'
        f'<polyline points="{pts}" class="line"/>{dots}{"".join(labels)}</svg>'
    )


def svg_bars(items: list[tuple[str, float, str]], label: str, *, money: str = "") -> Markup:
    """Horizontal bars: (label, value, tone) where tone is ok | amber | red | '' (orange)."""
    if not items:
        return Markup("")
    top = max(max(v for _, v, _ in items), 1)
    row_h, label_w, pad_r = 28, 140, 70
    height = row_h * len(items) + 4
    iw = _W - label_w - pad_r
    rows = []
    for i, (name, v, tone) in enumerate(items):
        yy = i * row_h + 2
        w = max(iw * v / top, 2 if v else 0)
        shown = f"{money}{v:,.0f}" if money else f"{int(v):,}"
        rows.append(
            f'<text x="0" y="{yy + 17}" class="lab">{escape(name[:22])}</text>'
            f'<rect x="{label_w}" y="{yy + 4}" width="{iw}" height="14" rx="4" class="track"/>'
            f'<rect x="{label_w}" y="{yy + 4}" width="{w:.1f}" height="14" rx="4" class="bar {tone}">'
            f"<title>{escape(name)}: {shown}</title></rect>"
            f'<text x="{label_w + iw + 8}" y="{yy + 17}" class="val">{shown}</text>'
        )
    return Markup(
        f'<svg class="chart bars" viewBox="0 0 {_W} {height}" role="img" aria-label="{escape(label)}">'
        f"{''.join(rows)}</svg>"
    )
