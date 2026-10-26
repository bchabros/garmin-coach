"""One saved-activity read shared by conversational and command adapters."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

from ..core import laps


def read(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Return captured laps and a separately rebuilt descriptive summary, without transport."""
    cur = conn.execute(
        "SELECT activity_id, date, gtype, discipline, name FROM activities WHERE activity_id=?",
        (activity_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {
            "activity": None,
            "laps": [],
            "summary": None,
            "unavailable_reason": "activity_not_found",
        }
    info = dict(zip([c[0] for c in cur.description], row))
    saved = conn.execute(
        "SELECT summary_json FROM activity_interval_summaries WHERE activity_id=?", (activity_id,)
    ).fetchone()
    result: dict[str, Any] = (
        json.loads(saved[0]) if saved else {"summary": None, "unavailable_reason": "not_recomputed"}
    )
    return {
        "activity": info,
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
    """Read compact eligible sessions in exactly one already selected complete week."""
    from ..marts.intervals import RUNNING_TYPES

    end = (dt.date.fromisoformat(week_start) + dt.timedelta(days=6)).isoformat()
    entries = []
    for activity_id, date, gtype, saved in conn.execute(
        "SELECT a.activity_id,a.date,a.gtype,s.summary_json FROM activities a "
        "LEFT JOIN activity_interval_summaries s USING(activity_id) "
        "WHERE a.date BETWEEN ? AND ? ORDER BY a.start_local,a.activity_id",
        (week_start, end),
    ):
        if gtype not in RUNNING_TYPES:
            continue
        result: dict[str, Any] = (
            json.loads(saved)
            if saved
            else {"summary": None, "unavailable_reason": "not_recomputed"}
        )
        summary = result["summary"]
        entries.append(
            {
                "activity_id": activity_id,
                "date": date,
                "unavailable_reason": result["unavailable_reason"],
                "work_count": summary["work_count"] if summary else None,
                "hr_change_first_to_last_bpm": summary["hr_change_first_to_last_bpm"]
                if summary
                else None,
                "work_recovery_difference": {
                    k: v for k, v in summary["work_recovery_difference"].items() if k != "pairs"
                }
                if summary
                else None,
                "outdoor_pace": summary["outdoor_pace"] if summary else None,
            }
        )
    return entries
