"""SIRENE connector (Licence Ouverte / etalab-2.0): French companies via the public
recherche-entreprises API.

Company-level fields only. The API also returns officers (`dirigeants`) and sole traders'
names; those are personal data and are never read, stored or emitted here.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.models import Company, Provenance, make_id
from leadcommons.packs.isic import SECTION_NAMES, naf_section

ENDPOINT = "https://recherche-entreprises.api.gouv.fr/search"

# INSEE `tranche_effectif_salarie` code -> SIZE_RANGES bucket.
# 01: 1-2, 02: 3-5, 03: 6-9 -> "1-10"; 11: 10-19, 12: 20-49 -> "11-50"; 21: 50-99,
# 22: 100-199 -> "51-200"; 31: 200-249, 32: 250-499 -> "201-500"; 41: 500-999 -> "501-1000";
# 42: 1000-1999, 51: 2000-4999 -> "1001-5000"; 52: 5000-9999 -> "5001-10000"; 53: 10000+.
# 00 (zero employees) and NN (non-employer / not stated) are unknown -> None.
TRANCHE_TO_SIZE: dict[str, str] = {
    "01": "1-10", "02": "1-10", "03": "1-10",
    "11": "11-50", "12": "11-50",
    "21": "51-200", "22": "51-200",
    "31": "201-500", "32": "201-500",
    "41": "501-1000",
    "42": "1001-5000", "51": "1001-5000",
    "52": "5001-10000",
    "53": "10001+",
}  # fmt: skip

# Biggest first, so a limited crawl returns the most prospect-worthy companies.
TRANCHES_DESC = ("53", "52", "51", "42", "41", "32", "31", "22", "21", "12", "11", "03", "02", "01")

# INSEE region codes (siege.region) -> names.
REGIONS: dict[str, str] = {
    "01": "Guadeloupe", "02": "Martinique", "03": "Guyane", "04": "La Réunion", "06": "Mayotte",
    "11": "Île-de-France", "24": "Centre-Val de Loire", "27": "Bourgogne-Franche-Comté",
    "28": "Normandie", "32": "Hauts-de-France", "44": "Grand Est", "52": "Pays de la Loire",
    "53": "Bretagne", "75": "Nouvelle-Aquitaine", "76": "Occitanie",
    "84": "Auvergne-Rhône-Alpes", "93": "Provence-Alpes-Côte d'Azur", "94": "Corse",
}  # fmt: skip


def size_from_tranche(code: str | None) -> str | None:
    """Map an INSEE workforce code to a SIZE_RANGES bucket (None for 00/NN/unknown)."""
    return TRANCHE_TO_SIZE.get((code or "").strip())


def parse_result(res: dict) -> Company | None:
    """Normalise one API result; returns None for sole traders, closed or non-public units."""
    siren = "".join(ch for ch in str(res.get("siren") or "") if ch.isdigit())
    if len(siren) != 9:
        return None
    if str(res.get("nature_juridique") or "").startswith("1"):
        return None  # entrepreneur individuel: the name is a natural person's name
    if res.get("statut_diffusion") not in (None, "O"):
        return None  # non-diffusible
    if res.get("etat_administratif") not in (None, "A"):
        return None
    legal = (res.get("nom_raison_sociale") or res.get("nom_complet") or "").strip()
    name = (res.get("nom_complet") or legal).strip()
    if not name:
        return None
    siege = res.get("siege") or {}
    city = (siege.get("libelle_commune") or "").strip().title() or None
    section = (res.get("section_activite_principale") or "").strip().upper() or naf_section(
        res.get("activite_principale")
    )
    created = str(res.get("date_creation") or "")[:4]
    founded = int(created) if created.isdigit() and 1500 <= int(created) <= 2100 else None
    return Company(
        id=make_id("co", "siren", siren),
        name=name,
        legal_name=legal or None,
        country="FR",
        region=REGIONS.get(str(siege.get("region") or "")),
        city=city,
        industry=section,
        industry_label=SECTION_NAMES.get(section) if section else None,
        size_range=size_from_tranche(res.get("tranche_effectif_salarie")),
        founded_year=founded,
        registry_ids={"siren": siren},
        sources=[
            Provenance(
                "sirene",
                "etalab-2.0",
                f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}",
            )
        ],
    )


class SireneConnector:
    """French companies from SIRENE (active, non-sole-trader), biggest workforce first."""

    name = "sirene"
    licence = "etalab-2.0"
    countries = ("FR",)

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        per_page: int = 25,
        min_interval: float = 0.3,  # ~3 req/s, well below the documented 7 req/s
        sleep=None,
        max_pages: int = 400,
    ) -> None:
        kwargs = {"sleep": sleep} if sleep else {}
        self._http = PoliteClient(client, min_interval=min_interval, **kwargs)
        self.per_page = min(25, per_page)
        self.max_pages = max_pages

    def _search(self, params: dict[str, str | int]) -> dict:
        return self._http.get_json(ENDPOINT, params, "application/json")

    def iter_companies(
        self, *, country: str | None = None, limit: int | None = None
    ) -> Iterator[Company]:
        """Yield active companies per workforce band (largest first) until `limit`."""
        if country and country.upper() != "FR":
            return
        emitted = 0
        seen: set[str] = set()
        for tranche in TRANCHES_DESC:
            page = 1
            while limit is None or emitted < limit:
                data = self._search(
                    {
                        "etat_administratif": "A",
                        "tranche_effectif_salarie": tranche,
                        "per_page": self.per_page,
                        "page": page,
                    }
                )
                results = data.get("results") or []
                for res in results:
                    company = parse_result(res)
                    if company is None or company.registry_ids["siren"] in seen:
                        continue
                    seen.add(company.registry_ids["siren"])
                    emitted += 1
                    yield company
                    if limit is not None and emitted >= limit:
                        return
                total_pages = int(data.get("total_pages") or 0)
                if not results or page >= min(total_pages, self.max_pages):
                    break
                page += 1

    def fetch_by_siren(self, sirens: list[str]) -> Iterator[Company]:
        """Yield the record of each SIREN (one request per number)."""
        for siren in dict.fromkeys(s for s in sirens if s):
            data = self._search({"q": siren, "per_page": 1, "page": 1})
            for res in data.get("results") or []:
                if str(res.get("siren")) == siren:
                    company = parse_result(res)
                    if company is not None:
                        yield company
