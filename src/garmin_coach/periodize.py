"""Periodization: the block calendar counted back from the athlete's goal race.

The pure block calendar (`periodize`) plus the thin `plan_block` rollup at the DB
boundary - the same shape as `zones` / `snapshot` / `overlap`. Blocks are a countdown
from the race date: no wall clock, no training history.

A week's anchor is the nearest `confirmed` priority-A race **on or after that week**.
A `tentative` race never anchors - the system does not taper for a race the athlete may
skip - and neither does a B/C race, however near. Weeks with no race ahead of them get
no plan at all, stated as None rather than guessed at.

Note the anchor is a function of the *week*, not of today: once a race is run it keeps
labelling the weeks that led up to it. "What am I training for now" and "what block was
that week in" are different questions, and the second stays true after the gun.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from . import db, thresholds as _thresholds
from .weeks import monday as _monday, weeks_between as _weeks_between

TAPER = "taper"

# Blocks that carry a planned deload. `peak` is short and `taper` is a downshift
# already, so neither gets one.
DELOAD_BLOCKS = ("base", "build")


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
    return _weeks_between(day, event_date)


def _block(weeks_out: int, taper: int, peak: int, build: int) -> str:
    """Label a week by its distance from the race: a countdown, not a calendar."""
    if weeks_out < taper:
        return TAPER
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


def build_plan(
    events: list[dict[str, Any]], data_start: str, thresholds: dict[str, float]
) -> list[dict[str, Any]]:
    """Build the block calendar across *every* confirmed priority-A race.

    A week's anchor is the nearest confirmed A race **on or after that week** - not the
    nearest race upcoming *today*. That distinction is the whole point: "what am I
    training for now" and "what block was that week in" are different questions, and the
    second is a historical fact that does not stop being true once the race is run. Each
    race therefore owns the weeks running up to it, from the previous race (exclusive) or
    from ``data_start``.

    Weeks after the last confirmed A race get no row: there the system genuinely does not
    know what is being trained for, and says so.

    Args:
        events: All recorded goal events.
        data_start: First date with real data; the left bound of the plan.
        thresholds: Effective coach thresholds (block lengths, deload cadence).

    Returns:
        One row per planned week, chronologically.
    """
    races = sorted(
        (e for e in events if e["priority"] == "A" and e["status"] == "confirmed"),
        key=lambda event: event["date"],
    )
    weeks: list[dict[str, Any]] = []
    segment_start = data_start
    for race in races:
        if _monday(race["date"]) < _monday(segment_start):
            continue  # a race already behind the segment: it labels nothing
        weeks.extend(periodize(race, segment_start, thresholds))
        segment_start = (_monday(race["date"]) + _dt.timedelta(weeks=1)).isoformat()
    return weeks


def rollup(conn: sqlite3.Connection, *, data_start_date: str) -> None:
    """Rebuild the `plan_block` mart from the goal events (safe to drop and rebuild).

    Takes no as-of date: the plan is a fact about the athlete's race calendar, not about
    when it is asked for, so any recompute reproduces the same rows. With no confirmed
    priority-A race the mart is left empty.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First date with real data; the left bound of the plan.
    """
    weeks = build_plan(db.list_goal_events(conn), data_start_date, _thresholds.read(conn))
    db.replace_plan_block(conn, weeks)
    conn.commit()


_CURRENT_PLAN_COLUMNS = (
    "week_start", "block", "weeks_to_event", "is_deload",
    "race_date", "race_type", "race_status", "race_date_precision",
)


def current_plan(conn: sqlite3.Connection, day: str) -> dict[str, Any] | None:
    """Return the plan row for the week ``day`` falls in, joined to its anchor race.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        day: Any calendar day; the week containing it is looked up.

    Returns:
        The week's block, countdown, planned-deload flag and derived ``taper_active``
        alongside the anchor race's date/type/status, or None when there is no plan.
        ``taper_active`` is derived here so no consumer re-tests the block string, and
        it goes false once the race has been run - the race week keeps its `taper`
        label (that is a fact about the week) but the taper itself ends at the gun.
    """
    row = conn.execute(
        """
        SELECT p.week_start, p.block, p.weeks_to_event, p.is_deload,
               e.date, e.type, e.status, e.date_precision
        FROM plan_block p
        JOIN goal_event e ON e.id = p.anchor_event_id
        WHERE p.week_start = ?
        """,
        (_monday(day).isoformat(),),
    ).fetchone()
    if row is None:
        return None
    plan = dict(zip(_CURRENT_PLAN_COLUMNS, row, strict=True))
    tapering = plan["block"] == TAPER and day <= plan["race_date"]
    plan["taper_active"] = 1 if tapering else 0
    return plan


def blocks_by_week(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Return every planned week keyed by `week_start` (empty without an anchor)."""
    return {
        row[0]: {"block": row[1], "weeks_to_event": row[2]}
        for row in conn.execute("SELECT week_start, block, weeks_to_event FROM plan_block")
    }


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
