"""Guard: the interface keeps the reference layout but uses its own words.

docs/dev/ux-inventory.md quotes the visible text of each reference screenshot in single
quotes. No quoted phrase of four words or more may appear word for word in our templates,
Python or JavaScript. Short labels ("Save to list", "Job titles") are fine and are not checked.
Navigation names the founder chose to keep go in KEPT_LABELS.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "dev" / "ux-inventory.md"
SOURCES = ROOT / "src" / "pepite"

KEPT_LABELS = {"email setup and health"}  # sidebar item name, kept on purpose


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def quoted_phrases() -> set[str]:
    phrases = set()
    for m in re.finditer(r"'([^'\n]{8,160})'", INVENTORY.read_text(encoding="utf-8")):
        q = m.group(1).strip()
        if len(q.split()) >= 4 and not q.startswith(("http", "/")):
            phrases.add(_norm(q).replace("apollo", "pepite"))
    return phrases - KEPT_LABELS


def test_inventory_has_phrases_to_check() -> None:
    assert len(quoted_phrases()) > 30


def test_no_reference_phrase_is_copied_word_for_word() -> None:
    phrases = quoted_phrases()
    copied = []
    for path in SOURCES.rglob("*"):
        if path.suffix not in {".html", ".py", ".js"}:
            continue
        text = _norm(path.read_text(encoding="utf-8"))
        copied += [f"{path.relative_to(ROOT)}: {p!r}" for p in phrases if p in text]
    assert not copied, "Write these in our own words:\n" + "\n".join(sorted(copied))
