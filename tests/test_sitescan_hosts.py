"""Candidate domains: TLD map, packs reader, Common Crawl index parsing (no network)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import duckdb
import pytest

from pepite.sitescan import hosts

PATHS_TXT = """\
cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33/subset=crawldiagnostics/part-00000-a.c000.gz.parquet
cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33/subset=warc/part-00001-b.c000.gz.parquet
cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33/subset=robotstxt/part-00000-c.c000.gz.parquet
cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33/subset=warc/part-00000-b.c000.gz.parquet
cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33/subset=warc/_SUCCESS
"""

COLLINFO = json.dumps(
    [
        {"id": "CC-MAIN-2026-33", "name": "August 2026 Index"},
        {"id": "CC-MAIN-2026-26", "name": "June 2026 Index"},
        {"id": "CC-NEWS-2026-08", "name": "not a main crawl"},
    ]
)


def test_tld_map_and_country_for_domain():
    for tld, cc in {
        "fr": "FR",
        "uk": "GB",
        "co.uk": "GB",
        "de": "DE",
        "no": "NO",
        "au": "AU",
        "com.au": "AU",
        "ca": "CA",
        "nl": "NL",
        "be": "BE",
        "dk": "DK",
        "ch": "CH",
        "it": "IT",
        "es": "ES",
    }.items():
        assert hosts.TLD_COUNTRY[tld] == cc
    assert hosts.country_for_domain("acme.fr") == "FR"
    assert hosts.country_for_domain("acme.co.uk") == "GB"
    assert hosts.country_for_domain("acme.org.uk") == "GB"
    assert hosts.country_for_domain("acme.com.au") == "AU"
    assert hosts.country_for_domain("acme.com") is None
    assert set(hosts.DEFAULT_TLDS) <= set(hosts.TLD_COUNTRY)


def test_parse_collinfo_picks_newest_main_crawl():
    assert hosts.parse_collinfo(COLLINFO) == "CC-MAIN-2026-33"
    with pytest.raises(ValueError):
        hosts.parse_collinfo("[]")


def test_parse_index_paths_keeps_warc_parquet_files_only():
    urls = hosts.parse_index_paths(PATHS_TXT)
    assert urls == [
        "https://data.commoncrawl.org/cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-33"
        f"/subset=warc/part-0000{n}-b.c000.gz.parquet"
        for n in (0, 1)
    ]


def _write(con, sql: str, path: Path) -> None:
    con.execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")


def test_packs_domains_reads_domain_then_website(tmp_path: Path):
    con = duckdb.connect()
    _write(
        con,
        "SELECT * FROM (VALUES ('Acme', 'acme.fr', NULL), ('Beta', NULL, 'https://www.Beta.fr/contact'),"
        " ('Dup', 'acme.fr', NULL), ('None', NULL, NULL), ('Odd', NULL, 'not a host'))"
        " t(name, domain, website)",
        tmp_path / "companies-FR-20261101.parquet",
    )
    # An old pack without the website column, and a country we do not ask for.
    _write(
        con,
        "SELECT * FROM (VALUES ('Gamma', 'gamma.co.uk')) t(name, domain)",
        tmp_path / "companies-GB-20261101.parquet",
    )
    _write(
        con,
        "SELECT * FROM (VALUES ('Delta', 'delta.de')) t(name, domain)",
        tmp_path / "companies-DE-20261101.parquet",
    )
    got = set(hosts.packs_domains(tmp_path, countries=["fr", "GB"]))
    assert got == {("acme.fr", "FR"), ("beta.fr", "FR"), ("gamma.co.uk", "GB")}
    assert ("delta.de", "DE") in set(hosts.packs_domains(tmp_path))


def _index_file(con, path: Path, rows: list[tuple[str, str, int]]) -> None:
    values = ", ".join(f"('{h}', '{d}', {s}, 'ignored')" for h, d, s in rows)
    _write(
        con,
        f"SELECT * FROM (VALUES {values}) t(url_host_tld, url_host_registered_domain, "
        "fetch_status, url)",
        path,
    )


def test_extract_file_and_build_domains_with_partial_output(tmp_path: Path):
    con = duckdb.connect()
    f1, f2 = tmp_path / "i0.parquet", tmp_path / "i1.parquet"
    _index_file(con, f1, [("fr", "a.fr", 200), ("fr", "a.fr", 200), ("de", "x.de", 200)])
    _index_file(con, f2, [("fr", "b.fr", 200), ("fr", "dead.fr", 404), ("fr", "a.fr", 200)])
    missing = tmp_path / "missing.parquet"  # a file that keeps failing

    assert hosts.extract_file(con, str(f1), "fr", tmp_path / "p.parquet") == 1

    out = tmp_path / "out"
    stats = hosts.build_domains(
        "fr",
        out,
        crawl="CC-MAIN-2026-33",
        files=[str(f1), str(f2), str(missing)],
        workers=2,
        attempts=2,
        connect=duckdb.connect,
        sleep=lambda s: None,
    )
    assert stats["domains"] == 2 and stats["files_done"] == 2
    assert stats["partial"] is True and stats["failed"] == [str(missing)]
    with gzip.open(out / "domains-fr.txt.gz", "rt") as fh:
        assert fh.read().split() == ["a.fr", "b.fr"]
    meta = json.loads((out / "domains-fr.json").read_text())
    assert meta["crawl"] == "CC-MAIN-2026-33" and meta["partial"] is True

    # A rerun resumes: the finished parts are not read again.
    calls: list[str] = []

    def spy():
        calls.append("connect")
        return duckdb.connect()

    again = hosts.build_domains(
        "fr",
        out,
        crawl="CC-MAIN-2026-33",
        files=[str(f1), str(f2)],
        connect=spy,
        sleep=lambda s: None,
    )
    assert again["partial"] is False and calls == []
    assert again["domains"] == 2


def test_read_domains_file_infers_country(tmp_path: Path):
    path = tmp_path / "d.txt.gz"
    with gzip.open(path, "wt") as fh:
        fh.write("# comment\nAcme.fr\n\nfoo.co.uk\nbar.com\n")
    assert list(hosts.read_domains_file(path)) == [
        ("acme.fr", "FR"),
        ("foo.co.uk", "GB"),
        ("bar.com", None),
    ]
    assert list(hosts.read_domains_file(path, "NO"))[0] == ("acme.fr", "NO")
