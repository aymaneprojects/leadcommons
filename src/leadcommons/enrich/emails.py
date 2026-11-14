"""Collect email addresses a company publishes on its own pages.

Sources: mailto links, visible text, JSON-LD and Cloudflare-protected addresses. Simple
obfuscations are undone ("name [at] domain [dot] com", "(at)", "name at domain dot com",
"&#64;"). Only addresses on the domain or its subdomains are kept; the domain must end at a
word boundary, so file names like "logo@2x.png" or "x@domain.com.png" never match.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from urllib.parse import unquote

from leadcommons.enrich.crawl import Page

_OPEN, _CLOSE = r"\s*[\[\(\{<]\s*", r"\s*[\]\)\}>]\s*"
_AT = rf"(?:@|{_OPEN}(?:at|@|arobase|ät|chez){_CLOSE})"
_OBF_DOT = rf"(?:{_OPEN}(?:dot|point|punkt|punto|\.){_CLOSE}|\s+(?:dot|point|punkt|punto)\s+)"
_ANY_DOT = rf"(?:\.|{_OBF_DOT})"
_LOCAL = r"(?<![\w.%+-])(?P<local>[a-z0-9](?:[a-z0-9._%+-]*[a-z0-9_%+-])?)"
_END = r"(?![a-z0-9-]|\.[a-z0-9])"


def _domain_part(domain: str, dot: str) -> str:
    labels = dot.join(re.escape(label) for label in domain.split("."))
    return rf"(?P<domain>(?:[a-z0-9-]+{dot})*{labels}){_END}"


def _patterns(domain: str) -> tuple[re.Pattern[str], ...]:
    """Plain or bracket-obfuscated addresses, and fully spelled-out "name at domain dot com"
    (the spelled form must also spell the dots, so "visit us at acme.com" is ignored)."""
    return (
        re.compile(_LOCAL + _AT + _domain_part(domain, _ANY_DOT), re.IGNORECASE),
        re.compile(_LOCAL + r"\s+at\s+" + _domain_part(domain, _OBF_DOT), re.IGNORECASE),
    )


def _cf_decode(encoded: str) -> str | None:
    """Decode a Cloudflare email-protection hex string (first byte is the XOR key)."""
    try:
        data = bytes.fromhex(encoded)
    except ValueError:
        return None
    if len(data) < 2:
        return None
    return bytes(b ^ data[0] for b in data[1:]).decode("utf-8", errors="replace")


def _texts(page: Page) -> Iterator[str]:
    tree = page.tree()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            yield unquote(href[7:].split("?")[0]).replace(",", " ")
        elif "email-protection#" in href:
            yield _cf_decode(href.rsplit("#", 1)[1]) or ""
    for node in tree.css("[data-cfemail]"):
        yield _cf_decode(node.attributes.get("data-cfemail") or "") or ""
    for script in tree.css('script[type="application/ld+json"]'):
        yield script.text() or ""
    yield page.text


def extract_emails(pages: Sequence[Page], domain: str) -> list[str]:
    """Unique lowercase addresses on `domain` (or a subdomain), in order of appearance."""
    patterns = _patterns(domain)
    found: list[str] = []
    for page in pages:
        for text in _texts(page):
            for pattern in patterns:
                for m in pattern.finditer(text):
                    host = re.sub(_OBF_DOT, ".", m.group("domain"), flags=re.IGNORECASE)
                    address = f"{m.group('local')}@{host}".lower()
                    if address not in found:
                        found.append(address)
    return found
