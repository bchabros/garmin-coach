"""Periodization: the block calendar counted back from the athlete's goal race.

Pure functions over goal events and a date - no DB, no wall clock, no training
history. Phase 9 ticket 01 lands the anchor selection; the block calendar itself
(`periodize`) and the `plan_block` mart follow in ticket 02.

The anchor is the single event everything counts back from: the nearest *upcoming*
`confirmed` event of priority A. A `tentative` event never anchors - the system does
not taper for a race the athlete may skip - and neither does a B/C event, however
near. With no anchor there is no plan, stated as None rather than guessed at.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any


def _monday(day: str) -> _dt.date:
    date = _dt.date.fromisoformat(day)
    return date - _dt.timedelta(days=date.weekday())


def anchor_event(events: list[dict[str, Any]], today: str) -> dict[str, Any] | None:
    """Return the goal event the block calendar counts back from, if there is one.

    Args:
        events: Goal-event rows (as stored in ``goal_event``).
        today: The as-of date (YYYY-MM-DD); an event on this day still anchors.

    Returns:
        The nearest upcoming ``confirmed`` priority-A event, or None when no event
        qualifies (no events, only tentative ones, or only races already run).
    """
    candidates = [
        event
        for event in events
        if event["priority"] == "A"
        and event["status"] == "confirmed"
        and event["date"] >= today
    ]
    return min(candidates, key=lambda event: event["date"], default=None)


def weeks_to_event(day: str, event_date: str) -> int:
    """Return whole weeks from ``day``'s Monday to the event's race week.

    Both dates are snapped to their Monday first, so the count is a whole number of
    calendar weeks and is 0 anywhere inside the race week itself.
    """
    return (_monday(event_date) - _monday(day)).days // 7
