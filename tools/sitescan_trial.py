"""Helpers of .github/workflows/sitescan-trial.yml: a deterministic sample and a log report.

    sitescan_trial.py sample --domains domains-no.txt.gz --n 3000 --out sample.txt
    sitescan_trial.py report --tld no --country NO --findings DIR --stats 'DIR/*.stats.json' \\
        --seconds 123 [--pack companies-NO.parquet]

The report prints company facts only (domains, register numbers, evidence URL paths, counts):
never page text, names or phone numbers.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import heapq
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from pepite.sitescan.shard import REGISTRY_COUNTRY

_CLEAN_ERROR = re.compile(r"^(?:dns|timeout|connect|tls|other): \w+$")


def sample(domains: Path, n: int, seed: str, out: Path) -> int:
    """The n domains with the smallest sha1(seed:domain): same input, same sample."""
    with gzip.open(domains, "rt", encoding="utf-8") as fh:
        names = {line.strip().lower() for line in fh if line.strip()}
    pick = heapq.nsmallest(n, names, key=lambda d: hashlib.sha1(f"{seed}:{d}".encode()).digest())
    out.write_text("".join(f"{d}\n" for d in sorted(pick)), encoding="utf-8")
    return len(pick)


def error_kind(error: str | None) -> str:
    """An error text without the parts that vary per site (host, path, content type)."""
    e = (error or "").strip()
    if _CLEAN_ERROR.match(e):
        return e
    if e.startswith("robots.txt disallows"):
        return "robots.txt disallows"
    return re.split(r"[:(]", e)[0].strip()[:60] or "(none)"


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def _rows(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def report(tld: str, country: str, findings: Path, stats: str, seconds: float, pack: Path | None):
    con = duckdb.connect()
    con.execute(
        f"CREATE TABLE f AS SELECT * FROM read_parquet('{findings}/**/findings-*.parquet') "
        "QUALIFY row_number() OVER (PARTITION BY domain ORDER BY scanned_at DESC) = 1"
    )
    total = _rows(con, "SELECT count(*) FROM f")[0][0]
    print(f"=== .{tld} ({country}) ===")
    procs = [json.loads(Path(p).read_text()) for p in sorted(glob.glob(stats))]
    per_proc = [p["domains"] / p["seconds"] for p in procs if p.get("seconds")]
    print(
        f"domains scanned: {total}; wall {seconds:.0f} s; "
        f"overall {total / max(seconds, 1):.2f} domains/s; "
        f"{len(procs)} processes, per process "
        + (f"{sum(per_proc) / len(per_proc):.2f} domains/s" if per_proc else "n/a")
    )
    if procs:
        reqs, pages = sum(p["requests"] for p in procs), sum(p["pages"] for p in procs)
        print(f"requests: {reqs} ({reqs / max(total, 1):.1f}/domain), pages read: {pages}")
    by_status = dict(_rows(con, "SELECT status, count(*) FROM f GROUP BY 1"))
    print("status:", {s: by_status.get(s, 0) for s in ("ok", "no-site", "robots", "error")})
    errs: dict[str, int] = {}
    for status, error, n in _rows(
        con, "SELECT status, error, count(*) FROM f WHERE status <> 'ok' GROUP BY 1, 2"
    ):
        key = f"{status}/{error_kind(error)}"
        errs[key] = errs.get(key, 0) + n
    print("top non-ok kinds:", dict(sorted(errs.items(), key=lambda kv: -kv[1])[:10]))
    ok = by_status.get("ok", 0)
    con.execute(
        """CREATE TABLE found AS
        SELECT domain, evidence_url, i.registry AS registry, i.kind AS kind,
               CASE WHEN i.registry = 'FR-SIREN' AND length(i.number) = 14
                    THEN left(i.number, 9) ELSE i.number END AS number
        FROM (SELECT *, unnest(from_json(ids,
              '[{"registry":"VARCHAR","number":"VARCHAR","kind":"VARCHAR"}]')) AS i
              FROM f WHERE status = 'ok' AND ids IS NOT NULL AND ids <> '[]')"""
    )
    with_ids = _rows(con, "SELECT count(DISTINCT domain) FROM found")[0][0]
    print(
        f"sites with >=1 register number: {with_ids} = {_pct(with_ids, ok)} of ok, "
        f"{_pct(with_ids, total)} of scanned"
    )
    print(
        "by registry (domains):",
        dict(
            _rows(
                con, "SELECT registry, count(DISTINCT domain) FROM found GROUP BY 1 ORDER BY 2 DESC"
            )
        ),
    )
    print(
        "by registry/kind (domains):",
        {
            f"{r}/{k}": n
            for r, k, n in _rows(
                con,
                "SELECT registry, kind, count(DISTINCT domain) FROM found "
                "GROUP BY 1, 2 ORDER BY 3 DESC",
            )
        },
    )
    con.execute(
        "CREATE TABLE amb AS SELECT domain, registry FROM found "
        "GROUP BY 1, 2 HAVING count(DISTINCT number) > 1"
    )
    print(
        "ambiguous sites (several numbers of one register):",
        _rows(con, "SELECT count(DISTINCT domain) FROM amb")[0][0],
    )
    print("sample findings (domain registry number evidence-path):")
    for d, r, n, u in _rows(
        con,
        "SELECT domain, registry, number, evidence_url FROM found "
        "ORDER BY md5(domain || number) LIMIT 25",
    ):
        print(f"  {d} {r} {n} {urlsplit(u or '').path or '/'}")
    if pack is None or not Path(pack).exists():
        print("precision: no register pack for this country; numbers are checksum-validated only")
        return
    precision(con, country, Path(pack))


def precision(con, country: str, pack: Path) -> None:
    regs = [r for r, c in REGISTRY_COUNTRY.items() if c == country]
    in_regs = ", ".join(f"'{r}'" for r in regs)
    con.execute(
        f"""CREATE TABLE p AS SELECT DISTINCT registry, company_number,
            nullif(lower(regexp_replace(domain, '^www\\.', '')), '') AS pdomain
        FROM read_parquet('{pack}') WHERE registry IN ({in_regs})"""
    )
    # Same rule as the shard: a domain with several numbers of one register is dropped.
    con.execute(
        f"""CREATE TABLE pairs AS SELECT DISTINCT domain, registry, number FROM found
        WHERE registry IN ({in_regs}) AND (domain, registry) NOT IN (SELECT * FROM amb)"""
    )
    n = _rows(con, "SELECT count(*) FROM pairs")[0][0]
    other = _rows(
        con,
        f"SELECT count(DISTINCT domain) FROM found WHERE registry NOT IN ({in_regs})",
    )[0][0]
    con.execute(
        """CREATE TABLE j AS SELECT pairs.*, p.company_number IS NOT NULL AS in_pack, p.pdomain
        FROM pairs LEFT JOIN p ON p.registry = pairs.registry AND p.company_number = pairs.number"""
    )
    hit = _rows(con, "SELECT count(*) FROM j WHERE in_pack")[0][0]
    print(f"--- precision against the {country} register pack ({', '.join(regs)}) ---")
    print(
        f"(domain, number) pairs in the pack's registers: {n}; domains with only other "
        f"registers (not checkable): {other}"
    )
    print(f"pair exists in the register pack: {hit} = {_pct(hit, n)}")
    both = _rows(con, "SELECT count(*) FROM j WHERE in_pack AND pdomain IS NOT NULL")[0][0]
    agree = _rows(con, "SELECT count(*) FROM j WHERE in_pack AND pdomain = domain")[0][0]
    print(
        f"pack already has a domain: {both}; same registrable domain: {agree} = {_pct(agree, both)}"
    )
    print("disagreements (registry number ours pack):")
    for r, num, d, pd in _rows(
        con,
        "SELECT registry, number, domain, pdomain FROM j WHERE in_pack AND pdomain IS NOT NULL "
        "AND pdomain <> domain ORDER BY md5(domain || number) LIMIT 15",
    ):
        print(f"  {r} {num} ours={d} pack={pd}")
    new = _rows(
        con, "SELECT count(*), count(DISTINCT number) FROM j WHERE in_pack AND pdomain IS NULL"
    )[0]
    print(f"NEW company-to-domain links (pack has no domain): {new[0]} for {new[1]} companies")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--domains", type=Path, required=True)
    s.add_argument("--n", type=int, required=True)
    s.add_argument("--seed", default="sitescan-trial")
    s.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("report")
    r.add_argument("--tld", required=True)
    r.add_argument("--country", required=True)
    r.add_argument("--findings", type=Path, required=True)
    r.add_argument("--stats", required=True)
    r.add_argument("--seconds", type=float, required=True)
    r.add_argument("--pack", type=Path)
    a = ap.parse_args()
    if a.cmd == "sample":
        print(sample(a.domains, a.n, a.seed, a.out))
    else:
        report(a.tld, a.country, a.findings, a.stats, a.seconds, a.pack)


if __name__ == "__main__":
    main()
