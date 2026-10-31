"""Validated source observation replacement, separate from retained legacy storage."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from typing import Any

FORECAST_FIELDS = ("time5K", "time10K", "timeHalfMarathon", "timeMarathon")


def day_summary(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """Read per-source counts/checks and bind a repair preview to current source facts."""
    result = {}
    for name, table, order in (
        ("weight", "weight_observations", "measurement_id"),
        ("race_forecasts", "garmin_race_forecasts", "date"),
    ):
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE date=? ORDER BY {order}", (date,)
        ).fetchall()
        check = conn.execute(
            "SELECT checked_at, error_kind FROM observation_checks WHERE stream=? AND date=?",
            (name, date),
        ).fetchone()
        result[name] = {
            "records": len(rows),
            "checked_at": check[0] if check else None,
            "error_kind": check[1] if check else None,
            "binding": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
        }
    return result


def record_check(
    conn: sqlite3.Connection, stream: str, date: str, error: str | None = None
) -> None:
    """Stage an attempt without changing the date of a prior successful observation."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO observation_checks VALUES (?,?,?,?,?) ON CONFLICT(stream,date) DO UPDATE SET "
        "checked_at=CASE WHEN excluded.error_kind IS NULL THEN excluded.checked_at "
        "ELSE observation_checks.checked_at END, last_attempt_at=excluded.last_attempt_at, "
        "error_kind=excluded.error_kind",
        (stream, date, now if error is None else None, now, error),
    )


def scan_state(conn: sqlite3.Connection, stream: str) -> dict[str, Any]:
    """Read durable scan progress; a missing state means no completed nightly bucket."""
    row = conn.execute(
        "SELECT successful_runs, last_successful_run, cycle_completed_at "
        "FROM observation_scans WHERE stream=?",
        (stream,),
    ).fetchone()
    runs, last, completed = row if row is not None else (0, None, None)
    return {
        "successful_runs": runs,
        "last_successful_run": last,
        "cycle_completed_at": completed,
        "next_bucket": runs % 7,
    }


def complete_scan(
    conn: sqlite3.Connection, stream: str, through_date: str, expected_runs: int
) -> None:
    """Stage only the reviewed bucket, refusing concurrent or same-cutoff advancement."""
    state = scan_state(conn, stream)
    runs = expected_runs + 1
    completed = (
        dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        if runs % 7 == 0
        else state["cycle_completed_at"]
    )
    conn.execute(
        "INSERT INTO observation_scans VALUES (?,?,?,?) ON CONFLICT(stream) DO UPDATE SET "
        "successful_runs=excluded.successful_runs, last_successful_run=excluded.last_successful_run, "
        "cycle_completed_at=excluded.cycle_completed_at "
        "WHERE observation_scans.successful_runs=? "
        "AND observation_scans.last_successful_run<excluded.last_successful_run",
        (stream, runs, through_date, completed, expected_runs),
    )


def replace_weight_day(conn: sqlite3.Connection, date: str, payload: Any) -> None:
    """Stage all weighings for one verified source day, preserving other days.

    Raises:
        ValueError: The response cannot establish a complete, valid requested day.
    """
    if not isinstance(payload, dict) or any(
        payload.get(key) != date for key in ("startDate", "endDate")
    ):
        raise ValueError("weight response does not match the requested day")
    items = payload.get("dateWeightList")
    if not isinstance(items, list):
        raise ValueError("weight response has no observation list")
    rows = [_weight_row(date, item) for item in items]
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("weight response has duplicate measurement identities")
    conn.execute("DELETE FROM weight_observations WHERE date = ?", (date,))
    conn.executemany("INSERT OR REPLACE INTO weight_observations VALUES (?,?,?,?,?)", rows)


def _weight_row(date: str, item: Any) -> tuple[int, str, int, float, str | None]:
    if not isinstance(item, dict) or item.get("calendarDate") != date:
        raise ValueError("weight measurement does not match the requested day")
    identity, instant, grams = (item.get(k) for k in ("samplePk", "timestampGMT", "weight"))
    if type(identity) is not int or type(instant) is not int or identity < 0 or instant < 0:
        raise ValueError("weight measurement lacks a valid identity or UTC timestamp")
    if (
        not isinstance(grams, int | float)
        or isinstance(grams, bool)
        or not math.isfinite(grams)
        or grams <= 0
    ):
        raise ValueError("weight measurement lacks a positive finite gram value")
    source = item.get("sourceType")
    if source is not None and not isinstance(source, str):
        raise ValueError("weight measurement has an invalid source type")
    return identity, date, instant, grams / 1000, source


def replace_forecast_day(conn: sqlite3.Connection, date: str, payload: Any) -> None:
    """Stage a dated daily forecast list, refusing latest or wrong-day responses.

    Raises:
        ValueError: The response cannot establish the requested day's forecasts.
    """
    if not isinstance(payload, list) or len(payload) > 1:
        raise ValueError("daily forecast response must contain at most one requested day")
    values = None
    if payload:
        item = payload[0]
        if not isinstance(item, dict) or item.get("calendarDate") != date:
            raise ValueError("forecast does not match the requested day")
        if not any(field in item for field in FORECAST_FIELDS):
            raise ValueError("forecast has no supported distance fields")
        values = [item.get(field) for field in FORECAST_FIELDS]
        for value in values:
            if value is not None and (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("forecast seconds must be positive finite numbers or null")
    conn.execute("DELETE FROM garmin_race_forecasts WHERE date = ?", (date,))
    if values is not None:
        conn.execute("INSERT INTO garmin_race_forecasts VALUES (?,?,?,?,?)", (date, *values))
