"""Explicit local comparison-condition changes, with no account transport."""

from __future__ import annotations

import sqlite3
from typing import Any

from .coach import strength_records


def declare(
    conn: sqlite3.Connection,
    activity_id: int,
    set_idx: int,
    binding: str,
    *,
    equipment: str,
    weight_basis: str,
    resistance_mode: str,
) -> dict[str, Any]:
    """Save explicit external-load comparison conditions against unchanged selected work."""
    if (
        resistance_mode != "external_load"
        or not isinstance(equipment, str)
        or not equipment.strip()
        or weight_basis not in {"total", "per_hand"}
    ):
        raise ValueError("explicit equipment and total/per_hand external-load basis are required")
    if conn.in_transaction:
        raise ValueError("comparison declaration requires its own transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = strength_records.working_set(conn, activity_id, set_idx)
        if (
            row is None
            or row["binding"] != binding
            or row["reason"]
            not in {None, "comparison_conditions_unknown", "comparison_conditions_stale"}
        ):
            raise ValueError("stale or unverified working-set evidence; read records again")
        conn.execute(
            "INSERT INTO strength_set_conditions(activity_id,set_idx,binding,equipment,weight_basis) VALUES(?,?,?,?,?) ON CONFLICT(activity_id,set_idx) DO UPDATE SET binding=excluded.binding,equipment=excluded.equipment,weight_basis=excluded.weight_basis,declared_at=CURRENT_TIMESTAMP",
            (activity_id, set_idx, binding, equipment.strip().lower(), weight_basis),
        )
        conn.commit()
        return {
            "saved": True,
            "activity_id": activity_id,
            "set_idx": set_idx,
            "conditions_source": "athlete_reported",
        }
    except BaseException:
        conn.rollback()
        raise
