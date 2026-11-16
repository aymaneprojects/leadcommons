"""Employee counts, revenue, listings and ownership from Wikidata (CC0, www.wikidata.org).

Only what Wikidata holds for the company is reported: P1128 employees (with point in time),
P2139 total revenue (with currency and year), P414 stock exchange with P249 ticker, P127 owner.
There is no funding-round data in Wikidata, so the Funding tab never invents any.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from pepite.connectors._http import PoliteClient
from pepite.insights.model import EmployeePoint, Listing, RevenuePoint, Trends
from pepite.packs.domains import normalize_domain

log = logging.getLogger(__name__)

ENTITY_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
API_URL = "https://www.wikidata.org/w/api.php"
_QID = re.compile(r"^Q\d+$")
MIN_INTERVAL = 0.3  # seconds between two API calls


def _claims(entity: dict[str, Any], prop: str) -> list[dict[str, Any]]:
    out = [c for c in entity.get("claims", {}).get(prop, []) if c.get("rank") != "deprecated"]
    preferred = [c for c in out if c.get("rank") == "preferred"]
    return out if not preferred or prop in {"P1128", "P2139", "P414"} else preferred


def _value(claim: dict[str, Any]) -> Any:
    snak = claim.get("mainsnak", {})
    return (snak.get("datavalue") or {}).get("value") if snak.get("snaktype") == "value" else None


def _qualifier(claim: dict[str, Any], prop: str) -> Any:
    for q in claim.get("qualifiers", {}).get(prop, []):
        if q.get("snaktype") == "value":
            return (q.get("datavalue") or {}).get("value")
    return None


def _date(time_value: Any) -> str:
    """ "+2022-12-31T00:00:00Z" -> "2022-12-31" (precision-aware: year-only stays a year)."""
    if not isinstance(time_value, dict):
        return ""
    m = re.match(r"^[+-](\d{4})-(\d{2})-(\d{2})", time_value.get("time", ""))
    if not m:
        return ""
    precision = int(time_value.get("precision", 11))
    if precision <= 9:
        return m.group(1)
    if precision == 10:
        return f"{m.group(1)}-{m.group(2)}"
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def _amount(value: Any) -> float | None:
    try:
        return float(value["amount"])
    except (KeyError, TypeError, ValueError):
        return None


def _qid(value: Any) -> str | None:
    qid = value.get("id") if isinstance(value, dict) else None
    return qid if isinstance(qid, str) and _QID.match(qid) else None


def parse_entity(entity: dict[str, Any], labels: dict[str, str] | None = None) -> Trends:
    """Trends from one Wikidata entity document; `labels` maps Q-ids to English labels."""
    labels = labels or {}
    qid = entity.get("id")
    trends = Trends(
        wikidata_id=qid, wikidata_url=f"https://www.wikidata.org/wiki/{qid}" if qid else None
    )
    seen: set[tuple[str, int]] = set()
    for c in _claims(entity, "P1128"):
        amount = _amount(_value(c))
        if amount is None or amount < 0:
            continue
        point = EmployeePoint(_date(_qualifier(c, "P585")), int(amount))
        if (point.date, point.count) not in seen:
            seen.add((point.date, point.count))
            trends.employees.append(point)
    trends.employees.sort(key=lambda p: p.date)
    for c in _claims(entity, "P2139"):
        value = _value(c)
        amount = _amount(value)
        if amount is None:
            continue
        unit = (value.get("unit") or "").rsplit("/", 1)[-1]
        when = _date(_qualifier(c, "P585"))
        trends.revenue.append(
            RevenuePoint(int(when[:4]) if when[:4].isdigit() else None, amount, labels.get(unit))
        )
    trends.revenue.sort(key=lambda r: r.year or 0)
    tickers = [v for c in _claims(entity, "P249") if isinstance(v := _value(c), str)]
    for c in _claims(entity, "P414"):
        exchange = labels.get(_qid(_value(c)) or "")
        ticker = _qualifier(c, "P249")
        if exchange:
            trends.listings.append(Listing(exchange, ticker if isinstance(ticker, str) else None))
    if not trends.listings and tickers:
        trends.listings.append(Listing("Stock exchange (not named)", tickers[0]))
    elif trends.listings and tickers and not any(x.ticker for x in trends.listings):
        trends.listings[0].ticker = tickers[0]
    for c in _claims(entity, "P127")[:3]:
        label = labels.get(_qid(_value(c)) or "")
        if label:
            trends.owned_by.append(label)
    return trends


def _label_ids(entity: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for c in _claims(entity, "P2139"):
        unit = ((_value(c) or {}).get("unit") or "").rsplit("/", 1)[-1] if _value(c) else ""
        ids.append(unit)
    for c in _claims(entity, "P414") + _claims(entity, "P127")[:3]:
        ids.append(_qid(_value(c)) or "")
    return list(dict.fromkeys(i for i in ids if _QID.match(i)))


def _fetch_entity(client: PoliteClient, qid: str) -> dict[str, Any] | None:
    data = client.get_json(ENTITY_URL.format(qid=qid), {}, "application/json")
    return (data.get("entities") or {}).get(qid)


def _fetch_labels(client: PoliteClient, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    data = client.get_json(
        API_URL,
        {"action": "wbgetentities", "ids": "|".join(ids[:50]), "props": "labels",
         "languages": "en", "format": "json"},
        "application/json",
    )  # fmt: skip
    return {
        k: v["labels"]["en"]["value"]
        for k, v in (data.get("entities") or {}).items()
        if v.get("labels", {}).get("en")
    }


def _site_domain(entity: dict[str, Any]) -> str | None:
    for c in _claims(entity, "P856"):
        v = _value(c)
        if isinstance(v, str) and (d := normalize_domain(v)):
            return d
    return None


def find_entity(
    client: PoliteClient, name: str, domain: str | None, qid: str | None
) -> dict[str, Any] | None:
    """The company's Wikidata entity: by known Q-id, else by name AND matching website."""
    if qid and _QID.match(qid):
        return _fetch_entity(client, qid)
    if not domain or not name:
        return None
    found = client.get_json(
        API_URL,
        {"action": "wbsearchentities", "search": name, "language": "en", "type": "item",
         "limit": 5, "format": "json"},
        "application/json",
    )  # fmt: skip
    for hit in found.get("search", [])[:3]:
        cand = hit.get("id")
        if isinstance(cand, str) and _QID.match(cand):
            entity = _fetch_entity(client, cand)
            if entity and _site_domain(entity) == domain:
                return entity
    return None


def fetch_trends(
    name: str,
    domain: str | None,
    qid: str | None = None,
    client: PoliteClient | None = None,
) -> Trends | None:
    """Trends for a company, or None when Wikidata has no matching entity. Raises on HTTP errors."""
    client = client or PoliteClient(
        min_interval=MIN_INTERVAL, max_retries=1, timeout=20.0, max_wait=5.0, max_total_wait=10.0
    )
    entity = find_entity(client, name, normalize_domain(domain) if domain else None, qid)
    if not entity:
        return None
    try:
        labels = _fetch_labels(client, _label_ids(entity))
    except httpx.HTTPError:
        labels = {}
    return parse_entity(entity, labels)
