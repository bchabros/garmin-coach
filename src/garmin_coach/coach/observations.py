"""Coherent offline reads of imported observations and source-check coverage."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from ..core import observations as recorded


def read(
    conn: sqlite3.Connection, *, from_date: str, to_date: str, data_start_date: str
) -> dict[str, Any]:
    """Read saved source facts and coverage without contacting Garmin.

    Raises:
        ValueError: The requested range is invalid, future, or before real history.
    """
    start, end = dt.date.fromisoformat(from_date), dt.date.fromisoformat(to_date)
    if start.isoformat() != from_date or end.isoformat() != to_date or start > end:
        raise ValueError("dates must be an ordered YYYY-MM-DD range")
    if from_date < data_start_date or end > dt.date.today():
        raise ValueError("range must be within configured real history and today")
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        return _read(conn, from_date, to_date, (end - start).days + 1)
    finally:
        if started:
            conn.rollback()


def _read(conn: sqlite3.Connection, start: str, end: str, days: int) -> dict[str, Any]:
    weight = _rows(
        conn.execute(
            "SELECT measurement_id, date, measured_at_utc_ms, weight_kg, source_type "
            "FROM weight_observations WHERE date BETWEEN ? AND ? ORDER BY date, measured_at_utc_ms, measurement_id",
            (start, end),
        )
    )
    forecasts = _rows(
        conn.execute(
            "SELECT * FROM garmin_race_forecasts WHERE date BETWEEN ? AND ? ORDER BY date",
            (start, end),
        )
    )
    return {
        "source": "garmin",
        "from_date": start,
        "to_date": end,
        "weight_unit": "kg",
        "forecast_unit": "s",
        "weight": weight,
        "race_forecasts": forecasts,
        "partial_today": end == dt.date.today().isoformat(),
        "checks": {
            name: _coverage(conn, name, start, end, days) for name in ("weight", "race_forecasts")
        },
        "error": None,
    }


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    keys = [column[0] for column in cur.description]
    return [dict(zip(keys, row)) for row in cur]


def _coverage(
    conn: sqlite3.Connection, name: str, start: str, end: str, days: int
) -> dict[str, Any]:
    checks = _rows(
        conn.execute(
            "SELECT date, checked_at, last_attempt_at, error_kind FROM observation_checks "
            "WHERE stream=? AND date BETWEEN ? AND ? ORDER BY date",
            (name, start, end),
        )
    )
    floor = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat(timespec="seconds")
    successful = [row for row in checks if row["checked_at"] and row["error_kind"] is None]
    fresh = [row for row in successful if row["checked_at"] >= floor]
    return {
        **recorded.scan_state(conn, name),
        "total_days": days,
        "successful_days": len(successful),
        "due_days": days - len(fresh),
        "never_checked_days": days - sum(bool(row["checked_at"]) for row in checks),
        "failed_dates": [row["date"] for row in checks if row["error_kind"]],
        "overdue_days": sum(row["checked_at"] < floor for row in successful),
        "last_attempt_at": max((row["last_attempt_at"] for row in checks), default=None),
        "incomplete": len(fresh) < days,
    }
