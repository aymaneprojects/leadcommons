"""Brønnøysund Register Centre (NLOD 2.0): roles of one Norwegian company, open API, no key.

`GET https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller` lists role groups
(daglig leder, styreleder, styremedlem...) with the people holding them. We keep people only,
not those who stepped down, and drop birth dates (the API returns them; we never store them).
"""

from __future__ import annotations

import re

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.connectors.people import CompanyRefs, OfficialPerson, tidy_case

API = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}/roller"
PUBLIC_PAGE = "https://data.brreg.no/enhetsregisteret/oppslag/enheter/{orgnr}"
# role code -> English title with the register's own word in brackets (titles.py knows both)
ROLES: dict[str, str] = {
    "DAGL": "CEO (daglig leder)",
    "LEDE": "Chair of the board (styreleder)",
    "NEST": "Deputy chair (nestleder)",
    "MEDL": "Board member (styremedlem)",
    "OBS": "Board observer (observatør)",
    "VARA": "Deputy board member (varamedlem)",
    "INNH": "Owner (innehaver)",
    "KOMP": "General partner (komplementar)",
    "DTSO": "Partner (deltaker med delt ansvar)",
    "DTPR": "Partner (deltaker med proratarisk ansvar)",
    "KONT": "Contact person (kontaktperson)",
}


def normalise_orgnr(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    return digits if len(digits) == 9 else None


def _person_name(person: dict) -> str | None:
    navn = person.get("navn") or {}
    parts = [navn.get("fornavn"), navn.get("mellomnavn"), navn.get("etternavn")]
    name = " ".join(str(p).strip() for p in parts if p and str(p).strip())
    return tidy_case(name) if len(name.split()) >= 2 else None


def parse_roles(data: dict, orgnr: str) -> list[OfficialPerson]:
    """People currently holding a role in one /roller response."""
    out = []
    for group in data.get("rollegrupper") or []:
        for role in group.get("roller") or []:
            code = ((role.get("type") or {}).get("kode") or "").upper()
            person = role.get("person")
            if code not in ROLES or not person or role.get("fratraadt") or role.get("avregistrert"):
                continue
            if person.get("erDoed"):
                continue
            name = _person_name(person)
            if name:
                out.append(
                    OfficialPerson(
                        name=name,
                        title=ROLES[code],
                        source="brreg",
                        source_url=PUBLIC_PAGE.format(orgnr=orgnr),
                        since=None,
                    )
                )
    return out


class BrregRoles:
    name = "brreg"
    label = "Brønnøysund register"
    licence = "NLOD-2.0"

    def __init__(self, client: httpx.Client | None = None, *, min_interval: float = 0.5):
        self._http = PoliteClient(client, min_interval=min_interval, timeout=30.0)

    def applies(self, refs: CompanyRefs) -> bool:
        return normalise_orgnr(refs.no_orgnr) is not None

    def fetch(self, refs: CompanyRefs) -> list[OfficialPerson]:
        orgnr = normalise_orgnr(refs.no_orgnr)
        if orgnr is None:
            return []
        try:
            data = self._http.get_json(API.format(orgnr=orgnr), {}, "application/json")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):  # unknown or deleted unit
                return []
            raise
        return parse_roles(data, orgnr)
