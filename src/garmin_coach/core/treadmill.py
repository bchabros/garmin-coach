"""Independent athlete-reported treadmill settings; ETL never writes these facts."""

from __future__ import annotations

import math
import sqlite3
from typing import Any, TypedDict

from pydantic import ConfigDict, with_config


@with_config(ConfigDict(extra="forbid", strict=True))
class SpeedReport(TypedDict):
    """One athlete-declared setting for a numbered observed work repetition."""

    repetition: int
    speed_kmh: float


def speed(value: Any) -> float:
    """Validate a finite positive console setting with a representable converted pace."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("console speed must be a finite positive number in km/h")
    try:
        result = float(value)
    except OverflowError:
        raise ValueError("console speed must be finite") from None
    if not math.isfinite(result) or result <= 0 or not math.isfinite(3600 / result):
        raise ValueError("console speed must be finite, positive and yield a finite pace")
    return result


def normalize(
    work_count: int,
    *,
    speed_kmh: float | None = None,
    repetitions: list[SpeedReport] | None = None,
) -> list[dict[str, Any]]:
    """Validate exactly one complete input mode before any persistence mutation."""
    if (speed_kmh is None) == (repetitions is None):
        raise ValueError("provide either one speed_kmh or explicit repetitions")
    if repetitions is None:
        value = speed(speed_kmh)
        return [{"repetition": i, "speed_kmh": value} for i in range(1, work_count + 1)]
    if not isinstance(repetitions, list) or not repetitions:
        raise ValueError("at least one report is required; use explicit clear to remove reports")
    rows: list[dict[str, Any]] = []
    seen = set()
    for report in repetitions:
        if not isinstance(report, dict) or set(report) != {"repetition", "speed_kmh"}:
            raise ValueError("each report requires only repetition and speed_kmh")
        number = report["repetition"]
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= work_count:
            raise ValueError("repetition must identify an observed work repetition")
        if number in seen:
            raise ValueError("duplicate repetition reference")
        seen.add(number)
        rows.append({"repetition": number, "speed_kmh": speed(report["speed_kmh"])})
    return sorted(rows, key=lambda row: row["repetition"])


def replace(
    conn: sqlite3.Connection,
    activity_id: int,
    binding: str,
    work_count: int,
    reports: list[dict[str, Any]],
) -> None:
    """Stage a complete replacement; the application owns commit and recomputation."""
    conn.execute("DELETE FROM treadmill_speed_sets WHERE activity_id=?", (activity_id,))
    conn.execute(
        "INSERT INTO treadmill_speed_sets VALUES (?,?,?)", (activity_id, binding, work_count)
    )
    conn.executemany(
        "INSERT INTO treadmill_speed_reports VALUES (?,?,?)",
        [(activity_id, r["repetition"], r["speed_kmh"]) for r in reports],
    )


def clear(conn: sqlite3.Connection, activity_id: int) -> bool:
    """Stage explicit removal, including stale reports, without reading captured work."""
    return (
        conn.execute(
            "DELETE FROM treadmill_speed_sets WHERE activity_id=?", (activity_id,)
        ).rowcount
        > 0
    )


def read(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any] | None:
    """Read the retained declaration independently of replaceable capture and mart rows."""
    row = conn.execute(
        "SELECT binding,work_count FROM treadmill_speed_sets WHERE activity_id=?", (activity_id,)
    ).fetchone()
    if row is None:
        return None
    reports = [
        {"repetition": number, "speed_kmh": value}
        for number, value in conn.execute(
            "SELECT repetition,speed_kmh FROM treadmill_speed_reports WHERE activity_id=? ORDER BY repetition",
            (activity_id,),
        )
    ]
    return {"binding": row[0], "work_count": row[1], "reports": reports}
