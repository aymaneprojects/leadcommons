"""Regression: the drawer scrim must not cover the drawer panel (it swallowed button clicks)."""

import re

from leadcommons.web.deps import STATIC_DIR

CSS = (STATIC_DIR / "css" / "app.css").read_text()


def _rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"{selector} rule missing"
    return match.group(1)


def test_scrim_stops_where_the_panel_starts() -> None:
    panel_width = re.search(r"width:\s*(min\([^)]*\))", _rule("#drawer"))
    assert panel_width, "#drawer must declare its width"
    scrim = _rule(".drawer-scrim")
    assert f"inset: 0 {panel_width.group(1)} 0 0" in scrim
