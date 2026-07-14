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
import sqlite3
from typing import Any

from . import db, thresholds as _thresholds

# Blocks that carry a planned deload. `peak` is short and `taper` is a downshift
# already, so neither gets one.
DELOAD_BLOCKS = ("base", "build")


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


def _block(weeks_out: int, taper: int, peak: int, build: int) -> str:
    """Label a week by its distance from the race: a countdown, not a calendar."""
    if weeks_out < taper:
        return "taper"
    if weeks_out < taper + peak:
        return "peak"
    if weeks_out < taper + peak + build:
        return "build"
    return "base"


def _mark_deloads(weeks: list[dict[str, Any]], cadence: int) -> None:
    """Flag the planned recovery weeks, in place.

    Every ``cadence`` weeks counted back from each block's *end*, so the athlete always
    enters the next block fresh. A block never opens with a deload: its first week is
    for ramping up, and at the left edge that week is bounded by ``data_start`` (an
    artifact of when data begins, not a training decision).
    """
    for week in weeks:
        week["is_deload"] = 0
    for name in DELOAD_BLOCKS:
        block_weeks = [week for week in weeks if week["block"] == name]
        for offset in range(0, len(block_weeks), cadence):
            index = len(block_weeks) - 1 - offset
            if index > 0:
                block_weeks[index]["is_deload"] = 1


def periodize(
    event: dict[str, Any], data_start: str, thresholds: dict[str, float]
) -> list[dict[str, Any]]:
    """Build the block calendar for one anchor event.

    Pure: a countdown from the race date, with no reference to the wall clock or to
    training history. ``taper`` / ``peak`` / ``build`` take fixed lengths from the
    thresholds and ``base`` absorbs everything earlier, so the athlete is always in
    some block however distant the race. The span is left-bounded by ``data_start``
    and runs out to the race week, future weeks included.

    Args:
        event: The anchor goal event (see `anchor_event`).
        data_start: First date with real data; the plan is not extended before it.
        thresholds: Effective coach thresholds (block lengths, deload cadence).

    Returns:
        One row per week, chronologically, ready for the ``plan_block`` mart.
    """
    taper = int(thresholds["taper_weeks"])
    peak = int(thresholds["peak_weeks"])
    build = int(thresholds["build_weeks"])
    race_week = _monday(event["date"])

    weeks: list[dict[str, Any]] = []
    week = _monday(data_start)
    while week <= race_week:
        weeks_out = (race_week - week).days // 7
        weeks.append({
            "week_start": week.isoformat(),
            "block": _block(weeks_out, taper, peak, build),
            "weeks_to_event": weeks_out,
            "anchor_event_id": event["id"],
        })
        week += _dt.timedelta(weeks=1)

    _mark_deloads(weeks, int(thresholds["deload_every_n_weeks"]))
    return weeks


def rollup(
    conn: sqlite3.Connection, *, data_start_date: str, through_date: str | None = None
) -> None:
    """Rebuild the `plan_block` mart from the goal events (safe to drop and rebuild).

    The anchor is resolved as of ``through_date``, not the wall clock, so recomputing
    a past date reproduces that day's plan. With no anchor the mart is left empty.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First date with real data; the left bound of the plan.
        through_date: The as-of date for anchor selection (default: today).
    """
    as_of = through_date or _dt.date.today().isoformat()
    anchor = anchor_event(db.list_goal_events(conn), as_of)
    weeks = periodize(anchor, data_start_date, _thresholds.read(conn)) if anchor else []
    db.replace_plan_block(conn, weeks)
    conn.commit()


def annotate(events: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """Return the goal events soonest first, each with its countdown and anchor flag.

    Args:
        events: Goal-event rows (as stored in ``goal_event``).
        today: The as-of date (YYYY-MM-DD).

    Returns:
        The events ordered soonest first, each carrying ``weeks_to_event`` and
        ``is_anchor``. At most one is the anchor; none is, when no confirmed
        priority-A race is upcoming.
    """
    anchor = anchor_event(events, today)
    return [
        {
            **event,
            "weeks_to_event": weeks_to_event(today, event["date"]),
            "is_anchor": anchor is not None and event["id"] == anchor["id"],
        }
        for event in sorted(events, key=lambda event: event["date"])
    ]
