"""Extract company register numbers printed on a company's own web page, keeping only valid ones.

`find_ids(text, country)` returns RegistryId values with the check digits verified, de-duplicated
by (registry, number) and in the order they appear. Pure functions: no network, no I/O.

One example per format (all numbers pass their check):

    FR-SIREN   "SIREN 552 081 317"              Luhn on 9 digits              kind siren
               "SIRET 552 081 317 00018"        Luhn on 14, SIREN = first 9   kind siret
               "RCS Nanterre B 552 081 317"     label RCS + city              kind rcs
               "FR 03 552 081 317"              key (12 + 3*(siren%97)) % 97  kind vat
    GB-CH      "Company number 00445790"        8 digits, zero-padded         kind company-number
               "Registered in Scotland SC123456"
    NO-BRREG   "Org.nr. 923 609 016 MVA"        mod 11, weights 3,2,7,6,5,4,3,2  kind org-number
    AU-ABN     "ABN 51 824 753 556"             mod 89                        kind abn
    AU-ACN     "ACN 004 085 616"                check digit, weights 8..1     kind acn
    NL-KVK     "KvK 33012286"                   no checksum                   kind kvk
    DK-CVR     "CVR 13580006"                   mod 11, weights 2,7,6,5,4,3,2,1  kind cvr
    BE-KBO     "BE 0403.200.393"                97 - (first 8 mod 97)         kind enterprise-number
    CH-UID     "CHE-109.322.551 MWST"           mod 11, weights 5,4,3,2,7,6,5,4  kind uid
    DE-HR      "Amtsgericht München, HRB 123456 B"  no checksum               kind hr
    IT-VAT     "P.IVA 00743110157"              Luhn on 11 digits             kind vat

Adding a country: write a finder (text -> list of _Hit, priority order) or a _Spec, then add it
to _RULES. find_ids walks _RULES; a country hint only changes the order and drops uncertain
(ambiguous-label) hits from other countries.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from leadcommons.sitescan import RegistryId

# One optional separator between digits: space, dot, hyphen, no-break space, narrow no-break space.
_SEP = r"[ .\-  ]"
_SEP_RE = re.compile(_SEP)


def _body(n: int) -> str:
    """Exactly n digits, each group optionally separated by one separator character."""
    return rf"[0-9](?:{_SEP}?[0-9]){{{n - 1}}}"


def _digits(n: int) -> str:
    """Pattern for exactly n digits, not part of a longer digit run."""
    return rf"(?<![0-9])(?<![0-9]{_SEP})" + _body(n) + rf"(?![0-9])(?!{_SEP}[0-9])"


def _clean(text: str) -> str:
    return _SEP_RE.sub("", text)


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    registry: str
    number: str
    raw: str
    kind: str
    certain: bool = True  # False: the label alone does not prove the country


@dataclass(frozen=True)
class _Spec:
    """A number of n digits that needs a label nearby, then a checksum."""

    n: int
    registry: str
    kind: str
    validate: Callable[[str], bool]
    labels: re.Pattern[str]
    window: int  # label must end within this many characters before the number
    strong: re.Pattern[str] | None = None  # if set, only these labels make the hit certain
    after: re.Pattern[str] | None = None  # a label written right after the number


def _label_window(text: str, start: int, window: int) -> str:
    """The text up to `window` characters before `start`, cut after any earlier digit.

    A label only counts for the number nearest to it: "KvK 33012286 ... CVR 13580006" gives
    "CVR" for the second number and nothing for the first.
    """
    before = text[max(0, start - window) : start]
    last_digit = max((i for i, ch in enumerate(before) if ch.isdigit()), default=-1)
    return before[last_digit + 1 :]


def _from_spec(text: str, spec: _Spec) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in re.finditer(_digits(spec.n), text):
        before = _label_window(text, m.start(), spec.window)
        after_hit = spec.after is not None and spec.after.match(text, m.end()) is not None
        if not (after_hit or spec.labels.search(before)):
            continue
        digits = _clean(m.group(0))
        if not spec.validate(digits):
            continue
        certain = after_hit or spec.strong is None or spec.strong.search(before) is not None
        hits.append(_Hit(m.start(), m.end(), spec.registry, digits, m.group(0), spec.kind, certain))
    return hits


# ---- checksums -------------------------------------------------------------------------------


def _always(_: str) -> bool:
    return True


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        value = int(ch)
        if i % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mod11_check(digits: str, weights: tuple[int, ...]) -> int | None:
    """Check digit = 11 - (weighted sum % 11), 11 -> 0, 10 -> None (invalid)."""
    check = 11 - sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
    if check == 11:
        return 0
    if check == 10:
        return None
    return check


_NO_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
_CH_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)
_DK_WEIGHTS = (2, 7, 6, 5, 4, 3, 2, 1)
_ABN_WEIGHTS = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)


def _no_ok(d: str) -> bool:
    return d[0] in "89" and _mod11_check(d[:8], _NO_WEIGHTS) == int(d[8])


def _ch_ok(d: str) -> bool:
    return _mod11_check(d[:8], _CH_WEIGHTS) == int(d[8])


def _dk_ok(d: str) -> bool:
    return sum(int(c) * w for c, w in zip(d, _DK_WEIGHTS, strict=True)) % 11 == 0


def _abn_ok(d: str) -> bool:
    values = [int(c) for c in d]
    values[0] -= 1
    return sum(v * w for v, w in zip(values, _ABN_WEIGHTS, strict=True)) % 89 == 0


def _acn_ok(d: str) -> bool:
    total = sum(int(d[i]) * (8 - i) for i in range(8))
    return (10 - total % 10) % 10 == int(d[8])


def _be_ok(d: str) -> bool:
    return 97 - int(d[:8]) % 97 == int(d[8:])


# ---- labels ----------------------------------------------------------------------------------

_FR_LABELS = re.compile(
    r"SIREN|SIRET|RCS|R\.C\.S|immatricul[ée]e|TVA|num[ée]ro\s+d['’]identification", re.I
)
_NO_LABELS = re.compile(r"org\.?\s?nr|organisasjonsnummer|foretaksregisteret|\bMVA\b", re.I)
_NO_AFTER = re.compile(r"\s{0,3}MVA\b", re.I)
_NL_LABELS = re.compile(r"KvK|KVK-nummer|Kamer van Koophandel|Chamber of Commerce", re.I)
_BE_STRONG = re.compile(
    r"\bBCE\b|\bKBO\b|num[ée]ro\s+d['’]entreprise|ondernemingsnummer"
    r"|(?-i:(?<![A-Za-z])BE[  ]?$)",
    re.I,
)
_BE_LABELS = re.compile(_BE_STRONG.pattern + r"|\bTVA\b|\bBTW\b|\bVAT\b", re.I)
_IT_STRONG = re.compile(r"P\.?\s?IVA|Partita\s+IVA", re.I)
_IT_LABELS = re.compile(_IT_STRONG.pattern + r"|\bVAT\b", re.I)
_GB_LABELS = re.compile(
    r"company\s+(?:registration\s+)?(?:number|no\b)|registered\s+number"
    r"|registered\s+in\s+(?:england|scotland|wales|northern\s+ireland)|companies\s+house",
    re.I,
)
_GB_STRONG = re.compile(r"companies\s+house|registered\s+in\s+(?:england|scotland)", re.I)

# ---- formats ---------------------------------------------------------------------------------

_FR_BARE = _Spec(9, "FR-SIREN", "siren", _luhn_ok, _FR_LABELS, window=40)
_FR_RCS_RE = re.compile(
    r"(?<![A-Za-z])R\.?\s?C\.?\s?S\.?\s+(?:[A-Za-zÀ-ÿ'’\-]+\s+){1,3}?(?:[AB]\s+)?"
    r"(?P<num>" + _digits(9) + r")",
)
_FR_VAT_RE = re.compile(
    r"(?<![A-Za-z])FR[  ]?(?P<key>[0-9]{2})(?:" + _SEP + r")?"
    r"(?P<num>" + _body(9) + r")(?![0-9])(?!" + _SEP + r"[0-9])"
)


def _find_fr(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in re.finditer(_digits(14), text):  # SIRET: Luhn on all 14 digits
        digits = _clean(m.group(0))
        if not _luhn_ok(digits):
            continue
        # A bare 14-digit run passes Luhn one time in ten (order or tracking numbers): without a
        # label nearby, the SIREN inside must pass its own Luhn check too.
        labelled = bool(_FR_LABELS.search(text[max(0, m.start() - 40) : m.start()]))
        if labelled or _luhn_ok(digits[:9]):
            hits.append(_Hit(m.start(), m.end(), "FR-SIREN", digits[:9], m.group(0), "siret"))
    for m in _FR_RCS_RE.finditer(text):
        digits = _clean(m.group("num"))
        if _luhn_ok(digits):
            hits.append(_Hit(m.start(), m.end(), "FR-SIREN", digits, m.group(0), "rcs"))
    for m in _FR_VAT_RE.finditer(text):
        siren = _clean(m.group("num"))
        key_ok = int(m.group("key")) == (12 + 3 * (int(siren) % 97)) % 97
        if key_ok and _luhn_ok(siren):
            hits.append(_Hit(m.start(), m.end(), "FR-SIREN", siren, m.group(0), "vat"))
    hits.extend(_from_spec(text, _FR_BARE))
    return hits


_GB_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?<![0-9]" + _SEP + r")"
    r"(?:(?:SC|NI|OC|SO|NC|LP|SL|FC|SE|GE|IP|SP|RS|NO|NP|NL|IC|CE|CS|AC|ZC|SZ|SR|SA)[0-9]{6}"
    r"|[0-9]{1,8})(?![0-9])(?!" + _SEP + r"[0-9])"
)


def _find_gb(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in _GB_NUMBER_RE.finditer(text):
        before = _label_window(text, m.start(), 60)
        if not _GB_LABELS.search(before):
            continue
        raw = m.group(0)
        number = raw.zfill(8) if raw.isdigit() else raw
        certain = _GB_STRONG.search(before) is not None
        hits.append(_Hit(m.start(), m.end(), "GB-CH", number, raw, "company-number", certain))
    return hits


_NO = _Spec(9, "NO-BRREG", "org-number", _no_ok, _NO_LABELS, window=40, after=_NO_AFTER)
_AU_ABN = _Spec(11, "AU-ABN", "abn", _abn_ok, re.compile(r"\bABN\b", re.I), window=20)
_AU_ACN = _Spec(9, "AU-ACN", "acn", _acn_ok, re.compile(r"\bACN\b", re.I), window=20)
_NL_KVK = _Spec(8, "NL-KVK", "kvk", _always, _NL_LABELS, window=40)
_DK_CVR = _Spec(8, "DK-CVR", "cvr", _dk_ok, re.compile(r"\bCVR\b", re.I), window=20)
_BE_KBO = _Spec(10, "BE-KBO", "enterprise-number", _be_ok, _BE_LABELS, window=40, strong=_BE_STRONG)
_IT_VAT = _Spec(11, "IT-VAT", "vat", _luhn_ok, _IT_LABELS, window=30, strong=_IT_STRONG)

_CH_RE = re.compile(
    r"(?<![A-Za-z])CHE[-.  ]?(?P<num>" + _body(9) + r")(?![0-9])(?!" + _SEP + r"[0-9])"
)


def _find_ch(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in _CH_RE.finditer(text):
        digits = _clean(m.group("num"))
        if _ch_ok(digits):
            hits.append(_Hit(m.start(), m.end(), "CH-UID", digits, m.group(0), "uid"))
    return hits


_DE_RE = re.compile(
    r"(?<![A-Za-z])(?P<reg>HR[AB])[  ]?(?P<num>[0-9]{1,6})"
    r"(?:[  ]?(?P<suffix>[A-Z])(?![A-Za-z]))?(?![0-9A-Za-z])"
)
_DE_COURT_RE = re.compile(
    r"(?:Amtsgericht|Registergericht)\s*:?\s*"
    r"[A-ZÄÖÜ][A-Za-zÀ-ÿ()\-]*(?:\s+(?!HR[AB]\b)[A-Za-zÀ-ÿ()\-]+){0,3}"
)


def _nearest_court(text: str, start: int, end: int) -> str | None:
    """The court named within 80 characters before or 40 after the register number."""
    lo = max(0, start - 80)
    best: tuple[int, str] | None = None
    for c in _DE_COURT_RE.finditer(text, lo):
        distance = max(0, start - c.end(), c.start() - end)
        if distance <= 40 and (best is None or distance < best[0]):
            best = (distance, c.group(0))
    return best[1] if best else None


def _find_de(text: str) -> list[_Hit]:
    hits: list[_Hit] = []
    for m in _DE_RE.finditer(text):
        number = m.group("reg") + m.group("num") + (m.group("suffix") or "")
        court = _nearest_court(text, m.start(), m.end())
        raw = f"{court}, {m.group(0)}" if court else m.group(0)
        hits.append(_Hit(m.start(), m.end(), "DE-HR", number, raw, "hr"))
    return hits


# Country code -> finder. Order matters only for overlapping matches within the same text.
_RULES: tuple[tuple[str, Callable[[str], list[_Hit]]], ...] = (
    ("FR", _find_fr),
    ("GB", _find_gb),
    ("NO", partial(_from_spec, spec=_NO)),
    ("AU", partial(_from_spec, spec=_AU_ABN)),
    ("AU", partial(_from_spec, spec=_AU_ACN)),
    ("NL", partial(_from_spec, spec=_NL_KVK)),
    ("DK", partial(_from_spec, spec=_DK_CVR)),
    ("BE", partial(_from_spec, spec=_BE_KBO)),
    ("CH", _find_ch),
    ("DE", _find_de),
    ("IT", partial(_from_spec, spec=_IT_VAT)),
)


def find_ids(text: str, country: str | None = None) -> list[RegistryId]:
    """Return the valid register numbers found in `text`, in order of appearance.

    `country` (ISO code such as "FR") is a hint: that country's formats are tried first, and
    numbers from other countries are kept only when their label makes the country certain.
    """
    hint = country.upper() if country else None
    candidates: list[tuple[int, _Hit]] = []  # (rule position, hit)
    for position, (code, finder) in enumerate(_RULES):
        for hit in finder(text):
            if hint and code != hint and not hit.certain:
                continue
            # the hint country sorts first among equally certain hits
            candidates.append((0 if code == hint else position + 1, hit))
    # certain hits claim their text before uncertain ones, so "KvK 33012286" stays Dutch even
    # where a generic "company number" label also reaches it
    candidates.sort(key=lambda item: (not item[1].certain, item[0]))
    taken: list[tuple[int, int]] = []
    accepted: list[_Hit] = []
    for _, hit in candidates:
        if any(hit.start < end and start < hit.end for start, end in taken):
            continue
        taken.append((hit.start, hit.end))
        accepted.append(hit)

    accepted.sort(key=lambda hit: hit.start)
    seen: set[tuple[str, str]] = set()
    result: list[RegistryId] = []
    for hit in accepted:
        key = (hit.registry, hit.number)
        if key in seen:
            continue
        seen.add(key)
        result.append(RegistryId(hit.registry, hit.number, hit.raw, hit.kind))
    return result


__all__ = ["find_ids"]
