"""The local record of the country packs downloaded from the published releases.

`<data_dir>/.installed-packs.json` says, per country, which file was downloaded, from which
release, with which checksum. It is how the app knows what "update" and "remove" may touch: a
file is only ever replaced or deleted when this record (or the older list of single-file
downloads, packs.DOWNLOADS_FILE) says the app downloaded it. Packs built by the user are never
in it.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from leadcommons.models import utcnow

INSTALLED_FILE = ".installed-packs.json"
FORMAT = 1


@dataclass
class InstalledCountry:
    country: str  # ISO alpha-2
    file: str  # file name inside the data directory
    release: str  # e.g. "packs-2026-12"
    sha256: str
    rows: int = 0
    bytes: int = 0
    built_at: str | None = None  # when the release was built (manifest)
    sources: list[str] = field(default_factory=list)
    installed_at: str = field(default_factory=lambda: utcnow().isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstalledCountry | None:
        try:
            return cls(
                country=str(data["country"]).upper(),
                file=Path(str(data["file"])).name,  # never leave the data directory
                release=str(data.get("release") or ""),
                sha256=str(data.get("sha256") or ""),
                rows=int(data.get("rows") or 0),
                bytes=int(data.get("bytes") or 0),
                built_at=data.get("built_at"),
                sources=[str(s) for s in data.get("sources") or []],
                installed_at=str(data.get("installed_at") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _path(data_dir: Path) -> Path:
    return Path(data_dir) / INSTALLED_FILE


def _read(data_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(data_dir: Path, *, existing_only: bool = True) -> dict[str, InstalledCountry]:
    """country -> what was installed. With existing_only, entries whose file is gone (deleted
    by hand) are left out."""
    out: dict[str, InstalledCountry] = {}
    raw = _read(data_dir).get("countries")
    for value in (raw or {}).values() if isinstance(raw, dict) else []:
        entry = InstalledCountry.from_dict(value) if isinstance(value, dict) else None
        if entry is None:
            continue
        if existing_only and not (Path(data_dir) / entry.file).is_file():
            continue
        out[entry.country] = entry
    return dict(sorted(out.items()))


def _write(data_dir: Path, countries: dict[str, InstalledCountry], **extra: Any) -> None:
    data = _read(data_dir)
    data.update(extra)
    data["format"] = FORMAT
    data["countries"] = {cc: asdict(e) for cc, e in sorted(countries.items())}
    data["updated_at"] = utcnow().isoformat(timespec="seconds")
    path = _path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record(data_dir: Path, entry: InstalledCountry, release: str | None = None) -> None:
    countries = load(data_dir, existing_only=False)
    countries[entry.country] = entry
    extra = {"release": release} if release else {}
    _write(data_dir, countries, **extra)


def forget(data_dir: Path, country: str) -> InstalledCountry | None:
    countries = load(data_dir, existing_only=False)
    entry = countries.pop(country.upper(), None)
    if entry is not None:
        _write(data_dir, countries)
    return entry


def release(data_dir: Path) -> str | None:
    """The release the newest download came from."""
    value = _read(data_dir).get("release")
    return str(value) if value else None


def files(data_dir: Path) -> set[str]:
    """Names of every file this record owns (existing or not)."""
    return {e.file for e in load(data_dir, existing_only=False).values()}


def clear(data_dir: Path) -> None:
    with contextlib.suppress(OSError):
        _path(data_dir).unlink()


__all__ = ["INSTALLED_FILE", "InstalledCountry", "forget", "load", "record", "release"]
