"""Coherent offline fitness-status and selected-period running evidence."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from . import activity
from .. import execution
from ..marts import weekly_fitness


def _range(start: str, end: str, data_start: str) -> tuple[str, str]:
    first, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or start > end:
        raise ValueError("use an ordered YYYY-MM-DD range")
    if start < data_start or last > dt.date.today():
        raise ValueError("range must stay within real history and today")
    return start, end


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(zip([c[0] for c in cur.description], row, strict=True)) for row in cur]


def _run(
    conn: sqlite3.Connection, row: dict[str, Any], reported: list[dict[str, Any]]
) -> dict[str, Any]:
    speed = row["avg_speed_mps"]
    return {
        **row,
        "source": "captured Garmin activity averages",
        "activity_average_pace_s_per_km": 1000 / speed if speed and speed > 0 else None,
        "measurement_context": {
            k: None
            for k in (
                "hr_sensor",
                "satellite_mode",
                "track_detection",
                "track_lane",
                "configured_max_hr",
                "matched_route",
            )
        },
        "context_reason": "Historical source fixtures do not substantiate these settings; observed max_hr is not configured maximum HR.",
        "athlete_reported_context": [
            r["note"] for r in reported if r["activity_id"] == row["activity_id"]
        ],
        "reported_context_source": "athlete report, unverified",
        "lap_evidence": activity.read(conn, row["activity_id"]),
    }


def _period(
    conn: sqlite3.Connection, start: str, end: str, reported: list[dict[str, Any]]
) -> dict[str, Any]:
    status = _rows(
        conn.execute(
            "SELECT date,status_source_date,status_phrase,fitness_trend,vo2max,vo2max_estimate_date FROM training_status_daily WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        )
    )
    # Retain dated daily rows, but exclude source values whose dates lie beyond this horizon.
    for row in status:
        if row["status_source_date"] and row["status_source_date"] > end:
            row["status_phrase"] = row["fitness_trend"] = None
        if row["vo2max_estimate_date"] and row["vo2max_estimate_date"] > end:
            row["vo2max"] = None
    rows = _rows(
        conn.execute(
            "SELECT activity_id,date,gtype,name,distance_m,dur_s,moving_dur_s,elapsed_dur_s,avg_speed_mps,avg_hr,max_hr,elev_gain_m,temp_c FROM activities WHERE date BETWEEN ? AND ? ORDER BY start_local,activity_id",
            (start, end),
        )
    )
    return {
        "from_date": start,
        "to_date": end,
        "partial_today": end == dt.date.today().isoformat(),
        "status": status,
        "estimate_event_count": None,
        "event_count_reason": "Source event identities unavailable; daily observations are not measurement counts.",
        "missing_status_days": (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
        + 1
        - len(status),
        "runs": [
            _run(conn, row, reported) for row in rows if row["gtype"] in execution.RUNNING_TYPES
        ],
    }


def read(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    data_start_date: str,
    baseline_from: str | None = None,
    baseline_to: str | None = None,
    reported_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare selected recorded periods without inferring matched conditions or causes.

    Athlete-reported context is attributed input, never verified sensor metadata.
    All related reads share one SQLite snapshot; existing transactions stay owned.
    """
    start, end = _range(from_date, to_date, data_start_date)
    if bool(baseline_from) != bool(baseline_to):
        raise ValueError("both baseline dates are required")
    baseline = (
        _range(baseline_from, baseline_to, data_start_date)
        if baseline_from and baseline_to
        else None
    )
    reported = reported_context or []
    if any(
        set(r) != {"activity_id", "note"}
        or type(r["activity_id"]) is not int
        or not isinstance(r["note"], str)
        for r in reported
    ):
        raise ValueError("reported context requires activity_id and an attributed note")
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        periods = {"selected": _period(conn, start, end, reported)}
        if baseline:
            periods["baseline"] = _period(conn, *baseline, reported)
        return {
            "periods": periods,
            "weekly_context": weekly_fitness.build(conn, end, data_start_date=data_start_date),
            "matched_runs": None,
            "comparability": "Not established: route, measurement settings and conditions may differ.",
            "caution": "GPS pace/distance and HR measurements affect interpretation. RPE or one benchmark cannot prove VO2max change or explain Garmin Maintaining/Productive.",
        }
    finally:
        if started:
            conn.rollback()
