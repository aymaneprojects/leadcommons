"""SIRENE connector: mappings, no personal data, paging."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
import respx

from leadcommons.connectors import USER_AGENT, SireneConnector
from leadcommons.connectors.sirene import ENDPOINT, TRANCHE_TO_SIZE, parse_result, size_from_tranche
from leadcommons.models import SIZE_RANGES, make_id
from leadcommons.packs.isic import naf_section

DATA = Path(__file__).parent / "fixtures" / "data"


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text())


def conn() -> SireneConnector:
    return SireneConnector(min_interval=0, sleep=lambda s: None)


@pytest.mark.parametrize(
    ("code", "bucket"),
    [
        ("00", None), ("NN", None), (None, None), ("", None), ("99", None),
        ("01", "1-10"), ("03", "1-10"), ("11", "11-50"), ("12", "11-50"), ("21", "51-200"),
        ("22", "51-200"), ("31", "201-500"), ("32", "201-500"), ("41", "501-1000"),
        ("42", "1001-5000"), ("51", "1001-5000"), ("52", "5001-10000"), ("53", "10001+"),
    ],
)  # fmt: skip
def test_tranche_mapping(code, bucket):
    assert size_from_tranche(code) == bucket
    assert set(TRANCHE_TO_SIZE.values()) <= set(SIZE_RANGES)


@pytest.mark.parametrize(
    ("naf", "section"),
    [("64.20Z", "K"), ("6201Z", "J"), ("01.11Z", "A"), ("10.71C", "C"), ("35.11Z", "D"),
     ("41.20A", "F"), ("47.11B", "G"), ("49.10Z", "H"), ("56.10A", "I"), ("68.20A", "L"),
     ("70.22Z", "M"), ("78.10Z", "N"), ("84.11Z", "O"), ("85.20Z", "P"), ("86.10Z", "Q"),
     ("90.01Z", "R"), ("96.02A", "S"), (None, None), ("", None), ("ZZ", None), ("04.00Z", None)],
)  # fmt: skip
def test_naf_section(naf, section):
    assert naf_section(naf) == section


def test_parse_result_fields_and_filters():
    page = load("sirene_page1.json")["results"]
    carrefour = parse_result(page[0])
    assert carrefour is not None
    assert carrefour.id == make_id("co", "siren", "652014051")
    assert carrefour.name == "CARREFOUR" and carrefour.legal_name == "CARREFOUR"
    assert carrefour.country == "FR" and carrefour.city == "Massy"
    assert carrefour.region == "Île-de-France"
    assert carrefour.industry == "K" and carrefour.size_range == "10001+"
    assert carrefour.founded_year == 1963 and carrefour.domain is None and carrefour.links == {}
    assert carrefour.registry_ids == {"siren": "652014051"}
    src = carrefour.sources[0]
    assert (src.source, src.licence) == ("sirene", "etalab-2.0")
    assert src.url.endswith("/652014051")
    assert parse_result(page[1]) is None  # sole trader (nature_juridique 1xxx): a person's name
    assert parse_result(page[2]) is None  # non-diffusible
    acme = parse_result(page[3])
    assert acme.industry == "J" and acme.size_range == "11-50" and acme.city == "Saint-Denis"
    assert acme.legal_name == "ACME FRANCE SAS"
    assert parse_result({"siren": "12", "nom_complet": "x"}) is None


def test_no_personal_data_leaks():
    for res in load("sirene_page1.json")["results"]:
        company = parse_result(res)
        if company:
            dump = json.dumps(asdict(company), default=str).upper()
            for banned in ("DUPONT", "JEANNE", "DURAND", "PAUL", "1970", "MARTIN"):
                assert banned not in dump


@respx.mock
def test_iter_companies_paging_and_params():
    route = respx.get(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=load("sirene_page1.json")),
            httpx.Response(200, json=load("sirene_page2.json")),
        ]
        + [httpx.Response(200, json={"results": [], "total_pages": 0})] * 20
    )
    out = list(conn().iter_companies(country="fr"))
    assert [c.registry_ids["siren"] for c in out] == ["652014051", "552100554", "300000001"]
    p = route.calls[0].request.url.params
    assert p["etat_administratif"] == "A" and p["tranche_effectif_salarie"] == "53"
    assert p["per_page"] == "25" and p["page"] == "1"
    assert route.calls[1].request.url.params["page"] == "2"
    assert route.calls[0].request.headers["user-agent"] == USER_AGENT


@respx.mock
def test_limit_and_other_countries():
    route = respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=load("sirene_page1.json"))
    )
    assert len(list(conn().iter_companies(country="FR", limit=1))) == 1
    assert route.call_count == 1
    assert list(conn().iter_companies(country="DE")) == []
    assert route.call_count == 1


@respx.mock
def test_fetch_by_siren():
    route = respx.get(ENDPOINT).mock(
        return_value=httpx.Response(200, json=load("sirene_page1.json"))
    )
    out = list(conn().fetch_by_siren(["552100554", "552100554"]))
    assert [c.name for c in out] == ["ACME FRANCE"]
    assert route.call_count == 1 and route.calls[0].request.url.params["q"] == "552100554"
