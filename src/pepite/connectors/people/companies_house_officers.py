"""Companies House (OGL v3): active directors and secretaries of one UK company.

Needs a free API key (https://developer.company-information.service.gov.uk/), read from
PEPITE_COMPANIES_HOUSE_KEY and sent as the basic-auth user name. Without it the source is
skipped with a reason the UI shows. Only what a prospecting list needs is kept: name, role and
appointment date. Dates of birth, home addresses, nationality and occupation are dropped.
"""

from __future__ import annotations

import os

import httpx

from pepite.connectors._http import PoliteClient
from pepite.connectors.people import (
    CompanyRefs,
    OfficialPerson,
    SourceSkipped,
    last_first_to_display,
)

KEY_ENV = "PEPITE_COMPANIES_HOUSE_KEY"
API = "https://api.company-information.service.gov.uk"
PUBLIC_PAGE = "https://find-and-update.company-information.service.gov.uk/company/{number}/officers"
PAGE_SIZE = 100
MAX_PAGES = 3
# officer_role -> title. Corporate officers (corporate-director ...) are companies: not listed.
ROLES: dict[str, str] = {
    "director": "Director",
    "nominee-director": "Director",
    "secretary": "Company secretary",
    "nominee-secretary": "Company secretary",
    "llp-member": "LLP member",
    "llp-designated-member": "LLP designated member",
    "judicial-factor": "Judicial factor",
}


def normalise_number(number: str | None) -> str | None:
    """8-character company number: digits are zero-padded, prefixes (SC, NI, OC) kept."""
    n = (number or "").strip().upper().replace(" ", "")
    if not n or not n.isalnum():
        return None
    return n.zfill(8) if n.isdigit() else n


def parse_officers(data: dict, number: str) -> list[OfficialPerson]:
    """Active appointments of one /officers response."""
    out = []
    for item in data.get("items") or []:
        role = str(item.get("officer_role") or "").lower()
        if item.get("resigned_on") or role not in ROLES:
            continue
        raw = str(item.get("name") or "").strip()
        if not raw:
            continue
        out.append(
            OfficialPerson(
                name=last_first_to_display(raw),
                title=ROLES[role],
                source="companies-house",
                source_url=PUBLIC_PAGE.format(number=number),
                since=item.get("appointed_on") or None,
            )
        )
    return out


class CompaniesHouseOfficers:
    name = "companies-house"
    label = "Companies House"
    licence = "OGL-3.0"

    def __init__(self, client: httpx.Client | None = None, *, min_interval: float = 0.6):
        # The API allows 600 requests per 5 minutes; we use one to three.
        self._http = PoliteClient(client, min_interval=min_interval, timeout=30.0)

    def applies(self, refs: CompanyRefs) -> bool:
        return normalise_number(refs.gb_number) is not None

    def fetch(self, refs: CompanyRefs) -> list[OfficialPerson]:
        number = normalise_number(refs.gb_number)
        if number is None:
            return []
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise SourceSkipped(
                "Companies House officers need a free API key. Create one at "
                f"developer.company-information.service.gov.uk and set {KEY_ENV}."
            )
        out: list[OfficialPerson] = []
        for page in range(MAX_PAGES):
            try:
                data = self._http.get_json(
                    f"{API}/company/{number}/officers",
                    {"items_per_page": PAGE_SIZE, "start_index": page * PAGE_SIZE},
                    "application/json",
                    auth=(key, ""),
                )
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 404:
                    return out
                if code in (401, 403):
                    raise SourceSkipped(
                        f"Companies House rejected the API key in {KEY_ENV} ({code}). "
                        "Check that it is a REST key and not a stream key."
                    ) from exc
                raise
            out.extend(parse_officers(data, number))
            if (page + 1) * PAGE_SIZE >= int(data.get("total_results") or 0):
                break
        return out
