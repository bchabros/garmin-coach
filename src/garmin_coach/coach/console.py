"""Source-aware reconciliation of current manual settings and verified execution."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..core import treadmill
from ..execution import Execution


def read(conn: sqlite3.Connection, observed: Execution) -> dict[str, Any] | None:
    """Expose current manual facts even when the cached metric generation is older."""
    if observed.activity is None:
        return None
    saved = treadmill.read(conn, observed.activity["activity_id"])
    if observed.activity["gtype"] != "treadmill_running" and saved is None:
        return None
    matched = bool(
        saved
        and observed.binding
        and saved["binding"] == observed.binding
        and observed.activity["gtype"] == "treadmill_running"
    )
    retained = (
        [{**r, "pace_s_per_km": 3600 / r["speed_kmh"]} for r in saved["reports"]] if saved else []
    )
    applied = retained if matched else []
    count = len(observed.work) if observed.binding is not None else None
    covered = {r["repetition"] for r in applied}
    return {
        "source": "athlete_reported_treadmill_console",
        "state": "matched" if matched else "stale" if saved else "none",
        "saved_binding": saved["binding"] if saved else None,
        "current_binding": observed.binding,
        "saved_work_count": saved["work_count"] if saved else None,
        "current_work_count": count,
        "reported_count": len(applied),
        "missing_repetitions": [i for i in range(1, count + 1) if i not in covered]
        if count is not None
        else None,
        "reports": retained,
        "applied_reports": applied,
        "caution": "Athlete-reported console setting, not calibrated belt speed or measured pace consistency.",
    }
