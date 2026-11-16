"""Small normalisation helpers shared by connectors and the pack builder."""

from __future__ import annotations

from pepite.models import SIZE_RANGES

_UPPER_BOUNDS = (10, 50, 200, 500, 1000, 5000, 10000)


def size_bucket(employees: int | float | None) -> str | None:
    """Map an employee count to one of SIZE_RANGES (None when unknown or non-positive)."""
    if employees is None or employees < 1:
        return None
    for bound, label in zip(_UPPER_BOUNDS, SIZE_RANGES, strict=False):
        if employees <= bound:
            return label
    return SIZE_RANGES[-1]
