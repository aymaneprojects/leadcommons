"""Best-effort mapping of industry labels / Wikidata industry QIDs to ISIC Rev.4 sections."""

from __future__ import annotations

# Ordered: first matching keyword wins, so specific terms come before generic ones.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("K", ("bank", "insurance", "financ", "invest", "asset management", "reinsur", "fintech",
           "credit", "payment", "brokerage", "pension")),
    ("J", ("software", "information technology", "telecom", "internet", "computer", "video game",
           "publishing", "broadcast", "television", "radio", "film", "data processing",
           "media", "news", "cloud", "e-commerce", "newspaper", "music industry", "digital")),
    ("Q", ("pharma", "health", "hospital", "medical", "biotech", "clinic", "drug")),
    ("P", ("education", "school", "universit", "training")),
    ("L", ("real estate", "property")),
    ("F", ("construction", "civil engineering", "building")),
    ("D", ("electric", "power generation", "utility", "energy supply", "renewable", "gas supply",
           "nuclear")),
    ("E", ("water supply", "waste", "recycling", "sewage")),
    ("B", ("mining", "oil and gas", "petroleum", "extraction", "quarr", "coal")),
    ("A", ("agricultur", "farming", "forestry", "fishing", "fishery", "viticulture",
           "wine growing")),
    ("H", ("transport", "logistics", "airline", "aviation", "shipping", "railway", "freight",
           "postal", "courier", "rail")),
    ("I", ("hotel", "restaurant", "hospitality", "catering", "food service", "tourism lodging")),
    ("G", ("retail", "wholesale", "trade", "supermarket", "distribution", "automotive dealer",
           "department store")),
    ("M", ("consult", "legal", "law", "advertis", "architect", "engineering", "research",
           "accounting", "design", "marketing", "public relations", "scientific")),
    ("N", ("staffing", "employment", "travel agency", "security service", "rental", "leasing",
           "facility")),
    ("R", ("sport", "gambling", "entertainment", "art", "museum", "casino", "leisure")),
    ("O", ("public administration", "government", "defen")),
    ("C", ("manufactur", "automotive", "car", "chemical", "textile", "apparel", "fashion",
           "clothing", "food industry", "food processing", "beverage", "brewing", "steel",
           "metal", "machinery", "aerospace", "electronics", "semiconductor", "cosmetic",
           "luxury", "furniture", "paper", "plastic", "shipbuilding", "toy", "tobacco",
           "pharmaceutical industry", "industry", "production", "vehicle", "aircraft",
           "weapon", "glass", "cement", "winery", "distill", "dairy", "confection")),
)  # fmt: skip

# A few unambiguous Wikidata industry items (labels cover the rest).
QID_TO_SECTION: dict[str, str] = {
    "Q8148": "C",  # manufacturing
    "Q22687": "K",  # bank
    "Q43183": "K",  # insurance
    "Q837171": "K",  # financial services
    "Q7397": "J",  # software
    "Q713102": "C",  # food industry
    "Q2425052": "C",  # automotive industry
    "Q2287072": "G",  # retail
    "Q385378": "F",  # construction
}

SECTION_NAMES: dict[str, str] = {
    "A": "Agriculture, forestry and fishing",
    "B": "Mining and quarrying",
    "C": "Manufacturing",
    "D": "Electricity, gas, steam and air conditioning supply",
    "E": "Water supply; sewerage, waste management",
    "F": "Construction",
    "G": "Wholesale and retail trade",
    "H": "Transportation and storage",
    "I": "Accommodation and food service activities",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "L": "Real estate activities",
    "M": "Professional, scientific and technical activities",
    "N": "Administrative and support service activities",
    "O": "Public administration and defence",
    "P": "Education",
    "Q": "Human health and social work activities",
    "R": "Arts, entertainment and recreation",
    "S": "Other service activities",
}


def isic_section(label: str | None = None, qid: str | None = None) -> str | None:
    """Best-effort ISIC Rev.4 section letter from an industry label or QID; None if unknown."""
    if qid and qid in QID_TO_SECTION:
        return QID_TO_SECTION[qid]
    if not label:
        return None
    low = label.lower()
    for section, words in _KEYWORDS:
        if any(w in low for w in words):
            return section
    return None


# NACE Rev.2 / NAF rev.2 division ranges -> section (NACE sections share ISIC Rev.4 letters).
_DIVISION_SECTIONS: tuple[tuple[int, int, str], ...] = (
    (1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"),
    (45, 47, "G"), (49, 53, "H"), (55, 56, "I"), (58, 63, "J"), (64, 66, "K"), (68, 68, "L"),
    (69, 75, "M"), (77, 82, "N"), (84, 84, "O"), (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"),
    (94, 96, "S"), (97, 98, "T"), (99, 99, "U"),
)  # fmt: skip


def naf_section(code: str | None) -> str | None:
    """ISIC/NACE section letter from a NAF/NACE code such as "64.20Z" or "6420Z"."""
    if not code:
        return None
    digits = "".join(ch for ch in code[:3] if ch.isdigit())[:2]
    if len(digits) < 2:
        return None
    div = int(digits)
    for lo, hi, letter in _DIVISION_SECTIONS:
        if lo <= div <= hi:
            return letter
    return None


# US SEC SIC code ranges (4-digit, inclusive) -> ISIC section; first match wins.
_SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "A"), (1000, 1499, "B"), (1500, 1799, "F"), (2000, 3999, "C"),
    (4000, 4799, "H"), (4800, 4899, "J"), (4941, 4941, "E"), (4950, 4959, "E"),
    (4900, 4999, "D"), (5000, 5799, "G"), (5800, 5899, "I"), (5900, 5999, "G"),
    (6500, 6599, "L"), (6798, 6798, "L"), (6000, 6799, "K"), (7000, 7099, "I"),
    (7200, 7299, "S"), (7310, 7319, "M"), (7370, 7379, "J"), (7300, 7399, "N"),
    (7500, 7599, "G"), (7600, 7699, "S"), (7830, 7833, "R"), (7800, 7899, "J"),
    (7900, 7999, "R"), (8000, 8099, "Q"), (8100, 8199, "M"), (8200, 8299, "P"),
    (8300, 8399, "Q"), (8700, 8999, "M"), (9100, 9799, "O"),
)  # fmt: skip


def sic_section(sic: str | int | None) -> str | None:
    """Best-effort ISIC section letter from a 4-digit SEC SIC code (None if unknown)."""
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    for lo, hi, letter in _SIC_RANGES:
        if lo <= n <= hi:
            return letter
    return None
