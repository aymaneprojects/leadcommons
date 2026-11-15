"""Per-country downloads from a release with manifest.json (schema 3): resume, sha256, the
installed record, replacing earlier downloads of the same country only, removing a country."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from leadcommons import packs
from leadcommons.packs import (
    DOWNLOADS_FILE,
    country_from_locale,
    default_country,
    download_file,
    install_countries,
    install_latest_pack,
    pack_catalog,
    parse_manifest,
    remove_country,
    run_pack_job,
    update_countries,
)
from leadcommons.packs import index as pack_index
from leadcommons.packs import installed as pack_installed
from leadcommons.packs.builder import write_parquet
from leadcommons.search import PACK_COLUMNS, CompanyFilters

REPO = "acme/packs"
API = f"https://api.github.com/repos/{REPO}/releases"


def _rows(cc: str, n: int, tag: str = "") -> list[dict]:
    out = []
    for i in range(n):
        r = dict.fromkeys(PACK_COLUMNS)
        r.update(id=f"co_{cc.lower()}{tag}{i:03d}", name=f"{cc} Company {tag}{i}", country=cc,
                 company_number=f"{cc}{i:06d}", sources_json="[]")  # fmt: skip
        out.append(r)
    return out


def _parquet(tmp_path: Path, name: str, rows: list[dict]) -> bytes:
    path = tmp_path / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(path, rows, {"built_at": "2026-12-01", "sources": "sirene"})
    return path.read_bytes()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeRelease:
    """A packs-* release on a fake GitHub: the API listing, manifest.json and the files."""

    def __init__(self, tag: str, files: dict[str, tuple[str, bytes]], published: str = "") -> None:
        self.tag, self.files, self.published = tag, files, published or tag
        self.base = f"https://github.com/{REPO}/releases/download/{tag}/"

    def manifest(self, **over) -> dict:
        countries = [
            {"country": cc, "file": name, "rows": 3, "bytes": len(data), "sha256": _sha(data),
             "sources": ["sirene"], "with_domain": 0}  # fmt: skip
            for name, (cc, data) in self.files.items()
        ]
        return {"schema": 3, "release": self.tag, "built_at": "2026-12-01T03:40:00+00:00",
                "countries": countries, **over}  # fmt: skip

    def api(self) -> dict:
        names = [*self.files, "manifest.json"]
        return {
            "tag_name": self.tag,
            "published_at": self.published,
            "assets": [{"name": n, "browser_download_url": self.base + n} for n in names],
        }

    def mock(self, manifest: dict | None = None) -> None:
        respx.get(self.base + "manifest.json").mock(
            return_value=httpx.Response(200, json=manifest or self.manifest())
        )
        for name, (_cc, data) in self.files.items():
            respx.get(self.base + name).mock(side_effect=_ranged(data))


def _ranged(data: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        rng = request.headers.get("range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            return httpx.Response(
                206,
                content=data[start:],
                headers={"content-range": f"bytes {start}-{len(data) - 1}/{len(data)}"},
            )
        return httpx.Response(200, content=data)

    return handler


def _github(*releases: FakeRelease) -> None:
    respx.get(API).mock(return_value=httpx.Response(200, json=[r.api() for r in releases]))
    for r in releases:
        r.mock()


@pytest.fixture()
def data(tmp_path: Path) -> Path:
    d = tmp_path / "packs"
    d.mkdir()
    return d


@pytest.fixture()
def dec(tmp_path: Path) -> FakeRelease:
    return FakeRelease(
        "packs-2026-12",
        {
            "companies-FR-20261201.parquet": ("FR", _parquet(tmp_path, "fr1", _rows("FR", 3))),
            "companies-GB-20261201.parquet": ("GB", _parquet(tmp_path, "gb1", _rows("GB", 2))),
        },
    )


@respx.mock
def test_install_country_downloads_checks_indexes_and_records(data: Path, dec) -> None:
    _github(dec)
    done = install_countries(data, ["fr"], REPO)
    assert [e.country for e in done] == ["FR"]
    f = data / "companies-FR-20261201.parquet"
    assert f.read_bytes() == dec.files[f.name][1]
    assert not (data / "companies-GB-20261201.parquet").exists()  # only what was asked
    assert pack_index.has_fresh_index(f, data)
    rec = pack_installed.load(data)["FR"]
    assert rec.file == f.name and rec.release == "packs-2026-12" and rec.sha256 == _sha(
        f.read_bytes()
    )
    search = packs.DuckDBCompanySearch(data)
    assert [s.kind for s in search._sources] == ["index"]
    assert search.search_companies(CompanyFilters()).total == 3
    cat = pack_catalog(data, REPO, offline=True)
    assert {c.country: c.state for c in cat.countries} == {"FR": "installed", "GB": "available"}


@respx.mock
def test_update_replaces_only_the_same_country(tmp_path: Path, data: Path, dec) -> None:
    _github(dec)
    install_countries(data, ["FR", "GB"], REPO)
    own = data / "companies-20260101.parquet"  # built by the user: never touched
    own.write_bytes(_parquet(tmp_path, "own", _rows("FR", 1, "own")))
    jan = FakeRelease(
        "packs-2027-01",
        {
            "companies-FR-20270101.parquet": ("FR", _parquet(tmp_path, "fr2", _rows("FR", 4, "n"))),
            "companies-GB-20261201.parquet": dec.files["companies-GB-20261201.parquet"],
        },
    )
    _github(jan, dec)
    cat = pack_catalog(data, REPO, refresh=True)
    assert {c.country: c.state for c in cat.countries} == {"FR": "update", "GB": "installed"}
    done = update_countries(data, REPO)
    assert [e.country for e in done] == ["FR"]
    assert not (data / "companies-FR-20261201.parquet").exists()
    assert (data / "companies-FR-20270101.parquet").exists()
    assert (data / "companies-GB-20261201.parquet").exists() and own.exists()
    assert pack_installed.load(data)["FR"].release == "packs-2027-01"
    left = sorted(p.name for p in pack_index.index_dir(data).glob("*.duckdb"))
    assert len(left) == 2 and not any("FR-20261201" in n for n in left)  # old index gone
    assert packs.DuckDBCompanySearch(data).search_companies(CompanyFilters()).total == 4 + 2 + 1


@respx.mock
def test_checksum_mismatch_keeps_nothing(data: Path, dec) -> None:
    respx.get(API).mock(return_value=httpx.Response(200, json=[dec.api()]))
    bad = dec.manifest()
    bad["countries"][0]["sha256"] = "0" * 64
    dec.mock(manifest=bad)
    with pytest.raises(ValueError, match="checksum"):
        install_countries(data, ["FR"], REPO)
    assert not list(data.glob("companies-FR*")) and pack_installed.load(data) == {}


@respx.mock
def test_download_resumes_a_partial_file(tmp_path: Path) -> None:
    payload = _parquet(tmp_path, "big", _rows("FR", 50))
    url = "https://x.test/companies-FR-1.parquet"
    route = respx.get(url).mock(side_effect=_ranged(payload))
    dest = tmp_path / "out" / "companies-FR-1.parquet"
    dest.parent.mkdir()
    Path(f"{dest}.part").write_bytes(payload[:100])  # an interrupted earlier download
    seen: list[tuple[int, int]] = []
    download_file(url, dest, size=len(payload), sha256=_sha(payload),
                  progress=lambda d, t: seen.append((d, t)))  # fmt: skip
    assert dest.read_bytes() == payload and not Path(f"{dest}.part").exists()
    assert route.calls[0].request.headers["range"] == "bytes=100-"
    assert seen[-1] == (len(payload), len(payload))


@respx.mock
def test_download_restarts_when_the_server_ignores_range(tmp_path: Path) -> None:
    payload = _parquet(tmp_path, "big", _rows("FR", 20))
    url = "https://x.test/companies-FR-2.parquet"
    respx.get(url).mock(return_value=httpx.Response(200, content=payload))
    dest = tmp_path / "companies-FR-2.parquet"
    Path(f"{dest}.part").write_bytes(b"garbage!" * 10)
    download_file(url, dest, size=len(payload), sha256=_sha(payload))
    assert dest.read_bytes() == payload


@respx.mock
def test_download_retries_after_a_dropped_connection(tmp_path: Path) -> None:
    payload = _parquet(tmp_path, "big", _rows("FR", 20))
    url = "https://x.test/companies-FR-3.parquet"
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadError("connection reset")
        return _ranged(payload)(request)

    respx.get(url).mock(side_effect=flaky)
    dest = tmp_path / "companies-FR-3.parquet"
    packs.time.sleep = lambda s: None  # type: ignore[attr-defined]  # no real waiting
    try:
        download_file(url, dest, size=len(payload), sha256=_sha(payload))
    finally:
        import time as _time

        packs.time.sleep = _time.sleep  # type: ignore[attr-defined]
    assert dest.read_bytes() == payload and calls["n"] == 2


@respx.mock
def test_country_file_replaces_that_country_in_an_old_single_file_download(
    tmp_path: Path, data: Path, dec
) -> None:
    old = data / "companies-20261101.parquet"  # downloaded from an older single-file release
    old.write_bytes(_parquet(tmp_path, "old", _rows("FR", 2, "o") + _rows("DE", 2, "o")))
    (data / DOWNLOADS_FILE).write_text(json.dumps([old.name]))
    _github(dec)
    install_countries(data, ["FR"], REPO)
    con = duckdb.connect()
    left = con.execute(f"SELECT DISTINCT country FROM '{old}'").fetchall()
    assert left == [("DE",)]  # its France rows are gone, Germany stays
    meta = dict(con.execute(f"SELECT key::VARCHAR, value::VARCHAR FROM parquet_kv_metadata('{old}')").fetchall())  # fmt: skip
    assert meta.get("sources") == "sirene"
    old2 = data / "companies-20261001.parquet"
    old2.write_bytes(_parquet(tmp_path, "old2", _rows("GB", 2, "x")))
    (data / DOWNLOADS_FILE).write_text(json.dumps([old.name, old2.name]))
    install_countries(data, ["GB"], REPO)
    assert not old2.exists() and old.exists()  # nothing left in it: deleted
    assert json.loads((data / DOWNLOADS_FILE).read_text()) == [old.name]


@respx.mock
def test_remove_country_only_touches_downloads(tmp_path: Path, data: Path, dec) -> None:
    _github(dec)
    install_countries(data, ["FR"], REPO)
    own = data / "companies-mine.parquet"
    own.write_bytes(_parquet(tmp_path, "mine", _rows("FR", 1, "m")))
    assert remove_country(data, "gb") is False  # never downloaded
    assert remove_country(data, "FR") is True
    assert not (data / "companies-FR-20261201.parquet").exists() and own.exists()
    assert pack_installed.load(data) == {}
    assert not list(pack_index.index_dir(data).glob("companies-FR-*"))


@respx.mock
def test_legacy_release_still_downloads_one_file(data: Path, tmp_path: Path) -> None:
    payload = _parquet(tmp_path, "legacy", _rows("FR", 2))
    url = f"https://github.com/{REPO}/releases/download/packs-2026-10/companies-20261001.parquet"
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=[{"tag_name": "packs-2026-10", "published_at": "2026-10-01",
                   "assets": [{"name": "companies-20261001.parquet",
                               "browser_download_url": url}]}],
        )  # fmt: skip
    )
    respx.get(url).mock(return_value=httpx.Response(200, content=payload))
    path = install_latest_pack(data, REPO)
    assert path.name == "companies-20261001.parquet"
    assert json.loads((data / DOWNLOADS_FILE).read_text()) == [path.name]
    assert pack_index.has_fresh_index(path, data)
    cat = pack_catalog(data, REPO, offline=True)
    assert cat.legacy and not cat.countries


@respx.mock
def test_latest_installs_your_own_country_first(data: Path, dec, monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_LOCALE", "en-GB")
    _github(dec)
    path = install_latest_pack(data, REPO)
    assert path.name == "companies-GB-20261201.parquet"
    assert list(pack_installed.load(data)) == ["GB"]


@respx.mock
def test_first_run_with_an_unpublished_country_is_not_an_error(data: Path, dec) -> None:
    _github(dec)
    res = run_pack_job(data, {"countries": ["JP"], "first_run": True}, repo=REPO)
    assert "No company data is published for JP" in res["message"]
    assert not list(data.glob("*.parquet"))
    res = run_pack_job(data, {"countries": ["FR"], "first_run": True}, repo=REPO)
    assert res["countries"] == ["FR"] and "Add more countries" in res["message"]


@respx.mock
def test_job_reports_progress_and_removes(data: Path, dec) -> None:
    _github(dec)
    seen: list[str] = []
    res = run_pack_job(data, {"action": "install", "countries": ["GB"]},
                       lambda phase, d, t: seen.append(phase), repo=REPO)  # fmt: skip
    assert res["countries"] == ["GB"]
    assert any(p.startswith("Downloading GB") for p in seen)
    assert any("fast search" in p for p in seen)
    res = run_pack_job(data, {"action": "remove", "countries": ["GB"]}, repo=REPO)
    assert res["removed"] == ["GB"]
    with pytest.raises(ValueError, match="No company data for XX"):
        run_pack_job(data, {"countries": ["XX"]}, repo=REPO)


def test_catalog_offline_and_unreachable(data: Path, dec) -> None:
    cat = pack_catalog(data, REPO, offline=True)
    assert cat.countries == [] and cat.error
    with respx.mock:
        _github(dec)
        assert len(pack_catalog(data, REPO).countries) == 2
    with respx.mock:
        respx.get(API).mock(side_effect=httpx.ConnectError("offline"))
        cat = pack_catalog(data, REPO, refresh=True)
    assert len(cat.countries) == 2 and "Could not reach GitHub" in (cat.error or "")
    assert len(pack_catalog(data, REPO).countries) == 2  # recent saved list: no network needed


def test_parse_manifest_keeps_only_safe_entries() -> None:
    good = {"country": "FR", "file": "companies-FR-1.parquet", "rows": 1, "bytes": 2,
            "sha256": "a" * 64}  # fmt: skip
    m = parse_manifest(
        {
            "schema": 3,
            "release": "packs-2026-12",
            "countries": [
                good,
                {**good, "country": "FR1"},
                {**good, "file": "../evil.parquet"},
                {**good, "file": "sub/x.parquet"},
                {**good, "file": ".hidden.parquet"},
                {**good, "file": "x.exe"},
                {**good, "sha256": "nope"},
                "junk",
            ],
        }
    )
    assert [c["file"] for c in m["countries"]] == ["companies-FR-1.parquet"]
    with pytest.raises(ValueError):
        parse_manifest({"schema": 2, "countries": []})


@pytest.mark.parametrize(
    "text, cc",
    [
        ("fr-FR", "FR"),
        ("en_GB.UTF-8", "GB"),
        ("de", "DE"),
        ("en", None),
        ("pt-BR", "BR"),
        ("FR", "FR"),
        ("C", None),
        ("C.UTF-8", None),
        ("POSIX", None),
        ("", None),
        ("sr_RS@latin", "RS"),
    ],
)
def test_country_from_locale(text: str, cc: str | None) -> None:
    assert country_from_locale(text) == cc


def test_default_country_prefers_the_desktop_locale(monkeypatch) -> None:
    monkeypatch.setenv("LEADCOMMONS_LOCALE", "nb-NO")
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert default_country() == "NO"
    monkeypatch.delenv("LEADCOMMONS_LOCALE")
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    assert default_country() == "US"
