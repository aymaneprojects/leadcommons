"""The hosted demo bundles the small all-countries demo file of the newest pack release."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vercel_build", ROOT / "scripts" / "vercel_build.py")
vercel_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vercel_build)  # type: ignore[union-attr]

REPO = "acme/packs"
API = f"https://api.github.com/repos/{REPO}/releases"
BASE = f"https://github.com/{REPO}/releases/download"


def _asset(tag: str, name: str) -> dict:
    return {"name": name, "browser_download_url": f"{BASE}/{tag}/{name}", "size": 10}


@respx.mock
def test_demo_file_of_the_newest_manifest_release() -> None:
    tag = "packs-2026-12"
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": tag,
                    "published_at": "2026-12-05T04:00:00Z",
                    "assets": [
                        _asset(tag, "manifest.json"),
                        _asset(tag, "companies-0-demo-20261205.parquet"),
                        _asset(tag, "companies-FR-20261205.parquet"),
                    ],
                },
                {
                    "tag_name": "packs-2026-11",
                    "published_at": "2026-11-14T15:00:00Z",
                    "assets": [_asset("packs-2026-11", "companies-20261114.parquet")],
                },
            ],
        )
    )
    respx.get(f"{BASE}/{tag}/manifest.json").mock(
        return_value=httpx.Response(
            200, json={"schema": 3, "files": {"demo": "companies-0-demo-20261205.parquet"}}
        )
    )
    assert vercel_build.demo_pack_url(REPO) == f"{BASE}/{tag}/companies-0-demo-20261205.parquet"


@respx.mock
def test_no_manifest_release_means_no_demo_file() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": "packs-2026-11",
                    "published_at": "2026-11-14T15:00:00Z",
                    "assets": [_asset("packs-2026-11", "companies-20261114.parquet")],
                }
            ],
        )
    )
    assert vercel_build.demo_pack_url(REPO) is None


@respx.mock
def test_a_manifest_naming_a_path_is_ignored() -> None:
    tag = "packs-2026-12"
    respx.get(API).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "tag_name": tag,
                    "published_at": "2026-12-05",
                    "assets": [_asset(tag, "manifest.json")],
                }
            ],
        )
    )
    respx.get(f"{BASE}/{tag}/manifest.json").mock(
        return_value=httpx.Response(200, json={"schema": 3, "files": {"demo": "../../etc/passwd"}})
    )
    assert vercel_build.demo_pack_url(REPO) is None
