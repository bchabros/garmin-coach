"""Observed external-load records over currently selected comparable working sets."""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from typing import Any

from .. import set_corrections


def _evidence(conn: sqlite3.Connection, aid: int, row: dict[str, Any]) -> dict[str, Any] | None:
    observed = conn.execute(
        "SELECT payload_json FROM exercise_set_observations WHERE activity_id=?", (aid,)
    ).fetchone()
    if observed is None or row["source"] != "captured":
        return None
    entries = [e for e in json.loads(observed[0])["exerciseSets"] if e["setType"] == "ACTIVE"]
    if row["set_idx"] >= len(entries):
        return None
    entry = entries[row["set_idx"]]
    identities = entry.get("exercises") or []
    if len(identities) != 1 or not identities[0].get("name") or entry.get("sets") not in (None, 1):
        return None
    identity = identities[0]
    if (
        identity.get("category"),
        identity["name"],
        entry.get("weight"),
        entry.get("repetitionCount"),
    ) != (row["category"], row["subcategory"], row["max_weight"], row["reps"]):
        return None
    return entry


def _candidate(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    aid, idx = row["activity_id"], row["set_idx"]
    evidence = _evidence(conn, aid, row)
    binding = set_corrections.fingerprint({"selected": row, "source_evidence": evidence})
    condition = conn.execute(
        "SELECT binding,equipment,weight_basis FROM strength_set_conditions WHERE activity_id=? AND set_idx=?",
        (aid, idx),
    ).fetchone()
    reason = None
    weight, reps = row["max_weight"], row["reps"]
    if evidence is None or "ASSIST" in (row["subcategory"] or ""):
        reason = "individual_working_set_identity_unverified"
    elif (
        type(weight) not in (int, float)
        or not math.isfinite(weight)
        or weight <= 0
        or type(reps) is not int
        or reps <= 0
    ):
        reason = "positive_external_weight_and_repetitions_required"
    elif condition is None:
        reason = "comparison_conditions_unknown"
    elif condition[0] != binding:
        reason = "comparison_conditions_stale"
    return {
        **row,
        "binding": binding,
        "weight_kg": weight / 1000
        if isinstance(weight, (int, float)) and math.isfinite(weight)
        else None,
        "reason": reason,
        "equipment": condition[1] if condition else None,
        "weight_basis": condition[2] if condition else None,
        "resistance_mode": "external_load",
        "conditions_source": "athlete_reported" if condition else None,
    }


def _selected(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT s.*,a.date FROM movement_sets s JOIN activities a USING(activity_id) WHERE a.date BETWEEN ? AND ? AND a.gtype IN ('strength_training','hiit') ORDER BY a.date,a.start_local,s.activity_id,s.set_idx",
        (start, end),
    )
    names = [c[0] for c in cur.description]
    return [_candidate(conn, dict(zip(names, row, strict=True))) for row in cur]


def _best(rows: list[dict[str, Any]]) -> dict[str, Any]:
    highest = max(row["weight_kg"] for row in rows)
    return {"weight_kg": highest, "ties": [row for row in rows if row["weight_kg"] == highest]}


def _records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["category"], row["subcategory"], row["equipment"], row["weight_basis"])].append(
            row
        )
    records = []
    for (category, name, equipment, basis), history in groups.items():
        milestones, previous = [], 0.0
        for row in history:
            if row["weight_kg"] > previous:
                milestones.append(row)
                previous = row["weight_kg"]
        repetitions = sorted({row["reps"] for row in history})
        records.append(
            {
                "category": category,
                "exercise": name,
                "equipment": equipment,
                "weight_basis": basis,
                "resistance_mode": "external_load",
                "heaviest": _best(history),
                "by_repetitions": {
                    str(n): _best([row for row in history if row["reps"] == n]) for n in repetitions
                },
                "history": history,
                "record_progression": milestones,
            }
        )
    return records


def read(conn: sqlite3.Connection, *, through_date: str, data_start_date: str) -> dict[str, Any]:
    """Read current bests, ties, history and unranked reasons in one offline snapshot."""
    start, end = dt.date.fromisoformat(data_start_date), dt.date.fromisoformat(through_date)
    if end < start or end > dt.date.today():
        raise ValueError("record horizon must be within real history and today")
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        rows = _selected(conn, start.isoformat(), end.isoformat())
        valid = [row for row in rows if row["reason"] is None]
        return {
            "through_date": through_date,
            "data_start": data_start_date,
            "records": _records(valid),
            "unranked": [row for row in rows if row["reason"]],
            "coverage": {
                "selected_sets": len(rows),
                "ranked_sets": len(valid),
                "unranked_sets": len(rows) - len(valid),
            },
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


def working_set(conn: sqlite3.Connection, activity_id: int, set_idx: int) -> dict[str, Any] | None:
    """Read one currently selected working-set candidate under the caller's snapshot."""
    day = conn.execute("SELECT date FROM activities WHERE activity_id=?", (activity_id,)).fetchone()
    selected = _selected(conn, day[0], day[0]) if day else []
    return next(
        (row for row in selected if (row["activity_id"], row["set_idx"]) == (activity_id, set_idx)),
        None,
    )
