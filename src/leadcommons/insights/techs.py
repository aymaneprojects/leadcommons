"""Technology detection: run the fingerprint rules over crawled pages, headers, cookies, DNS."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from selectolax.lexbor import LexborHTMLParser

from leadcommons.insights.model import Technology
from leadcommons.insights.techrules import RULES, Rule

_URL_ATTRS = (("script", "src"), ("link", "href"), ("img", "src"), ("iframe", "src"),
              ("source", "src"), ("embed", "src"))  # fmt: skip
_MAX_HTML = 1_500_000


def page_signals(html: str) -> tuple[list[tuple[str, str]], list[str]]:
    """(tag, url) pairs of embedded resources and "name: content" strings of <meta> tags."""
    tree = LexborHTMLParser(html[:_MAX_HTML])
    urls: list[tuple[str, str]] = []
    for tag, attr in _URL_ATTRS:
        for node in tree.css(tag):
            value = (node.attributes.get(attr) or "").strip()
            if value:
                urls.append((tag, value))
    metas = []
    for node in tree.css("meta"):
        name = node.attributes.get("name") or node.attributes.get("property") or ""
        content = node.attributes.get("content") or ""
        if name:
            metas.append(f"{name}: {content}")
    return urls, metas


def _clip(text: str, n: int = 140) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _first(rx: re.Pattern[str], items: Iterable[str]) -> str | None:
    for item in items:
        if rx.search(item):
            return item
    return None


def _match_rule(
    rule: Rule,
    urls: list[tuple[str, str]],
    metas: list[str],
    htmls: list[str],
    headers: Mapping[str, str],
    cookies: list[str],
    dns_records: Mapping[str, list[str]],
) -> tuple[str, str] | None:
    """Evidence (source, text) of the strongest signal that matches, or None."""
    if rule.src:
        for tag, url in urls:
            if rule.src.search(url):
                return "script" if tag == "script" else "html", f"{tag} {_attr(tag)}: {_clip(url)}"
    if rule.meta and (hit := _first(rule.meta, metas)):
        return "meta", f"meta {_clip(hit)}"
    for name, rx in rule.header:
        value = headers.get(name)
        if value is not None and (rx is None or rx.search(value)):
            return "header", f"header {name}: {_clip(value, 80)}" if value else f"header {name}"
    if rule.cookie and (hit := _first(rule.cookie, cookies)):
        return "cookie", f"cookie: {hit}"
    for kind, rx in rule.dns:
        if hit := _first(rx, dns_records.get(kind, [])):
            return "dns", f"{kind.upper()} record: {_clip(hit, 90)}"
    if rule.html:
        for html in htmls:
            m = rule.html.search(html)
            if m:
                return "html", f"page source: {_clip(m.group(0), 80)}"
    return None


def _attr(tag: str) -> str:
    return "href" if tag == "link" else "src"


def detect(
    html_pages: list[str],
    *,
    headers: Mapping[str, str] | None = None,
    cookies: list[str] | None = None,
    dns_records: Mapping[str, list[str]] | None = None,
) -> list[Technology]:
    """Technologies found, one entry per technology, sorted by category then name."""
    urls: list[tuple[str, str]] = []
    metas: list[str] = []
    for html in html_pages:
        u, m = page_signals(html)
        urls += u
        metas += m
    htmls = [h[:_MAX_HTML] for h in html_pages]
    low_headers = {k.lower(): v for k, v in (headers or {}).items()}
    found: dict[str, Technology] = {}
    for rule in RULES:
        hit = _match_rule(rule, urls, metas, htmls, low_headers, cookies or [], dns_records or {})
        if hit and rule.name not in found:
            found[rule.name] = Technology(rule.name, rule.category, hit[1], hit[0])
    return sorted(found.values(), key=lambda t: (t.category, t.name.lower()))
