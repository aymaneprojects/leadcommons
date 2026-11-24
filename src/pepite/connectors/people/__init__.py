"""Official, public sources of who runs one company, fetched on demand on the user's machine.

Nothing here is bulk: each source answers for a single company (by its registry ids) and its
answer is never written to a pack. Sources: Wikidata (CC0), SEC insider filings (public domain),
Companies House officers (OGL), Brønnøysund roles (NLOD). See docs/people-sources.md.

A source has `name`, `licence`, `applies(refs)` and `fetch(refs) -> list[OfficialPerson]`. It
raises `SourceSkipped(reason)` when it cannot run here (missing key or contact), with a reason the
UI can show. `official_people` runs every source that applies and never lets one failure hide the
others.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx

from pepite.models import Company

log = logging.getLogger(__name__)


class SourceSkipped(Exception):
    """The source cannot run now (no API key, no declared contact). str(exc) is shown to users."""


@dataclass(frozen=True)
class CompanyRefs:
    """The identifiers of one company that official sources can be asked about."""

    name: str | None = None
    domain: str | None = None
    wikidata_id: str | None = None  # Q123
    lei: str | None = None
    cik: str | None = None  # SEC, any padding
    siren: str | None = None
    gb_number: str | None = None  # Companies House number
    no_orgnr: str | None = None  # Brønnøysund organisation number

    @classmethod
    def from_company(cls, company: Company | None, domain: str | None = None) -> CompanyRefs:
        if company is None:
            return cls(domain=domain or None)
        ids = {k.lower(): str(v).strip() for k, v in (company.registry_ids or {}).items() if v}
        registry = (company.registry or "").upper()
        number = (company.company_number or "").strip() or None
        wikidata = (ids.get("wikidata") or ids.get("wikidata_id") or "").upper()
        siren = ids.get("siren") or (number if registry == "FR-SIREN" else None)
        return cls(
            name=company.name or None,
            domain=domain or company.domain or None,
            wikidata_id=wikidata if re.fullmatch(r"Q\d+", wikidata) else None,
            lei=(ids.get("lei") or "").upper() or None,
            cik=ids.get("cik") or (number if registry == "US-SEC" else None),
            siren=siren,
            gb_number=ids.get("gb-ch") or (number if registry == "GB-CH" else None),
            no_orgnr=ids.get("no-brreg") or (number if registry == "NO-BRREG" else None),
        )


@dataclass(frozen=True)
class OfficialPerson:
    """One person with one role at the company, as one source states it."""

    name: str  # display order ("Jane Doe"), case tidied
    title: str  # role as the source words it (English; Norwegian kept in brackets)
    source: str  # "wikidata", "edgar", "companies-house", "brreg"
    source_url: str  # page where the statement can be checked
    since: str | None = None  # ISO date the role started, when the source says


@dataclass
class SourceReport:
    """What happened with one source, for the UI: ok / skipped (with a reason) / error."""

    source: str
    label: str
    licence: str
    status: Literal["ok", "skipped", "error"]
    count: int = 0
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "label": self.label,
            "licence": self.licence,
            "status": self.status,
            "count": self.count,
            "reason": self.reason,
        }


class PeopleSource(Protocol):
    name: str
    label: str
    licence: str

    def applies(self, refs: CompanyRefs) -> bool: ...

    def fetch(self, refs: CompanyRefs) -> list[OfficialPerson]: ...


# ------------------------------------------------------------------ shared helpers

_PARTICLES = frozenset("de du des van von der den da das dos di del della la le ten ter zu".split())
_SUFFIXES = frozenset("jr sr ii iii iv v esq".split())


def tidy_case(text: str) -> str:
    """Title-case ALL-CAPS words only ("COOK" -> "Cook"); mixed-case words are kept."""
    return " ".join(w.title() if len(w) > 1 and w.isupper() else w for w in text.split())


def last_first_to_display(raw: str) -> str:
    """ "SMITH, John William" or "SMITH JOHN W" (SEC order) -> "John William Smith"."""
    raw = " ".join(raw.replace(" ", " ").split())
    if "," in raw:
        last, _, given = raw.partition(",")
        given_tokens = given.split()
        last_tokens = last.split()
    else:
        tokens = raw.split()
        take = 1
        while take < len(tokens) - 1 and tokens[take - 1].lower() in _PARTICLES:
            take += 1
        last_tokens, given_tokens = tokens[:take], tokens[take:]
    given_tokens = [t for t in given_tokens if t.strip(".").lower() not in _SUFFIXES]
    given_tokens = [t + "." if len(t) == 1 else t for t in given_tokens]  # middle initials
    return tidy_case(" ".join(given_tokens + last_tokens))


def source_label(url: str) -> str:
    """Friendly source name for a URL (chips in the UI): "Wikidata", "SEC EDGAR", else the host."""
    host = (urlparse(url).netloc or url).lower().removeprefix("www.")
    for needle, label in _HOST_LABELS:
        if host == needle or host.endswith("." + needle):
            return label
    return host or url


_HOST_LABELS: tuple[tuple[str, str], ...] = (
    ("wikidata.org", "Wikidata"),
    ("sec.gov", "SEC EDGAR"),
    ("company-information.service.gov.uk", "Companies House"),
    ("brreg.no", "Brønnøysund register"),
)


def all_sources(client: httpx.Client | None = None) -> list[PeopleSource]:
    """One instance of every source, in the order their titles are preferred when merging."""
    from pepite.connectors.people.brreg_roles import BrregRoles
    from pepite.connectors.people.companies_house_officers import CompaniesHouseOfficers
    from pepite.connectors.people.edgar_insiders import EdgarInsiders
    from pepite.connectors.people.wikidata_people import WikidataPeople

    return [
        EdgarInsiders(client),
        CompaniesHouseOfficers(client),
        BrregRoles(client),
        WikidataPeople(client),
    ]


def official_people(
    refs: CompanyRefs, sources: Iterable[PeopleSource] | None = None
) -> tuple[list[OfficialPerson], list[SourceReport]]:
    """Ask every source that applies to `refs`; return all people and one report per source."""
    people: list[OfficialPerson] = []
    reports: list[SourceReport] = []
    for source in all_sources() if sources is None else sources:
        if not source.applies(refs):
            continue
        try:
            found = source.fetch(refs)
        except SourceSkipped as exc:
            reports.append(
                SourceReport(source.name, source.label, source.licence, "skipped", 0, str(exc))
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("people source %s failed: %s", source.name, exc)
            reason = f"{source.label} did not answer ({type(exc).__name__})."
            reports.append(
                SourceReport(source.name, source.label, source.licence, "error", 0, reason)
            )
        else:
            people.extend(found)
            reports.append(
                SourceReport(source.name, source.label, source.licence, "ok", len(found))
            )
    return people, reports


__all__: Sequence[str] = [
    "CompanyRefs",
    "OfficialPerson",
    "PeopleSource",
    "SourceReport",
    "SourceSkipped",
    "all_sources",
    "official_people",
    "source_label",
]
