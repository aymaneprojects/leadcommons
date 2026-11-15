"""SEC EDGAR insider filings (public domain): officers and directors of one US filer.

Forms 3, 4 and 5 name each reporting owner with `<officerTitle>` and `<isDirector>` flags. We read
the newest filings of the issuer (data.sec.gov submissions), keep the latest statement per owner
and return those who are officers or directors in it. "Current" means: reported in the last
24 months and not marked as no longer an officer or director in their latest filing.

Fair use: declared contact (LEADCOMMONS_CONTACT_EMAIL) and at most 10 requests per second.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx

from leadcommons.connectors._http import PoliteClient
from leadcommons.connectors.edgar import CONTACT_ENV, SUBMISSIONS_URL, edgar_user_agent, pad_cik
from leadcommons.connectors.people import (
    CompanyRefs,
    OfficialPerson,
    SourceSkipped,
    last_first_to_display,
)

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{dashed}-index.htm"
FORMS = frozenset({"3", "4", "5", "3/A", "4/A", "5/A"})
WINDOW_DAYS = 730
MAX_FILINGS = 60
MIN_INTERVAL = 0.15  # about 7 requests per second, under the SEC's 10
# Reporting owners that are funds or companies, not people.
_ENTITY = re.compile(
    r"\b(llc|l\.?l\.?c|l\.?p|lp|inc|corp|co|ltd|plc|trust|fund|funds|capital|partners|holdings|"
    r"group|bank|management|investments?|advisors|advisers|ventures|foundation|company|n\.?v|"
    r"s\.?a|gmbh|ag)\b\.?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Owner:
    """One reporting owner as written in one filing."""

    cik: str
    name: str
    is_officer: bool
    is_director: bool
    officer_title: str | None
    other_text: str | None
    filed: date
    accession: str


def _flag(node: ET.Element | None, tag: str) -> bool:
    text = (node.findtext(tag) or "").strip().lower() if node is not None else ""
    return text in {"1", "true"}


def parse_ownership(xml_text: str, filed: date, accession: str) -> list[Owner]:
    """Reporting owners of one Form 3/4/5 document (empty list when it is not parseable)."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:  # no DTDs from the network
        return []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []
    owners = []
    for ro in root.iter("reportingOwner"):
        ident = ro.find("reportingOwnerId")
        rel = ro.find("reportingOwnerRelationship")
        name = " ".join((ident.findtext("rptOwnerName") or "").split()) if ident is not None else ""
        if not name:
            continue
        owners.append(
            Owner(
                cik=(ident.findtext("rptOwnerCik") or "").strip(),
                name=name,
                is_officer=_flag(rel, "isOfficer"),
                is_director=_flag(rel, "isDirector"),
                officer_title=(rel.findtext("officerTitle") or "").strip() or None
                if rel is not None
                else None,
                other_text=(rel.findtext("otherText") or "").strip() or None
                if rel is not None
                else None,
                filed=filed,
                accession=accession,
            )
        )
    return owners


def _is_person(name: str) -> bool:
    return not _ENTITY.search(name) and len(name.split()) >= 2


def _title(owner: Owner) -> str | None:
    parts = []
    if owner.is_officer:
        parts.append(owner.officer_title or "Officer")
    if owner.is_director:
        parts.append("Director")
    if not parts and owner.other_text and not re.search(r"10%|ten percent", owner.other_text, re.I):
        parts.append(owner.other_text)
    return ", ".join(parts) or None


def current_insiders(
    owners: list[Owner], issuer_cik: str, today: date | None = None
) -> list[OfficialPerson]:
    """Latest statement per owner; keep officers and directors reported in the window."""
    today = today or datetime.now(UTC).date()
    oldest = today - timedelta(days=WINDOW_DAYS)
    latest: dict[str, Owner] = {}
    for o in owners:
        key = o.cik or o.name.lower()
        if key not in latest or (o.filed, o.accession) > (latest[key].filed, latest[key].accession):
            latest[key] = o
    out = []
    for o in latest.values():
        title = _title(o)
        if title is None or o.filed < oldest or not _is_person(o.name):
            continue
        acc = o.accession.replace("-", "")
        out.append(
            OfficialPerson(
                name=last_first_to_display(o.name),
                title=title,
                source="edgar",
                source_url=INDEX_URL.format(cik=int(issuer_cik), acc=acc, dashed=o.accession),
                since=None,
            )
        )
    return sorted(out, key=lambda p: p.name)


def recent_insider_filings(
    submissions: dict, today: date | None = None, limit: int = MAX_FILINGS
) -> list[tuple[str, str, date]]:
    """(accession, XML file name, filing date) of the newest Forms 3/4/5, newest first."""
    today = today or datetime.now(UTC).date()
    oldest = today - timedelta(days=WINDOW_DAYS)
    recent = (submissions.get("filings") or {}).get("recent") or {}
    rows = zip(
        recent.get("form") or [],
        recent.get("accessionNumber") or [],
        recent.get("filingDate") or [],
        recent.get("primaryDocument") or [],
        strict=False,
    )
    out = []
    for form, acc, filed, doc in rows:
        if form not in FORMS or not doc:
            continue
        try:
            day = date.fromisoformat(filed)
        except ValueError:
            continue
        if day < oldest:
            continue
        # primaryDocument points at the styled HTML view ("xslF345X05/doc.xml"); the raw XML is
        # the same file name one level up.
        out.append((acc, doc.rsplit("/", 1)[-1], day))
    out.sort(key=lambda r: (r[2], r[0]), reverse=True)
    return out[:limit]


class EdgarInsiders:
    name = "edgar"
    label = "SEC EDGAR"
    licence = "public-domain"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        min_interval: float = MIN_INTERVAL,
        today: date | None = None,  # tests pin the clock
    ):
        self._client = client
        self._min_interval = min_interval
        self._today = today

    def applies(self, refs: CompanyRefs) -> bool:
        return pad_cik(refs.cik) is not None

    def _get(self, http: PoliteClient, url: str, *, text: bool = False):
        try:
            return (
                http.get_text(url, {}, "*/*")
                if text
                else http.get_json(url, {}, "application/json")
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise SourceSkipped(
                    f"SEC EDGAR refused the request (403). Set {CONTACT_ENV} to your contact "
                    "email: the SEC requires requests to declare who is behind them."
                ) from exc
            raise

    def fetch(self, refs: CompanyRefs) -> list[OfficialPerson]:
        cik = pad_cik(refs.cik)
        if cik is None:
            return []
        if not os.environ.get(CONTACT_ENV, "").strip():
            raise SourceSkipped(
                f"SEC insider filings need a declared contact. Set {CONTACT_ENV} to your email "
                "address (the SEC asks every automated client to identify itself)."
            )
        http = PoliteClient(
            self._client, min_interval=self._min_interval, user_agent=edgar_user_agent()
        )
        try:
            submissions = self._get(http, SUBMISSIONS_URL.format(cik=cik))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        owners: list[Owner] = []
        for accession, doc, filed in recent_insider_filings(submissions, self._today):
            url = ARCHIVE_URL.format(cik=int(cik), acc=accession.replace("-", ""), doc=doc)
            try:
                xml_text = self._get(http, url, text=True)
            except httpx.HTTPStatusError:
                continue  # one missing filing must not hide the others
            owners.extend(parse_ownership(xml_text, filed, accession))
        return current_insiders(owners, cik, self._today)
