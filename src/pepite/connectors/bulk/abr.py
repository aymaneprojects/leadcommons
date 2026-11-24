"""Australia: ABN Bulk Extract of the Australian Business Register (data.gov.au). CC-BY-3.0-AU.

Download: dataset `abn-bulk-extract` on data.gov.au (CKAN API `package_show`), two ZIP files
(`public_split_1_10.zip`, `public_split_11_20.zip`, ~0.5 GB each) holding twenty XML files
(`YYYYMMDD_Public01.xml` ... `Public20.xml`, ~10 GB of XML, ~20M ABNs, current and cancelled).
Refreshed weekly. One `<ABR recordLastUpdatedDate replaced>` element per ABN in a `<Transfer>`
root:

    <ABN status="ACT|CAN" ABNStatusFromDate="YYYYMMDD">11 digits</ABN>
    <EntityType><EntityTypeInd>PRV</EntityTypeInd><EntityTypeText>...</EntityTypeText></EntityType>
    <MainEntity> (organisations) or <LegalEntity> (individuals: IndividualName, not read)
      <NonIndividualName type="MN"><NonIndividualNameText>name</NonIndividualNameText>
      <BusinessAddress><AddressDetails><State/><Postcode/></AddressDetails></BusinessAddress>
    <ASICNumber ASICNumberType="...">ACN</ASICNumber>   (companies)
    <OtherEntity><NonIndividualName type="BN|TRD|OTN">business / trading names</...>

The XML is stream-parsed (`xml.etree.ElementTree.iterparse`, each record cleared once read) in
batches of 100,000 rows into one Parquet file per XML member (memory stays flat; a finished
member is not parsed again when a run resumes), then filtered and mapped with DuckDB.

Kept (documented rule): active ABNs (`status="ACT"`, not replaced) of non-individual entities with
an organisation name. Dropped: individuals / sole traders (IND), self-managed superannuation funds
(SMF, private retirement funds named after their members), deceased estates (DES), family
investment trusts (DIT) unless they carry a business name, and partnerships, trusts or other
unincorporated entities whose name is persons' names (`_rules.personal_name_sql`: a given name
or an initial and no trade word) unless they carry a business name that is not. Companies (an
ASIC number, or entity type PRV / PUB) are legal persons and always kept.

Mapped: ABN -> registry AU-ABN + company_number; main name -> legal_name (and name); a business
name replaces the display name for trusts ("THE TRUSTEE FOR ...") and person-named partnerships;
state -> region, postcode; entity type text -> legal_form; ABN status date -> founded_year when
after 2000 (ABNs were issued to every existing business in 1999-2000, so those dates are not
founding dates). The extract has no street address, town or industry code.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import IO
from xml.etree import ElementTree as ET

from pepite.connectors.bulk import (
    BuildOptions,
    BulkSource,
    ShardStats,
    provenance_json,
    register,
)
from pepite.connectors.bulk import _rules as rules
from pepite.packs import bulk
from pepite.packs.builder import BUILD_HINT

log = logging.getLogger(__name__)

NAME = "abr"
DATASET_API = "https://data.gov.au/data/api/3/action/package_show?id=abn-bulk-extract"
DATASET_PAGE = "https://data.gov.au/data/dataset/abn-bulk-extract"
URL_ENV = "PEPITE_ABR_URL"  # override: URLs or local .zip / .xml files, space-separated

FIELDS: tuple[str, ...] = (
    "abn",
    "status",
    "status_from",
    "replaced",
    "updated",
    "entity_type",
    "entity_type_text",
    "main_name",
    "business_name",
    "state",
    "postcode",
    "asic_number",
)
BATCH_ROWS = 100_000
DROP_TYPES: dict[str, str] = {
    "SMF": "self-managed super fund",
    "DES": "deceased estate",
}
COMPANY_TYPES = ("PRV", "PUB")
STATES: dict[str, str] = {
    "NSW": "New South Wales",
    "VIC": "Victoria",
    "QLD": "Queensland",
    "WA": "Western Australia",
    "SA": "South Australia",
    "TAS": "Tasmania",
    "ACT": "Australian Capital Territory",
    "NT": "Northern Territory",
}


# --------------------------------------------------------------------------- finding the files


def zip_urls(package: dict) -> list[str]:
    """ZIP resources of the `abn-bulk-extract` package (CKAN package_show answer), in order."""
    resources = (package.get("result") or {}).get("resources") or []
    urls = [
        r["url"]
        for r in resources
        if isinstance(r, dict)
        and str(r.get("url", "")).lower().split("?")[0].endswith(".zip")
        and "public" in (str(r.get("url", "")) + str(r.get("name", ""))).lower()
    ]

    def key(url: str) -> tuple[int, str]:
        m = re.search(r"split_(\d+)", url)
        return (int(m.group(1)) if m else 0, url)

    return sorted(dict.fromkeys(urls), key=key)


def resolve_urls() -> list[str]:
    if override := os.environ.get(URL_ENV, "").strip():
        return override.split()
    urls = zip_urls(json.loads(bulk.get_text(DATASET_API, user_agent=rules.user_agent())))
    if not urls:
        raise RuntimeError(f"no public_split ZIP found in {DATASET_API}")
    return urls


# --------------------------------------------------------------------------- XML stream parsing


class _Tags:
    """Element names, with the document's namespace when it has one."""

    def __init__(self, ns: str = "") -> None:
        for tag in ("ABR ABN EntityType EntityTypeInd EntityTypeText MainEntity NonIndividualName "
                    "NonIndividualNameText BusinessAddress AddressDetails State Postcode "
                    "ASICNumber OtherEntity").split():  # fmt: skip
            setattr(self, tag, ns + tag)


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    value = " ".join(elem.text.split())
    return value or None


def record(abr: ET.Element, t: _Tags) -> tuple | None:
    """One kept-candidate row from an <ABR> element; None for cancelled ABNs and individuals."""
    abn = abr.find(t.ABN)
    if abn is None or abn.get("status") != "ACT":
        return None
    etype = abr.find(t.EntityType)
    code = _text(etype.find(t.EntityTypeInd)) if etype is not None else None
    main = abr.find(t.MainEntity)
    if code == "IND" or main is None:
        return None
    name_el = main.find(t.NonIndividualName)
    details = main.find(f"{t.BusinessAddress}/{t.AddressDetails}")
    business = trading = None
    for other in abr.iterfind(f"{t.OtherEntity}/{t.NonIndividualName}"):
        kind = other.get("type")
        if kind == "BN" and business is None:
            business = _text(other.find(t.NonIndividualNameText))
        elif kind == "TRD" and trading is None:
            trading = _text(other.find(t.NonIndividualNameText))
    return (
        _text(abn),
        "ACT",
        abn.get("ABNStatusFromDate"),
        abr.get("replaced"),
        abr.get("recordLastUpdatedDate"),
        code,
        _text(etype.find(t.EntityTypeText)) if etype is not None else None,
        _text(name_el.find(t.NonIndividualNameText)) if name_el is not None else None,
        business or trading,
        _text(details.find(t.State)) if details is not None else None,
        _text(details.find(t.Postcode)) if details is not None else None,
        _text(abr.find(t.ASICNumber)),
    )


def iter_records(stream: IO[bytes]) -> Iterator[tuple | None]:
    """Yield `record()` for every <ABR> of an XML stream, clearing parsed elements as it goes."""
    context = ET.iterparse(stream, events=("start", "end"))
    root = None
    tags = _Tags()
    for event, elem in context:
        if root is None:
            root = elem
            ns = elem.tag[: elem.tag.index("}") + 1] if elem.tag.startswith("{") else ""
            tags = _Tags(ns)
            continue
        if event == "end" and elem.tag == tags.ABR:
            yield record(elem, tags)
            root.clear()  # drop the finished record (and every earlier one) from the tree


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError(BUILD_HINT) from exc
    return pa, pq


def parse_to_parquet(
    stream: IO[bytes], out: Path, *, label: str, max_rows: int | None = None
) -> tuple[int, int]:
    """Stream-parse one XML file into `out` (Parquet, all text). Returns (ABNs read, rows kept)."""
    pa, pq = _pyarrow()
    schema = pa.schema([(f, pa.string()) for f in FIELDS])
    part = out.with_name(out.name + ".part")
    out.parent.mkdir(parents=True, exist_ok=True)
    seen = kept = 0
    cols: list[list] = [[] for _ in FIELDS]
    t0 = last = time.monotonic()

    def flush(writer) -> None:
        if cols[0]:
            writer.write_table(pa.table(dict(zip(FIELDS, cols, strict=True)), schema=schema))
            for c in cols:
                c.clear()

    with pq.ParquetWriter(part, schema, compression="zstd") as writer:
        for rec in iter_records(stream):
            seen += 1
            if rec is not None:
                for c, v in zip(cols, rec, strict=True):
                    c.append(v)
                kept += 1
                if len(cols[0]) >= BATCH_ROWS:
                    flush(writer)
                if max_rows is not None and kept >= max_rows:
                    break
            if seen % 50_000 == 0 and time.monotonic() - last >= 30:
                last = time.monotonic()
                log.info("%s: %s ABNs read, %s kept, %.0f ABNs/s", label, f"{seen:,}",
                         f"{kept:,}", seen / (last - t0))  # fmt: skip
        flush(writer)
    part.replace(out)
    log.info("%s: %s ABNs read, %s active organisations, %.0f s", label, f"{seen:,}",
             f"{kept:,}", time.monotonic() - t0)  # fmt: skip
    return seen, kept


def xml_members(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        names = [i.filename for i in zf.infolist() if i.filename.lower().endswith(".xml")]
    return sorted(names)


@contextlib.contextmanager
def open_member(zip_path: Path, member: str) -> Iterator[IO[bytes]]:
    """A streaming reader over one ZIP member (no extraction to disk).

    Python's zipfile cannot read Deflate64 members (large archives made on Windows sometimes use
    it); those are piped through the `unzip` command instead.
    """
    zf = zipfile.ZipFile(zip_path)
    try:
        try:
            stream = zf.open(member)
        except NotImplementedError:
            if not shutil.which("unzip"):
                raise
            log.info("%s: compression not supported by zipfile, piping through unzip", member)
            proc = subprocess.Popen(["unzip", "-p", str(zip_path), member], stdout=subprocess.PIPE)
            try:
                yield proc.stdout
            finally:
                proc.stdout.close()
                proc.wait()
            return
        with stream:
            yield stream
    finally:
        zf.close()


# --------------------------------------------------------------------------- build


def build(options: BuildOptions) -> ShardStats:
    # A test run (limit) reads every file too: its sample is the rows with the smallest hash of
    # the register key (packs.bulk.SAMPLE_ORDER), which must be drawn from the whole register.
    urls = resolve_urls()
    raw_dir = options.work_dir / "raw"
    parts: list[Path] = []
    read = 0
    for i, url in enumerate(urls, start=1):
        name = Path(url.split("?")[0]).name or f"abn-bulk-{i}.zip"
        path = bulk.download(url, options.work_dir / name, user_agent=rules.user_agent(),
                             label=f"{NAME} {name}")  # fmt: skip
        if path.suffix.lower() == ".xml":
            members: list[tuple[str, Path | None]] = [(path.name, None)]
        else:
            members = [(m, path) for m in xml_members(path)]
        for member, zpath in members:
            out = raw_dir / (Path(member).stem + ".parquet")
            counted = out.with_suffix(".read")
            if out.exists() and counted.exists():
                log.info("%s: %s already parsed", NAME, member)
                read += int(counted.read_text() or 0)
            else:
                label = f"{NAME} {Path(member).name}"
                if zpath is None:
                    with path.open("rb") as fh:
                        seen, _ = parse_to_parquet(fh, out, label=label)
                else:
                    with open_member(zpath, member) as fh:
                        seen, _ = parse_to_parquet(fh, out, label=label)
                counted.write_text(str(seen))
                read += seen
            parts.append(out)
        if path.suffix.lower() == ".zip":
            bulk.discard(path, options.work_dir)  # parsed: free the disk
    con = bulk.connect(options.work_dir)
    try:
        url = urls[0] if len(urls) == 1 and bulk.is_url(urls[0]) else DATASET_PAGE
        return transform(con, parts, options, url=url, read=read)
    finally:
        con.close()


def transform(
    con, parts: list[Path], options: BuildOptions, *, url: str | None = None, read: int = 0
) -> ShardStats:
    """Parsed ABR rows (Parquet) -> shard (abr.parquet)."""
    files = "[" + ", ".join(bulk.sql_str(str(p)) for p in parts) + "]"
    con.execute(f"""
        CREATE OR REPLACE TABLE au_raw AS
        SELECT * FROM read_parquet({files})
        QUALIFY row_number() OVER (PARTITION BY abn ORDER BY updated DESC NULLS LAST) = 1
    """)
    total = bulk.count(con, "au_raw")
    log.info("%s: %d active organisations staged (of %s ABNs read)", NAME, total,
             f"{read:,}" if read else "?")  # fmt: skip
    company = f"(asic_number IS NOT NULL OR entity_type IN {bulk.sql_in(COMPANY_TYPES)})"
    con.execute(f"""
        CREATE OR REPLACE TABLE au_stage AS
        WITH t AS (
          SELECT *, lc_digits(abn) AS abn_n, lc_clean(main_name) AS main,
                 lc_clean(business_name) AS bn, {company} AS is_company
          FROM au_raw
        ), p AS (
          SELECT *,
            NOT is_company AND {rules.personal_name_sql("main")} AS main_personal,
            bn IS NOT NULL AND NOT {rules.personal_name_sql("bn")} AS bn_ok
          FROM t
        )
        SELECT *,
          CASE
            WHEN length(abn_n) <> 11 OR main IS NULL THEN 'no number or name'
            WHEN coalesce(replaced, 'N') = 'Y' THEN 'replaced ABN'
            {" ".join(f"WHEN entity_type = '{k}' THEN '{v}'" for k, v in DROP_TYPES.items())}
            WHEN entity_type = 'DIT' AND NOT bn_ok THEN 'family investment trust'
            WHEN main_personal AND NOT bn_ok THEN 'named after persons'
          END AS dropped
        FROM p
    """)
    con.execute("DROP TABLE au_raw")
    counts = con.execute(
        "SELECT dropped, count(*) FROM au_stage WHERE dropped IS NOT NULL "
        "GROUP BY dropped ORDER BY count(*) DESC, dropped"
    ).fetchall()
    notes = "; ".join(
        x
        for x in (
            f"{read - total:,} cancelled ABNs or individuals skipped while parsing" if read else "",
            ("dropped: " + ", ".join(f"{n:,} {why}" for why, n in counts)) if counts else "",
        )
        if x
    )
    display = (
        "CASE WHEN bn_ok AND (main_personal OR entity_type = 'DIT' "
        "OR upper(main) LIKE 'THE TRUSTEE FOR %') THEN bn ELSE main END"
    )
    exprs = {
        "name": display,
        "legal_name": "CASE WHEN main_personal THEN NULL ELSE main END",
        "country": "'AU'",
        "region": rules.map_case_sql("upper(lc_clean(state))", STATES, "lc_clean(state)"),
        "founded_year": "CASE WHEN lc_year(status_from) >= 2001 THEN lc_year(status_from) END",
        "sources_json": bulk.sql_str(provenance_json(SOURCE, url)),
        "registry": "'AU-ABN'",
        "company_number": "abn_n",
        "status": "'active'",
        "legal_form": "lc_clean(entity_type_text)",
        "postal_code": "CASE WHEN regexp_full_match(lc_clean(postcode), '[0-9]{4}') "
        "THEN lc_clean(postcode) END",
    }
    stats = bulk.write_source_shard(
        con,
        SOURCE,
        options,
        exprs,
        "(SELECT * FROM au_stage WHERE dropped IS NULL) AS au_kept",
        total=total,
        order="company_number",
        notes=notes,
    )
    stats.skipped += max(0, read - total)
    return stats


SOURCE = register(
    BulkSource(
        name=NAME,
        title="Australian Business Register, ABN Bulk Extract",
        licence="CC-BY-3.0-AU",
        licence_url="https://creativecommons.org/licenses/by/3.0/au/",
        homepage=DATASET_PAGE,
        countries=frozenset({"AU"}),
        build=build,
        attribution="ABN Bulk Extract, Australian Business Register (Australian Taxation "
        "Office), licensed under Creative Commons Attribution 3.0 Australia (CC BY 3.0 AU), "
        "https://creativecommons.org/licenses/by/3.0/au/. Data was selected and transformed by "
        "Pépite (active non-individual ABNs only).",
    )
)
