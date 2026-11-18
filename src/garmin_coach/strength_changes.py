"""Explicit local comparison-condition changes, with no account transport."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from .core import strength_sets

logger = logging.getLogger(__name__)


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
    """Save explicit resistance mode and comparison conditions against unchanged work."""
    if (
        resistance_mode not in strength_sets.RESISTANCE_MODES
        or not isinstance(equipment, str)
        or not equipment.strip()
        or weight_basis not in strength_sets.WEIGHT_BASES
    ):
        raise ValueError(
            "explicit equipment, resistance mode and total/per_hand basis are required"
        )
    if conn.in_transaction:
        raise ValueError("comparison declaration requires its own transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = strength_sets.working_set(conn, activity_id, set_idx)
        if row is None or row["binding"] != binding or not row["declarable"]:
            raise ValueError("stale or unverified working-set evidence; read records again")
        conn.execute(
            "INSERT INTO strength_set_conditions "
            "(activity_id,set_idx,binding,equipment,weight_basis,resistance_mode) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(activity_id,set_idx) DO UPDATE SET binding=excluded.binding,"
            "equipment=excluded.equipment,weight_basis=excluded.weight_basis,"
            "resistance_mode=excluded.resistance_mode,declared_at=CURRENT_TIMESTAMP",
            (
                activity_id,
                set_idx,
                binding,
                equipment.strip().lower(),
                weight_basis,
                resistance_mode,
            ),
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
