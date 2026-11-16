"""Which pages of a company's site to read, and what to read on them. Pure functions, no network.

- guess_paths: likely legal-notice / imprint / about / contact paths by country, tried when the
  home page links do not reveal them.
- rank_links: the best legal / about / contact links on a home page, same registrable domain only.
- page_title: the cleaned page title (or og:site_name).
- visible_text: the visible text of a page, footer included, scripts and styles dropped.
- switchboard_phones: company switchboard numbers written next to a label, as E.164.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

GUESS_PATHS: dict[str, list[str]] = {
    "FR": [
        "/mentions-legales",
        "/mentions-legales/",
        "/mentions",
        "/informations-legales",
        "/a-propos",
        "/contact",
    ],
    "GB": ["/legal", "/legal-notice", "/terms", "/about", "/about-us", "/contact"],
    "AU": ["/legal", "/legal-notice", "/terms", "/about", "/about-us", "/contact"],
    "CA": ["/legal", "/legal-notice", "/terms", "/about", "/about-us", "/contact"],
    "US": ["/legal", "/legal-notice", "/terms", "/about", "/about-us", "/contact"],
    "DE": ["/impressum", "/impressum/", "/kontakt"],
    "AT": ["/impressum", "/impressum/", "/kontakt"],
    "CH": ["/impressum", "/impressum/", "/kontakt"],
    "NO": ["/om-oss", "/kontakt", "/personvern"],
    "NL": ["/colofon", "/over-ons", "/contact", "/disclaimer"],
    "BE": ["/colofon", "/over-ons", "/contact", "/disclaimer"],
    "DK": ["/om-os", "/kontakt"],
    "IT": ["/note-legali", "/chi-siamo", "/contatti"],
    "ES": ["/aviso-legal", "/contacto"],
}
_UNKNOWN_COUNTRY_PATHS = [
    "/legal-notice",
    "/mentions-legales",
    "/impressum",
    "/legal",
    "/about",
    "/contact",
]
_MAX_GUESSES = 6


def guess_paths(country: str | None) -> list[str]:
    """Candidate paths for a country code (case-insensitive), most likely first, at most 6."""
    key = (country or "").strip().upper()
    return list(GUESS_PATHS.get(key, _UNKNOWN_COUNTRY_PATHS))[:_MAX_GUESSES]


# Keyword phrases by category, in the languages the packs cover. Matched on folded tokens.
_LEGAL = (
    "mentions legales", "mention legale", "mentions", "informations legales", "legal notice",
    "legal notices", "legal information", "legal", "impressum", "imprint", "colofon",
    "aviso legal", "avisos legales", "note legali", "juridisk", "juridiske", "terms",
    "terms of use", "terms of service", "cgv", "cgu", "conditions generales", "disclaimer",
    "rechtliches", "rechtlicher hinweis",
)  # fmt: skip
_ABOUT = (
    "about", "about us", "qui sommes nous", "a propos", "nous connaitre", "om oss", "om os",
    "over ons", "uber uns", "ueber uns", "chi siamo", "quienes somos", "sobre nosotros",
    "nosotros", "la societe", "la societa",
)  # fmt: skip
_CONTACT = (
    "contact", "contact us", "contactez nous", "contactez", "kontakt", "kontakta",
    "kontakt oss", "contacto", "contatti", "contattaci", "contactar", "contacteer ons",
    "kontaktformular",
)  # fmt: skip
_CATEGORIES: tuple[tuple[int, tuple[str, ...]], ...] = (
    (3, _LEGAL),
    (2, _ABOUT),
    (1, _CONTACT),
)

_TRACKING_PARAMS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid", "ref", "_ga"}
_SKIP_EXT = re.compile(
    r"\.(?:pdf|jpe?g|png|gif|svg|webp|ico|zip|gz|docx?|xlsx?|pptx?|mp[34]|mov|css|js|json|xml"
    r"|ics|vcf)$",
    re.IGNORECASE,
)
_BLOCK_TAGS = (
    "p,div,br,li,ul,ol,h1,h2,h3,h4,h5,h6,tr,td,th,table,section,article,header,footer,aside,"
    "nav,dt,dd,dl,address,blockquote,figure,figcaption,main,form,hr,pre"
)
_BREAK = "\x1e"  # record separator: never present in page text
_SKIP_TAGS = "script,style,noscript,svg,template"


def _fold(text: str) -> str:
    """Lowercase, accents removed, punctuation turned into spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", no_accents.lower()))


def _tokens(text: str) -> list[str]:
    return _fold(text).split()


_CATEGORY_TOKENS = tuple(
    (weight, tuple(tuple(_tokens(p)) for p in phrases)) for weight, phrases in _CATEGORIES
)


def _score(path: str, label: str) -> int:
    """Best category weight of any keyword phrase found in the URL path or the link text."""
    path_toks = _tokens(path.replace("/", " "))
    label_toks = _tokens(label)
    best = 0
    for weight, phrases in _CATEGORY_TOKENS:
        if weight <= best:
            continue
        for phrase in phrases:
            n = len(phrase)
            for toks in (path_toks, label_toks):
                if any(tuple(toks[i : i + n]) == phrase for i in range(len(toks) - n + 1)):
                    best = weight
                    break
            if best == weight:
                break
    return best


def _norm_domain(domain: str) -> str:
    d = domain.strip().lower()
    if "://" in d:
        d = urlsplit(d).hostname or ""
    d = d.split("/")[0].split(":")[0].strip(".")
    return d.removeprefix("www.")


def _on_domain(host: str | None, domain: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


def _clean_url(url: str) -> str:
    """Drop the fragment and tracking query parameters."""
    url = urldefrag(url)[0]
    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _url_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path.rstrip('/') or '/'}?{parts.query}"


def rank_links(html: str, base_url: str, domain: str, limit: int = 3) -> list[str]:
    """Best legal / about / contact links on the home page, as absolute URLs.

    Only http(s) links on `domain` or its subdomains count. The home page itself is excluded.
    Legal notices rank above about pages, which rank above contact pages; ties keep page order.
    """
    dom = _norm_domain(domain)
    home_key = _url_key(base_url)
    tree = LexborHTMLParser(html)
    best: dict[str, tuple[int, int, str]] = {}  # key -> (score, first index, url)
    for index, a in enumerate(tree.css("a[href]")):
        href = (a.attributes.get("href") or "").strip()
        if (
            not href
            or href.startswith("#")
            or href.lower().startswith(("mailto:", "tel:", "javascript:", "data:"))
        ):
            continue
        url = _clean_url(urljoin(base_url, href))
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not _on_domain(parts.hostname, dom):
            continue
        if _SKIP_EXT.search(parts.path):
            continue
        key = _url_key(url)
        if key == home_key or (parts.path in {"", "/"} and not parts.query):
            continue
        score = _score(parts.path, a.text(separator=" "))
        if score == 0:
            continue
        if key not in best or score > best[key][0]:
            first = best[key][1] if key in best else index
            best[key] = (score, first, url)
    ordered = sorted(best.values(), key=lambda item: (-item[0], item[1]))
    return [url for _, _, url in ordered[:limit]]


def page_title(html: str) -> str | None:
    """Cleaned <title> (at most 200 characters), else og:site_name, else None."""
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    if title_node is not None:
        title = " ".join((title_node.text() or "").split())
        if title:
            return title[:200]
    for meta in tree.css("meta"):
        prop = (meta.attributes.get("property") or meta.attributes.get("name") or "").strip()
        if prop.lower() == "og:site_name":
            name = " ".join((meta.attributes.get("content") or "").split())
            if name:
                return name[:200]
    return None


def visible_text(html: str, limit: int = 200_000) -> str:
    """Visible text, one line per block element, footer included, cut to `limit` characters."""
    tree = LexborHTMLParser(html)
    for node in list(tree.css(_SKIP_TAGS)):
        node.decompose()
    root = tree.body if tree.body is not None else tree.root
    if root is None:
        return ""
    for node in root.css(_BLOCK_TAGS):  # block boundaries become line breaks
        node.insert_before(_BREAK)
        node.insert_after(_BREAK)
    lines = (" ".join(line.split()) for line in root.text(separator=" ").split(_BREAK))
    return "\n".join(line for line in lines if line)[:limit]


_LABEL_RE = re.compile(
    r"(?<!\w)(?:t[ée]l[ée]phone|t[ée]l[ée]fonnummer|telefonnummer|t[ée]l[ée]fono|telefoon"
    r"|telefono|telefon|t[ée]l|phone|tlf|standard|switchboard)(?!\w)",
    re.IGNORECASE,
)
_FAX_RE = re.compile(r"(?<!\w)(?:fax|telefax)(?!\w)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<!\w)(\+?\d(?:[ \t.\-/()]*\d){6,})")
_WINDOW = 25
_MAX_PHONES = 3


def _mobile(national: str, country: str) -> bool:
    """True for mobile prefixes of `national` (digits as written, trunk 0 included)."""
    if country == "FR":
        return national[:2] in {"06", "07"}
    if country == "GB":
        return national.startswith("07")
    if country == "DE":
        return national[:3] in {"015", "016", "017"}
    if country == "NO":
        return national[:1] in {"4", "9"}
    if country == "NL":
        return national.startswith("06")
    if country == "BE":
        return national.startswith("04")
    if country == "ES":
        return national[:1] in {"6", "7"}
    if country == "AU":
        return national.startswith("04")
    return False


# Country code -> (international prefix, national length or None, trunk "0" stripped)
_DIALING: dict[str, tuple[str, tuple[int, ...] | None, bool]] = {
    "FR": ("+33", None, True),
    "GB": ("+44", None, True),
    "DE": ("+49", None, True),
    "NO": ("+47", (8,), False),
    "NL": ("+31", None, True),
    "BE": ("+32", None, True),
    "DK": ("+45", (8,), False),
    "IT": ("+39", None, False),
    "ES": ("+34", (9,), False),
    "AU": ("+61", None, True),
    "CA": ("+1", (10,), False),
    "US": ("+1", (10,), False),
}


def _normalise(raw: str, country: str | None) -> str | None:
    """E.164 for one matched number, or None when it is not a usable company landline."""
    digits = re.sub(r"\D", "", raw)
    if not 8 <= len(digits) <= 15:
        return None
    if raw.lstrip().startswith("+"):
        intl = "+" + digits  # already international: kept as written, unless it is a mobile
        for key, (prefix, _lengths, trunk) in sorted(
            _DIALING.items(), key=lambda kv: -len(kv[1][0])
        ):
            if intl.startswith(prefix):
                rest = intl[len(prefix) :]
                national = ("0" + rest) if trunk else rest
                if _mobile(national, key) or (key == "IT" and rest.startswith("3")):
                    return None  # mobiles are likely personal, even written as +CC
                break
        return intl
    key = (country or "").strip().upper()
    if key not in _DIALING:
        return None
    prefix, lengths, trunk = _DIALING[key]
    if trunk:
        if not digits.startswith("0") or len(digits) < 9:
            return None
        if _mobile(digits, key):
            return None
        e164 = prefix + digits[1:]
    else:
        if lengths is not None and len(digits) not in lengths:
            return None
        if key == "IT" and not digits.startswith("0"):
            return None  # Italian mobiles (3..) have no trunk 0 and are personal
        if _mobile(digits, key):
            return None
        e164 = prefix + digits
    return e164 if 8 <= len(re.sub(r"\D", "", e164)) <= 15 else None


def switchboard_phones(text: str, country: str | None) -> list[str]:
    """Company switchboard numbers: labelled (Tél, Phone, Telefon...), not fax, not mobile.

    Each number is normalised to E.164 using `country`; numbers already written with +CC are
    kept as written. Order of appearance, de-duplicated, at most 3.
    """
    found: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        start = match.start()
        window = text[max(0, start - _WINDOW) : start]
        labels = [(m.start(), "label") for m in _LABEL_RE.finditer(window)]
        faxes = [(m.start(), "fax") for m in _FAX_RE.finditer(window)]
        keywords = sorted(labels + faxes)
        if not keywords or keywords[-1][1] == "fax":
            continue
        e164 = _normalise(match.group(1), country)
        if e164 and e164 not in found:
            found.append(e164)
            if len(found) == _MAX_PHONES:
                break
    return found


__all__ = [
    "GUESS_PATHS",
    "guess_paths",
    "page_title",
    "rank_links",
    "switchboard_phones",
    "visible_text",
]
