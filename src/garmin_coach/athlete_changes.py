"""Complete athlete-entered changes shared by terminal and conversation.

Core facts are committed before their derived consequences. Failed recomputation
retains the saved fact and reports older derived evidence, rather than rejecting it.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from .core import db, events, manual_sets
from .marts import features, periodize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChangeResult:
    """A durable-write outcome, independently describing its derived refresh.

    Attributes:
        data: Saved identity and domain evidence; empty for an unsaved refusal.
        saved: Whether the core fact was committed.
        recomputed: True/False for an attempted mart pass, None when not required.
        error: The refusal, write failure or post-save consequence failure.
        recovery_command: An offline operator command for a saved, unrefreshed change.
    """

    data: dict[str, Any]
    saved: bool
    recomputed: bool | None = None
    error: str | None = None
    recovery_command: str | None = None

    def as_data(self) -> dict[str, Any]:
        """Return common evidence fields without confusing saved facts with refreshed marts."""
        result = {
            **self.data,
            "saved": self.saved,
            "recomputed": self.recomputed,
            "error": self.error,
        }
        if self.recovery_command is not None:
            result["recovery_command"] = self.recovery_command
        return result


def _record(
    conn: sqlite3.Connection,
    write: Callable[[], dict[str, Any]],
    rebuild: Callable[[dict[str, Any]], None] | None = None,
) -> ChangeResult:
    """Commit the fact before attempting its separately transactional consequence."""
    try:
        data = write()
        conn.commit()
    except ValueError as exc:
        return ChangeResult({}, saved=False, error=str(exc))
    except sqlite3.Error as exc:
        conn.rollback()
        return ChangeResult({}, saved=False, error=f"could not save change: {exc}")
    if rebuild is None:
        return ChangeResult(data, saved=True)
    try:
        rebuild(data)
    except Exception as exc:  # noqa: BLE001 - the fact is durable even if a mart step fails
        conn.rollback()
        logger.exception("athlete_changes: recomputation failed after saving a change")
        return ChangeResult(
            data,
            saved=True,
            recomputed=False,
            error=f"record saved; recomputation failed: {exc}",
            recovery_command="garmin-coach features",
        )
    return ChangeResult(data, saved=True, recomputed=True)


def log_session_rpe(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    rpe: int,
    soreness: int | None = None,
    mood: int | None = None,
    note: str | None = None,
    source: str = "manual",
    data_start_date: str,
) -> ChangeResult:
    """Save reported session effort and refresh load, preserving facts on refresh failure."""
    return _record(
        conn,
        lambda: _write_session_rpe(
            conn,
            activity_id=activity_id,
            rpe=rpe,
            soreness=soreness,
            mood=mood,
            note=note,
            source=source,
        ),
        lambda data: features.features(
            conn, data_start_date=data_start_date, from_date=data["date"]
        ),
    )


def _check_range(name: str, value: int | None, lo: int, hi: int) -> None:
    """Raise ValueError when an optional integer input falls outside [lo, hi]."""
    if value is not None and not (lo <= value <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi} (got {value})")


def _write_session_rpe(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    rpe: int,
    soreness: int | None = None,
    mood: int | None = None,
    note: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    """Validate and stage reported effort; the application commits and refreshes it."""
    _check_range("rpe", rpe, 1, 10)
    _check_range("soreness", soreness, 1, 10)
    _check_range("mood", mood, 1, 10)
    activity_date = _activity_date(conn, activity_id)
    db.upsert_session_rpe(
        conn,
        {
            "activity_id": activity_id,
            "rpe": rpe,
            "soreness": soreness,
            "mood": mood,
            "source": source,
            "notes": note,
        },
    )
    return {"activity_id": activity_id, "rpe": rpe, "date": activity_date}


def log_niggle(
    conn: sqlite3.Connection,
    *,
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> ChangeResult:
    """Save a reported complaint, available to the next digest without a mart pass."""
    return _record(
        conn,
        lambda: _write_niggle(conn, body_part=body_part, severity=severity, date=date, note=note),
    )


def _write_niggle(
    conn: sqlite3.Connection,
    *,
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Validate and stage a complaint for direct digest reads."""
    _check_range("severity", severity, 1, 5)
    day = date or dt.date.today().isoformat()
    db.upsert_niggle(
        conn, {"date": day, "body_part": body_part, "severity": severity, "note": note}
    )
    return {"body_part": body_part, "severity": severity, "date": day}


def log_activity_sets(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    stations: list[str | manual_sets.ManualStationDetails],
    data_start_date: str,
) -> ChangeResult:
    """Replace a manual overlay and refresh movement/load, retaining saved facts on failure."""
    return _record(
        conn,
        lambda: {
            "activity_id": activity_id,
            **_write_activity_sets(conn, activity_id=activity_id, stations=stations),
        },
        lambda data: features.features(
            conn, data_start_date=data_start_date, from_date=data["date"]
        ),
    )


def _unmapped_stations(conn: sqlite3.Connection, names: list[str]) -> list[str]:
    """The logged names that ``exercise_pattern`` does not know yet (coverage drift)."""
    known = {r[0] for r in conn.execute("SELECT subcategory FROM exercise_pattern")}
    return sorted({n for n in names if n not in known})


def _write_activity_sets(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    stations: list[str | manual_sets.ManualStationDetails],
) -> dict[str, Any]:
    """Normalize and stage the complete overlay without touching captured sets."""
    rows = manual_sets.normalize(activity_id, stations)
    activity_date = _activity_date(conn, activity_id)
    db.replace_manual_activity_sets(conn, activity_id, rows)
    return {
        "date": activity_date,
        "n_sets": len(rows),
        "unmapped": _unmapped_stations(conn, [r["subcategory"] for r in rows]),
    }


def _event_change(
    conn: sqlite3.Connection, write: Callable[[], int], data_start_date: str
) -> ChangeResult:
    """Keep pre-write block evidence separate from the newly saved event."""
    empty = {"event": None, "block_before": None, "block_after": None}
    try:
        before = _current_block(conn)
    except sqlite3.Error as exc:
        return ChangeResult(empty, saved=False, error=f"could not inspect the current block: {exc}")
    result = _record(
        conn,
        lambda: {**empty, "event_id": write(), "block_before": before},
        lambda data: features.rebuild_marts(conn, data_start_date=data_start_date),
    )
    if not result.saved:
        return replace(result, data=empty)
    try:
        event = _event_row(conn, result.data["event_id"])
        after = _current_block(conn) if result.recomputed else None
    except (sqlite3.Error, ValueError) as exc:
        error = f"{result.error}; " if result.error else ""
        return replace(
            result, error=f"{error}record saved; could not read updated event context: {exc}"
        )
    return replace(result, data={**result.data, "event": event, "block_after": after})


def event_add(
    conn: sqlite3.Connection,
    *,
    date: str,
    type: str,  # noqa: A002 - the domain's goal-event field
    priority: str,
    status: str,
    date_precision: str,
    target: str | None = None,
    note: str | None = None,
    data_start_date: str,
) -> ChangeResult:
    """Save a goal event and report the refreshed current block or honest partial result."""
    return _event_change(
        conn,
        lambda: events.add_goal_event(
            conn,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
        ),
        data_start_date,
    )


def _current_block(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The block calendar's row for the week that holds today, or None without an anchor.

    Carries the anchor race's id beside the block, so a report of the move names the
    race the periodization is now dated from.
    """
    row = periodize.current_plan(conn, dt.date.today().isoformat())
    if row is None:
        return None
    return {**row, "anchor_event_id": periodize.anchor_event_id(conn, row["week_start"])}


def _event_row(conn: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    """The recorded race the write touched, annotated as ``event list`` shows it.

    Looked up by the id the write returned rather than by the fields the caller
    passed: the writer normalises what it stores, so a date the athlete typed in
    another shape would never match itself.
    """
    rows = periodize.annotate(db.list_goal_events(conn), dt.date.today().isoformat())
    return next((row for row in rows if row["id"] == event_id), None)


def event_update(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    date: str | None = None,
    type: str | None = None,  # noqa: A002 - the domain's goal-event field
    priority: str | None = None,
    status: str | None = None,
    date_precision: str | None = None,
    target: str | None = None,
    note: str | None = None,
    data_start_date: str,
) -> ChangeResult:
    """Save event corrections and report their current-block consequences honestly."""

    def write() -> int:
        try:
            events.update_goal_event(
                conn,
                event_id,
                date=date,
                type=type,
                priority=priority,
                status=status,
                date_precision=date_precision,
                target=target,
                note=note,
            )
        except sqlite3.IntegrityError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                raise
            conn.rollback()
            raise ValueError(
                "another goal event already uses this date and type; inspect "
                "`garmin-coach event list` and update the recorded event"
            ) from None
        return event_id

    return _event_change(conn, write, data_start_date)


def _activity_date(conn: sqlite3.Connection, activity_id: int) -> str:
    """Resolve the recorded activity before staging an athlete-entered observation."""
    row = conn.execute(
        "SELECT date(start_local) FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"activity {activity_id} not found; run `garmin-coach sync` first")
    return row[0]
