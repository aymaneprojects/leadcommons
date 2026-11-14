"""People and public-email extraction from fixture pages (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from leadcommons.enrich.crawl import Page
from leadcommons.enrich.emails import extract_emails
from leadcommons.enrich.people import extract_people, is_plausible_name
from leadcommons.enrich.titles import guess_department, map_seniority
from leadcommons.models import Person, Seniority, make_id

FIXTURES = Path(__file__).parent / "fixtures" / "enrich"


def page(name: str, url: str) -> Page:
    return Page(url, (FIXTURES / name).read_text(encoding="utf-8"))


def by_name(people: list[Person]) -> dict[str, Person]:
    return {p.full_name: p for p in people}


def test_json_ld_team_page() -> None:
    url = "https://acme.example/team"
    people = by_name(extract_people([page("team_jsonld.html", url)], "acme.example"))
    assert set(people) == {"Jane Doe", "Robert Smith", "Priya Patel", "Wei Zhang", "Tom O'Brien"}
    jane = people["Jane Doe"]
    assert jane.id == make_id("pe", "acme.example", "Jane Doe")
    assert jane.company_domain == "acme.example"
    assert jane.source_urls == [url]
    # An explicit jobTitle beats the "Founder" default, which beats nothing.
    assert (jane.title, jane.seniority, jane.department) == (
        "Chief Executive Officer",
        Seniority.C_SUITE,
        "executive",
    )
    robert = people["Robert Smith"]  # founder string in JSON-LD, title from his team card
    assert (robert.title, robert.seniority, robert.department) == (
        "Co-founder, Head of Sales",
        Seniority.FOUNDER,
        "sales",
    )
    assert people["Priya Patel"].title == "Head of Engineering"  # givenName + familyName
    assert people["Wei Zhang"].seniority is Seniority.VP  # "Dr." dropped, jobTitle as list
    tom = people["Tom O'Brien"]  # team card only
    assert (tom.seniority, tom.department) == (Seniority.INTERN, "support")


def test_team_cards_without_json_ld() -> None:
    people = by_name(
        extract_people([page("team_cards.html", "https://nuage.example/equipe")], "nuage.example")
    )
    # Brand ("Atelier Nuage"), city ("New York") and headings ("Nos valeurs") are rejected.
    assert set(people) == {
        "Marie-Claire Dubois",
        "Thomas Müller",
        "Luis Fernández García",
        "Camille Laurent",
        "Hélène Garnier",
    }
    assert people["Marie-Claire Dubois"].title == "Directrice Marketing"
    assert people["Thomas Müller"].seniority is Seniority.SENIOR  # "MÜLLER" title-cased
    assert people["Luis Fernández García"].department == "sales"
    assert people["Camille Laurent"].title == "Stagiaire RH"
    assert people["Hélène Garnier"].title == "Présidente"  # microdata, not the prose around it


def test_french_mentions_legales() -> None:
    people = by_name(
        extract_people(
            [page("mentions_legales.html", "https://nuage.example/mentions-legales")],
            "nuage.example",
        )
    )
    assert set(people) == {"Jean-Pierre Dupont", "Sophie Lefèvre", "Hélène Garnier"}
    assert people["Jean-Pierre Dupont"].title == "Président"
    assert people["Jean-Pierre Dupont"].seniority is Seniority.C_SUITE
    assert people["Sophie Lefèvre"].title == "Directrice de la publication"
    assert people["Hélène Garnier"].title == "Responsable de la publication"  # name on next line


def test_german_impressum() -> None:
    people = by_name(
        extract_people(
            [page("impressum.html", "https://muster.example/impressum")], "muster.example"
        )
    )
    assert set(people) == {"Jürgen Köhler", "Anna-Lena Schmidt", "Max Mustermann"}
    assert people["Jürgen Köhler"].title == "Geschäftsführer"
    assert people["Jürgen Köhler"].seniority is Seniority.C_SUITE
    assert people["Anna-Lena Schmidt"].department == "executive"
    assert people["Max Mustermann"].title == "Prokurist"  # role in parentheses wins


def test_legal_sentence_form() -> None:
    p = Page("https://x.example/legal", "<p>Le directeur de la publication est Paul Durand.</p>")
    (person,) = extract_people([p], "x.example")
    assert (person.full_name, person.title) == ("Paul Durand", "Directeur de la publication")


def test_dedupe_merges_source_urls() -> None:
    pages = [
        page("team_cards.html", "https://nuage.example/equipe"),
        page("mentions_legales.html", "https://nuage.example/mentions-legales"),
        page("team_cards.html", "https://nuage.example/fr/equipe"),
    ]
    people = extract_people(pages, "nuage.example")
    helene = [p for p in people if p.full_name == "Hélène Garnier"]
    assert len(helene) == 1
    assert helene[0].title == "Présidente"  # structured data first
    assert helene[0].source_urls == [
        "https://nuage.example/equipe",
        "https://nuage.example/fr/equipe",
        "https://nuage.example/mentions-legales",
    ]
    assert len({p.id for p in people}) == len(people)


@pytest.mark.parametrize(
    ("title", "seniority", "department"),
    [
        ("Founder & CEO", Seniority.FOUNDER, "executive"),
        ("Co-founder & CTO", Seniority.FOUNDER, "engineering"),
        ("Owner", Seniority.OWNER, "executive"),
        ("PDG", Seniority.C_SUITE, "executive"),
        ("DG", Seniority.C_SUITE, "executive"),
        ("Directeur général", Seniority.C_SUITE, "executive"),
        ("Geschäftsführerin", Seniority.C_SUITE, "executive"),
        ("Chief Financial Officer", Seniority.C_SUITE, "finance"),
        ("Managing Partner", Seniority.PARTNER, None),
        ("Associée", Seniority.PARTNER, None),
        ("Vice President, Sales", Seniority.VP, "sales"),
        ("Head of People", Seniority.HEAD, None),
        ("Vertriebsleiter", Seniority.HEAD, "sales"),
        ("Leiterin Buchhaltung", Seniority.HEAD, "finance"),
        ("Directeur commercial", Seniority.DIRECTOR, "sales"),
        ("Director de Marketing", Seniority.DIRECTOR, "marketing"),
        ("Responsable RH", Seniority.MANAGER, "hr"),
        ("Teamleiter Kundenservice", Seniority.MANAGER, "support"),
        ("Product Manager", Seniority.MANAGER, "product"),
        ("Senior Software Engineer", Seniority.SENIOR, "engineering"),
        ("Juriste", None, "legal"),
        ("Développeur", Seniority.ENTRY, "engineering"),
        ("Assistant to the CEO", Seniority.ENTRY, "executive"),
        ("Stagiaire marketing", Seniority.INTERN, "marketing"),
        ("Praktikant Logistik", Seniority.INTERN, "operations"),
        ("Becaria de ventas", Seniority.INTERN, "sales"),
        ("Gardener", None, None),
    ],
)
def test_seniority_and_department(
    title: str, seniority: Seniority | None, department: str | None
) -> None:
    assert map_seniority(title) is seniority
    assert guess_department(title) == department


@pytest.mark.parametrize(
    "name",
    ["Jane Doe", "Jean-Pierre Dupont", "Ludwig van der Rohe", "Tom O'Brien", "John F. Kennedy"],
)
def test_plausible_names(name: str) -> None:
    assert is_plausible_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Jane",  # one token
        "Our Team",
        "Read More",
        "Managing Director",
        "Agent 007",
        "jane doe",
        "Acme Holding GmbH",
        "New York",
        "A B",
        "Jane Marie Anne Louise Doe",
    ],
)
def test_implausible_names(name: str) -> None:
    assert not is_plausible_name(name)


def test_brand_name_is_rejected() -> None:
    brands = frozenset({"ateliernuage"})
    assert not is_plausible_name("Atelier Nuage", brands)
    assert is_plausible_name("Claire Martin", brands)


def test_obfuscated_and_hidden_emails() -> None:
    emails = extract_emails(
        [page("obfuscated.html", "https://acme.example/contact")], "acme.example"
    )
    assert set(emails) == {
        "jean.dupont@acme.example",  # [at] [dot]
        "presse@acme.example",  # (at) (dot)
        "marie@acme.example",  # spelled out " at " / " dot "
        "support@acme.example",  # &#64;
        "paul.martin@acme.example",  # mailto, lowercased, query dropped
        "lucie.bernard@acme.example",  # Cloudflare data-cfemail
        "office@acme.example",  # JSON-LD
        "compta@billing.acme.example",  # subdomain kept
    }
    assert len(emails) == len(set(emails))
    # Not: "us@acme.example" (prose), gmail/other domains, image names, script contents.


def test_public_emails_only_on_domain() -> None:
    pages = [page("team_jsonld.html", "https://acme.example/team")]
    assert extract_emails(pages, "acme.example") == [
        "robert.smith@acme.example",
        "hello@acme.example",
        "jane.doe@acme.example",
    ]
    assert extract_emails(pages, "other.example") == []
