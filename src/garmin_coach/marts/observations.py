"""Dated standing facts derived only from imported observations."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

LOOKBACK_DAYS = 28


def weight(
    conn: sqlite3.Connection,
    through_date: str,
    min_span_days: float,
    *,
    data_start_date: str | None = None,
) -> dict[str, Any] | None:
    """Select each source day's last weighing and its supported 28-day change."""
    rows = conn.execute(
        "SELECT date, weight_kg, measured_at_utc_ms, source_type FROM weight_observations "
        "WHERE date >= ? AND date <= ? ORDER BY date, measured_at_utc_ms, measurement_id",
        (data_start_date or "0001-01-01", through_date),
    ).fetchall()
    daily = {row[0]: row for row in rows}
    marker = _marker(
        [(row[0], row[1]) for row in daily.values()], through_date, min_span_days, "kg"
    )
    if marker is None:
        return None
    return {**marker, "measurement_time_utc_ms": rows[-1][2], "measurement_source": rows[-1][3]}


def race_forecasts(
    conn: sqlite3.Connection,
    through_date: str,
    min_span_days: float,
    *,
    data_start_date: str | None = None,
) -> dict[str, Any]:
    """Read each distance's dated Garmin estimate and independently supported change."""
    result: dict[str, Any] = {}
    for distance, column in (
        ("5k", "time_5k_s"),
        ("10k", "time_10k_s"),
        ("half_marathon", "time_half_s"),
        ("marathon", "time_marathon_s"),
    ):
        rows = conn.execute(
            f"SELECT date, {column} FROM garmin_race_forecasts "
            f"WHERE date >= ? AND date <= ? AND {column} IS NOT NULL ORDER BY date",
            (data_start_date or "0001-01-01", through_date),
        ).fetchall()
        result[distance] = _marker(rows, through_date, min_span_days, "s")
    return result


def _marker(
    series: list[tuple[str, float]], through_date: str, min_span_days: float, unit: str
) -> dict[str, Any] | None:
    if not series:
        return None
    date, value = series[-1]
    floor = (dt.date.fromisoformat(through_date) - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    recent = [(day, number) for day, number in series if day >= floor]
    span = (dt.date.fromisoformat(date) - dt.date.fromisoformat(recent[0][0])).days if recent else 0
    enough = len(recent) >= 2 and span >= min_span_days
    return {
        "source": "garmin",
        "unit": unit,
        "date": date,
        "value": value,
        "delta": value - recent[0][1] if enough else None,
        "span_days": span if enough else None,
        "baseline_date": recent[0][0] if enough else None,
        "points": len(recent),
    }
