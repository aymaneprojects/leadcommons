"""Name normalisation, pattern inference and candidate generation."""

from __future__ import annotations

import pytest

from leadcommons.enrich import guess_emails
from leadcommons.enrich.names import (
    clean_name,
    country_hint,
    email_variants,
    name_key,
    name_variants,
)
from leadcommons.enrich.patterns import (
    PATTERNS,
    infer_patterns,
    is_role_mailbox,
    learn_format,
    rank_guesses,
    render,
    shape_patterns,
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
    assert confidences[0] <= 45  # the most common format worldwide, nothing more


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
    assert guesses[0].probability >= 0.75  # {first}: a namesake could own it, so not 0.95
    assert guesses[1].address == "claire.martin@acme.example"


def test_published_full_name_address_and_namesakes() -> None:
    known = ["claire.martin@acme.example"]
    alone = rank_guesses("Claire Martin", "acme.example", known, ["Claire Martin"])
    assert alone[0].address == known[0] and alone[0].probability >= 0.9
    twins = rank_guesses("Claire Martin", "acme.example", known, ["Claire Martin"] * 2)
    assert twins[0].probability < 0.5  # two Claire Martins on the site: could be the other one


def test_german_name_on_german_domain() -> None:
    addresses = [g.address for g in rank_guesses("Jürgen von Müller", "firma.de")]
    spellings = [
        "juergen.vonmueller@firma.de",
        "juergen.mueller@firma.de",
        "jurgen.vonmuller@firma.de",
        "jurgen.muller@firma.de",
    ]
    assert addresses[0] == spellings[0]
    assert [a for a in addresses[:8] if a in spellings] == spellings


def test_unusable_name_gives_no_guesses() -> None:
    assert guess_emails(person("  "), "acme.example") == []


# ----------------------------------------------------------------- probabilities and house rules


def test_probabilities_are_ranked_and_leave_room_for_irregulars() -> None:
    guesses = rank_guesses("Claire Martin", "acme.example", ["jane.doe@acme.example"], ["Jane Doe"])
    probs = [g.probability for g in guesses]
    assert probs == sorted(probs, reverse=True)
    assert 0 < sum(probs) < 1  # some people's addresses follow no pattern


def test_more_consistent_evidence_means_more_confidence() -> None:
    names = ["Jane Doe", "Robert Smith", "Ann Lee", "Omar Haddad"]
    emails = ["jane.doe", "robert.smith", "ann.lee", "omar.haddad"]
    p1 = rank_guesses("Claire Martin", "acme.example", [emails[0] + "@acme.example"], names)
    p4 = rank_guesses("Claire Martin", "acme.example", [e + "@acme.example" for e in emails], names)
    assert p1[0].address == p4[0].address == "claire.martin@acme.example"
    assert p4[0].probability > p1[0].probability > 0.5


def test_mixed_formats_keep_both() -> None:
    names = ["Jane Doe", "Robert Smith", "Ann Lee", "Omar Haddad", "Eva Berg"]
    known = ["jane.doe", "robert.smith", "ann.lee", "ohaddad", "eberg"]
    fmt = learn_format([k + "@acme.example" for k in known], "acme.example", names)
    shares = {s.pattern: s.probability for s in fmt.ranked()}
    assert 0.5 < shares["{first}.{last}"] < 0.7
    assert 0.3 < shares["{f}{last}"] < 0.45
    assert "3 of 5" in fmt.describe("{first}.{last}")


def test_house_rule_umlauts_are_learned() -> None:
    names = ["Jörg Müller", "Bärbel Krämer"]
    plain = ["jorg.muller@firma.de", "barbel.kramer@firma.de"]
    expanded = ["joerg.mueller@firma.de", "baerbel.kraemer@firma.de"]
    assert rank_guesses("Jürgen Köhler", "firma.de", plain, names)[0].address == (
        "jurgen.kohler@firma.de"
    )
    assert rank_guesses("Jürgen Köhler", "firma.de", expanded, names)[0].address == (
        "juergen.koehler@firma.de"
    )


def test_house_rule_particles_and_hyphens_are_learned() -> None:
    names = ["Jan de Vries", "Pieter van Dijk", "Anne-Marie Bos"]
    known = ["jan.vries@bedrijf.nl", "pieter.dijk@bedrijf.nl", "annemarie.bos@bedrijf.nl"]
    top = rank_guesses("Joost van den Berg", "bedrijf.nl", known, names)[0]
    assert top.address == "joost.berg@bedrijf.nl"
    top = rank_guesses("Jean-Luc Smit", "bedrijf.nl", known, names)[0]
    assert top.address == "jeanluc.smit@bedrijf.nl"


def test_spanish_company_with_joined_surnames() -> None:
    """Colleagues show both surnames, joined in addresses; a page showing one surname leaves
    the second unknown, so the guess from it is not trusted."""
    names = ["Luis Jiménez Pérez", "Marta Ruiz López", "Ana García Sanz"]
    known = [
        "luis.jimenezperez@empresa.es",
        "marta.ruizlopez@empresa.es",
        "ana.garciasanz@empresa.es",
    ]
    both = rank_guesses("Pablo Gómez Molina", "empresa.es", known, names)[0]
    assert both.address == "pablo.gomezmolina@empresa.es"
    one = rank_guesses("Pilar Santos", "empresa.es", known, names)[0]
    assert one.probability < 0.4 < both.probability


def test_cyrillic_and_greek_names_are_transliterated() -> None:
    addresses = [g.address for g in rank_guesses("Юлия Смирнова", "firma.ru")][:6]
    assert "yulia.smirnova@firma.ru" in addresses
    assert "iuliia.smirnova@firma.ru" in addresses
    assert rank_guesses("Γιώργος Παπαδόπουλος", "etairia.gr")[0].address == (
        "giorgos.papadopoulos@etairia.gr"
    )
    assert rank_guesses("王伟", "gongsi.cn") == []  # no spelling without a dictionary


def test_east_asian_name_order() -> None:
    def top(name: str, domain: str) -> tuple[str, str]:
        v = email_variants(name, domain)[0]
        return v.first, v.last

    assert top("ZHANG Wei", "gongsi.cn") == ("wei", "zhang")  # in capitals: the family name
    assert top("Wei Zhang", "acme.example") == ("wei", "zhang")
    assert top("Tanaka Hiroshi", "kaisha.jp") == ("hiroshi", "tanaka")
    assert top("Lee Smith", "acme.example") == ("lee", "smith")
    names = ["Wang Fang", "Liu Yang"]
    known = ["wang.fang@gongsi.cn", "liu.yang@gongsi.cn"]  # family name first in addresses
    assert rank_guesses("Chen Jing", "gongsi.cn", known, names)[0].address == (
        "chen.jing@gongsi.cn"
    )


def test_arabic_names() -> None:
    v = email_variants("Khalid bin Saeed Al-Harbi", "sharika.sa")[0]
    assert (v.first, v.last) == ("khalid", "alharbi")  # the patronymic is never in an address
    spellings = {(v.first, v.last) for v in email_variants("Karim Ben Youssef", "x.ma")}
    assert ("karim", "benyoussef") in spellings


def test_nicknames_are_candidates() -> None:
    addresses = [g.address for g in rank_guesses("Robert Smith", "acme.example")]
    assert addresses[0] == "robert.smith@acme.example"
    assert "bob.smith@acme.example" in addresses


@pytest.mark.parametrize(
    ("local", "pattern"),
    [
        ("a.durand", "{f}.{last}"),
        ("anna.berg", "{first}.{last}"),
        ("berg.anna", "{last}.{first}"),
        ("anna_berg", "{first}_{last}"),
        ("anna", "{first}"),
        ("annab", "{first}{l}"),
        ("annaberg", "{first}{last}"),
        ("jdurand", "{f}{last}"),
    ],
)
def test_shape_of_unnamed_addresses(local: str, pattern: str) -> None:
    shapes = shape_patterns(local)
    assert max(shapes, key=shapes.__getitem__) == pattern


def test_country_hint() -> None:
    assert country_hint("firma.de") == "de"
    assert country_hint("acme.co.uk") == "uk"
    assert country_hint("acme.com.mx") == "mx"
    assert country_hint("acme.com") == ""
    assert country_hint("firma.de.example") == "de"  # test fixtures under .example
