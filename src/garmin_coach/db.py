"""SQLite connection, schema bootstrap, and idempotent upsert helpers.

Schema is shipped as a package resource (src/garmin_coach/schema.sql) and loaded
via importlib.resources so the package is self-contained. Upserts key on the
primary key (activity_id / date) so re-running a backfill converges, never
duplicates. raw_payloads is append-only by design.
"""

from __future__ import annotations

import datetime as _dt
import importlib.resources
import sqlite3
from typing import Any


def connect(path: str) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _schema_sql() -> str:
    return importlib.resources.files("garmin_coach").joinpath("schema.sql").read_text()


# Columns added to pre-existing core tables after their first release. CREATE ...
# IF NOT EXISTS cannot add a column to a table that already exists, so bootstrap
# backfills these with ALTER for DBs created by an earlier schema version.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "activities": {"temp_c": "REAL"},  # Phase 6: per-activity temperature
    "daily_metrics": {"load_strength": "REAL"},  # Phase 7: blended strength load
    "weekly_metrics": {  # Phase 7: weekly strength load + its share
        "load_strength": "REAL",
        "strength_share": "REAL",
    },
}


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create all tables/views idempotently, then add any missing columns."""
    conn.executescript(_schema_sql())
    _migrate_columns(conn)
    conn.commit()


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """Add later-introduced columns to tables that predate them (idempotent)."""
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def insert_raw(
    conn: sqlite3.Connection,
    endpoint: str,
    ref_date: str,
    payload: str,
    fetched_at: str | None = None,
) -> None:
    """Append a raw payload. Never overwrites (PK includes fetched_at)."""
    fetched_at = fetched_at or _dt.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR IGNORE INTO raw_payloads(fetched_at, endpoint, ref_date, payload) "
        "VALUES (?,?,?,?)",
        (fetched_at, endpoint, ref_date, payload),
    )


def get_sync_watermark(conn: sqlite3.Connection, stream: str) -> str | None:
    """Return the last synced date for a stream, if it has been initialized."""
    row = conn.execute(
        "SELECT last_synced_date FROM sync_state WHERE stream=?", (stream,)
    ).fetchone()
    return row[0] if row else None


def set_sync_watermark(conn: sqlite3.Connection, stream: str, last_synced_date: str) -> None:
    """Store a stream watermark idempotently."""
    conn.execute(
        """
        INSERT INTO sync_state(stream, last_synced_date, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(stream) DO UPDATE SET
            last_synced_date=excluded.last_synced_date,
            updated_at=excluded.updated_at
        """,
        (stream, last_synced_date, _dt.datetime.now().isoformat(timespec="seconds")),
    )


def bootstrap_sync_watermark(
    conn: sqlite3.Connection, stream: str, core_table: str, data_start_date: str
) -> str:
    """Initialize a missing stream watermark from core data or the data start date."""
    existing = get_sync_watermark(conn, stream)
    if existing is not None:
        return existing

    row = conn.execute(f"SELECT MAX(date) FROM {core_table}").fetchone()
    watermark = row[0] if row and row[0] else (
        _dt.date.fromisoformat(data_start_date) - _dt.timedelta(days=1)
    ).isoformat()
    set_sync_watermark(conn, stream, watermark)
    return watermark


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], pk: str) -> None:
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != pk)
    conflict = f"DO UPDATE SET {updates}" if updates else "DO NOTHING"
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) {conflict}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_activity(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert an `activities` row by activity ID."""
    _upsert(conn, "activities", row, pk="activity_id")


def upsert_daily(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Upsert a one-row-per-date table (sleep, hrv_nightly, daily_wellness, ...)."""
    _upsert(conn, table, row, pk="date")


def upsert_session_rpe(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `session_rpe` row by activity ID (re-logging corrects it)."""
    _upsert(conn, "session_rpe", row, pk="activity_id")


def upsert_zones(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert the singleton `athlete_zones` mart row (id=1)."""
    _upsert(conn, "athlete_zones", row, pk="id")


def upsert_status(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert the singleton `athlete_status` snapshot mart row (id=1)."""
    _upsert(conn, "athlete_status", row, pk="id")


def upsert_weekly(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `weekly_metrics` row by week_start (the Monday)."""
    _upsert(conn, "weekly_metrics", row, pk="week_start")


def replace_weekly_plan_actual(
    conn: sqlite3.Connection, week_start: str, rows: list[dict[str, Any]]
) -> None:
    """Replace per-day plan-vs-actual facts for one completed week."""
    conn.execute("DELETE FROM weekly_plan_actual WHERE week_start = ?", (week_start,))
    conn.executemany(
        """
        INSERT INTO weekly_plan_actual(week_start, dow, date, planned, actual, matched)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                week_start,
                row["dow"],
                row["date"],
                row["planned"],
                row["actual"],
                1 if row["match"] else 0,
            )
            for row in rows
        ],
    )
