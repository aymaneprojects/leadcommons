"""The local record of downloaded countries (packs/installed.py)."""

from __future__ import annotations

import json
from pathlib import Path

from pepite.packs import installed
from pepite.packs.installed import INSTALLED_FILE, InstalledCountry


def _entry(cc: str, file: str, release: str = "packs-2026-12") -> InstalledCountry:
    return InstalledCountry(country=cc, file=file, release=release, sha256="a" * 64, rows=10)


def test_record_load_forget(tmp_path: Path) -> None:
    (tmp_path / "companies-FR-1.parquet").write_bytes(b"x")
    (tmp_path / "companies-GB-1.parquet").write_bytes(b"x")
    installed.record(tmp_path, _entry("fr", "companies-FR-1.parquet"), release="packs-2026-12")
    installed.record(tmp_path, _entry("GB", "companies-GB-1.parquet"))
    got = installed.load(tmp_path)
    assert list(got) == ["FR", "GB"] and got["FR"].rows == 10 and got["FR"].installed_at
    assert installed.release(tmp_path) == "packs-2026-12"
    assert installed.files(tmp_path) == {"companies-FR-1.parquet", "companies-GB-1.parquet"}
    # a newer file of the same country replaces the entry
    (tmp_path / "companies-FR-2.parquet").write_bytes(b"x")
    installed.record(tmp_path, _entry("FR", "companies-FR-2.parquet", "packs-2027-01"))
    assert installed.load(tmp_path)["FR"].file == "companies-FR-2.parquet"
    assert installed.forget(tmp_path, "fr").release == "packs-2027-01"
    assert list(installed.load(tmp_path)) == ["GB"]
    assert installed.forget(tmp_path, "FR") is None


def test_entries_of_deleted_files_are_not_installed(tmp_path: Path) -> None:
    installed.record(tmp_path, _entry("FR", "companies-FR-1.parquet"))  # file never existed
    assert installed.load(tmp_path) == {}
    assert list(installed.load(tmp_path, existing_only=False)) == ["FR"]


def test_broken_or_hostile_record_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / INSTALLED_FILE
    path.write_text("not json")
    assert installed.load(tmp_path) == {}
    outside = tmp_path / "keep.parquet"
    outside.write_bytes(b"x")
    data = tmp_path / "packs"
    data.mkdir()
    (data / INSTALLED_FILE).write_text(
        json.dumps(
            {
                "countries": {
                    "FR": {"country": "FR", "file": "../keep.parquet", "release": "r"},
                    "XX": {"nope": 1},
                    "YY": "junk",
                }
            }
        )
    )
    got = installed.load(data, existing_only=False)
    assert list(got) == ["FR"] and got["FR"].file == "keep.parquet"  # never a path outside
    assert installed.load(data) == {}  # data/keep.parquet does not exist


def test_record_is_written_atomically_and_keeps_other_keys(tmp_path: Path) -> None:
    (tmp_path / INSTALLED_FILE).write_text(json.dumps({"repo": "acme/packs", "countries": {}}))
    installed.record(tmp_path, _entry("FR", "f.parquet"))
    data = json.loads((tmp_path / INSTALLED_FILE).read_text())
    assert data["repo"] == "acme/packs" and data["format"] == 1 and "FR" in data["countries"]
    assert not list(tmp_path.glob("*.part"))
