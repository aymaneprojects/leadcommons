"""GLEIF connector (CC0): active legal entities from the public LEI records API."""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.models import Company, Provenance, make_id

ENDPOINT = "https://api.gleif.org/api/v1/lei-records"


def parse_record(rec: dict) -> Company | None:
    """Normalise one GLEIF lei-record (JSON:API resource) into a Company."""
    lei = (rec.get("id") or rec.get("attributes", {}).get("lei") or "").strip().upper()
    entity = rec.get("attributes", {}).get("entity", {})
    name = ((entity.get("legalName") or {}).get("name") or "").strip()
    if not lei or not name:
        return None
    addr = entity.get("legalAddress") or {}
    region = addr.get("region") or None
    if region and "-" in region:  # ISO 3166-2 like "FR-75" -> keep the subdivision part
        region = region.split("-", 1)[1]
    country = (addr.get("country") or "").upper() or None
    return Company(
        id=make_id("co", "lei", lei),
        name=name,
        legal_name=name,
        country=country,
        region=region,
        city=(addr.get("city") or None),
        registry_ids={"lei": lei},
        sources=[Provenance("gleif", "CC0-1.0", f"https://search.gleif.org/#/record/{lei}")],
    )


class GleifConnector:
    """Pages through GLEIF LEI records for a country (no website data)."""

    name = "gleif"
    licence = "CC0-1.0"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        page_size: int = 200,
        min_interval: float = 1.0,
        sleep=None,
    ) -> None:
        kwargs = {"sleep": sleep} if sleep else {}
        self._http = PoliteClient(client, min_interval=min_interval, **kwargs)
        self.page_size = page_size

    def iter_companies(
        self, *, country: str | None = None, limit: int | None = None
    ) -> Iterator[Company]:
        """Yield active entities, page by page, until `limit` or the last page."""
        number = 1
        emitted = 0
        while limit is None or emitted < limit:
            params: dict[str, str | int] = {
                "filter[entity.status]": "ACTIVE",
                "page[size]": self.page_size,
                "page[number]": number,
            }
            if country:
                params["filter[entity.legalAddress.country]"] = country.upper()
            data = self._http.get_json(ENDPOINT, params, "application/vnd.api+json")
            rows = data.get("data", [])
            for rec in rows:
                company = parse_record(rec)
                if company is None:
                    continue
                emitted += 1
                yield company
                if limit is not None and emitted >= limit:
                    return
            last = data.get("meta", {}).get("pagination", {}).get("lastPage")
            if not rows or (last is not None and number >= last):
                return
            number += 1

    def fetch_by_lei(self, leis: list[str], batch: int = 100) -> Iterator[Company]:
        """Yield GLEIF records for the given LEIs (used to enrich Wikidata rows)."""
        for i in range(0, len(leis), batch):
            chunk = [x.strip().upper() for x in leis[i : i + batch] if x.strip()]
            if not chunk:
                continue
            params: dict[str, str | int] = {
                "filter[lei]": ",".join(chunk),
                "page[size]": self.page_size,
            }
            data = self._http.get_json(ENDPOINT, params, "application/vnd.api+json")
            for rec in data.get("data", []):
                company = parse_record(rec)
                if company is not None:
                    yield company
