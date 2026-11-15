"""Quality report of a merged pack: QUALITY.md (readable) and quality.json.

    leadcommons pack quality packs-out/ [--json]

Per country and overall: rows, input rows per source, output rows per source, merges made by
key type (registry, siren, cik, lei, wikidata, name_postcode, name_city), duplicates removed,
inactive companies dropped, fill rate of every pack column, share of rows with a domain or a
website, with exact employees and with revenue, where the domains came from, and health checks:
pack columns, unique ids, ids and names present, no inactive rows, one country per file,
manifest matching the files (bytes, sha256, rows), valid sources_json, unique LEIs, and a
warning count of names that look like a natural person ("Firstname Lastname" with no company
word). The report is published: it never quotes a name, only counts and company ids.

Numbers that only the merge knows (input rows, merges, duplicates) are read from each file's
Parquet metadata (`merge_stats`, written by packs/merge.py); the rest is measured on the files.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from leadcommons.packs.linking import install_macros, lit
from leadcommons.search import PACK_COLUMNS

FIRST_NAMES: frozenset[str] = frozenset(
    """jean marie pierre michel philippe alain nicolas christophe patrick daniel bernard eric
    laurent frederic stephane thierry julien olivier sebastien francois isabelle nathalie sylvie
    catherine sophie christine valerie sandrine anne monique francoise martine celine veronique
    jacques andre rene claude louis paul henri marc antoine maxime alexandre romain guillaume
    camille julie emilie aurelie claire lucie lea manon chloe emma sarah laura pascal didier
    yves gerard serge herve dominique brigitte chantal nadine josiane mohamed ahmed ali karim
    john james robert michael william david richard joseph thomas charles christopher matthew
    anthony mark donald steven andrew joshua kenneth kevin brian george edward peter mary
    patricia jennifer linda elizabeth barbara susan jessica karen nancy lisa margaret betty
    sandra ashley emily donna michelle carol amanda melissa deborah stephanie rebecca helen
    sharon cynthia kathleen amy angela anna olivia charlotte jack harry oliver ian simon neil
    gary stephen alan graham colin keith martin philip tony lee ryan adam samuel benjamin
    daniel jonathan nicholas timothy jason jeffrey scott eric gregory frank raymond patrick
    ole lars hans per knut jan bjorn arne kari ingrid liv marit hilde nina erik terje morten
    geir tor svein odd nils kjell rune espen jorunn solveig astrid klaus wolfgang jurgen stefan
    andreas markus maria giuseppe giovanni antonio mario luigi francesco marco carlos jose juan
    luis manuel javier miguel ana carmen""".split()
)
COMPANY_WORDS: frozenset[str] = frozenset(
    """ltd limited plc llp llc lp inc corp corporation co company cie sa sas sasu sarl eurl sci
    snc scs scop gie gmbh mbh ag kg se as asa ab oy oyj bv nv pty pte spa srl sl group groupe
    holding holdings services service consulting conseil conseils international partners
    associates association asso trust foundation fondation bank banque capital invest
    investment investments immobilier immo transport transports construction batiment solutions
    technologies technology tech systems global france europe uk store shop restaurant cafe
    hotel garage auto motors energy media studio design agency agence club sport sports school
    ecole university universite hospital clinique pharmacie pharmacy farm ferme cabinet atelier
    boulangerie industries industrie trading import export management marketing logistics
    properties property estate developments engineering church eglise fund fonds""".split()
)
FAIL, WARN, OK = "fail", "warn", "ok"
SHARE_COLUMNS = ("domain", "website", "employees", "revenue")


def _read_manifest(packs_dir: Path) -> dict | None:
    try:
        data = json.loads((packs_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def pack_files(packs_dir: Path) -> tuple[dict | None, list[Path]]:
    """The manifest (or None) and the country files it lists (else every companies-*.parquet)."""
    manifest = _read_manifest(packs_dir)
    if manifest and isinstance(manifest.get("countries"), list):
        files = [packs_dir / str(c.get("file", "")) for c in manifest["countries"]]
    else:
        files = sorted(packs_dir.glob("companies-*.parquet"))
    return manifest, files


def _txt(v: object) -> str:
    return v.decode() if isinstance(v, bytes | bytearray) else str(v)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _share(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def _person_sql(col: str = "name") -> str:
    """True when `col` looks like 'Firstname Lastname' (or 'LASTNAME Firstname')."""
    first = "[" + ", ".join(lit(n) for n in sorted(FIRST_NAMES)) + "]"
    words = "[" + ", ".join(lit(w) for w in sorted(COMPANY_WORDS)) + "]"
    toks = f"regexp_split_to_array(trim(lower(strip_accents({col}))), '\\s+')"
    return f"""(
      regexp_full_match(trim(lower(strip_accents({col}))), '[a-z]+([ \\-''][a-z]+){{1,2}}')
      AND len({toks}) BETWEEN 2 AND 3
      AND (list_contains({first}, split_part({toks}[1], '-', 1))
           OR list_contains({first}, split_part({toks}[-1], '-', 1)))
      AND NOT list_has_any({toks}, {words})
    )"""


def _file_metrics(con, path: Path, manifest_entry: dict | None) -> dict:
    p = lit(str(path))
    kv = {
        _txt(k): _txt(v)
        for k, v in con.execute(f"SELECT key, value FROM parquet_kv_metadata({p})").fetchall()
    }
    cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({p})").fetchall()]
    m = re.match(r"companies-([A-Z]{2})-", path.name)
    cc = kv.get("country") or (m[1] if m else "")
    have = [c for c in PACK_COLUMNS if c in cols]
    fills = ", ".join(f"count({c})" for c in have)
    status = "lk_status(status)" if "status" in cols else "NULL"
    row = con.execute(f"""
      SELECT count(*), {fills},
        count(*) FILTER (WHERE {status} = 'inactive'),
        count(*) FILTER (WHERE domain IS NOT NULL OR website IS NOT NULL),
        count(*) FILTER (WHERE id IS NULL OR trim(id) = '' OR name IS NULL OR trim(name) = ''),
        count(*) FILTER (WHERE country IS DISTINCT FROM {lit(cc)}),
        count(*) FILTER (WHERE sources_json IS NULL OR NOT json_valid(sources_json)),
        count(*) FILTER (WHERE {_person_sql()}),
        count(*) FILTER (WHERE industry IS NOT NULL AND NOT regexp_full_match(industry, '[A-U]'))
      FROM read_parquet({p})
    """).fetchone()
    rows = int(row[0])
    fill = {c: int(v) for c, v in zip(have, row[1 : 1 + len(have)], strict=True)}
    tail = (int(v) for v in row[1 + len(have) :])
    inactive, dom_or_web, missing, wrong_cc, bad_json, persons, bad_industry = tail
    sample = [
        r[0]
        for r in con.execute(
            f"SELECT id FROM read_parquet({p}) WHERE {_person_sql()} ORDER BY id LIMIT 10"
        ).fetchall()
    ] if persons else []  # fmt: skip
    try:
        merge_stats = json.loads(kv.get("merge_stats", "{}") or "{}")
    except ValueError:
        merge_stats = {}
    out = {
        "country": cc,
        "file": path.name,
        "rows": rows,
        "bytes": path.stat().st_size,
        "columns_ok": cols == list(PACK_COLUMNS),
        "missing_columns": [c for c in PACK_COLUMNS if c not in cols],
        "fill": fill,
        "fill_rate": {c: _share(fill.get(c, 0), rows) for c in PACK_COLUMNS},
        "with_domain": fill.get("domain", 0),
        "with_domain_or_website": dom_or_web,
        "share_with_domain": _share(fill.get("domain", 0), rows),
        "share_with_domain_or_website": _share(dom_or_web, rows),
        "share_with_employees": _share(fill.get("employees", 0), rows),
        "share_with_revenue": _share(fill.get("revenue", 0), rows),
        "inactive_rows": inactive,
        "missing_id_or_name": missing,
        "wrong_country_rows": wrong_cc,
        "invalid_sources_json": bad_json,
        "person_like_names": persons,
        "person_like_sample_ids": sample,
        "industry_not_isic_section": bad_industry,
        "release": kv.get("release"),
        "built_at": kv.get("built_at"),
        "sources": [s for s in kv.get("sources", "").split(",") if s],
        "input_rows": merge_stats.get("input_rows", {}),
        "input_rows_total": merge_stats.get("input_rows_total"),
        "rows_by_source": merge_stats.get("rows_by_source", {}),
        "merges": merge_stats.get("merges", {}),
        "same_source_duplicates": merge_stats.get("same_source_duplicates", {}),
        "duplicates_removed": merge_stats.get("duplicates_removed"),
        "inactive_dropped": merge_stats.get("inactive_dropped"),
        "not_in_register_dropped": merge_stats.get("not_in_register_dropped"),
        "id_collisions_dropped": merge_stats.get("id_collisions_dropped"),
        "identifiers_cleared": merge_stats.get("identifiers_cleared", {}),
        "domains_by_source": merge_stats.get("domains_by_source", {}),
        "domain_via_registry_ids": merge_stats.get("domain_via_registry_ids"),
        "link_stats": json.loads(kv["link_stats"]) if kv.get("link_stats") else None,
    }
    if not out["rows_by_source"]:  # not written by pack merge: measure from sources_json
        out["rows_by_source"] = {
            str(s): int(n)
            for s, n in con.execute(f"""
              SELECT s, count(*) FROM (
                SELECT unnest(list_distinct(json_extract_string(sources_json, '$[*].source'))) AS s
                FROM read_parquet({p}) WHERE json_valid(sources_json)
              ) GROUP BY s ORDER BY s
            """).fetchall()
            if s
        }
    if manifest_entry is not None:
        out["manifest_ok"] = (
            manifest_entry.get("rows") == rows
            and manifest_entry.get("bytes") == out["bytes"]
            and manifest_entry.get("sha256") == _sha256(path)
        )
    return out


def _sum_dicts(items: list[dict]) -> dict[str, int]:
    total: dict[str, int] = {}
    for d in items:
        for k, v in (d or {}).items():
            total[k] = total.get(k, 0) + int(v or 0)
    return dict(sorted(total.items()))


def compute_quality(packs_dir: Path) -> dict:
    """Measure a merged pack directory (see module docstring)."""
    import duckdb

    packs_dir = Path(packs_dir)
    manifest, files = pack_files(packs_dir)
    con = duckdb.connect(":memory:")
    install_macros(con)
    entries = {str(c.get("file")): c for c in (manifest or {}).get("countries", [])}
    countries, missing_files = [], []
    for f in files:
        if not f.is_file():
            missing_files.append(f.name)
            continue
        countries.append(_file_metrics(con, f, entries.get(f.name) if manifest else None))
    existing = [f for f in files if f.is_file()]
    lst = "[" + ", ".join(lit(str(f)) for f in existing) + "]"
    dup_ids = dup_leis = 0
    if existing:
        dup_ids = int(con.execute(f"""
          SELECT count(*) FROM (SELECT id FROM read_parquet({lst}) GROUP BY id HAVING count(*) > 1)
        """).fetchone()[0])  # fmt: skip
        dup_leis = int(con.execute(f"""
          SELECT count(*) FROM (SELECT lei FROM read_parquet({lst}, union_by_name = true)
                                WHERE lei IS NOT NULL GROUP BY lei HAVING count(*) > 1)
        """).fetchone()[0])  # fmt: skip
    con.close()

    rows = sum(c["rows"] for c in countries)
    fill = _sum_dicts([c["fill"] for c in countries])
    dom_or_web = sum(c["with_domain_or_website"] for c in countries)
    persons = sum(c["person_like_names"] for c in countries)

    def known(key: str) -> int | None:
        vals = [c[key] for c in countries if c.get(key) is not None]
        return sum(vals) if vals else None

    overall = {
        "countries": len(countries),
        "rows": rows,
        "input_rows": _sum_dicts([c["input_rows"] for c in countries]),
        "input_rows_total": known("input_rows_total"),
        "rows_by_source": _sum_dicts([c["rows_by_source"] for c in countries]),
        "merges": _sum_dicts([c["merges"] for c in countries]),
        "same_source_duplicates": _sum_dicts([c["same_source_duplicates"] for c in countries]),
        "duplicates_removed": known("duplicates_removed"),
        "inactive_dropped": known("inactive_dropped"),
        "not_in_register_dropped": known("not_in_register_dropped"),
        "id_collisions_dropped": known("id_collisions_dropped"),
        "domains_by_source": _sum_dicts([c["domains_by_source"] for c in countries]),
        "domain_via_registry_ids": known("domain_via_registry_ids"),
        "fill": fill,
        "fill_rate": {c: _share(fill.get(c, 0), rows) for c in PACK_COLUMNS},
        "with_domain": fill.get("domain", 0),
        "with_domain_or_website": dom_or_web,
        "share_with_domain": _share(fill.get("domain", 0), rows),
        "share_with_domain_or_website": _share(dom_or_web, rows),
        "share_with_employees": _share(fill.get("employees", 0), rows),
        "share_with_revenue": _share(fill.get("revenue", 0), rows),
        "person_like_names": persons,
        "duplicate_ids": dup_ids,
        "duplicate_leis": dup_leis,
    }

    def check(name: str, ok: bool, detail: str, level: str = FAIL) -> dict:
        return {"check": name, "status": OK if ok else level, "detail": detail}

    bad_cols = [c["file"] for c in countries if not c["columns_ok"]]
    checks = [
        check("files_present", bool(countries) and not missing_files,
              f"{len(countries)} files" + (f", missing: {missing_files}" if missing_files
                                           else "")),
        check("pack_columns", not bad_cols, "exactly PACK_COLUMNS" if not bad_cols
              else f"wrong columns in {bad_cols}"),
        check("ids_unique", dup_ids == 0, f"{dup_ids} duplicated ids across {rows:,} rows"),
        check("ids_and_names_present", not any(c["missing_id_or_name"] for c in countries),
              f"{sum(c['missing_id_or_name'] for c in countries)} rows without id or name"),
        check("no_inactive_rows", not any(c["inactive_rows"] for c in countries),
              f"{sum(c['inactive_rows'] for c in countries)} inactive rows"),
        check("one_country_per_file", not any(c["wrong_country_rows"] for c in countries),
              f"{sum(c['wrong_country_rows'] for c in countries)} rows in another country's file"),
        check("sources_json_valid", not any(c["invalid_sources_json"] for c in countries),
              f"{sum(c['invalid_sources_json'] for c in countries)} rows with invalid JSON"),
        check("lei_unique", dup_leis == 0, f"{dup_leis} LEIs on more than one row", WARN),
        check("industry_isic_section",
              not any(c["industry_not_isic_section"] for c in countries),
              f"{sum(c['industry_not_isic_section'] for c in countries)} industry values are "
              "not an ISIC section letter (A-U)", WARN),
        check("person_like_names", persons == 0,
              f"{persons} names look like a natural person ({_share(persons, rows):.2%}); "
              "sample ids in quality.json", WARN),
    ]  # fmt: skip
    if manifest is None:
        checks.append(check("manifest", False, "no manifest.json", WARN))
    else:
        bad = [c["file"] for c in countries if not c.get("manifest_ok")]
        checks.append(check("manifest", not bad, "rows, bytes and sha256 match" if not bad
                            else f"mismatch for {bad}"))  # fmt: skip
    statuses = {c["status"] for c in checks}
    status = FAIL if FAIL in statuses else WARN if WARN in statuses else OK
    first = countries[0] if countries else {}
    return {
        "schema": 3,
        "release": (manifest or {}).get("release") or first.get("release"),
        "built_at": (manifest or {}).get("built_at") or first.get("built_at"),
        "measured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": status,
        "overall": overall,
        "countries": countries,
        "checks": checks,
        "link_stats": first.get("link_stats"),
    }


# -- rendering -----------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _n(x: int | None) -> str:
    return "n/a" if x is None else f"{x:,}"


def render_markdown(q: dict) -> str:
    """QUALITY.md."""
    o, cs = q["overall"], q["countries"]
    warn = sum(c["status"] == WARN for c in q["checks"])
    fail = sum(c["status"] == FAIL for c in q["checks"])
    lines = [
        f"# Pack quality: {q.get('release') or 'unreleased'}",
        "",
        f"Built {q.get('built_at') or 'n/a'}, measured {q['measured_at']}. "
        f"**Status: {q['status']}** ({fail} failed, {warn} warnings). "
        f"{_n(o['rows'])} companies in {o['countries']} countries.",
        "",
        "Every number below is measured on the published files; input rows, merges and "
        "duplicates come from the merge itself. Names are never quoted here. 'Not in register': "
        "companies that carry a register number (from GLEIF or Wikidata) that the register's "
        "own list of active companies does not hold, dropped as not active.",
        "",
        "## Summary",
        "",
        "| Country | Rows | Input rows | Duplicates removed | Inactive dropped | "
        "Not in register | With domain | Domain or website | Exact employees | Revenue |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in [*cs, {**o, "country": "**All**"}]:
        lines.append(
            f"| {c['country']} | {_n(c['rows'])} | {_n(c.get('input_rows_total'))} | "
            f"{_n(c.get('duplicates_removed'))} | {_n(c.get('inactive_dropped'))} | "
            f"{_n(c.get('not_in_register_dropped'))} | "
            f"{_pct(c['share_with_domain'])} | {_pct(c['share_with_domain_or_website'])} | "
            f"{_pct(c['share_with_employees'])} | {_pct(c['share_with_revenue'])} |"
        )
    lines += ["", "## Health checks", "", "| Check | Status | Detail |", "| --- | --- | --- |"]
    lines += [f"| {c['check']} | {c['status']} | {c['detail']} |" for c in q["checks"]]

    keys = sorted({k for c in cs for k in c["merges"]})
    lines += [
        "",
        "## Merges by key",
        "",
        "Rows folded into another row of the same company, by the strongest key they shared "
        "(registry: a register number; siren; cik; lei; wikidata; name_postcode / name_city: "
        "the careful name fallback).",
        "",
    ]
    if keys:
        lines += [
            "| Country | " + " | ".join(keys) + " | same-source duplicates |",
            "| --- | " + " | ".join("---:" for _ in keys) + " | ---: |",
        ]
        for c in [*(c for c in cs if c["merges"]), {**o, "country": "**All**"}]:
            lines.append(
                f"| {c['country']} | "
                + " | ".join(_n(c["merges"].get(k, 0)) for k in keys)
                + f" | {_n(sum(c['same_source_duplicates'].values()))} |"
            )
    else:
        lines.append("No merges.")

    sources = sorted({s for c in cs for s in (*c["rows_by_source"], *c["input_rows"])})
    lines += [
        "",
        "## Rows per source",
        "",
        "Companies in the pack that hold data from each source (input rows in brackets).",
        "",
        "| Country | " + " | ".join(sources) + " |",
        "| --- | " + " | ".join("---:" for _ in sources) + " |",
    ]
    for c in [*cs, {**o, "country": "**All**"}]:
        cells = []
        for s in sources:
            out_n, in_n = c["rows_by_source"].get(s), c["input_rows"].get(s)
            cells.append("" if out_n is None and in_n is None
                         else f"{_n(out_n or 0)} ({_n(in_n or 0)})")  # fmt: skip
        lines.append(f"| {c['country']} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Where domains come from",
        "",
        f"Domains attached to a registered company through a registry identifier (Wikidata, "
        f"EDGAR, GLEIF bridges): {_n(o.get('domain_via_registry_ids'))}.",
        "",
        "| Source | Companies with a domain from it |",
        "| --- | ---: |",
    ]
    lines += [f"| {s} | {_n(n)} |" for s, n in o["domains_by_source"].items()]

    top = sorted(cs, key=lambda c: -c["rows"])[:8]
    lines += [
        "",
        "## Fill rate per column",
        "",
        "Share of rows with a value (all countries, then the largest ones; every country is in "
        "quality.json).",
        "",
        "| Column | All | " + " | ".join(c["country"] for c in top) + " |",
        "| --- | ---: | " + " | ".join("---:" for _ in top) + " |",
    ]
    for col in PACK_COLUMNS:
        lines.append(
            f"| {col} | {_pct(o['fill_rate'][col])} | "
            + " | ".join(_pct(c["fill_rate"][col]) for c in top)
            + " |"
        )
    ls = q.get("link_stats") or {}
    if ls:
        lines += [
            "",
            "## Linking",
            "",
            f"- Rows read: {_n(ls.get('rows'))}; rows sharing a key with another row: "
            f"{_n(ls.get('linked_rows'))}; keys shared: {_n(ls.get('linking_keys'))}.",
            f"- Label propagation: {ls.get('rounds')} rounds, {_n(ls.get('components'))} "
            "linked components.",
            f"- Register guard: {_n(ls.get('conflicted_components'))} components held two numbers "
            f"of one register (or registers of two countries) and were split; "
            f"{_n(ls.get('detached_rows'))} rows left on their own.",
            f"- Name fallback: {_n(ls.get('fallback_postcode'))} by name + postcode, "
            f"{_n(ls.get('fallback_city'))} by name + city.",
            f"- Keys ignored because too many rows shared them: {_n(ls.get('hub_keys'))}.",
        ]
    lines.append("")
    return "\n".join(lines)


def summary_text(q: dict) -> str:
    """Short console summary."""
    o = q["overall"]
    merges = ", ".join(f"{k} {v:,}" for k, v in o["merges"].items()) or "none"
    lines = [
        f"{q.get('release') or 'pack'}: {o['rows']:,} companies in {o['countries']} countries "
        f"(status: {q['status']})",
        f"  domain {_pct(o['share_with_domain'])}, domain or website "
        f"{_pct(o['share_with_domain_or_website'])}, exact employees "
        f"{_pct(o['share_with_employees'])}, revenue {_pct(o['share_with_revenue'])}",
        f"  duplicates removed {_n(o['duplicates_removed'])} ({merges}); "
        f"inactive dropped {_n(o['inactive_dropped'])}, not in register "
        f"{_n(o['not_in_register_dropped'])}",
    ]
    for c in q["countries"]:
        lines.append(
            f"  {c['country']}  {c['rows']:>12,} rows  domain {_pct(c['share_with_domain']):>6}  "
            f"employees {_pct(c['share_with_employees']):>6}  {c['file']}"
        )
    for c in q["checks"]:
        if c["status"] != OK:
            lines.append(f"  [{c['status']}] {c['check']}: {c['detail']}")
    return "\n".join(lines)


def write_quality(packs_dir: Path) -> dict:
    """Measure packs_dir and write QUALITY.md and quality.json there. Returns the report."""
    packs_dir = Path(packs_dir)
    q = compute_quality(packs_dir)
    (packs_dir / "quality.json").write_text(json.dumps(q, indent=2) + "\n", encoding="utf-8")
    (packs_dir / "QUALITY.md").write_text(render_markdown(q), encoding="utf-8")
    return q


__all__ = ["compute_quality", "pack_files", "render_markdown", "summary_text", "write_quality"]
