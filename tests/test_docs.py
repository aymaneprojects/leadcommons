"""Cheap guard so the docs do not drift from the code: commands, settings and links."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import typer.main

from pepite.cli import app

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted([ROOT / "README.md", *ROOT.glob("docs/**/*.md")])
TEXTS = [*DOCS, ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md", ROOT / "deploy" / ".env.example"]

# Names the docs mention that are not read by src/ on purpose.
NOT_IN_SRC = {
    "PEPITE_REPO": "read by scripts/install.sh and install.ps1",
    "PEPITE_SKIP_PACK": "read by scripts/install.sh and install.ps1",
    "PEPITE_VERSION": "read by scripts/install.sh and install.ps1",
    "PEPITE_BACKEND": "read by the Electron shell (desktop/electron/main.js)",
}


def _code(text: str) -> list[str]:
    """The code in a Markdown file: fenced blocks and `inline` spans."""
    fenced = re.findall(r"```[^\n]*\n(.*?)```", text, re.S)
    inline = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    return fenced + inline


def _commands() -> list[tuple[str, str]]:
    found = []
    for path in TEXTS:
        for code in _code(path.read_text(encoding="utf-8")):
            code = code.replace("pepite pepite ", "pepite ")  # docker compose exec pepite pepite
            for m in re.finditer(r"(?<![\w./~-])pepite((?: +[a-z][a-z-]*)*)", code):
                if m.group(1).split()[:1] != ["pepite"]:  # pg_dump -U pepite pepite
                    found.append((path.name, m.group(1)))
    return found


def _exists(words: str) -> bool:
    cmd = typer.main.get_command(app)
    for word in words.split():
        if not hasattr(cmd, "commands"):
            return True  # a command without sub-commands: the rest are arguments
        if word not in cmd.commands:
            return False
        cmd = cmd.commands[word]
    return True


@pytest.mark.parametrize(("name", "words"), sorted(set(_commands())))
def test_documented_command_exists(name: str, words: str) -> None:
    assert _exists(words), f"{name} documents `pepite{words}`, which is not a command"


def test_documented_settings_are_read_by_the_code() -> None:
    in_src = {
        v
        for p in (ROOT / "src").rglob("*.py")
        for v in re.findall(r"PEPITE_[A-Z0-9_]*[A-Z0-9]", p.read_text(encoding="utf-8"))
    }
    for path in TEXTS:
        for var in re.findall(r"PEPITE_[A-Z0-9_]*[A-Z0-9]", path.read_text(encoding="utf-8")):
            assert var in in_src or var in NOT_IN_SRC, f"{path.name} mentions unknown {var}"


def test_relative_links_exist() -> None:
    link = re.compile(r"""(?:\]\(|(?:src|href)=")([^)"\s]+)""")
    for path in DOCS:
        for target in link.findall(path.read_text(encoding="utf-8")):
            if re.match(r"(?:[a-z]+:|#)", target):
                continue
            dest = (path.parent / target.split("#")[0].split("?")[0]).resolve()
            assert dest.exists(), f"{path.relative_to(ROOT)} links to missing {target}"
