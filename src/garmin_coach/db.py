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
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _schema_sql() -> str:
    return importlib.resources.files("garmin_coach").joinpath("schema.sql").read_text()


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create all tables/views idempotently (CREATE ... IF NOT EXISTS)."""
    conn.executescript(_schema_sql())
    conn.commit()


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


def _upsert(conn: sqlite3.Connection, table: str, row: dict[str, Any], pk: str) -> None:
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != pk)
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({pk}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_activity(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    _upsert(conn, "activities", row, pk="activity_id")


def upsert_daily(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    """Upsert a one-row-per-date table (sleep, hrv_nightly, daily_wellness, ...)."""
    _upsert(conn, table, row, pk="date")
