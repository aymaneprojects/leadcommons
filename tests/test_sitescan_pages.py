from __future__ import annotations

from pepite.sitescan.pages import (
    GUESS_PATHS,
    guess_paths,
    page_title,
    rank_links,
    switchboard_phones,
    visible_text,
)

FR_HOME = """<!doctype html>
<html><head>
<title>  Acme   SAS |  Accueil </title>
<meta property="og:site_name" content="Acme">
<script>var hidden = '<a href="/mentions-script">Mentions</a>';</script>
</head>
<body>
<nav>
  <a href="/">Accueil</a>
  <a href="/produits">Produits</a>
  <a href="/qui-sommes-nous">Qui sommes-nous</a>
</nav>
<main><p>Acme conçoit des outils de gestion.</p></main>
<footer>
  <p>Acme SAS, 12 rue des Lilas, 75011 Paris</p>
  <a href="https://www.acme.fr/mentions-legales?utm_source=newsletter&amp;utm_medium=mail#top"
     >Mentions légales</a>
  <a href="mailto:contact@acme.fr">Écrire</a>
  <a href="tel:+33100000000">Appeler</a>
  <a href="https://www.autre-site.com/mentions-legales">Mentions légales</a>
  <a href="/contact">Contact</a>
  <p>RCS Paris 512 345 678</p>
</footer>
</body></html>
"""

DE_HOME = """<html><head><title>Müller GmbH</title></head><body>
<ul>
  <li><a href="leistungen/">Leistungen</a></li>
  <li><a href="http://mueller-gmbh.de/kontakt/">Kontakt</a></li>
</ul>
<footer>
  <a href="impressum.html">Impressum</a> |
  <a href="https://shop.mueller-gmbh.de/impressum">Impressum Shop</a>
</footer>
</body></html>
"""

EN_HOME = """<html><head>
<meta property="og:site_name" content="Brook &amp; Co Ltd">
<title></title>
</head><body>
<header><a href="/">Home</a><a href="/about-us">About us</a></header>
<footer>
  <a href="https://brookandco.co.uk/legal-notice/#company">Legal notice</a>
  <a href="/contact-us">Contact us</a>
  <a href="https://brookandco.co.uk/">Home</a>
  <a href="https://facebook.com/brookandco">Legal</a>
  <a href="/brochure.pdf">Legal brochure</a>
</footer>
</body></html>
"""

HOME = "https://acme.fr/"


def test_guess_paths_by_country_most_likely_first():
    assert guess_paths("FR") == GUESS_PATHS["FR"]
    assert guess_paths("fr")[0] == "/mentions-legales"
    assert "/contact" in guess_paths("GB") and "/legal-notice" in guess_paths("GB")
    assert guess_paths("DE")[0] == "/impressum"
    assert guess_paths("IT") == ["/note-legali", "/chi-siamo", "/contatti"]


def test_guess_paths_limits_and_unknown_country():
    for country, paths in GUESS_PATHS.items():
        assert len(guess_paths(country)) <= 6, country
        assert all(p.startswith("/") for p in paths), country
    unknown = guess_paths("ZZ")
    assert unknown and len(unknown) <= 6
    assert guess_paths(None) == unknown


def test_rank_links_french_footer_legal_first_and_ignores_foreign_links():
    links = rank_links(FR_HOME, HOME, "acme.fr")
    assert links[0] == "https://www.acme.fr/mentions-legales"  # tracking params and fragment gone
    assert "https://www.autre-site.com/mentions-legales" not in links
    assert all("mailto:" not in u and "tel:" not in u for u in links)
    assert "https://acme.fr/qui-sommes-nous" in links  # about comes after legal
    assert links.index("https://acme.fr/qui-sommes-nous") < links.index("https://acme.fr/contact")
    assert HOME not in links and "https://acme.fr/" not in links
    assert "https://acme.fr/mentions-script" not in links  # inside a script, not a link
    assert len(links) == 3


def test_rank_links_german_relative_and_subdomain_links():
    links = rank_links(DE_HOME, "https://mueller-gmbh.de/", "mueller-gmbh.de")
    assert links[0] == "https://mueller-gmbh.de/impressum.html"
    assert "https://shop.mueller-gmbh.de/impressum" in links  # subdomain is the same domain
    assert "http://mueller-gmbh.de/kontakt/" in links  # http is allowed, kept as written
    assert "https://mueller-gmbh.de/leistungen/" not in links  # not a legal, about or contact link


def test_rank_links_english_limit_dedupe_and_skips():
    links = rank_links(EN_HOME, "https://brookandco.co.uk/", "www.brookandco.co.uk", limit=2)
    assert links == [
        "https://brookandco.co.uk/legal-notice/",
        "https://brookandco.co.uk/about-us",
    ]
    all_links = rank_links(EN_HOME, "https://brookandco.co.uk/", "brookandco.co.uk", limit=10)
    assert "https://facebook.com/brookandco" not in all_links
    assert not any(u.endswith(".pdf") for u in all_links)
    assert len(all_links) == len(set(all_links))
    assert "https://brookandco.co.uk/contact-us" in all_links


def test_page_title_rules():
    assert page_title(FR_HOME) == "Acme SAS | Accueil"
    assert page_title("<title>x" + "y" * 300 + "</title>") == "x" + "y" * 199
    no_title = '<meta property="og:site_name" content=" Brook  &amp; Co "><title> </title>'
    assert page_title(no_title) == "Brook & Co"
    assert page_title("<html><body>nothing</body></html>") is None


def test_visible_text_keeps_footer_and_drops_scripts():
    text = visible_text(FR_HOME)
    assert "Acme conçoit des outils de gestion." in text
    assert "RCS Paris 512 345 678" in text  # footer kept
    assert "Mentions légales" in text
    assert "var hidden" not in text and "mentions-script" not in text
    assert "Accueil" in text and "Qui sommes-nous" in text
    assert "\n" in text  # one line per block


def test_visible_text_limit_and_empty():
    assert len(visible_text(FR_HOME, limit=30)) == 30
    assert visible_text("") == ""
    assert visible_text("<style>p{}</style><script>x()</script>") == ""


def test_switchboard_phones_labelled_landline_kept_and_normalised():
    text = "Acme SAS. Tél : 01 23 45 67 89. Siège social, Paris."
    assert switchboard_phones(text, "FR") == ["+33123456789"]


def test_switchboard_phones_drops_fax_mobile_and_unlabelled():
    text = (
        "Tél : 01 23 45 67 89\n"
        "Fax : 01 23 45 67 90\n"
        "Mobile : 06 12 34 56 78\n"
        "Le standard est au 02 00 00 00 00\n"
        "Numéro SIREN : 01 02 03 04 05\n"
    )
    phones = switchboard_phones(text, "FR")
    assert phones == ["+33123456789", "+33200000000"]


def test_switchboard_phones_german_and_gb_normalisation_and_mobiles():
    de = "Telefon: 030 1234567 | Telefax: 030 7654321 | Mobil 0171 2345678"
    assert switchboard_phones(de, "DE") == ["+4930" + "1234567"]
    gb = "Tel: 020 7946 0000 · Mobile 07700 900123 · Phone +44 20 7946 0001"
    assert switchboard_phones(gb, "GB") == ["+442079460000", "+442079460001"]


def test_switchboard_phones_other_countries_and_dedupe_limit():
    no = "Telefon: 22 33 44 55 · Mobil 912 34 567 · Telefon: 22 33 44 55"
    assert switchboard_phones(no, "NO") == ["+4722334455"]
    es = "Teléfono: 91 123 45 67"
    assert switchboard_phones(es, "ES") == ["+34911234567"]
    us = "Phone: (212) 555-0100 Telephone: 212 555 0101 Tel: 212 555 0102 Tel: 212 555 0103"
    assert switchboard_phones(us, "US") == ["+12125550100", "+12125550101", "+12125550102"]
    nothing_known = "Tél : 01 23 45 67 89"
    assert switchboard_phones(nothing_known, None) == []
    assert switchboard_phones("Tel: +33 1 23 45 67 89", None) == ["+33123456789"]
    assert switchboard_phones("Tel: 1234", "FR") == []  # too short


def test_international_mobiles_are_dropped_too() -> None:
    text = "Tél : +33 6 12 34 56 78 — Standard : +33 1 40 00 00 00 — Phone: +44 7700 900123"
    assert switchboard_phones(text, "FR") == ["+33140000000"]
