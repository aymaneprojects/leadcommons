"""Findings -> bulk shard: exact columns, ambiguous group sites dropped, FR siren filled."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from leadcommons.search import PACK_COLUMNS
from leadcommons.sitescan.fetch import _write_batch
from leadcommons.sitescan.shard import build_shard


def row(
    domain: str,
    ids: list[tuple[str, str]],
    *,
    status: str = "ok",
    phones: list[str] | None = None,
    home: str | None = None,
    scanned: str = "2026-11-10T08:00:00+00:00",
) -> tuple:
    return (
        domain,
        None,
        status,
        None,
        "Some title",
        home or f"https://{domain}/",
        f"https://{domain}/mentions-legales" if ids else None,
        json.dumps([{"registry": r, "number": n, "raw": n, "kind": "x"} for r, n in ids]),
        json.dumps(phones or []),
        2,
        scanned,
    )


def shard_rows(out: Path) -> list[dict]:
    con = duckdb.connect()
    cur = con.execute(
        f"SELECT * FROM read_parquet('{out}/sitescan.parquet') ORDER BY domain, registry"
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def test_shard_columns_and_values(tmp_path: Path):
    findings = tmp_path / "findings"
    findings.mkdir()
    _write_batch(
        [
            row("acme.fr", [("FR-SIREN", "552081317")], phones=["+33140203040", "+33140203041"]),
            row("widgets.co.uk", [("GB-CH", "00445790")], home="http://www.widgets.co.uk/"),
            row("nokia.no", [("NO-BRREG", "923609016")]),
        ],
        findings / "findings-00001.parquet",
    )
    stats = build_shard(findings, tmp_path / "shards")
    con = duckdb.connect()
    cols = [
        d[0]
        for d in con.execute(
            f"SELECT * FROM read_parquet('{tmp_path}/shards/sitescan.parquet')"
        ).description
    ]
    assert tuple(cols) == PACK_COLUMNS
    rows = {r["domain"]: r for r in shard_rows(tmp_path / "shards")}
    assert stats["rows"] == 3 and stats["domains_emitted"] == 3
    fr = rows["acme.fr"]
    assert (fr["registry"], fr["company_number"], fr["siren"], fr["country"]) == (
        "FR-SIREN",
        "552081317",
        "552081317",
        "FR",
    )
    assert fr["website"] == "https://acme.fr/"
    assert fr["phone"] == "+33140203040"  # the first switchboard number
    assert fr["name"] is None and fr["id"]
    prov = json.loads(fr["sources_json"])
    assert prov[0]["source"] == "sitescan"
    assert prov[0]["licence"] == "n/a (public web pages, facts only)"
    assert prov[0]["url"] == "https://acme.fr/mentions-legales"
    assert prov[0]["fetched_at"] == "2026-11-10T08:00:00+00:00"
    gb = rows["widgets.co.uk"]
    assert (gb["country"], gb["siren"], gb["website"]) == ("GB", None, "http://www.widgets.co.uk/")
    assert rows["nokia.no"]["country"] == "NO"


def test_ambiguous_group_sites_are_dropped(tmp_path: Path):
    findings = tmp_path / "f"
    findings.mkdir()
    _write_batch(
        [
            # A group site listing two different French companies: emitted for neither.
            row("group.fr", [("FR-SIREN", "552081317"), ("FR-SIREN", "732829320")]),
            # SIREN and SIRET of the same company are one company.
            row("same.fr", [("FR-SIREN", "552081317"), ("FR-SIREN", "55208131700018")]),
            # Ambiguous in France but one clear UK company: only the UK row stays.
            row(
                "mixed.com",
                [("FR-SIREN", "552081317"), ("FR-SIREN", "732829320"), ("GB-CH", "00445790")],
            ),
        ],
        findings / "findings-00001.parquet",
    )
    stats = build_shard(findings, tmp_path / "out")
    rows = shard_rows(tmp_path / "out")
    assert [(r["domain"], r["registry"], r["company_number"]) for r in rows] == [
        ("mixed.com", "GB-CH", "00445790"),
        ("same.fr", "FR-SIREN", "552081317"),
    ]
    assert stats["domains_ambiguous"] == 2  # group.fr and mixed.com (in FR)
    assert stats["domains_with_ids"] == 3


def test_only_ok_findings_and_pack_registers_and_latest_scan(tmp_path: Path):
    findings = tmp_path / "f"
    findings.mkdir()
    _write_batch(
        [
            row("robots.fr", [("FR-SIREN", "552081317")], status="robots"),
            row("empty.fr", []),
            row("kvk.nl", [("NL-KVK", "33012286")]),  # not a register the packs use
            row("moved.fr", [("FR-SIREN", "552081317")], scanned="2026-10-01T00:00:00+00:00"),
        ],
        findings / "findings-00001.parquet",
    )
    _write_batch(
        [row("moved.fr", [("FR-SIREN", "732829320")], scanned="2026-11-01T00:00:00+00:00")],
        findings / "findings-00002.parquet",
    )
    stats = build_shard(findings, tmp_path / "out")
    rows = shard_rows(tmp_path / "out")
    assert [(r["domain"], r["company_number"]) for r in rows] == [("moved.fr", "732829320")]
    assert stats["domains_with_other_registers"] == 1
    assert stats["by_registry"] == {"FR-SIREN": 1}


def test_shard_has_no_person_columns_and_merges_by_register_key(tmp_path: Path):
    findings = tmp_path / "f"
    findings.mkdir()
    _write_batch([row("acme.fr", [("FR-SIREN", "552081317")])], findings / "findings-00001.parquet")
    build_shard(findings / "findings-00001.parquet", tmp_path / "out")  # a single file works too
    (r,) = shard_rows(tmp_path / "out")
    # the stable id is the same as for the register row of that SIREN
    from leadcommons.packs.bulk import stable_company_id

    assert r["id"] == stable_company_id("FR-SIREN", "552081317")

    # end to end: the real shard attaches its website and phone to the register company
    from leadcommons.packs.merge import merge_shards
    from tests.test_pack_merge import BUILT, fr, rows, write_shard

    write_shard(tmp_path / "out", "sirene_stock", [fr("552081317", "ACME SAS")])
    merge_shards(tmp_path / "out", tmp_path / "packs", built_at=BUILT)
    (company,) = rows(tmp_path / "packs", "FR").values()
    assert company["name"] == "ACME SAS" and company["domain"] == "acme.fr"
    assert company["website"] == "https://acme.fr/"


def test_cli_run_part_and_shard_read_part_directories(tmp_path: Path, monkeypatch):
    from typer.testing import CliRunner

    from leadcommons import cli
    from leadcommons.sitescan import fetch

    domains = tmp_path / "d.txt"
    domains.write_text("\n".join(f"site{i}.fr" for i in range(8)) + "\n")
    seen: list[list[str]] = []

    def fake_run(feed, out, **kw):
        seen.append([d for d, _ in feed])
        out.mkdir(parents=True, exist_ok=True)
        _write_batch(
            [row(d, [("FR-SIREN", "552081317")]) for d, _ in [(seen[-1][0], 0)]],
            out / "findings-00001.parquet",
        )
        return {"domains": len(seen[-1])}

    monkeypatch.setattr(fetch, "run_scan", fake_run)
    runner = CliRunner()
    for k in (0, 1):
        res = runner.invoke(
            cli.app,
            [
                "sitescan",
                "run",
                "--domains",
                str(domains),
                "--out",
                str(tmp_path / "f"),
                "--part",
                f"{k}/2",
            ],
        )
        assert res.exit_code == 0, res.output
    assert sorted(seen[0] + seen[1]) == sorted(f"site{i}.fr" for i in range(8))
    assert not set(seen[0]) & set(seen[1])
    res = runner.invoke(
        cli.app, ["sitescan", "shard", str(tmp_path / "f"), "--out", str(tmp_path / "o")]
    )
    assert res.exit_code == 0, res.output
    assert len(shard_rows(tmp_path / "o")) == 2
