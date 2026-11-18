"""Offline source-observation and conditional historical-check coverage."""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def activities(
    conn: sqlite3.Connection, *, from_date: str, through_date: str
) -> list[dict[str, Any]]:
    """Read source/check facts for every known eligible activity in the horizon."""
    cursor = conn.execute(
        "SELECT a.activity_id,a.date,o.checked_on,o.observed_at,t.attempted_on,t.error,o.payload_json,"
        "(SELECT COUNT(*) FROM activity_sets s WHERE s.activity_id=a.activity_id) AS captured_sets "
        "FROM activities a LEFT JOIN exercise_set_observations o USING(activity_id) "
        "LEFT JOIN exercise_set_attempts t USING(activity_id) "
        "WHERE a.gtype IN ('strength_training','hiit') AND a.date BETWEEN ? AND ? "
        "ORDER BY a.date,a.activity_id",
        (from_date, through_date),
    )
    names = [column[0] for column in cursor.description]
    result = []
    for row in cursor:
        item = dict(zip(names, row, strict=True))
        item["source_active_sets"] = _active_count(item.pop("payload_json"))
        result.append(item)
    return result


def _active_count(encoded: str | None) -> int | None:
    if encoded is None:
        return None
    try:
        entries = json.loads(encoded)["exerciseSets"]
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            return None
        return sum(e.get("setType") == "ACTIVE" for e in entries)
    except (ValueError, TypeError, KeyError):
        return None


def coverage(
    conn: sqlite3.Connection, *, through_date: str, data_start_date: str, recent_days: int = 3
) -> dict[str, Any]:
    """Expose conditional seven-night progress, including failed and missed checks."""
    end = dt.date.fromisoformat(through_date)
    cutoff = (end - dt.timedelta(days=max(1, recent_days) - 1)).isoformat()
    overdue = (end - dt.timedelta(days=7)).isoformat()
    rows = activities(
        conn,
        from_date=data_start_date,
        through_date=(dt.date.fromisoformat(cutoff) - dt.timedelta(days=1)).isoformat(),
    )
    items = [
        {**row, "due": row["checked_on"] is None or row["checked_on"] <= overdue} for row in rows
    ]
    return {
        "through_date": through_date,
        "recent_window_start": cutoff,
        "eligible": len(items),
        "due": sum(i["due"] for i in items),
        "never_checked": sum(i["checked_on"] is None for i in items),
        "overdue": sum(i["checked_on"] is not None and i["due"] for i in items),
        "failed": sum(i["error"] is not None for i in items),
        "activities": items,
        "condition": "Coverage requires successful nights and source reads; observation dates are not edit times.",
    }
