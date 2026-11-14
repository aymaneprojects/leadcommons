"""Build a company data pack (Parquet) from open-data connectors."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from leadcommons.models import Company, Provenance, make_id
from leadcommons.search import PACK_COLUMNS

log = logging.getLogger(__name__)

DEFAULT_COUNTRIES = ("FR", "DE", "GB", "ES", "IT", "NL", "BE", "US", "CA", "MA")
SOURCE_LICENCES = {"wikidata": "CC0-1.0", "gleif": "CC0-1.0"}
SOURCE_URLS = {
    "wikidata": "https://www.wikidata.org/",
    "gleif": "https://www.gleif.org/en/lei-data/gleif-golden-copy",
}

SCHEMA = pa.schema(
    [pa.field(c, pa.int32() if c == "founded_year" else pa.string()) for c in PACK_COLUMNS]
)


def company_id(c: Company) -> str:
    """Canonical id: LEI, else Wikidata id, else domain."""
    lei = c.registry_ids.get("lei")
    if lei:
        return make_id("co", "lei", lei)
    qid = c.registry_ids.get("wikidata")
    if qid:
        return make_id("co", "wd", qid)
    if c.domain:
        return make_id("co", "domain", c.domain)
    return c.id


def merge_companies(base: Company, other: Company) -> Company:
    """Merge `other` into `base`: keep base values, fill gaps; GLEIF supplies legal data."""
    gleif = any(p.source == "gleif" for p in other.sources)
    if gleif or not base.legal_name:
        base.legal_name = other.legal_name or base.legal_name
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


class Deduper:
    """Index companies by LEI, Wikidata id and (for registry-less rows) domain."""

    def __init__(self) -> None:
        self.items: list[Company] = []
        self._idx: dict[tuple[str, str], int] = {}

    def _keys(self, c: Company) -> list[tuple[str, str]]:
        keys = [(k, v.upper() if k == "lei" else v) for k, v in c.registry_ids.items() if v]
        if c.domain and not c.registry_ids:
            keys.append(("domain", c.domain))
        return keys

    def add(self, c: Company) -> Company:
        """Add or merge a company; returns the stored record."""
        keys = self._keys(c)
        for key in keys:
            pos = self._idx.get(key)
            if pos is not None:
                merged = merge_companies(self.items[pos], c)
                for k in self._keys(merged):
                    self._idx.setdefault(k, pos)
                return merged
        c.id = company_id(c)
        self.items.append(c)
        for key in keys:
            self._idx[key] = len(self.items) - 1
        return c

    def leis(self) -> list[str]:
        return [c.registry_ids["lei"] for c in self.items if c.registry_ids.get("lei")]


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
    }


def write_parquet(path: Path, rows: list[dict], metadata: dict[str, str]) -> None:
    """Write rows (PACK_COLUMNS) as zstd parquet with key-value metadata."""
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    table = table.replace_schema_metadata({k: v.encode() for k, v in metadata.items()})
    pq.write_table(table, path, compression="zstd")


def write_attribution(
    path: Path, sources: Sequence[str], built_at: str, counts: dict[str, int]
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
    path.write_text("\n".join(lines), encoding="utf-8")


def build_pack(
    out_dir: Path,
    sources: Sequence[str] = ("wikidata", "gleif"),
    countries: Sequence[str] | None = None,
    limit_per_source: int | None = None,
    *,
    connectors: dict[str, object] | None = None,
    progress: Callable[[str], None] | None = None,
    filename: str | None = None,
) -> Path:
    """Fetch, normalise, dedupe and write out_dir/companies-YYYYMMDD.parquet + ATTRIBUTION.md."""
    from leadcommons.connectors import CONNECTORS

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    built_at = now.isoformat(timespec="seconds")
    conns = {}
    for s in sources:
        if connectors and s in connectors:
            conns[s] = connectors[s]
        elif s in CONNECTORS:
            conns[s] = CONNECTORS[s]()
        else:
            raise ValueError(f"unknown source: {s!r}")
    cs = [c.upper() for c in (countries or DEFAULT_COUNTRIES)]
    path = out_dir / (filename or f"companies-{now:%Y%m%d}.parquet")
    metadata = {"built_at": built_at, "sources": ",".join(sources)}
    counts: dict[str, int] = {}
    seen_ids: set[str] = set()
    tmp = path.with_suffix(".parquet.tmp")
    writer = pq.ParquetWriter(
        tmp, SCHEMA.with_metadata({k: v.encode() for k, v in metadata.items()}), compression="zstd"
    )
    try:
        for country in cs:  # stream one country at a time
            dd = Deduper()
            if "wikidata" in conns:
                for c in conns["wikidata"].iter_companies(country=country, limit=limit_per_source):
                    dd.add(c)
                    if progress and len(dd.items) % 500 == 0:
                        progress(f"{country}: {len(dd.items)} wikidata companies")
            if "gleif" in conns:
                gl = conns["gleif"]
                if "wikidata" in conns:
                    leis = dd.leis()
                    if leis:
                        for c in gl.fetch_by_lei(leis):
                            dd.add(c)
                else:
                    for c in gl.iter_companies(country=country, limit=limit_per_source):
                        dd.add(c)
            rows = []
            for c in dd.items:
                if c.id in seen_ids:
                    continue
                seen_ids.add(c.id)
                rows.append(company_to_row(c))
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=SCHEMA))
            counts[country] = len(rows)
            log.info("built %s: %d rows", country, len(rows))
            if progress:
                progress(f"{country}: {len(rows)} rows")
    except BaseException:
        writer.close()
        tmp.unlink(missing_ok=True)
        raise
    writer.close()
    tmp.replace(path)
    write_attribution(out_dir / "ATTRIBUTION.md", sources, built_at, counts)
    return path
