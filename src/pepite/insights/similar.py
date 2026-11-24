"""Lookalikes: companies like a given company (industry, size, country) and similar people."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pepite.enrich import titles
from pepite.models import SIZE_RANGES, Company, Person
from pepite.search import CompanyFilters

SCAN_CAP = 2000  # candidates read when the search backend has no lookalike query of its own
_SIZE_INDEX = {s: i for i, s in enumerate(SIZE_RANGES)}


def size_distance(a: str | None, b: str | None) -> int | None:
    """Buckets between two size ranges (0 same, 1 neighbouring); None when unknown."""
    if a not in _SIZE_INDEX or b not in _SIZE_INDEX:
        return None
    return abs(_SIZE_INDEX[a] - _SIZE_INDEX[b])


def rank_lookalikes(
    company: Company, candidates: Iterable[Company], limit: int = 25
) -> list[Company]:
    """Same ISIC industry and same or neighbouring size bucket; same country first, then closer
    size, then companies with a known domain, then name. The company itself is excluded."""
    if not company.industry:
        return []
    scored: list[tuple[tuple[Any, ...], Company]] = []
    for c in candidates:
        if c.id == company.id or c.industry != company.industry:
            continue
        dist = size_distance(company.size_range, c.size_range) if company.size_range else 0
        if dist is None or dist > 1:
            continue
        same_country = bool(company.country) and c.country == company.country
        scored.append(((0 if same_country else 1, dist, c.domain is None, c.name.lower(), c.id), c))
    scored.sort(key=lambda t: t[0])
    return [c for _, c in scored[: max(0, int(limit))]]


def _scan(search: Any, company: Company) -> list[Company]:
    flt = CompanyFilters(industries=[company.industry] if company.industry else [])
    out: list[Company] = []
    page = 1
    while len(out) < SCAN_CAP:
        res = search.search_companies(flt, page=page, per_page=500)
        out.extend(res.items)
        if not res.items or page * res.per_page >= res.total:
            break
        page += 1
    return out


def lookalikes(search: Any, company: Company, limit: int = 25) -> list[Company]:
    """Companies similar to `company` in the packs (industry, size, country)."""
    if not company.industry:
        return []
    helper = getattr(search, "lookalike_companies", None)
    if callable(helper):
        return helper(company, limit)
    return rank_lookalikes(company, _scan(search, company), limit)


# -- people ---------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset({"and", "the", "for", "und", "der", "die", "das", "des", "les", "del", "de"})


def _title_words(title: str | None) -> set[str]:
    return {w for w in _WORD.findall(titles.fold(title or "")) if w not in _STOP}


def person_similarity(person: Person, other: Person) -> int:
    """Higher is more alike: same seniority 3, same department 2, +1 per shared title word."""
    score = 0
    sen = person.seniority or titles.map_seniority(person.title)
    if sen and sen == (other.seniority or titles.map_seniority(other.title)):
        score += 3
    dep = person.department or titles.guess_department(person.title)
    if dep and dep == (other.department or titles.guess_department(other.title)):
        score += 2
    return score + len(_title_words(person.title) & _title_words(other.title))


def similar_people(candidates: Iterable[Person], person: Person, limit: int = 25) -> list[Person]:
    """People with a similar title and seniority, most alike first (the person is excluded)."""
    scored = [
        (-s, p.full_name.lower(), p)
        for p in candidates
        if p.id != person.id and (s := person_similarity(person, p)) > 0
    ]
    scored.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in scored[: max(0, int(limit))]]
