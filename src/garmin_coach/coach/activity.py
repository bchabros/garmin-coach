"""One saved-activity read shared by conversational and command adapters."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .. import execution
from ..core import laps
from . import console


@contextmanager
def _snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    # Keep execution, the declaration and cached metrics in one read generation.
    # Caller-owned transactions (including features) retain their ownership.
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if started:
            conn.rollback()


def read(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Read coherent captured/manual evidence and cached metrics without transport."""
    with _snapshot(conn):
        return _read(conn, activity_id)


def _read(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    observed = execution.read(conn, activity_id)
    info = observed.activity
    if info is None:
        return {
            "activity": None,
            "laps": [],
            "summary": None,
            "unavailable_reason": "activity_not_found",
        }
    saved = conn.execute(
        "SELECT summary_json FROM activity_interval_summaries WHERE activity_id=?", (activity_id,)
    ).fetchone()
    result: dict[str, Any] = (
        json.loads(saved[0]) if saved else {"summary": None, "unavailable_reason": "not_recomputed"}
    )
    return {
        "activity": info,
        "work_execution": observed.as_data(),
        "reported_console": console.read(conn, observed),
        "partial_today": info["date"] == dt.date.today().isoformat(),
        "captured_source": "Garmin get_activity_splits",
        "group_source": "Garmin get_activity_typed_splits",
        "treadmill_pace_caution": (
            "Captured pace/distance has uncertain sensor provenance and accuracy; no pace spread."
            if info["gtype"] == "treadmill_running"
            else None
        ),
        "laps": laps.rows(conn, "activity_laps", activity_id),
        **result,
    }


def markers(conn: sqlite3.Connection, activity_id: int) -> dict[str, bool]:
    """Return compact captured/derived evidence availability for an activity."""
    has_laps = (
        conn.execute(
            "SELECT 1 FROM activity_laps WHERE activity_id=? LIMIT 1", (activity_id,)
        ).fetchone()
        is not None
    )
    saved = conn.execute(
        "SELECT summary_json FROM activity_interval_summaries WHERE activity_id=?", (activity_id,)
    ).fetchone()
    return {
        "has_laps": has_laps,
        "has_interval_summary": bool(saved and json.loads(saved[0])["summary"]),
    }


def weekly(conn: sqlite3.Connection, week_start: str) -> list[dict[str, Any]]:
    """Read one coherent snapshot of the already selected complete week's sessions."""
    end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()
    with _snapshot(conn):
        rows = conn.execute(
            "SELECT a.activity_id,a.date,a.gtype,s.summary_json,h.activity_id FROM activities a "
            "LEFT JOIN activity_interval_summaries s USING(activity_id) "
            "LEFT JOIN treadmill_speed_sets h USING(activity_id) "
            "WHERE a.date BETWEEN ? AND ? ORDER BY a.start_local,a.activity_id",
            (week_start, end),
        ).fetchall()
        return [
            _weekly_entry(
                conn, activity_id, date, saved, gtype == "treadmill_running" or bool(has_report)
            )
            for activity_id, date, gtype, saved, has_report in rows
            if gtype in execution.RUNNING_TYPES
        ]


def _weekly_console(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any] | None:
    reported = console.read(conn, execution.read(conn, activity_id))
    if reported is None:
        return None
    return {
        k: reported[k]
        for k in (
            "state",
            "source",
            "current_work_count",
            "reported_count",
            "missing_repetitions",
            "applied_reports",
        )
    }


def _weekly_entry(
    conn: sqlite3.Connection, activity_id: int, date: str, saved: str | None, include_console: bool
) -> dict[str, Any]:
    result: dict[str, Any] = (
        json.loads(saved) if saved else {"summary": None, "unavailable_reason": "not_recomputed"}
    )
    summary = result["summary"]
    return {
        "activity_id": activity_id,
        "date": date,
        "reported_console": _weekly_console(conn, activity_id) if include_console else None,
        "unavailable_reason": result["unavailable_reason"],
        "work_count": summary["work_count"] if summary else None,
        "hr_change_first_to_last_bpm": summary["hr_change_first_to_last_bpm"] if summary else None,
        "work_recovery_difference": {
            k: v for k, v in summary["work_recovery_difference"].items() if k != "pairs"
        }
        if summary
        else None,
        "outdoor_pace": summary["outdoor_pace"] if summary else None,
    }
