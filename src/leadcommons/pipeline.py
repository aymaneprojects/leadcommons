"""CSV enrichment pipeline: read a prospect list, find and verify a public email per row."""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from leadcommons.models import Person, make_id
from leadcommons.search import CompanyFilters

log = logging.getLogger(__name__)

MAX_ROWS = 5000

OUTPUT_COLUMNS = (
    "email",
    "email_status",
    "email_confidence",
    "email_pattern",
    "company_domain_resolved",
    "notes",
)

ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": ("first_name", "firstname", "first name", "prénom", "prenom", "given_name"),
    "last_name": ("last_name", "lastname", "last name", "nom", "surname", "family_name"),
    "full_name": ("full_name", "fullname", "full name", "name", "nom complet", "nom_complet"),
    "company_domain": ("company_domain", "domain", "website", "site", "url", "site web"),
    "company_name": ("company_name", "company", "entreprise", "société", "societe"),
}

ProgressCb = Callable[[int, int], None]


def normalize_domain(value: str | None) -> str | None:
    """Reduce a URL / website / bare domain to a lowercase bare domain, or None."""
    if not value:
        return None
    v = value.strip().lower()
    if not v:
        return None
    if "@" in v and "//" not in v:
        v = v.rsplit("@", 1)[1]
    if "//" not in v:
        v = "//" + v
    host = urlparse(v).hostname or ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host) and not re.fullmatch(
        r"(xn--)?[^\s/]+\.[^\s/]+", host
    ):
        return None
    return host


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _sniff(text: str) -> str:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        header = sample.splitlines()[0] if sample else ""
        return max((",", ";", "\t"), key=header.count)


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    lowered = {f.strip().lower(): f for f in fieldnames if f}
    mapping: dict[str, str] = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in lowered:
                mapping[key] = lowered[n]
                break
    return mapping


def enrich_csv(
    input_path: str | Path,
    output_path: str | Path,
    *,
    store: Any,
    search: Any,
    settings: Any,
    progress: ProgressCb | None = None,
) -> dict[str, Any]:
    from leadcommons import enrich

    text = _decode(Path(input_path).read_bytes())
    delimiter = _sniff(text)
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    fieldnames = [f for f in (reader.fieldnames or []) if f is not None]
    if not fieldnames:
        raise ValueError("CSV has no header row")
    rows = [r for r in reader if any((v or "").strip() for v in r.values() if isinstance(v, str))]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Too many rows: {len(rows)} (max {MAX_ROWS})")
    cols = _map_columns(fieldnames)
    if "company_domain" not in cols and "company_name" not in cols:
        raise ValueError("CSV needs a company domain/website or company name column")
    if not (cols.keys() & {"first_name", "last_name", "full_name"}):
        raise ValueError("CSV needs a name column (first_name/last_name or full_name)")

    def cell(row: dict[str, Any], key: str) -> str:
        col = cols.get(key)
        v = row.get(col) if col else None
        return v.strip() if isinstance(v, str) else ""

    out_fields = fieldnames + [c for c in OUTPUT_COLUMNS if c not in fieldnames]
    domain_by_company: dict[str, str | None] = {}
    results_cache: dict[tuple[str, str], Any] = {}
    total = len(rows)
    found = 0
    out_rows: list[dict[str, Any]] = []

    for i, row in enumerate(rows, start=1):
        out = {k: row.get(k, "") for k in fieldnames}
        out.update({c: "" for c in OUTPUT_COLUMNS})
        try:
            notes = _process_row(
                row, out, cell, search, store, settings, enrich, domain_by_company, results_cache
            )
        except Exception as exc:  # one bad row must not abort the whole file
            log.exception("enrich_csv row %s failed", i)
            notes = f"error: {type(exc).__name__}"
        out["notes"] = notes
        if out["email"]:
            found += 1
        out_rows.append(out)
        if progress:
            progress(i, total)

    _write_atomic(Path(output_path), out_fields, out_rows)
    return {"rows": total, "found": found, "output": str(output_path)}


def _process_row(
    row: dict[str, Any],
    out: dict[str, Any],
    cell: Callable[[dict[str, Any], str], str],
    search: Any,
    store: Any,
    settings: Any,
    enrich: Any,
    domain_by_company: dict[str, str | None],
    results_cache: dict[tuple[str, str], Any],
) -> str:
    first, last, full = cell(row, "first_name"), cell(row, "last_name"), cell(row, "full_name")
    name = full or f"{first} {last}".strip()
    if not name:
        return "missing name"

    domain = normalize_domain(cell(row, "company_domain"))
    company = cell(row, "company_name")
    note = ""
    if not domain and company:
        key = company.lower()
        if key not in domain_by_company:
            domain_by_company[key] = None
            try:
                page = search.search_companies(CompanyFilters(text=company), page=1, per_page=1)
                if page.items and page.items[0].domain:
                    domain_by_company[key] = normalize_domain(page.items[0].domain)
            except Exception:
                log.exception("domain resolution failed for %r", company)
        domain = domain_by_company[key]
        if domain:
            note = "domain resolved from company name"
    if not domain:
        return "no domain"
    out["company_domain_resolved"] = domain

    cache_key = (domain, name.lower())
    if cache_key not in results_cache:
        person = Person(id=make_id("pe", domain, name), full_name=name, company_domain=domain)
        results_cache[cache_key] = enrich.find_email(
            person, domain, smtp_probe=bool(getattr(settings, "smtp_probe", False))
        )
    cand = results_cache[cache_key]
    if cand is None:
        return "; ".join(x for x in (note, "no email found") if x)
    if store.is_suppressed(cand.address):
        return "; ".join(x for x in (note, "suppressed") if x)
    out["email"] = cand.address
    out["email_status"] = str(cand.status.value if hasattr(cand.status, "value") else cand.status)
    out["email_confidence"] = cand.confidence
    out["email_pattern"] = cand.pattern or ""
    return note


def _write_atomic(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
