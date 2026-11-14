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
