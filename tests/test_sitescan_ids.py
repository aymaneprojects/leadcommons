"""Tests for pepite.sitescan.ids.find_ids: register numbers printed on company pages."""

from __future__ import annotations

import pytest

from pepite.sitescan.ids import find_ids

# ---- check-digit helpers, written from each algorithm's definition (not from ids.py) ---------


def luhn_complete(body: str) -> str:
    """Append the Luhn check digit to `body`."""
    total = 0
    for i, ch in enumerate(reversed(body)):  # rightmost body digit is doubled (check goes after)
        value = int(ch)
        if i % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return body + str((10 - total % 10) % 10)


def mod11_check(body: str, weights: tuple[int, ...]) -> str | None:
    """11 - (weighted sum % 11), where 11 -> 0 and 10 means no valid number."""
    check = 11 - sum(int(d) * w for d, w in zip(body, weights, strict=True)) % 11
    if check == 11:
        return "0"
    if check == 10:
        return None
    return str(check)


NO_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
CH_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4)
DK_WEIGHTS = (2, 7, 6, 5, 4, 3, 2, 1)


def no_complete(body8: str) -> str:
    check = mod11_check(body8, NO_WEIGHTS)
    assert check is not None
    return body8 + check


def ch_complete(body8: str) -> str:
    check = mod11_check(body8, CH_WEIGHTS)
    assert check is not None, "pick another body"
    return body8 + check


def dk_complete(body7: str) -> str:
    """The 8th digit has weight 1 in the CVR sum, so it is (-sum) mod 11, which must be a digit."""
    total = sum(int(d) * w for d, w in zip(body7, DK_WEIGHTS, strict=False))
    check = (-total) % 11
    assert check <= 9, "pick another body"
    return body7 + str(check)


def be_complete(body8: str) -> str:
    """Belgian enterprise number: last two digits = 97 - (first eight mod 97)."""
    return body8 + f"{97 - int(body8) % 97:02d}"


def acn_complete(body8: str) -> str:
    total = sum(int(body8[i]) * (8 - i) for i in range(8))
    return body8 + str((10 - total % 10) % 10)


def abn_complete(body9: str) -> str:
    """Two trailing digits found by search: the first digit counts as (digit - 1) in the ABN sum."""
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    for x in "0123456789":
        for y in "0123456789":
            digits = [int(c) for c in body9 + x + y]
            digits[0] -= 1
            if sum(d * w for d, w in zip(digits, weights, strict=True)) % 89 == 0:
                return body9 + x + y
    raise AssertionError("no check digits for this body")


def fr_vat_key(siren: str) -> str:
    return f"{(12 + 3 * (int(siren) % 97)) % 97:02d}"


def siret(siren: str, nic4: str) -> str:
    """SIRET = SIREN + 4 digits + Luhn check digit over all 14."""
    body13 = siren + nic4
    return luhn_complete(body13)


SIREN_A = "552081317"  # real large-company SIREN, passes Luhn
SIREN_B = "542051180"  # real large-company SIREN, passes Luhn
assert luhn_complete(SIREN_A[:8]) == SIREN_A
assert luhn_complete(SIREN_B[:8]) == SIREN_B
ABN_A = "51824753556"  # ATO example ABN
ABN_B = abn_complete("530040856")
NO_A = "923609016"
NO_B = no_complete("81234567")
DK_A = "13580006"
DK_B = dk_complete("1234567")
BE_A = "0403200393"
BE_B = be_complete("01234567")
CH_A = "109322551"
CH_B = ch_complete("12345670")
IT_A = "00743110157"
IT_B = luhn_complete("1234567890")


def as_tuples(text: str, country: str | None = None) -> list[tuple[str, str, str]]:
    return [(r.registry, r.number, r.kind) for r in find_ids(text, country)]


# ---- valid numbers: at least two per format ---------------------------------------------------

VALID: list[tuple[str, str | None, list[tuple[str, str, str]]]] = [
    # FR-SIREN: bare with label, SIRET, RCS (with and without section letter), VAT key
    (f"SIREN {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}", None, [("FR-SIREN", SIREN_A, "siren")]),
    (
        f"Siège social. SIREN : {SIREN_B[:3]}.{SIREN_B[3:6]}.{SIREN_B[6:]}",
        "FR",
        [("FR-SIREN", SIREN_B, "siren")],
    ),
    (f"SIRET {SIREN_A} {siret(SIREN_A, '0001')[9:]}", None, [("FR-SIREN", SIREN_A, "siret")]),
    (
        f"RCS Nanterre B {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}",
        None,
        [("FR-SIREN", SIREN_A, "rcs")],
    ),
    (f"RCS Paris {SIREN_B[:3]} {SIREN_B[3:6]} {SIREN_B[6:]}", None, [("FR-SIREN", SIREN_B, "rcs")]),
    (
        f"TVA FR {fr_vat_key(SIREN_A)} {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}",
        None,
        [("FR-SIREN", SIREN_A, "vat")],
    ),
    # GB-CH: 8 digits zero-padded, and a letter-prefixed number
    ("Company number 00445790", "GB", [("GB-CH", "00445790", "company-number")]),
    ("Company number 445790", None, [("GB-CH", "00445790", "company-number")]),
    ("Registered in Scotland SC123456", None, [("GB-CH", "SC123456", "company-number")]),
    # NO-BRREG: label before, MVA after
    (f"Org.nr. {NO_A[:3]} {NO_A[3:6]} {NO_A[6:]} MVA", "NO", [("NO-BRREG", NO_A, "org-number")]),
    (f"Organisasjonsnummer: {NO_B}", None, [("NO-BRREG", NO_B, "org-number")]),
    # AU-ABN and AU-ACN
    (f"ABN {ABN_A[:2]} {ABN_A[2:5]} {ABN_A[5:8]} {ABN_A[8:]}", "AU", [("AU-ABN", ABN_A, "abn")]),
    (f"ABN {ABN_B}", None, [("AU-ABN", ABN_B, "abn")]),
    ("ACN 004 085 616", None, [("AU-ACN", "004085616", "acn")]),
    (f"ACN {acn_complete('12345678')}", None, [("AU-ACN", acn_complete("12345678"), "acn")]),
    # NL-KVK: label, no checksum
    ("KvK 33012286", "NL", [("NL-KVK", "33012286", "kvk")]),
    ("Kamer van Koophandel nummer 12345678", None, [("NL-KVK", "12345678", "kvk")]),
    # DK-CVR
    (f"CVR {DK_A}", None, [("DK-CVR", DK_A, "cvr")]),
    (f"CVR-nr. {DK_B[:2]} {DK_B[2:4]} {DK_B[4:6]} {DK_B[6:]}", "DK", [("DK-CVR", DK_B, "cvr")]),
    # BE-KBO: BE prefix, and the "numéro d'entreprise" label
    (f"BE {BE_A[:4]}.{BE_A[4:7]}.{BE_A[7:]}", "BE", [("BE-KBO", BE_A, "enterprise-number")]),
    (
        f"Numéro d'entreprise : {BE_B[:4]}.{BE_B[4:7]}.{BE_B[7:]}",
        None,
        [("BE-KBO", BE_B, "enterprise-number")],
    ),
    # CH-UID
    (f"CHE-{CH_A[:3]}.{CH_A[3:6]}.{CH_A[6:]} MWST", None, [("CH-UID", CH_A, "uid")]),
    (f"CHE{CH_B}", None, [("CH-UID", CH_B, "uid")]),
    # DE-HR: court before the number, court after the number
    ("Amtsgericht München, HRB 123456 B", "DE", [("DE-HR", "HRB123456B", "hr")]),
    ("HRA 4711, Registergericht: Berlin (Charlottenburg)", None, [("DE-HR", "HRA4711", "hr")]),
    # IT-VAT: P.IVA and "Partita IVA" labels
    (f"P.IVA {IT_A}", "IT", [("IT-VAT", IT_A, "vat")]),
    (f"Partita IVA: {IT_B}", None, [("IT-VAT", IT_B, "vat")]),
]


@pytest.mark.parametrize(("text", "country", "expected"), VALID)
def test_valid_numbers_are_found(text, country, expected):
    assert as_tuples(text, country) == expected


def test_raw_keeps_the_text_as_written():
    (hit,) = find_ids(f"SIREN {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}")
    assert hit.raw == f"{SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}"


def test_de_raw_includes_the_court_name():
    (hit,) = find_ids("HRA 4711, Registergericht: Berlin (Charlottenburg)")
    assert hit.raw == "Registergericht: Berlin (Charlottenburg), HRA 4711"


def test_non_breaking_spaces_are_accepted():
    text = f"SIREN {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}"
    assert as_tuples(text) == [("FR-SIREN", SIREN_A, "siren")]


# ---- invalid numbers: bad checksum, or a bare number with no label -----------------------------

INVALID: list[str] = [
    # FR: bad Luhn; bare 9 digits without a label; SIRET with bad Luhn; VAT with a wrong key
    "SIREN 552 081 318",
    "552 081 317",
    "SIRET 55208131700019",
    f"TVA FR 04 {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}",
    # GB: 11 digits is too long; a bare number with no label
    "Company number 12345678901",
    "Registration no 00445790",
    # NO: check digit wrong; first digit not 8 or 9
    "Org.nr. 923 609 017",
    "Org.nr. 723609016",
    # AU: bad ABN checksum; ACN without its label
    "ABN 51 824 753 557",
    "004 085 616",
    # NL: no label; too short
    "33012286",
    "KvK 3301228",
    # DK: bad mod-11; no label
    "CVR 13580007",
    "13580006",
    # BE: bad mod-97; no label or prefix
    "BE 0403.200.394",
    "0403.200.393",
    # CH: bad check digit; no CHE prefix
    "CHE-109.322.552",
    "109.322.551",
    # DE: not HRB or HRA; no register letters
    "Amtsgericht München, HRX 123456",
    "Amtsgericht München 123456 B",
    # IT: bad Luhn; no label
    "P.IVA 00743110158",
    "00743110157",
]


@pytest.mark.parametrize("text", INVALID)
def test_invalid_numbers_are_dropped(text):
    assert find_ids(text) == []


def test_phone_number_and_iban_give_nothing():
    text = (
        "Tél. +33 1 42 68 53 00 — Fax 01 42 68 53 01. "
        "IBAN FR76 3000 6000 0112 3456 7890 189 — BE68 5390 0754 7034"
    )
    assert find_ids(text) == []


def test_number_inside_a_longer_digit_run_is_ignored():
    assert find_ids(f"SIREN 1{SIREN_A}") == []
    assert find_ids(f"SIREN {SIREN_A}0") == []


# ---- footer mixing several formats -------------------------------------------------------------

FOOTER = (
    f"ACME SAS · 12 rue X, 75002 Paris · SIREN {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]} · "
    f"RCS Nanterre B {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]} · "
    "Tel +33 1 42 68 53 00 · Company number 00445790 · Org.nr. 923 609 016 MVA · "
    "KvK 33012286 · ABN 51 824 753 556 · CVR 13580006 · BE 0403.200.393 · "
    "CHE-109.322.551 MWST · HRB 123456 B · P.IVA 00743110157"
)


def test_footer_mixing_formats_returns_each_once_in_order():
    assert as_tuples(FOOTER) == [
        ("FR-SIREN", SIREN_A, "siren"),
        ("GB-CH", "00445790", "company-number"),
        ("NO-BRREG", "923609016", "org-number"),
        ("NL-KVK", "33012286", "kvk"),
        ("AU-ABN", "51824753556", "abn"),
        ("DK-CVR", "13580006", "cvr"),
        ("BE-KBO", "0403200393", "enterprise-number"),
        ("CH-UID", "109322551", "uid"),
        ("DE-HR", "HRB123456B", "hr"),
        ("IT-VAT", "00743110157", "vat"),
    ]


def test_same_number_twice_is_reported_once():
    text = f"SIREN {SIREN_A} ... SIRET {siret(SIREN_A, '0001')} ... RCS Paris {SIREN_A}"
    assert as_tuples(text) == [("FR-SIREN", SIREN_A, "siren")]


# ---- country hint -------------------------------------------------------------------------------


def test_hint_keeps_numbers_whose_label_makes_the_country_certain():
    # KvK is Dutch by its label alone, so a French hint must not drop it
    assert as_tuples("KvK 33012286", "FR") == [("NL-KVK", "33012286", "kvk")]


def test_hint_drops_ambiguous_labels_from_other_countries():
    # "TVA" alone does not prove Belgium; with a French hint the Belgian number is dropped
    text = f"Numéro TVA {BE_A[:4]}.{BE_A[4:7]}.{BE_A[7:]}"
    assert as_tuples(text) == [("BE-KBO", BE_A, "enterprise-number")]
    assert as_tuples(text, "FR") == []
    assert as_tuples(text, "be") == [("BE-KBO", BE_A, "enterprise-number")]


def test_hint_does_not_change_an_unambiguous_french_number():
    text = f"SIREN {SIREN_A[:3]} {SIREN_A[3:6]} {SIREN_A[6:]}"
    assert as_tuples(text, "FR") == as_tuples(text) == [("FR-SIREN", SIREN_A, "siren")]


def test_empty_text_gives_nothing():
    assert find_ids("") == []


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _bare_number_not_a_siret() -> str:
    """14 digits that pass Luhn while their first 9 digits do not."""
    for n in range(10_000_000_000_000, 10_000_000_000_200):
        s = str(n)
        if _luhn(s) and not _luhn(s[:9]):
            return s
    raise AssertionError("no example found")


def test_bare_siret_needs_a_valid_siren_inside_or_a_label() -> None:
    number = _bare_number_not_a_siret()
    assert find_ids(f"Commande {number} expédiée") == []
    assert [i.kind for i in find_ids(f"SIRET : {number}")] == ["siret"]
