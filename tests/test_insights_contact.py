"""Business contact extraction: phones (E.164), generic mailboxes, address, company socials."""

from __future__ import annotations

from pathlib import Path

import pytest

from leadcommons.enrich.crawl import Page
from leadcommons.insights import contact

FIXTURES = Path(__file__).parent / "fixtures" / "insights"


def page(name: str, url: str = "https://nordwind.example/contact") -> Page:
    return Page(url, (FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("raw", "country", "expected"),
    [
        ("+49 40 1234567", None, "+49401234567"),
        ("0049 40 1234567", None, "+49401234567"),
        ("040 1234567", "DE", "+49401234567"),
        ("01 23 45 67 89", "FR", "+33123456789"),
        ("+33 (0)1 23 45 67 89", "FR", "+33123456789"),
        ("(415) 555-2671", "US", "+14155552671"),
        ("1-415-555-2671", "US", "+14155552671"),
        ("06 1234 5678", "IT", "+390612345678"),
        ("040 1234567 ext. 12", "DE", "+49401234567"),
        ("040 1234567", None, None),
        ("040 1234567", "ZZ", None),
        ("12", "FR", None),
        ("+1234", None, None),
    ],
)
def test_to_e164(raw: str, country: str | None, expected: str | None) -> None:
    assert contact.to_e164(raw, country) == expected


def test_fixture_extraction() -> None:
    home = Page("https://nordwind.example/", (FIXTURES / "home.html").read_text())
    found = contact.extract([home, page("contact.html")], "nordwind.example", "DE")
    numbers = {p.e164 for p in found.phones}
    assert numbers == {"+49401234567", "+49407654321", "+49401111111"}  # no personal mobiles
    assert all(not (p.e164 or "").startswith("+49170") for p in found.phones)
    assert sorted(found.emails) == ["contact@nordwind.example", "info@nordwind.example"]
    assert found.address == {
        "street": "Hafenstrasse 1",
        "postal_code": "20457",
        "city": "Hamburg",
        "country": "DE",
    }
    assert found.social == {
        "linkedin": "https://www.linkedin.com/company/nordwind-data",
        "x": "https://x.com/nordwinddata",
        "facebook": "https://www.facebook.com/nordwinddata",
    }


def test_phones_deduplicated_across_sources() -> None:
    found = contact.extract(
        [
            page("contact.html"),
            Page("https://nordwind.example/b", '<a href="tel:+49 40 7654321">x</a>'),
        ],
        "nordwind.example",
        "DE",
    )
    assert [p.e164 for p in found.phones].count("+49407654321") == 1


def test_unknown_country_keeps_raw_number() -> None:
    html = '<a href="tel:040 1234567">Call us</a><a href="tel:+44 20 7946 0958">UK</a>'
    found = contact.extract([Page("https://x.example/", html)], "x.example", None)
    by_raw = {p.number: p for p in found.phones}
    assert by_raw["040 1234567"].e164 is None
    assert by_raw["+44 20 7946 0958"].e164 == "+442079460958"
    assert by_raw["040 1234567"].label == "Call us"


def test_only_generic_mailboxes_on_own_domain() -> None:
    html = (
        '<a href="mailto:Jane.Doe@x.example">j</a><a href="mailto:info@x.example?subject=hi">i</a>'
        '<a href="mailto:support@other.example">o</a><p>sales@sub.x.example, bob@x.example</p>'
    )
    found = contact.extract([Page("https://x.example/", html)], "x.example", "FR")
    assert sorted(found.emails) == ["info@x.example", "sales@sub.x.example"]


def test_person_card_numbers_and_profiles_are_ignored() -> None:
    html = (
        '<div class="our-team"><div class="card"><a href="tel:+33 6 12 34 56 78">Call</a>'
        '<a href="mailto:contact@x.example">mail</a></div></div>'
        '<a href="https://www.linkedin.com/in/jane">Jane</a>'
        '<a href="https://www.facebook.com/sharer.php?u=x">share</a>'
        '<a href="https://github.com/acme/repo">repo</a><a href="https://github.com/acme">org</a>'
    )
    found = contact.extract([Page("https://x.example/", html)], "x.example", "FR")
    assert found.phones == [] and found.emails == []
    assert found.social == {"github": "https://github.com/acme"}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.linkedin.com/company/acme/",
            ("linkedin", "https://www.linkedin.com/company/acme"),
        ),
        ("https://www.linkedin.com/in/someone", None),
        ("https://twitter.com/acme", ("x", "https://x.com/acme")),
        ("https://twitter.com/intent/tweet?text=1", None),
        ("https://www.youtube.com/@acme", ("youtube", "https://www.youtube.com/@acme")),
        ("https://www.instagram.com/p/abc", None),
        ("https://example.com/acme", None),
    ],
)
def test_social_profile(url: str, expected) -> None:
    assert contact.social_profile(url) == expected
