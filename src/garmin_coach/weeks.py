"""Monday-anchored week arithmetic.

A leaf: pure calendar math, no DB and no domain knowledge. It exists so the pure
signal seam (``signals.py``) can count weeks without importing a mart builder -
importing ``periodize`` there would drag the persistence layer into a module whose
whole contract is "rows in, findings out".
"""

from __future__ import annotations

import datetime as _dt


def monday(day: str) -> _dt.date:
    """Return the Monday of the week containing ``day`` (YYYY-MM-DD)."""
    date = _dt.date.fromisoformat(day)
    return date - _dt.timedelta(days=date.weekday())


def weeks_between(day: str, other: str) -> int:
    """Return whole weeks from ``day``'s Monday to ``other``'s Monday.

    Snapping both ends to their Monday first makes the count a whole number of
    calendar weeks, and 0 anywhere inside the same week.
    """
    return (monday(other) - monday(day)).days // 7
