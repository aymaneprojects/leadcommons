"""SEC EDGAR connector (public domain): US-listed filers via company_tickers + submissions."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.connectors.base import USER_AGENT
from leadcommons.models import Company, Provenance, make_id
from leadcommons.packs.domains import normalize_domain
from leadcommons.packs.isic import sic_section

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CONTACT_ENV = "LEADCOMMONS_CONTACT_EMAIL"

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI".split()
)


def edgar_user_agent() -> str:
    """USER_AGENT plus the operator's contact email when LEADCOMMONS_CONTACT_EMAIL is set."""
    email = os.environ.get(CONTACT_ENV, "").strip()
    return f"{USER_AGENT} contact:{email}" if email else USER_AGENT


def pad_cik(cik: str | int | None) -> str | None:
    """Zero-pad a CIK to 10 digits (None if not numeric)."""
    digits = str(cik or "").strip()
    return digits.zfill(10) if digits.isdigit() and int(digits) > 0 else None


def parse_submission(data: dict) -> Company | None:
    """Normalise a data.sec.gov submissions document into a Company."""
    cik = pad_cik(data.get("cik"))
    name = (data.get("name") or "").strip()
    if not cik or not name:
        return None
    biz = (data.get("addresses") or {}).get("business") or {}
    state = (biz.get("stateOrCountry") or "").strip().upper()
    in_us = state in US_STATES
    website = (data.get("website") or "").strip()
    if website and "://" not in website:
        website = "http://" + website
    sic = str(data.get("sic") or "").strip()
    return Company(
        id=make_id("co", "cik", cik),
        name=name,
        legal_name=name,
        domain=normalize_domain(website),
        country="US" if in_us else None,
        region=state if in_us else None,
        city=(biz.get("city") or "").strip().title() or None,
        industry=sic_section(sic),
        industry_label=(data.get("sicDescription") or "").strip() or None,
        registry_ids={"cik": cik},
        links={"website": website} if website else {},
        sources=[
            Provenance(
                "edgar",
                "public-domain",
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}",
            )
        ],
    )


class EdgarConnector:
    """US filers from SEC EDGAR; needs a declared contact (LEADCOMMONS_CONTACT_EMAIL)."""

    name = "edgar"
    licence = "public-domain"
    countries = ("US",)

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        min_interval: float = 0.5,  # 2 req/s; SEC allows 10
        sleep=None,
    ) -> None:
        kwargs = {"sleep": sleep} if sleep else {}
        self._http = PoliteClient(
            client, min_interval=min_interval, user_agent=edgar_user_agent(), **kwargs
        )
        self._tickers: list[str] | None = None
        self._failures = 0

    def _get(self, url: str) -> dict:
        try:
            return self._http.get_json(url, {}, "application/json")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise RuntimeError(
                    f"SEC EDGAR refused the request (403). Set {CONTACT_ENV} to a contact "
                    "email so requests declare who is behind them."
                ) from exc
            raise

    def ciks(self) -> list[str]:
        """CIKs of all ticker-listed companies, in the SEC file's order (deduplicated)."""
        if self._tickers is None:
            data = self._get(TICKERS_URL)
            rows = data.values() if isinstance(data, dict) else data
            ordered = (pad_cik(r.get("cik_str")) for r in rows if isinstance(r, dict))
            self._tickers = list(dict.fromkeys(c for c in ordered if c))
        return self._tickers

    def _details(self, cik: str) -> Company | None:
        try:
            company = parse_submission(self._get(SUBMISSIONS_URL.format(cik=cik)))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            return self._skip(cik, exc)
        except httpx.HTTPError as exc:
            return self._skip(cik, exc)
        self._failures = 0
        return company

    def _skip(self, cik: str, exc: Exception) -> None:
        log.warning("edgar: CIK %s failed, skipped (%s)", cik, exc)
        self._failures += 1
        if self._failures >= 5:  # the service is clearly unhealthy: stop
            raise exc
        return None

    def iter_companies(
        self, *, country: str | None = None, limit: int | None = None
    ) -> Iterator[Company]:
        """Yield US-addressed filers (details fetched one at a time) until `limit`."""
        if country and country.upper() != "US":
            return
        emitted = 0
        for cik in self.ciks():
            if limit is not None and emitted >= limit:
                return
            company = self._details(cik)
            if company is None or company.country != "US":
                continue
            emitted += 1
            yield company

    def fetch_by_cik(self, ciks: list[str]) -> Iterator[Company]:
        """Yield submissions details for each CIK (used to enrich Wikidata rows)."""
        for cik in dict.fromkeys(pad_cik(c) for c in ciks):
            if cik and (company := self._details(cik)) is not None:
                yield company
