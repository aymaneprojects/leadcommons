"""The interface in another language: template source translation, messages, language choice."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment

from pepite.config import Settings
from pepite.web import i18n
from pepite.web.app import create_app

CATALOG = {
    "Save": "Enregistrer",
    "Saved": "Enregistré",
    "companies": "entreprises",
    "Search companies": "Rechercher des entreprises",
    "Delete this list?": "Supprimer cette liste ?",
    "Close": "Fermer l'aide",
    "Open": "Ouvrir",
    "Saved {0}.": "{0} est enregistré.",
    "Added {0} to {1}.": "{0} ajouté(s) à {1}.",
    "Looking for its people now.": "Recherche de ses contacts en cours.",
    "Country": "Pays",
    "Removed {0} compan{1} and {2} contact{3}.": "{0} entreprise{1} et {2} contact{3} retiré{3}.",
}


@pytest.fixture
def french(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """French with a small known catalog instead of the shipped one."""
    (tmp_path / "fr").mkdir()
    (tmp_path / "fr" / "test.json").write_text(json.dumps(CATALOG), encoding="utf-8")
    monkeypatch.setattr(i18n, "LOCALE_DIR", tmp_path)
    i18n.catalog.cache_clear()
    i18n._patterns.cache_clear()
    token = i18n._lang.set("fr")
    yield
    i18n._lang.reset(token)
    i18n.catalog.cache_clear()
    i18n._patterns.cache_clear()


def render(source: str, **ctx: object) -> str:
    env = Environment(extensions=[i18n.Translate], autoescape=True, finalize=i18n._finalize)
    env.pepite_lang = "fr"  # type: ignore[attr-defined]
    return env.from_string(source).render(**ctx)


# ---- template source


def test_text_and_attributes_are_translated_values_are_not(french) -> None:
    html = render(
        '<button class="btn" title="Open">Save</button>'
        '<input placeholder="Search companies"> <p>Saved {{ n }} companies</p><b>{{ name }}</b>',
        n=3,
        name="Save",  # a company called "Save" keeps its name
    )
    assert '<button class="btn" title="Ouvrir">Enregistrer</button>' in html
    assert 'placeholder="Rechercher des entreprises"' in html
    assert "<p>Enregistré 3 entreprises</p><b>Save</b>" in html


def test_a_value_inside_a_tag_does_not_confuse_the_scanner(french) -> None:
    html = render('<a href="/x/{{ id }}" title="Open" data-n="{{ n }}">Save</a> Save', id=7, n=1)
    assert html == '<a href="/x/7" title="Ouvrir" data-n="1">Enregistrer</a> Enregistrer'


def test_code_scripts_comments_and_comparisons_are_left_alone(french) -> None:
    source = (
        "<code>Save</code><pre>Save</pre><script>var Save = 1 < 2; // Save</script>"
        "<!-- Save > --><style>.Save{}</style><p>1 < 2</p><b>Save</b>"
    )
    assert render(source) == source.replace("<b>Save</b>", "<b>Enregistrer</b>")


def test_confirm_text_cannot_break_out_of_its_javascript_string(french) -> None:
    html = render("""<form onsubmit="return confirm('Delete this list?')"><b title="Close">x</b>""")
    assert "confirm('Supprimer cette liste ?')" in html
    assert 'title="Fermer l’aide"' not in html and 'title="Fermer l\'aide"' in html
    assert "confirm('Fermer l’aide')" in render("""<a onclick="confirm('Close')">x</a>""")


def test_string_literals_shown_to_the_reader_are_translated_logic_is_not(french) -> None:
    source = (
        "{% macro btn(label) %}<i>{{ label }}</i>{% endmacro %}"
        '{{ btn("Save") }}{% set t = "Open" %}{{ t }}{{ x|default("Close") }}'
        '{% if kind == "Save" %}yes{% endif %}{{ {"Save": 1}["Save"] }}'
        '{{ "Save" ~ "d" }}{% if "Save" in kinds %}in{% endif %}'
    )
    out = render(source, kind="Save", kinds=["Save"])
    assert out == "<i>Enregistrer</i>OuvrirFermer l&#39;aideyes1Savedin"


def test_python_labels_are_translated_when_printed_plain_strings_are_not(french) -> None:
    out = render("{{ a }}|{{ b }}|{{ a|lower }}", a=i18n.L("Country"), b="Country")
    assert out == "Pays|Country|country"
    assert i18n.t("Saved {0}.").format("X") == "X est enregistré."


# ---- messages


def test_messages_are_matched_to_the_text_they_were_built_from(french) -> None:
    assert i18n.message("Saved Acme & Sons.") == "Acme & Sons est enregistré."
    assert i18n.message("Added 3 to My list.") == "3 ajouté(s) à My list."
    both = i18n.message("Saved Acme. Looking for its people now.")
    assert both == "Acme est enregistré. Recherche de ses contacts en cours."
    assert i18n.message("Something nobody translated.") == "Something nobody translated."
    # English plural endings glued to a word become French ones
    plural = i18n.message("Removed 2 companies and 1 contact.")
    assert plural == "2 entreprises et 1 contact retiré."
    assert i18n.message("Saved Acme.", lang="en") == "Saved Acme."


def test_language_is_the_cookie_then_the_browser() -> None:
    assert i18n.pick("fr", "en-US,en;q=0.9") == "fr"
    assert i18n.pick(None, "fr-FR,fr;q=0.9,en;q=0.8") == "fr"
    assert i18n.pick(None, "de-DE,de;q=0.9,fr;q=0.5") == "fr"
    assert i18n.pick("xx", "de-DE") == "en" and i18n.pick(None, None) == "en"


# ---- the app

PAGES = [
    "/", "/companies", "/companies?all=1", "/people", "/lists", "/enrich", "/sequences",
    "/emails", "/calls", "/tasks", "/meetings", "/conversations", "/deals", "/workflows",
    "/workflows/new", "/analytics", "/assistant", "/onboarding", "/opt-out", "/settings",
    "/settings/account", "/settings/mailboxes", "/settings/sending", "/settings/email-health",
    "/settings/data-packs", "/settings/privacy", "/settings/deal-stages",
    "/settings/integrations", "/settings/api", "/settings/about",
]  # fmt: skip
_TAG = re.compile(r"<(/?[a-zA-Z][\w-]*)([^>]*)>")
_KEPT = re.compile(r'\b(id|class|href|name|type|action|method|value|hx-get|hx-post)="([^"]*)"')


def skeleton(html: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """Tags with the attributes that carry behaviour: what must not change with the language."""
    return [(m.group(1), tuple(_KEPT.findall(m.group(2)))) for m in _TAG.finditer(html)]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    home = tmp_path_factory.mktemp("i18n")
    settings = Settings(home=home, data_dir=home / "packs")
    settings.database_url = f"sqlite:///{home / 'db.sqlite'}"
    return TestClient(create_app(settings))


@pytest.mark.parametrize("url", PAGES)
def test_french_pages_have_the_same_structure_as_the_english_ones(
    client: TestClient, url: str
) -> None:
    english = client.get(url)
    french_page = client.get(url, headers={"accept-language": "fr-FR,fr;q=0.9"})
    assert english.status_code == french_page.status_code == 200
    assert '<html lang="fr"' in french_page.text and '<html lang="en"' in english.text
    en, fr = skeleton(english.text), skeleton(french_page.text)
    assert len(en) == len(fr)
    for a, b in zip(en, fr, strict=True):
        if a != b and a[0] == b[0] == "html":
            continue
        # an <option value> / <input value> may hold a label; everything else must match
        assert a[0] == b[0] and [x for x in a[1] if x[0] != "value"] == [
            x for x in b[1] if x[0] != "value"
        ], (url, a, b)


def test_language_choice_is_remembered_in_a_cookie(client: TestClient) -> None:
    assert '<html lang="en"' in client.get("/settings/account").text
    answer = client.post("/settings/language", data={"lang": "fr"}, follow_redirects=False)
    assert answer.status_code == 303 and "lang=fr" in answer.headers["set-cookie"]
    assert '<html lang="fr"' in client.get("/settings/account").text
    client.post("/settings/language", data={"lang": "xx"})  # unknown: ignored
    assert '<html lang="fr"' in client.get("/settings/account").text
    client.post("/settings/language", data={"lang": "en"})
    assert '<html lang="en"' in client.get("/settings/account").text


def test_toasts_are_translated_on_their_way_out() -> None:
    headers = [(b"hx-trigger", json.dumps({"toast": {"message": "Hello.", "kind": "ok"}}).encode())]
    assert list(i18n._toast_headers(headers, "en")) == headers
    assert list(i18n._toast_headers([(b"x", b"1"), (b"hx-trigger", b"not json")], "fr")) == [
        (b"x", b"1"),
        (b"hx-trigger", b"not json"),
    ]
