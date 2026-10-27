"""Captured lap/group evidence, normalized independently of its interpretation."""

from __future__ import annotations

import math
import sqlite3
from typing import Any

_FIELDS = {
    "start_gmt": "startTimeGMT",
    "dur_s": "duration",
    "moving_dur_s": "movingDuration",
    "elapsed_dur_s": "elapsedDuration",
    "distance_m": "distance",
    "avg_speed_mps": "averageSpeed",
    "avg_hr": "averageHR",
    "max_hr": "maxHR",
}
_LAP_FIELDS = {
    "lap_index": "lapIndex",
    "message_index": "messageIndex",
    "wkt_step_index": "wktStepIndex",
    "wkt_index": "wktIndex",
    "intensity_type": "intensityType",
}


def _scalar(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -(2**63) <= value < 2**63 else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value if isinstance(value, str) else None


def normalize(
    activity_id: int, payload: dict[str, Any], *, typed: bool = False
) -> list[dict[str, Any]]:
    """Normalize captured rows to scalar observations, retaining unknown fields as null.

    Missing fields and malformed individual rows never raise; source indices are
    observations, while row_idx is only storage identity within this response.
    """
    rows = payload.get("splits" if typed else "lapDTOs")
    if not isinstance(rows, list):
        return []
    fields = _FIELDS | (
        {"source_type": "type", "message_index": "messageIndex"} if typed else _LAP_FIELDS
    )
    return [
        {
            "activity_id": activity_id,
            "row_idx": i,
            **{key: _scalar(row.get(source)) for key, source in fields.items()},
        }
        for i, value in enumerate(rows)
        for row in [value if isinstance(value, dict) else {}]
    ]


def normalize_members(activity_id: int, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Retain explicit typed-split membership without inferring missing references."""
    rows = payload.get("splits")
    if not isinstance(rows, list):
        return []
    members: list[dict[str, Any]] = []
    for split_idx, row in enumerate(rows):
        refs = row.get("lapIndexes") if isinstance(row, dict) else None
        if isinstance(refs, list):
            members.extend(
                {
                    "activity_id": activity_id,
                    "split_idx": split_idx,
                    "member_idx": i,
                    "lap_index": _scalar(ref),
                }
                for i, ref in enumerate(refs)
            )
    return members


def _insert(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        columns = ",".join(row)
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )


def replace(
    conn: sqlite3.Connection,
    activity_id: int,
    captured: dict[str, Any],
    typed: dict[str, Any],
) -> None:
    """Atomically replace one complete capture and invalidate its old derived summary.

    The caller owns the surrounding import transaction. A savepoint also preserves
    the prior set if replacement fails before that transaction finishes.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT replace_laps")
    try:
        for table in (
            "activity_interval_summaries",
            "activity_split_laps",
            "activity_splits",
            "activity_laps",
        ):
            conn.execute(f"DELETE FROM {table} WHERE activity_id=?", (activity_id,))
        _insert(conn, "activity_laps", normalize(activity_id, captured))
        _insert(conn, "activity_splits", normalize(activity_id, typed, typed=True))
        _insert(conn, "activity_split_laps", normalize_members(activity_id, typed))
    except BaseException:
        conn.execute("ROLLBACK TO replace_laps")
        raise
    finally:
        conn.execute("RELEASE replace_laps")


def rows(conn: sqlite3.Connection, table: str, activity_id: int) -> list[dict[str, Any]]:
    """Read ordered captured evidence from one of the three owned tables."""
    if table not in ("activity_laps", "activity_splits", "activity_split_laps"):
        raise ValueError("unknown captured-lap table")
    order = "split_idx, member_idx" if table == "activity_split_laps" else "row_idx"
    cur = conn.execute(
        f"SELECT * FROM {table} WHERE activity_id=? ORDER BY {order}", (activity_id,)
    )
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row)) for row in cur]
