"""Site scan: find each company's website by reading public pages of candidate domains.

SHARED CONTRACT (lead). Companies must publish their legal details on their own website
(France: mentions légales with SIREN/RCS; UK: company number; Germany: Impressum with the
Handelsregister number; Norway: org. nr; Australia: ABN...). Reading a domain's home and legal
pages and finding a valid register number there links that domain to exactly one company, with
proof. Modules:

- ids.py     extract and validate register numbers from page text (pure functions)
- pages.py   which pages of a site to read (legal notice / imprint / about / contact) and in
             what order, from links found on the home page (pure functions)
- fetch.py   polite crawler: robots.txt, one host at a time, small page budget per domain,
             checkpoints; writes Findings to Parquet
- hosts.py   candidate domains per country (e.g. from Common Crawl's index)

Only facts about the company are kept (register numbers, the page they were on, the page title,
switchboard phone numbers). Never names, emails or phone numbers of people.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RegistryId:
    """A register number found on a page, already validated (check digits, length)."""

    registry: str  # same codes as the packs: FR-SIREN, GB-CH, NO-BRREG, AU-ABN, DE-HRB, ...
    number: str  # normalised: digits (and letters where the register uses them), no spaces
    raw: str  # the text as written on the page, for the evidence
    kind: str = ""  # how it was written: "siren", "siret", "rcs", "vat", "company-number", ...


@dataclass
class Finding:
    """What a scan learned about one domain."""

    domain: str  # registrable domain, lowercase
    ids: list[RegistryId] = field(default_factory=list)
    evidence_url: str | None = None  # page where the strongest id was found
    title: str | None = None  # home page <title>, cleaned
    phones: list[str] = field(
        default_factory=list
    )  # switchboard numbers, E.164, from legal/contact pages
    status: str = "ok"  # ok | no-site | robots | error
    error: str | None = None


__all__ = ["Finding", "RegistryId"]
