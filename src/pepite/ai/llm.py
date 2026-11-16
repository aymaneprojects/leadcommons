"""Optional LLM engine (OpenAI-compatible chat API, e.g. Ollama at http://localhost:11434/v1).

Enabled only when PEPITE_LLM_URL and PEPITE_LLM_MODEL are set. Only the query text
is ever sent. The reply is validated strictly; unknown values are dropped; any error returns
None so the caller falls back to the rules result.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx

from pepite.ai import lexicon as lx
from pepite.ai.rules import SENIORITY_LABELS, Kind, ParsedQuery, parse_rules
from pepite.models import SIZE_RANGES, Seniority
from pepite.packs.isic import SECTION_NAMES
from pepite.search import CompanyFilters, PeopleFilters

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10.0
MAX_TITLES, MAX_KEYWORDS, MAX_ITEM_LEN = 5, 6, 60

ISIC_LETTERS = tuple(sorted(SECTION_NAMES))
COUNTRY_CODES = tuple(sorted(lx.COUNTRY_NAMES))
SENIORITY_VALUES = tuple(s.value for s in Seniority)

SYSTEM_PROMPT = (
    "You convert a prospecting search request (any language) into JSON filters. "
    "Reply with ONE JSON object and nothing else, with exactly these keys:\n"
    '{"countries": [ISO 3166-1 alpha-2 codes], "size_ranges": [...], "industries": [...], '
    '"titles": [job titles], "seniorities": [...], "keywords": [free words]}\n'
    f"Allowed countries: {', '.join(COUNTRY_CODES)}.\n"
    f"Allowed size_ranges (employee counts): {', '.join(SIZE_RANGES)}.\n"
    f"Allowed industries (ISIC Rev.4 section letters): {', '.join(ISIC_LETTERS)}.\n"
    f"Allowed seniorities: {', '.join(SENIORITY_VALUES)}.\n"
    "Use [] for anything not stated. Never invent filters that the request does not imply."
)


def llm_config() -> tuple[str, str, str | None] | None:
    url = os.environ.get("PEPITE_LLM_URL", "").strip()
    model = os.environ.get("PEPITE_LLM_MODEL", "").strip()
    if not url or not model:
        return None
    return url.rstrip("/"), model, os.environ.get("PEPITE_LLM_API_KEY") or None


def _strings(value: Any, limit: int, max_len: int = MAX_ITEM_LEN) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and len(item.strip()) <= max_len:
            v = item.strip()
            if v not in out:
                out.append(v)
    return out[:limit]


def validate(payload: Any) -> dict[str, list[Any]] | None:
    """Strict validation of the model reply; None if it is not a JSON object."""
    if not isinstance(payload, dict):
        return None
    countries = [
        c.upper() for c in _strings(payload.get("countries"), 20, 2) if c.upper() in COUNTRY_CODES
    ]
    sizes = [s for s in _strings(payload.get("size_ranges"), 20) if s in SIZE_RANGES]
    industries = [
        s.upper() for s in _strings(payload.get("industries"), 20, 1) if s.upper() in ISIC_LETTERS
    ]
    sens = [Seniority(s) for s in _strings(payload.get("seniorities"), 20) if s in SENIORITY_VALUES]
    return {
        "countries": list(dict.fromkeys(countries)),
        "size_ranges": [s for s in SIZE_RANGES if s in sizes],
        "industries": list(dict.fromkeys(industries)),
        "seniorities": list(dict.fromkeys(sens)),
        "titles": _strings(payload.get("titles"), MAX_TITLES),
        "keywords": [k.lower() for k in _strings(payload.get("keywords"), MAX_KEYWORDS, 40)],
    }


def _extract_json(content: str) -> Any:
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    return json.loads(text)


def call_llm(text: str) -> dict[str, list[Any]] | None:
    cfg = llm_config()
    if cfg is None:
        return None
    base, model, key = cfg
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    try:
        resp = httpx.post(
            f"{base}/chat/completions", json=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return validate(_extract_json(content))
    except Exception as exc:  # network, HTTP, JSON, schema: always fall back to rules
        log.warning("LLM query parsing failed (%s); using rules", type(exc).__name__)
        return None


def merge(rules: ParsedQuery, llm: dict[str, list[Any]]) -> ParsedQuery:
    """Rules win per field; the LLM only fills fields the rules left empty."""
    explanation = list(rules.explanation)
    used = False
    c, p = rules.company, rules.people

    countries = c.countries or llm["countries"]
    sizes = c.size_ranges or llm["size_ranges"]
    industries = c.industries or llm["industries"]
    keywords = c.keywords or llm["keywords"]
    titles = p.titles or llm["titles"]
    seniorities = p.seniorities or llm["seniorities"]

    if not c.countries and countries:
        explanation += [f"(AI) Country: {lx.COUNTRY_NAMES.get(x, x)}" for x in countries]
        used = True
    if not c.size_ranges and sizes:
        explanation.append(f"(AI) Size: {', '.join(sizes)} employees")
        used = True
    if not c.industries and industries:
        explanation += [f"(AI) Industry: {SECTION_NAMES[x]} ({x})" for x in industries]
        used = True
    if not p.titles and titles:
        explanation += [f"(AI) Title: {t}" for t in titles]
        used = True
    if not p.seniorities and seniorities:
        explanation += [f"(AI) Seniority: {SENIORITY_LABELS[s]} ({s.value})" for s in seniorities]
        used = True
    if not c.keywords and keywords:
        explanation += [f"(AI) Keyword: {k}" for k in keywords]
        used = True
    if not used:
        return rules

    company = CompanyFilters(
        countries=list(countries),
        industries=list(industries),
        size_ranges=list(sizes),
        keywords=list(keywords),
    )
    people = PeopleFilters(
        titles=list(titles),
        seniorities=list(seniorities),
        company=CompanyFilters(
            countries=list(countries),
            industries=list(industries),
            size_ranges=list(sizes),
            keywords=list(keywords),
        ),
    )
    return ParsedQuery(
        company=company,
        people=people,
        explanation=explanation,
        unparsed=rules.unparsed if not (not c.keywords and keywords) else [],
        engine="llm",
    )


def parse_query(text: str, kind: Kind = "companies") -> ParsedQuery:
    """Parse a plain-language request into search filters (rules, optionally boosted by an LLM)."""
    rules = parse_rules(text, kind)
    if llm_config() is None:
        return rules
    llm = call_llm(text)
    return merge(rules, llm) if llm else rules
