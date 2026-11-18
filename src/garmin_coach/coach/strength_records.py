"""Observed external-load records over currently selected comparable working sets."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from collections import defaultdict
from typing import Any

from ..core import set_observations, strength_sets
from ..core.config import DEFAULT_RECHECK_DAYS

logger = logging.getLogger(__name__)


def _best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    highest = max(row["weight_grams"] for row in rows)
    return {
        "weight_kg": highest / 1000,
        "ties": [row for row in rows if row["weight_grams"] == highest],
        "record_progression": _progression(rows),
    }


def _progression(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    milestones, previous = [], 0
    for row in rows:
        if row["weight_grams"] > previous:
            milestones.append(row)
            previous = row["weight_grams"]
    return milestones


def _records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["category"], row["subcategory"], row["equipment"], row["weight_basis"])].append(
            row
        )
    records = []
    for (category, name, equipment, basis), history in groups.items():
        repetitions = sorted({row["reps"] for row in history})
        records.append(
            {
                "category": category,
                "exercise": name,
                "equipment": equipment,
                "weight_basis": basis,
                "resistance_mode": strength_sets.EXTERNAL_LOAD,
                "heaviest": _best(history),
                "by_repetitions": {
                    str(n): _best([row for row in history if row["reps"] == n]) for n in repetitions
                },
                "history": history,
                "record_progression": _progression(history),
            }
        )
    return records


def _coverage(
    conn: sqlite3.Connection, rows: list[dict[str, Any]], start: str, end: str, recent_days: int
) -> dict[str, Any]:
    observations = set_observations.activities(conn, from_date=start, through_date=end)
    missing = [a["activity_id"] for a in observations if a["checked_on"] is None]
    empty = [a["activity_id"] for a in observations if a["source_active_sets"] == 0]
    missing_rows = [
        a["activity_id"]
        for a in observations
        if a["source_active_sets"] is not None and a["source_active_sets"] > a["captured_sets"]
    ]
    ranked = sum(r["reason"] is None for r in rows)
    return {
        "selected_sets": len(rows),
        "ranked_sets": ranked,
        "unranked_sets": len(rows) - ranked,
        "eligible_activities": len(observations),
        "activities": observations,
        "source_missing_activity_ids": missing,
        "observed_empty_activity_ids": empty,
        "source_rows_missing_activity_ids": missing_rows,
        "history_rechecks": set_observations.coverage(
            conn, through_date=end, data_start_date=start, recent_days=recent_days
        ),
        "scope": "Known strength/HIIT activities only; missing activity imports cannot be inferred.",
    }


def read(
    conn: sqlite3.Connection,
    *,
    through_date: str,
    data_start_date: str,
    today: dt.date,
    recent_days: int = DEFAULT_RECHECK_DAYS,
) -> dict[str, Any]:
    """Read current bests, ties, history and unranked reasons in one offline snapshot."""
    start, end = dt.date.fromisoformat(data_start_date), dt.date.fromisoformat(through_date)
    if end < start or end > today:
        raise ValueError("record horizon must be within real history and today")
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        rows = strength_sets.selected(conn, start.isoformat(), end.isoformat())
        valid = [row for row in rows if row["reason"] is None]
        return {
            "through_date": through_date,
            "data_start": data_start_date,
            "records": _records(valid),
            "unranked": [row for row in rows if row["reason"]],
            "coverage": _coverage(conn, rows, start.isoformat(), end.isoformat(), recent_days),
            "definition": "Best in available comparable recorded working-set history, not a lifetime-best guarantee.",
            "garmin_pr_relation": "Local observed-result calculations; Garmin personal records are independent.",
            "unsupported": [
                "estimated_1rm",
                "bodyweight_only",
                "assisted_load",
                "legacy_circuit_aggregates",
            ],
        }
    finally:
        if started:
            conn.rollback()
