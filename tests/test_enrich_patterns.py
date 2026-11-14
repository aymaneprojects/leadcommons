"""Name normalisation, pattern inference and candidate generation."""

from __future__ import annotations

import pytest

from leadcommons.enrich import guess_emails
from leadcommons.enrich.names import clean_name, name_key, name_variants
from leadcommons.enrich.patterns import (
    PATTERNS,
    infer_patterns,
    is_role_mailbox,
    rank_guesses,
    render,
)
from leadcommons.models import EmailStatus, Person, make_id


def person(name: str, domain: str = "acme.example") -> Person:
    return Person(id=make_id("pe", domain, name), full_name=name, company_domain=domain)


@pytest.mark.parametrize(
    ("raw", "cleaned"),
    [
        ("M. Jean-Pierre DUPONT", "Jean-Pierre Dupont"),
        ("Prof. Dr. Anna Weber", "Anna Weber"),
        ("Dr. Wei Zhang, PhD", "Wei Zhang"),
        ("Jane Doe (CEO)", "Jane Doe"),
        ("Jane Doe – Founder", "Jane Doe"),
        ("Mme Sophie Lefèvre", "Sophie Lefèvre"),
    ],
)
def test_clean_name(raw: str, cleaned: str) -> None:
    assert clean_name(raw) == cleaned


def test_name_key_ignores_case_accents_and_hyphens() -> None:
    assert name_key("Jean-Pierre DUPONT") == name_key("jean pierre Dupont")
    assert name_key("Hélène Garnier") == "helene garnier"


def test_variants_accents() -> None:
    assert name_variants("José Ñúñez") == [("jose", "nunez")]
    assert name_variants("François Groß") == [("francois", "gross")]


def test_variants_german_umlauts_both_spellings() -> None:
    assert name_variants("Jürgen Müller", german_first=True) == [
        ("juergen", "mueller"),
        ("jurgen", "muller"),
    ]
    assert name_variants("Jürgen Müller")[0] == ("jurgen", "muller")


def test_variants_particles_hyphens_apostrophes() -> None:
    assert name_variants("Ludwig van der Rohe") == [("ludwig", "vanderrohe"), ("ludwig", "rohe")]
    assert name_variants("Charles de Gaulle") == [("charles", "degaulle"), ("charles", "gaulle")]
    assert name_variants("Jean-Pierre Dupont") == [
        ("jean-pierre", "dupont"),
        ("jeanpierre", "dupont"),
    ]
    assert name_variants("Tom O'Brien") == [("tom", "obrien")]
    assert name_variants("John F. Kennedy") == [("john", "kennedy")]
    assert name_variants("Cher") == [("cher", "")]


def test_render() -> None:
    assert render("{first}.{last}", "jane", "doe") == "jane.doe"
    assert render("{f}{last}", "jane", "doe") == "jdoe"
    assert render("{first}{l}", "jane", "doe") == "janed"
    assert render("{last}{f}", "jane", "doe") == "doej"
    assert render("{first}", "cher", "") == "cher"
    assert render("{f}.{last}", "cher", "") is None


@pytest.mark.parametrize(
    ("local", "role"),
    [
        ("info", True),
        ("contact", True),
        ("bonjour", True),
        ("kontakt", True),
        ("sales-fr", True),
        ("jobs2", True),
        ("jane.doe", False),
        ("jdoe", False),
    ],
)
def test_role_mailboxes(local: str, role: bool) -> None:
    assert is_role_mailbox(local) is role


def test_inference_from_site_names() -> None:
    known = ["jdoe@acme.example", "rsmith@acme.example", "contact@acme.example"]
    scores = infer_patterns(known, "acme.example", ["Jane Doe", "Robert Smith", "Priya Patel"])
    assert (scores[0].pattern, scores[0].evidence) == ("{f}{last}", 2.0)
    assert all(s.evidence == 0 for s in scores[1:])
    guesses = rank_guesses("Priya Patel", "acme.example", known, ["Jane Doe", "Robert Smith"])
    assert guesses[0].address == "ppatel@acme.example"
    assert guesses[0].pattern == "{f}{last}"


def test_inference_handles_accents_and_particles() -> None:
    known = ["francois.lefevre@acme.example", "c.degaulle@acme.example"]
    scores = {
        s.pattern: s.evidence
        for s in infer_patterns(known, "acme.example", ["François Lefèvre", "Charles de Gaulle"])
    }
    assert scores["{first}.{last}"] == 1.0
    assert scores["{f}.{last}"] == 1.0


def test_role_mailboxes_give_no_evidence() -> None:
    known = ["contact@acme.example", "info@acme.example", "jobs@acme.example"]
    scores = infer_patterns(known, "acme.example", ["Jane Doe"])
    assert [s.pattern for s in scores] == list(PATTERNS)
    assert all(s.evidence == 0 for s in scores)


def test_no_evidence_uses_global_priors() -> None:
    cands = guess_emails(person("Claire Martin"), "acme.example")
    assert [c.address for c in cands[:5]] == [
        "claire.martin@acme.example",
        "claire@acme.example",
        "cmartin@acme.example",
        "clairemartin@acme.example",
        "c.martin@acme.example",
    ]
    assert cands[0].pattern == "{first}.{last}"
    assert all(c.status is EmailStatus.UNKNOWN for c in cands)
    confidences = [c.confidence for c in cands]
    assert confidences == sorted(confidences, reverse=True)
    assert confidences[0] <= 20  # low confidence without evidence


def test_shape_evidence_without_names() -> None:
    cands = guess_emails(
        person("Claire Martin"), "acme.example", ["a.durand@acme.example", "b.roux@acme.example"]
    )
    assert cands[0].address == "c.martin@acme.example"
    assert cands[0].confidence > guess_emails(person("Claire Martin"), "acme.example")[0].confidence


def test_published_address_comes_first() -> None:
    known = ["claire@acme.example", "jane.doe@acme.example"]
    guesses = rank_guesses("Claire Martin", "acme.example", known, ["Jane Doe"])
    assert guesses[0].address == "claire@acme.example"
    assert guesses[0].points == 50
    assert guesses[1].address == "claire.martin@acme.example"


def test_german_name_on_german_domain() -> None:
    addresses = [g.address for g in rank_guesses("Jürgen von Müller", "firma.de")]
    assert addresses[:4] == [
        "juergen.vonmueller@firma.de",
        "juergen.mueller@firma.de",
        "jurgen.vonmuller@firma.de",
        "jurgen.muller@firma.de",
    ]


def test_unusable_name_gives_no_guesses() -> None:
    assert guess_emails(person("  "), "acme.example") == []
