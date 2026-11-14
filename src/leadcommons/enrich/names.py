"""Person-name helpers: accent folding, cleaning, dedupe keys and email-friendly variants."""

from __future__ import annotations

import re
import unicodedata


def _table(spec: str) -> dict[int, str]:
    """str.translate table from "à:a ß:ss ..." pairs, upper-case forms included."""
    table: dict[int, str] = {}
    for pair in spec.split():
        src, dst = pair.split(":")
        table[ord(src)] = dst
        if len(src.upper()) == 1 and src.upper() != src:
            table[ord(src.upper())] = dst.capitalize()
    return table


# Common accents; anything else falls back to Unicode decomposition (NFKD).
_FOLD = _table(
    "à:a á:a â:a ã:a ä:a å:a ā:a ç:c č:c ć:c è:e é:e ê:e ë:e ē:e ę:e ě:e ì:i í:i î:i ï:i ı:i "
    "ñ:n ń:n ò:o ó:o ô:o õ:o ö:o ø:o ō:o ù:u ú:u û:u ü:u ů:u ý:y ÿ:y š:s ś:s ş:s ž:z ź:z ż:z "
    "ł:l đ:d ð:d ř:r ß:ss æ:ae œ:oe þ:th"
)
_GERMAN = _table("ä:ae ö:oe ü:ue")

# Lowercase name particles that start a compound surname (de Gaulle, van der Berg).
PARTICLES = frozenset("de du des van von der den da das dos di del della la le ten ter zu".split())

_HONORIFIC = re.compile(
    r"^(?:(?:dr|prof|mr|mrs|ms|mx|mme|mlle|herr|frau|sra?|srta|dott|ing|mag)\.?|m\.|me\."
    r"|dipl\.?-[a-z]+\.?)\s+",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\s+(?:ph\.?d|mba|msc|bsc|md|jr|sr|ii|iii|iv|esq|cpa|ll\.?m)\.?$", re.I)
MAX_VARIANTS = 8


def fold(text: str, *, german: bool = False) -> str:
    """Strip accents with a small table (é->e, ß->ss); `german` maps ä/ö/ü to ae/oe/ue."""
    if german:
        text = text.translate(_GERMAN)
    text = text.translate(_FOLD)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def squeeze(text: str) -> str:
    """Folded lowercase alphanumerics only: "Atelier Nuage" -> "ateliernuage"."""
    return re.sub(r"[^a-z0-9]", "", fold(text).lower())


def name_key(name: str) -> str:
    """Dedupe key: "Jean-Pierre DUPONT" and "jean pierre dupont" give the same key."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(name).lower()))


def _fix_case(token: str) -> str:
    return token.title() if len(token) > 1 and token.isupper() else token


def clean_name(raw: str) -> str | None:
    """Drop honorifics, suffixes, brackets and trailing qualifiers; title-case ALL-CAPS tokens."""
    s = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", raw)
    s = re.split(r"[,;|/•·]|\s[-–—]\s", s)[0]
    s = " ".join(s.split())
    while m := _HONORIFIC.match(s):
        s = s[m.end() :]
    while m := _SUFFIX.search(s):
        s = s[: m.start()]
    s = " ".join(_fix_case(t) for t in s.split()).strip(" :-–—")
    if s.endswith(".") and len(s.split()[-1]) > 2:  # sentence full stop, not an initial
        s = s[:-1]
    return s or None


def _surname_options(rest: list[str]) -> list[list[str]]:
    """Surname token lists to try, most likely first."""
    if len(rest) <= 1:
        return [rest]
    idx = next((i for i, t in enumerate(rest) if t.lower() in PARTICLES), None)
    if idx is not None:
        compound = rest[idx:]
        core = [t for t in compound if t.lower() not in PARTICLES]
        return [compound, core] if core else [compound]
    # No particle: middle names dropped, compound (Spanish) surname, first surname only.
    return [rest[-1:], rest, rest[:1]]


def _spellings(tokens: list[str], german: bool) -> list[str]:
    """Email-safe spellings of a name part: hyphen kept and removed, apostrophes removed."""
    joined = re.sub(r"[^a-z0-9-]", "", fold("".join(tokens), german=german).lower()).strip("-")
    if not joined:
        return []
    return [joined, joined.replace("-", "")] if "-" in joined else [joined]


def name_variants(full_name: str, *, german_first: bool = False) -> list[tuple[str, str]]:
    """(first, last) pairs ready for email patterns, most likely first.

    Accents are folded; a name with ä/ö/ü yields both "ae" and "a" spellings (the "ae" one
    first when `german_first`). Compound surnames also yield a particle-free variant, hyphens a
    hyphen-free one. Single-token names give (first, "").
    """
    cleaned = clean_name(full_name) or ""
    tokens = [t.strip(".") for t in cleaned.split() if t.strip(".")]
    if not tokens:
        return []
    first, rest = tokens[0], tokens[1:]
    if rest:
        rest = [t for t in rest[:-1] if len(t) > 1] + rest[-1:]  # drop middle initials
    schemes = [False]
    if any(c in "äöüÄÖÜ" for c in cleaned):
        schemes = [True, False] if german_first else [False, True]
    out: list[tuple[str, str]] = []
    for german in schemes:
        for surname in _surname_options(rest):
            for f in _spellings([first], german):
                for last in _spellings(surname, german) or [""]:
                    if (f, last) not in out:
                        out.append((f, last))
    return out[:MAX_VARIANTS]
