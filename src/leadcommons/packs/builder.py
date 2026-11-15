"""Build a company data pack (Parquet) from open-data connectors."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from leadcommons.models import Company, Provenance, make_id
from leadcommons.search import PACK_COLUMNS, PACK_FLOAT_COLUMNS, PACK_INT_COLUMNS

log = logging.getLogger(__name__)

DEFAULT_COUNTRIES = ("FR", "DE", "GB", "ES", "IT", "NL", "BE", "US", "CA", "MA")
DEFAULT_SOURCES = ("wikidata", "gleif")
# Registries that only cover one country; included by default just for that country.
COUNTRY_SOURCES = {"FR": "sirene", "US": "edgar"}
REGISTRY_SOURCES = ("gleif", "sirene", "edgar")
SOURCE_LICENCES = {
    "wikidata": "CC0-1.0",
    "gleif": "CC0-1.0",
    "sirene": "etalab-2.0",
    "edgar": "public-domain",
}
SOURCE_URLS = {
    "wikidata": "https://www.wikidata.org/",
    "gleif": "https://www.gleif.org/en/lei-data/gleif-golden-copy",
    "sirene": "https://recherche-entreprises.api.gouv.fr/ (INSEE SIRENE, Licence Ouverte 2.0)",
    "edgar": "https://www.sec.gov/edgar (U.S. Securities and Exchange Commission)",
}

BUILD_HINT = "Building packs needs the 'build' extra: pip install 'leadcommons[build]'"


def _pa():
    """Import pyarrow lazily (it is only needed to write packs, not to query them)."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(BUILD_HINT) from exc
    return pa, pq


def pack_schema():
    """Arrow schema of a pack: whole numbers int32, revenue float64, everything else string."""
    pa, _ = _pa()

    def kind(col: str):
        if col in PACK_INT_COLUMNS:
            return pa.int32()
        return pa.float64() if col in PACK_FLOAT_COLUMNS else pa.string()

    return pa.schema([pa.field(c, kind(c)) for c in PACK_COLUMNS])


def company_id(c: Company) -> str:
    """Canonical id: LEI, else Wikidata id, else SIREN, else CIK, else domain."""
    lei = c.registry_ids.get("lei")
    if lei:
        return make_id("co", "lei", lei)
    qid = c.registry_ids.get("wikidata")
    if qid:
        return make_id("co", "wd", qid)
    if c.registry_ids.get("siren"):
        return make_id("co", "siren", c.registry_ids["siren"])
    if c.registry_ids.get("cik"):
        return make_id("co", "cik", c.registry_ids["cik"])
    if c.domain:
        return make_id("co", "domain", c.domain)
    return c.id


def merge_companies(base: Company, other: Company) -> Company:
    """Merge `other` into `base`: keep base values, fill gaps; GLEIF supplies legal data."""
    registry = any(p.source in REGISTRY_SOURCES for p in other.sources)
    if registry or not base.legal_name:
        base.legal_name = other.legal_name or base.legal_name
    if any(p.source == "wikidata" for p in other.sources):
        # Wikidata keeps name/domain even when a registry row was stored first.
        base.name = other.name or base.name
        base.domain = other.domain or base.domain
    for f in ("domain", "country", "region", "city", "industry", "industry_label",
              "size_range", "founded_year"):  # fmt: skip
        if getattr(base, f) in (None, ""):
            setattr(base, f, getattr(other, f))
    for k, v in other.registry_ids.items():
        base.registry_ids.setdefault(k, v)
    for k, v in other.links.items():
        base.links.setdefault(k, v)
    have = {(p.source, p.url) for p in base.sources}
    base.sources.extend(p for p in other.sources if (p.source, p.url) not in have)
    base.id = company_id(base)
    return base


_NAME_NOISE = frozenset(
    "the inc corp corporation co company ltd llc plc sa sas sarl se nv ag spa limited".split()
)


def name_key(name: str) -> str:
    """Lowercase alphanumeric tokens of a company name without legal-form words."""
    toks = ["".join(ch for ch in t if ch.isalnum()) for t in name.lower().split()]
    return " ".join(t for t in toks if t and t not in _NAME_NOISE)


class Deduper:
    """Index companies by LEI, Wikidata id, SIREN, CIK and (registry-less rows) domain.

    Rows from national registries (SIRENE, EDGAR) also fall back to an exact normalised-name
    match within the same country against rows that lack that registry id.
    """

    def __init__(self) -> None:
        self.items: list[Company] = []
        self._idx: dict[tuple[str, str], int] = {}

    def _keys(self, c: Company) -> list[tuple[str, str]]:
        keys = [(k, v.upper() if k == "lei" else v) for k, v in c.registry_ids.items() if v]
        if c.domain and not c.registry_ids:
            keys.append(("domain", c.domain))
        return keys

    def _name_key(self, c: Company) -> tuple[str, str] | None:
        nk = name_key(c.name)
        return ("name", f"{c.country}|{nk}") if nk and c.country else None

    def _by_name(self, c: Company) -> int | None:
        if not any(p.source in ("sirene", "edgar") for p in c.sources):
            return None
        key = self._name_key(c)
        pos = self._idx.get(key) if key else None
        if pos is None:
            return None
        mine = {k for k in c.registry_ids if k in ("siren", "cik")}
        return pos if mine and not mine & set(self.items[pos].registry_ids) else None

    def add(self, c: Company) -> Company:
        """Add or merge a company; returns the stored record."""
        keys = self._keys(c)
        pos = next((self._idx[k] for k in keys if k in self._idx), None)
        if pos is None:
            pos = self._by_name(c)
        if pos is not None:
            merged = merge_companies(self.items[pos], c)
            for k in self._keys(merged):
                self._idx.setdefault(k, pos)
            return merged
        c.id = company_id(c)
        self.items.append(c)
        pos = len(self.items) - 1
        for key in keys:
            self._idx[key] = pos
        nkey = self._name_key(c)
        if nkey:
            self._idx.setdefault(nkey, pos)
        return c

    def ids(self, kind: str) -> list[str]:
        """All registry ids of one kind ("lei", "siren", "cik") held so far."""
        return [c.registry_ids[kind] for c in self.items if c.registry_ids.get(kind)]

    def leis(self) -> list[str]:
        return self.ids("lei")


def _prov_json(sources: Iterable[Provenance]) -> str:
    return json.dumps(
        [
            {
                "source": p.source,
                "licence": p.licence,
                "url": p.url,
                "fetched_at": p.fetched_at.isoformat(),
            }
            for p in sources
        ]
    )


def company_to_row(c: Company) -> dict:
    """Flatten a Company into a PACK_COLUMNS row."""
    return {
        "id": c.id,
        "name": c.name,
        "legal_name": c.legal_name,
        "domain": c.domain,
        "country": c.country,
        "region": c.region,
        "city": c.city,
        "industry": c.industry,
        "industry_label": c.industry_label,
        "size_range": c.size_range,
        "founded_year": c.founded_year,
        "lei": c.registry_ids.get("lei"),
        "wikidata_id": c.registry_ids.get("wikidata"),
        "website": c.links.get("website"),
        "sources_json": _prov_json(c.sources),
        "siren": c.registry_ids.get("siren"),
        "cik": c.registry_ids.get("cik"),
        "registry": c.registry,
        "company_number": c.company_number,
        "status": c.status,
        "legal_form": c.legal_form,
        "address": c.address,
        "postal_code": c.postal_code,
        "phone": c.phone,
        "employees": c.employees,
        "revenue": c.revenue,
        "revenue_currency": c.revenue_currency,
        "revenue_year": c.revenue_year,
    }


def write_parquet(path: Path, rows: list[dict], metadata: dict[str, str]) -> None:
    """Write rows (PACK_COLUMNS) as zstd parquet with key-value metadata."""
    pa, pq = _pa()
    table = pa.Table.from_pylist(rows, schema=pack_schema())
    table = table.replace_schema_metadata({k: v.encode() for k, v in metadata.items()})
    pq.write_table(table, path, compression="zstd")


def write_attribution(
    path: Path,
    sources: Sequence[str],
    built_at: str,
    counts: dict[str, int],
    skipped: Sequence[dict[str, str]] = (),
) -> None:
    """Write ATTRIBUTION.md describing sources, licences, build date and counts."""
    lines = [
        "# Data attribution",
        "",
        f"This company pack was built on {built_at} by LeadCommons from public open data.",
        "",
        "## Sources",
        "",
    ]
    for s in sources:
        lines.append(
            f"- **{s}**: {SOURCE_URLS.get(s, '')} (licence: {SOURCE_LICENCES.get(s, 'n/a')})"
        )
    lines += [
        "",
        "Wikidata and GLEIF data are released under CC0 1.0 (public domain dedication).",
        "SIRENE data (INSEE) is reused under the Licence Ouverte / Etalab 2.0.",
        "SEC EDGAR data is a U.S. government work in the public domain.",
        "",
        "## Counts",
        "",
    ]
    total = sum(counts.values())
    lines += [f"- {k}: {v}" for k, v in sorted(counts.items())]
    lines += [
        f"- **total**: {total}",
        "",
        "Company-level data only; no personal data is included.",
        "",
    ]
    if skipped:
        lines += ["## Skipped (source failed for this country)", ""]
        lines += [f"- {k['source']} / {k['country']}: {k['reason']}" for k in skipped]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _active_sources(
    country: str, sources: Sequence[str], conns: dict[str, object], implicit: bool
) -> list[str]:
    """Sources to run for one country (single-country registries only where they apply)."""
    active = [x for x in sources if x in conns]
    if implicit and (extra := COUNTRY_SOURCES.get(country)) and extra in conns:
        active.append(extra)
    out = []
    for name in active:
        covered = getattr(conns[name], "countries", None)
        if covered is None or country in covered:
            out.append(name)
    return out


def _run_country(
    country: str,
    active: list[str],
    conns: dict[str, object],
    limit: int | None,
    skipped: list[dict[str, str]],
    progress: Callable[[str], None] | None,
) -> Deduper:
    """Run every active source for one country; a failing source is recorded and skipped."""
    dd = Deduper()

    def step(name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:300]
            log.warning("%s %s skipped: %s", name, country, reason)
            skipped.append({"country": country, "source": name, "reason": reason})

    def wikidata() -> None:
        for c in conns["wikidata"].iter_companies(country=country, limit=limit):
            dd.add(c)
            if progress and len(dd.items) % 500 == 0:
                progress(f"{country}: {len(dd.items)} wikidata companies")
        log.info("wikidata %s: %d companies", country, len(dd.items))

    def gleif() -> None:
        gl = conns["gleif"]
        before = len(dd.items)
        if "wikidata" in active:
            leis = dd.leis()
            merged = 0
            for c in gl.fetch_by_lei(leis) if leis else ():
                dd.add(c)
                merged += 1
            log.info("gleif %s: merged %d LEIs", country, merged)
        else:
            for c in gl.iter_companies(country=country, limit=limit):
                dd.add(c)
            log.info("gleif %s: %d rows", country, len(dd.items) - before)

    def registry(name: str, kind: str, fetch: str) -> Callable[[], None]:
        def run() -> None:
            conn = conns[name]
            merged = extra = 0
            if ids := dd.ids(kind):
                for c in getattr(conn, fetch)(ids):
                    dd.add(c)
                    merged += 1
            for c in conn.iter_companies(country=country, limit=limit):  # extra rows
                dd.add(c)
                extra += 1
            log.info("%s %s: %d rows (%d merged by id)", name, country, extra + merged, merged)

        return run

    if "wikidata" in active:
        step("wikidata", wikidata)
    if "gleif" in active:
        step("gleif", gleif)
    if "sirene" in active:
        step("sirene", registry("sirene", "siren", "fetch_by_siren"))
    if "edgar" in active:
        step("edgar", registry("edgar", "cik", "fetch_by_cik"))
    return dd


def _skipped_text(skipped: Sequence[dict[str, str]]) -> str:
    return json.dumps(list(skipped), ensure_ascii=False)


def build_pack(
    out_dir: Path,
    sources: Sequence[str] = DEFAULT_SOURCES,
    countries: Sequence[str] | None = None,
    limit_per_source: int | None = None,
    *,
    connectors: dict[str, object] | None = None,
    progress: Callable[[str], None] | None = None,
    filename: str | None = None,
) -> Path:
    """Fetch, normalise, dedupe and write out_dir/companies-YYYYMMDD.parquet + ATTRIBUTION.md.

    With the default `sources`, the national registry for a country is added automatically
    (FR -> sirene, US -> edgar); explicit sources are used as given. A source that fails for a
    country (after the clients' retries) is logged and skipped; the build only fails when the
    final pack would be empty. Skipped (country, source, reason) entries are recorded in the
    parquet metadata ("skipped") and in ATTRIBUTION.md; only sources that contributed rows are
    listed as sources.
    """
    import tempfile

    from leadcommons.connectors import CONNECTORS

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    built_at = now.isoformat(timespec="seconds")
    implicit = tuple(sources) == DEFAULT_SOURCES
    wanted = list(sources) + (list(COUNTRY_SOURCES.values()) if implicit else [])
    conns: dict[str, object] = {}
    for s in dict.fromkeys(wanted):
        if connectors and s in connectors:
            conns[s] = connectors[s]
        elif s in CONNECTORS:
            conns[s] = CONNECTORS[s]()
        else:
            raise ValueError(f"unknown source: {s!r}")
    cs = [c.upper() for c in (countries or DEFAULT_COUNTRIES)]
    plan = {c: _active_sources(c, sources, conns, implicit) for c in cs}
    path = out_dir / (filename or f"companies-{now:%Y%m%d}.parquet")
    pa, pq = _pa()
    schema = pack_schema()
    counts: dict[str, int] = {}
    contributed: set[str] = set()
    skipped: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    started = time.monotonic()
    log.info("building pack: countries=%s sources=%s limit=%s", cs, list(dict.fromkeys(wanted)),
             limit_per_source)  # fmt: skip
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".build-") as work:
        parts: list[Path] = []
        for country in cs:  # stream one country at a time, spilled to a part file
            t0 = time.monotonic()
            dd = _run_country(country, plan[country], conns, limit_per_source, skipped, progress)
            rows = []
            for c in dd.items:
                if c.id in seen_ids:
                    continue
                seen_ids.add(c.id)
                contributed.update(p.source for p in c.sources)
                rows.append(company_to_row(c))
            if rows:
                part = Path(work) / f"{len(parts):03d}.parquet"
                pq.write_table(pa.Table.from_pylist(rows, schema=schema), part, compression="zstd")
                parts.append(part)
            counts[country] = len(rows)
            log.info("country %s done: %d rows (%.1f s)", country, len(rows), time.monotonic() - t0)
            if progress:
                progress(f"{country}: {len(rows)} rows")
        total = sum(counts.values())
        if total == 0:
            detail = "; ".join(f"{k['source']} {k['country']}: {k['reason']}" for k in skipped)
            raise RuntimeError(
                f"no companies were collected, pack not written ({detail or 'no data'})"
            )
        used = [s for s in dict.fromkeys(wanted) if s in contributed]
        metadata = {"built_at": built_at, "sources": ",".join(used)}
        if skipped:
            metadata["skipped"] = _skipped_text(skipped)
        tmp = path.with_suffix(".parquet.tmp")
        writer = pq.ParquetWriter(
            tmp,
            schema.with_metadata({k: v.encode() for k, v in metadata.items()}),
            compression="zstd",
        )
        try:
            for part in parts:
                for batch in pq.ParquetFile(part).iter_batches():
                    writer.write_batch(batch)
        except BaseException:
            writer.close()
            tmp.unlink(missing_ok=True)
            raise
        writer.close()
        tmp.replace(path)
    write_attribution(out_dir / "ATTRIBUTION.md", used, built_at, counts, skipped)
    log.info(
        "pack built: %s, %d rows, sources=%s, %d skipped, %.1f s",
        path, total, ",".join(used), len(skipped), time.monotonic() - started,
    )  # fmt: skip
    return path
