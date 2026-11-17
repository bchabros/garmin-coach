"""Coherent offline fitness-status and selected-period running evidence."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Any

from . import activity
from .. import execution
from ..core import db
from ..marts import weekly_fitness

logger = logging.getLogger(__name__)


def _validate_range(start: str, end: str, data_start: str, today: dt.date) -> tuple[str, str]:
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or first > last:
        raise ValueError("use an ordered YYYY-MM-DD range")
    if first < dt.date.fromisoformat(data_start) or last > today:
        raise ValueError("range must stay within real history and today")
    return start, end


def _baseline(
    first: str | None, last: str | None, selected_start: str, data_start: str, today: dt.date
) -> tuple[str, str] | None:
    if (first is None) != (last is None):
        raise ValueError("both baseline dates are required")
    if first is None or last is None:
        return None
    period = _validate_range(first, last, data_start, today)
    if last >= selected_start:
        raise ValueError(
            "baseline must end before the selected period; overlapping or reversed periods "
            "are not comparable"
        )
    return period


def _reported_context(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(row, dict)
        or set(row) != {"activity_id", "note"}
        or type(row["activity_id"]) is not int
        or row["activity_id"] <= 0
        or not isinstance(row["note"], str)
        or not row["note"].strip()
        for row in value
    ):
        raise ValueError(
            "reported context requires a list of activity_id and attributed note objects"
        )
    return value


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor]


def _status_history(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    rows = _rows(
        conn.execute(
            "SELECT date,status_source_date,status_phrase,fitness_trend,vo2max,"
            "vo2max_estimate_date "
            "FROM training_status_daily WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        )
    )
    for row in rows:
        if row["status_source_date"] and row["status_source_date"] > end:
            row["status_phrase"] = row["fitness_trend"] = None
        if row["vo2max_estimate_date"] and row["vo2max_estimate_date"] > end:
            row["vo2max"] = None
    return rows


def _run(
    conn: sqlite3.Connection, row: dict[str, Any], reported: list[dict[str, Any]], today: dt.date
) -> dict[str, Any]:
    speed = row["avg_speed_mps"]
    return {
        **row,
        "source": "captured Garmin activity averages",
        "activity_average_pace_s_per_km": 1000 / speed if speed and speed > 0 else None,
        "measurement_context": {
            key: None
            for key in (
                "hr_sensor",
                "satellite_mode",
                "track_detection",
                "track_lane",
                "configured_max_hr",
                "matched_route",
            )
        },
        "context_reason": (
            "Historical source fixtures do not substantiate these settings; "
            "observed max_hr is not configured maximum HR."
        ),
        "athlete_reported_context": [
            r["note"] for r in reported if r["activity_id"] == row["activity_id"]
        ],
        "reported_context_source": "athlete report, unverified",
        "lap_evidence": activity.read(conn, row["activity_id"], today=today),
    }


def _runs(
    conn: sqlite3.Connection, start: str, end: str, reported: list[dict[str, Any]], today: dt.date
) -> list[dict[str, Any]]:
    types = sorted(execution.RUNNING_TYPES)
    marks = ",".join("?" for _ in types)
    rows = _rows(
        conn.execute(
            "SELECT activity_id,date,gtype,name,distance_m,dur_s,moving_dur_s,elapsed_dur_s,"
            "avg_speed_mps,avg_hr,max_hr,elev_gain_m,temp_c FROM activities "
            f"WHERE date BETWEEN ? AND ? AND gtype IN ({marks}) ORDER BY start_local,activity_id",
            (start, end, *types),
        )
    )
    return [_run(conn, row, reported, today) for row in rows]


def _period(
    conn: sqlite3.Connection, start: str, end: str, reported: list[dict[str, Any]], today: dt.date
) -> dict[str, Any]:
    status = _status_history(conn, start, end)
    return {
        "from_date": start,
        "to_date": end,
        "partial_today": end == today.isoformat(),
        "status": status,
        "estimate_event_count": None,
        "event_count_reason": (
            "Source event identities unavailable; daily observations are not measurement counts."
        ),
        "missing_status_days": (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        + 1
        - len(status),
        "runs": _runs(conn, start, end, reported, today),
    }


def read(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    data_start_date: str,
    today: dt.date,
    baseline_from: str | None = None,
    baseline_to: str | None = None,
    reported_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read disjoint before/after evidence with an explicit clock and unknown settings.

    Reported context stays attributed. Nested reads share one SQLite snapshot;
    existing caller-owned transactions remain open after success or failure.
    """
    start, end = _validate_range(from_date, to_date, data_start_date, today)
    baseline = _baseline(baseline_from, baseline_to, start, data_start_date, today)
    reported = _reported_context(reported_context)
    with db.read_snapshot(conn):
        periods = {"selected": _period(conn, start, end, reported, today)}
        if baseline:
            periods["baseline"] = _period(conn, *baseline, reported, today)
        return {
            "periods": periods,
            "weekly_context": weekly_fitness.build(conn, end, data_start_date=data_start_date),
            "matched_runs": None,
            "comparability": (
                "Not established: route, measurement settings and conditions may differ."
            ),
            "caution": (
                "GPS pace/distance and HR measurements affect interpretation. RPE or one benchmark "
                "cannot prove VO2max change or explain Garmin Maintaining/Productive."
            ),
        }
