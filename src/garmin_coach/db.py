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


def _insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    """Bulk-insert row dicts into a table (columns taken from the first row's keys)."""
    if not rows:
        return
    cols = list(rows[0].keys())
    placeholders = ",".join("?" for _ in cols)
    conn.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})",
        [[row[c] for c in cols] for row in rows],
    )


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


def replace_activity_sets(
    conn: sqlite3.Connection, activity_id: int, rows: list[dict[str, Any]]
) -> None:
    """Replace one activity's `activity_sets` rows (idempotent re-fetch).

    Replace-all semantics keyed on ``activity_id``: a re-fetch fully supersedes the
    prior sets, so a changed set count never leaves orphan rows behind.
    """
    conn.execute("DELETE FROM activity_sets WHERE activity_id=?", (activity_id,))
    _insert_rows(conn, "activity_sets", rows)


def upsert_daily(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Upsert a one-row-per-date table (sleep, hrv_nightly, daily_wellness, ...)."""
    _upsert(conn, table, row, pk="date")


def upsert_session_rpe(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `session_rpe` row by activity ID (re-logging corrects it)."""
    _upsert(conn, "session_rpe", row, pk="activity_id")


def upsert_niggle(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `niggle` row by (date, body_part); a re-log updates severity/note."""
    conn.execute(
        "INSERT INTO niggle(date, body_part, severity, note) VALUES (?,?,?,?) "
        "ON CONFLICT(date, body_part) DO UPDATE SET "
        "severity=excluded.severity, note=excluded.note",
        (row["date"], row["body_part"], row["severity"], row.get("note")),
    )


_GOAL_EVENT_COLUMNS = (
    "id", "date", "type", "priority", "status", "date_precision", "target_s", "note",
)

_GOAL_EVENT_UPDATABLE = frozenset(_GOAL_EVENT_COLUMNS) - {"id"}


def upsert_goal_event(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `goal_event` row by (date, type); re-adding the same race converges."""
    conn.execute(
        "INSERT INTO goal_event(date, type, priority, status, date_precision, target_s, note) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(date, type) DO UPDATE SET "
        "priority=excluded.priority, status=excluded.status, "
        "date_precision=excluded.date_precision, target_s=excluded.target_s, "
        "note=excluded.note",
        (
            row["date"], row["type"], row["priority"], row["status"],
            row["date_precision"], row.get("target_s"), row.get("note"),
        ),
    )


def update_goal_event(conn: sqlite3.Connection, event_id: int, **fields: Any) -> None:
    """Partially update one `goal_event` row (e.g. pin a date, confirm a start).

    Args:
        conn: Open SQLite connection.
        event_id: The event's `id`.
        **fields: Any of the goal-event columns except `id`; omitted ones keep
            their stored value.

    Raises:
        ValueError: If a field is not an updatable goal-event column, or if no event
            has that id (a typo'd id must not read as a successful no-op).
    """
    unknown = set(fields) - _GOAL_EVENT_UPDATABLE
    if unknown:
        raise ValueError(f"unknown goal_event field(s): {','.join(sorted(unknown))}")
    if not fields:
        return
    assignments = ",".join(f"{name}=?" for name in fields)
    cursor = conn.execute(
        f"UPDATE goal_event SET {assignments} WHERE id=?",
        [*fields.values(), event_id],
    )
    if cursor.rowcount == 0:
        raise ValueError(f"no goal event with id {event_id}; run `garmin-coach event list`")


def list_goal_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every recorded goal event, soonest first."""
    rows = conn.execute(
        f"SELECT {','.join(_GOAL_EVENT_COLUMNS)} FROM goal_event ORDER BY date"
    ).fetchall()
    return [dict(zip(_GOAL_EVENT_COLUMNS, row, strict=True)) for row in rows]


def upsert_zones(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert the singleton `athlete_zones` mart row (id=1)."""
    _upsert(conn, "athlete_zones", row, pk="id")


def upsert_status(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert the singleton `athlete_status` snapshot mart row (id=1)."""
    _upsert(conn, "athlete_status", row, pk="id")


def upsert_weekly(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """Upsert a `weekly_metrics` row by week_start (the Monday)."""
    _upsert(conn, "weekly_metrics", row, pk="week_start")


def replace_plan_block(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """Rebuild the `plan_block` mart wholesale; an empty plan clears it."""
    conn.execute("DELETE FROM plan_block")
    _insert_rows(conn, "plan_block", rows)


def replace_pattern_overlap(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    """Rebuild the `pattern_overlap` mart wholesale from a fresh computation."""
    conn.execute("DELETE FROM pattern_overlap")
    _insert_rows(conn, "pattern_overlap", rows)


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
