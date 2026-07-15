"""Tool functions behind the coach MCP server (epic #18).

Pure functions over the finished DB and the report artifacts; the protocol
layer in ``mcp_server`` only wires them up. Every response is wrapped in a
freshness envelope so a chat session can never mistake partial same-day data
for final numbers. No new computation happens here: each tool wraps a reader
or a seam the CLI already uses (the golden rule holds - nothing in this
module talks to Garmin).
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from typing import Any

from . import db, digest, periodize, report, snapshot

# Mart fields that accumulate during the day; they are only final after the
# nightly run. Morning-complete streams (sleep, HRV, readiness) never appear.
PARTIAL_INTRADAY_FIELDS = (
    "load_day",
    "acute7",
    "chronic28",
    "acwr",
    "load_low",
    "load_high",
    "load_anaerobic",
    "load_strength",
    "z1_min",
    "z2_min",
    "z3_min",
    "z4_min",
    "z5_min",
    "rhr",
    "rhr_delta",
    "stress_avg",
    "bb_min",
    "bb_max",
    "bb_recharge",
)

# Compact activity projection: enough for "what did I do?", no per-minute series.
_ACTIVITY_COLUMNS = (
    "activity_id",
    "date",
    "gtype",
    "discipline",
    "name",
    "dur_s",
    "distance_m",
    "avg_hr",
    "avg_speed_mps",
    "aero_te",
    "anaero_te",
    "training_load",
)


def _freshness(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the freshness envelope from the mart horizon vs the actual today."""
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    data_through = row[0] if row else None
    today_included = data_through == dt.date.today().isoformat()
    return {
        "data_through": data_through,
        "today_included": today_included,
        "partial_fields": list(PARTIAL_INTRADAY_FIELDS) if today_included else [],
    }


def _wrap(conn: sqlite3.Connection, data: Any) -> dict[str, Any]:
    return {"data": data, "freshness": _freshness(conn)}


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the athlete_status snapshot row (None until features has run)."""
    return _wrap(conn, snapshot.read(conn))


def get_digest(conn: sqlite3.Connection, to_date: str | None = None) -> dict[str, Any]:
    """Build and return the cited digest for a horizon (default: latest mart day)."""
    thresholds = report.read_thresholds(conn)
    return _wrap(conn, digest.build_digest(conn, to_date=to_date, thresholds=thresholds))


def get_recent_activities(conn: sqlite3.Connection, n: int = 10) -> dict[str, Any]:
    """Return the n most recent activities, newest first, as a compact projection."""
    cur = conn.execute(
        f"SELECT {', '.join(_ACTIVITY_COLUMNS)} FROM activities ORDER BY start_local DESC LIMIT ?",
        (n,),
    )
    return _wrap(conn, _rows(cur))


def get_weekly(conn: sqlite3.Connection, week_start: str | None = None) -> dict[str, Any]:
    """Return weekly mart rows plus the plan-vs-actual grid for those weeks."""
    if week_start is None:
        weeks = _rows(conn.execute("SELECT * FROM weekly_metrics ORDER BY week_start"))
        plan_actual = _rows(
            conn.execute("SELECT * FROM weekly_plan_actual ORDER BY week_start, date")
        )
    else:
        weeks = _rows(
            conn.execute("SELECT * FROM weekly_metrics WHERE week_start = ?", (week_start,))
        )
        plan_actual = _rows(
            conn.execute(
                "SELECT * FROM weekly_plan_actual WHERE week_start = ? ORDER BY date",
                (week_start,),
            )
        )
    return _wrap(conn, {"weeks": weeks, "plan_actual": plan_actual})


def get_zones(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the current athlete_zones row (anchor, bounds, paces, staleness)."""
    rows = _rows(conn.execute("SELECT * FROM athlete_zones WHERE id = 1"))
    return _wrap(conn, rows[0] if rows else None)


def get_recommendation(conn: sqlite3.Connection, date: str | None = None) -> dict[str, Any]:
    """Return the session recommendation block targeting ``date`` (default: tomorrow).

    Mirrors the author path: the digest horizon is the day before the target,
    and the digest's embedded recommendation block is returned as-is.
    """
    to_date = None
    if date is not None:
        to_date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    thresholds = report.read_thresholds(conn)
    dg = digest.build_digest(conn, to_date=to_date, thresholds=thresholds)
    return _wrap(conn, dg.get("recommendation"))


def get_events(conn: sqlite3.Connection, today: str | None = None) -> dict[str, Any]:
    """Return the goal races annotated with countdowns and the anchor flag."""
    day = today or dt.date.today().isoformat()
    return _wrap(conn, periodize.annotate(db.list_goal_events(conn), day))


def get_workout_status(
    conn: sqlite3.Connection, date: str, reports_dir: str = "reports"
) -> dict[str, Any]:
    """Return the authored spec and push receipt for a date (None when absent)."""
    day_dir = pathlib.Path(reports_dir) / date
    workout = _read_json(day_dir / "workout.json")
    push = _read_json(day_dir / "push.json")
    return _wrap(conn, {"date": date, "workout": workout, "push": push})


def _read_json(path: pathlib.Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())
