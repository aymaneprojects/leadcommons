"""Business contact details from a company's own public pages.

Kept: business phone numbers (tel: links, JSON-LD telephone), generic role mailboxes such as
contact@ or info@, the postal address from JSON-LD and the company's own social profiles.
Never kept: personal mailboxes, numbers inside people/team cards, personal social profiles.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from typing import Any
from urllib.parse import unquote, urlsplit

from leadcommons.enrich.crawl import Page, in_domain
from leadcommons.insights.model import Contact, Phone

# country -> (calling code, national numbers start with a trunk "0" that is dropped)
_CALLING: dict[str, tuple[str, bool]] = {
    "FR": ("33", True), "DE": ("49", True), "GB": ("44", True), "US": ("1", False),
    "CA": ("1", False), "ES": ("34", False), "IT": ("39", False), "NL": ("31", True),
    "BE": ("32", True), "CH": ("41", True), "AT": ("43", True), "PT": ("351", False),
    "IE": ("353", True), "LU": ("352", False), "DK": ("45", False), "SE": ("46", True),
    "NO": ("47", False), "FI": ("358", True), "PL": ("48", False), "CZ": ("420", False),
    "SK": ("421", True), "SI": ("386", True), "HR": ("385", True), "BG": ("359", True),
    "RO": ("40", True), "HU": ("36", False), "GR": ("30", False), "EE": ("372", False),
    "LV": ("371", False), "LT": ("370", False), "AU": ("61", True), "NZ": ("64", True),
    "IN": ("91", True), "JP": ("81", True), "KR": ("82", True), "CN": ("86", False),
    "HK": ("852", False), "SG": ("65", False), "AE": ("971", True), "IL": ("972", True),
    "TR": ("90", True), "ZA": ("27", True), "MA": ("212", True), "DZ": ("213", True),
    "TN": ("216", False), "SN": ("221", False), "BR": ("55", True), "MX": ("52", False),
    "CL": ("56", False), "CO": ("57", False),
}  # fmt: skip

GENERIC_LOCALS = frozenset(
    "contact info hello hi hola bonjour hallo kontakt contacto contatto contatti mail office "
    "sales vente ventes commercial support service customerservice helpdesk help enquiries "
    "enquiry inquiries inquiry admin administration accounts accounting billing finance legal "
    "privacy dpo rgpd press presse media pr marketing communication communications team "
    "hr jobs careers career recruitment recrutement bewerbung karriere reception booking "
    "reservations reservation orders order shop partners partnership general welcome "
    "secretariat bureau empresa ventas atencion".split()
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PERSON_HINT = re.compile(
    r"team|member|staff|person|employee|people|author|bio|profile|speaker", re.I
)
_ORG_SUFFIX = ("Organization", "Corporation", "Business", "Store", "Restaurant", "Hotel", "Company")
_SOCIAL_SKIP = {
    "share", "sharer", "sharer.php", "intent", "home", "i", "login", "signup", "dialog", "tr",
    "plugins", "hashtag", "search", "explore", "watch", "embed", "p", "reel", "status", "pages",
}  # fmt: skip


def to_e164(raw: str, country: str | None = None) -> str | None:
    """Normalise a phone number to E.164, or None when the country cannot be determined."""
    text = re.split(r"(?i)\b(?:ext|extension|poste|x)\b\.?", raw, maxsplit=1)[0]
    text = re.sub(r"\(\s*0\s*\)", "", text)
    plus = text.strip().startswith("+")
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if plus or digits.startswith("00"):
        digits = digits[2:] if not plus and digits.startswith("00") else digits
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    spec = _CALLING.get((country or "").upper())
    if spec is None:
        return None
    code, trunk = spec
    if trunk and digits.startswith("0"):
        digits = digits[1:]
    if code == "1" and len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if code == "1" and len(digits) != 10:
        return None
    full = code + digits
    return f"+{full}" if 8 <= len(full) <= 15 else None


def _ld_nodes(page: Page) -> Iterator[dict[str, Any]]:
    for node in page.tree().css('script[type="application/ld+json"]'):
        raw = (node.text() or "").strip()
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [data]
        while stack:
            cur = stack.pop()
            if isinstance(cur, list):
                stack.extend(cur)
            elif isinstance(cur, dict):
                yield cur
                stack.extend(v for v in cur.values() if isinstance(v, dict | list))


def _types(node: dict[str, Any]) -> list[str]:
    t = node.get("@type")
    return [x for x in ([t] if isinstance(t, str) else t or []) if isinstance(x, str)]


def _is_org(node: dict[str, Any]) -> bool:
    types = _types(node)
    return bool(types) and "Person" not in types and any(t.endswith(_ORG_SUFFIX) for t in types)


def _in_person_card(node: Any) -> bool:
    cur = node
    for _ in range(4):
        cur = cur.parent
        if cur is None:
            return False
        attrs = cur.attributes
        if _PERSON_HINT.search(f"{attrs.get('class') or ''} {attrs.get('id') or ''}"):
            return True
        if attrs.get("itemtype", "") and attrs["itemtype"].rstrip("/").endswith("Person"):
            return True
    return False


def social_profile(url: str) -> tuple[str, str] | None:
    """(network, normalised URL) when `url` is a company page of a social network."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    seg = [unquote(s) for s in parts.path.split("/") if s]
    if not seg or parts.scheme not in {"http", "https"}:
        return None
    first = seg[0].lower()
    if host.endswith("linkedin.com"):
        if first in {"company", "school"} and len(seg) >= 2:
            return "linkedin", f"https://www.linkedin.com/{first}/{seg[1]}"
        return None
    if first in _SOCIAL_SKIP or first.endswith((".php", ".html")):
        return None
    if host in {"twitter.com", "x.com"} and len(seg) == 1:
        return "x", f"https://x.com/{seg[0]}"
    if host in {"facebook.com", "fb.com"} and len(seg) == 1 and not first.startswith("profile"):
        return "facebook", f"https://www.facebook.com/{seg[0]}"
    if host == "instagram.com" and len(seg) == 1:
        return "instagram", f"https://www.instagram.com/{seg[0]}/"
    if host in {"youtube.com", "youtu.be"} and (
        first.startswith("@") or first in {"channel", "c", "user"}
    ):
        return "youtube", f"https://www.youtube.com/{'/'.join(seg[:2])}"
    if host == "github.com" and len(seg) == 1:
        return "github", f"https://github.com/{seg[0]}"
    if host == "tiktok.com" and first.startswith("@") and len(seg) == 1:
        return "tiktok", f"https://www.tiktok.com/{seg[0]}"
    if host == "xing.com" and first == "companies" and len(seg) >= 2:
        return "xing", f"https://www.xing.com/companies/{seg[1]}"
    return None


def _address(node: dict[str, Any]) -> dict[str, str] | None:
    addr = node.get("address")
    if isinstance(addr, list):
        addr = addr[0] if addr else None
    if isinstance(addr, str) and addr.strip():
        return {"text": " ".join(addr.split())}
    if not isinstance(addr, dict):
        return None
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    out = {
        "street": addr.get("streetAddress"),
        "postal_code": addr.get("postalCode"),
        "city": addr.get("addressLocality"),
        "region": addr.get("addressRegion"),
        "country": country,
    }
    out = {
        k: " ".join(str(v).split())
        for k, v in out.items()
        if isinstance(v, str | int) and str(v).strip()
    }
    return out or None


def _generic_email(address: str, domain: str | None) -> str | None:
    address = address.strip().lower().split("?")[0]
    if not _EMAIL.fullmatch(address):
        return None
    local, _, host = address.partition("@")
    if local not in GENERIC_LOCALS:
        return None
    if domain and not in_domain(host, domain):
        return None
    return address


def extract(pages: Iterable[Page], domain: str | None, country: str | None) -> Contact:
    """Contact details found on `pages` of the company's own site."""
    contact = Contact()
    phones: dict[str, Phone] = {}
    emails: dict[str, None] = {}

    def add_phone(raw: str, source: str, label: str | None = None) -> None:
        raw = " ".join(unquote(raw).split())
        if len(re.sub(r"\D", "", raw)) < 6:
            return
        e164 = to_e164(raw, country)
        key = e164 or re.sub(r"\D", "", raw)
        phones.setdefault(key, Phone(raw, e164, source, label))

    for page in pages:
        tree = page.tree()
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip()
            low = href.lower()
            if low.startswith("tel:"):
                if not _in_person_card(a):
                    text = " ".join(a.text(separator=" ").split())
                    add_phone(
                        href[4:],
                        page.url,
                        text if text and not re.fullmatch(r"[\d\s+().-]+", text) else None,
                    )
            elif low.startswith("mailto:") and not _in_person_card(a):
                if mail := _generic_email(unquote(href[7:]), domain):
                    emails.setdefault(mail)
            elif (net := social_profile(href)) and net[0] not in contact.social:
                contact.social[net[0]] = net[1]
        for m in _EMAIL.finditer(page.text):
            if mail := _generic_email(m.group(0), domain):
                emails.setdefault(mail)
        for node in _ld_nodes(page):
            if not _is_org(node) and "ContactPoint" not in _types(node):
                continue
            tel = node.get("telephone")
            for t in [tel] if isinstance(tel, str) else tel or []:
                if isinstance(t, str):
                    add_phone(t, "json-ld")
            if isinstance(node.get("email"), str) and (
                mail := _generic_email(node["email"], domain)
            ):
                emails.setdefault(mail)
            if contact.address is None and _is_org(node):
                contact.address = _address(node)
            same = node.get("sameAs")
            for url in [same] if isinstance(same, str) else same or []:
                if (
                    isinstance(url, str)
                    and (net := social_profile(url))
                    and net[0] not in contact.social
                ):
                    contact.social[net[0]] = net[1]
    contact.phones = list(phones.values())
    contact.emails = list(emails)
    return contact
