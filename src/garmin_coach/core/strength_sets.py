"""Selected strength evidence and explicitly declared comparison conditions."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from typing import Any

from .evidence import fingerprint

logger = logging.getLogger(__name__)

EXTERNAL_LOAD = "external_load"
ASSISTED_LOAD = "assisted_load"
BODYWEIGHT_ONLY = "bodyweight_only"
RESISTANCE_MODES = (EXTERNAL_LOAD, ASSISTED_LOAD, BODYWEIGHT_ONLY)
WEIGHT_BASES = ("total", "per_hand")


def _entry_reason(entry: dict[str, Any], row: dict[str, Any]) -> str | None:
    if entry.get("sets") not in (None, 1):
        return "legacy_circuit_aggregate"
    identities = entry.get("exercises") or []
    if (
        not isinstance(identities, list)
        or len(identities) != 1
        or not isinstance(identities[0], dict)
        or not identities[0].get("name")
        or not identities[0].get("category")
    ):
        return "working_set_identity_unverified"
    identity = identities[0]
    if (
        identity["category"],
        identity["name"],
        entry.get("weight"),
        entry.get("repetitionCount"),
    ) != (row["category"], row["subcategory"], row["max_weight"], row["reps"]):
        return "source_evidence_mismatch"
    return None


def _evidence(
    conn: sqlite3.Connection, row: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    if row["source"] != "captured":
        return None, "legacy_circuit_aggregate"
    observed = conn.execute(
        "SELECT payload_json FROM exercise_set_observations WHERE activity_id=?",
        (row["activity_id"],),
    ).fetchone()
    if observed is None:
        return None, "source_observation_missing"
    try:
        payload = json.loads(observed[0])
        entries = payload["exerciseSets"]
        if payload["activityId"] != row["activity_id"] or not isinstance(entries, list):
            return None, "source_observation_invalid"
        if any(not isinstance(e, dict) for e in entries):
            return None, "source_observation_invalid"
        active = [entry for entry in entries if entry.get("setType") == "ACTIVE"]
    except (ValueError, TypeError, KeyError):
        return None, "source_observation_invalid"
    if not 0 <= row["set_idx"] < len(active):
        return None, "source_set_missing"
    entry = active[row["set_idx"]]
    reason = _entry_reason(entry, row)
    return (None, reason) if reason else (entry, None)


def _grams(value: Any) -> int | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return round(value)


def _assistance_indicated(name: str | None) -> bool:
    # Conservative hint only, never a substitute for declared resistance mode.
    return bool({"ASSIST", "ASSISTED", "ASSISTANCE"}.intersection((name or "").split("_")))


def _rank_reason(row: dict[str, Any], condition: Any, binding: str) -> str | None:
    if condition and condition[0] != binding:
        return "comparison_conditions_stale"
    mode = condition[3] if condition else None
    if mode == ASSISTED_LOAD:
        return "assisted_load_unsupported"
    if mode == BODYWEIGHT_ONLY:
        return "bodyweight_only_unsupported"
    if _assistance_indicated(row["subcategory"]):
        return "assistance_indicated_by_exercise_name"
    if (_grams(row["max_weight"]) or 0) <= 0:
        return "positive_external_weight_required"
    if type(row["reps"]) is not int or row["reps"] <= 0:
        return "positive_repetitions_required"
    if mode is None:
        return "comparison_conditions_unknown"
    return None


def _candidate(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    aid, idx = row["activity_id"], row["set_idx"]
    evidence, reason = _evidence(conn, row)
    binding = fingerprint({"selected": row, "source_evidence": evidence})
    condition = conn.execute(
        "SELECT binding,equipment,weight_basis,resistance_mode FROM strength_set_conditions "
        "WHERE activity_id=? AND set_idx=?",
        (aid, idx),
    ).fetchone()
    current = condition is not None and condition[0] == binding
    weight = _grams(row["max_weight"])
    return {
        **row,
        "binding": binding,
        "declarable": evidence is not None,
        "weight_grams": weight,
        "weight_kg": weight / 1000 if weight is not None else None,
        "reason": reason or _rank_reason(row, condition, binding),
        "equipment": condition[1] if current else None,
        "weight_basis": condition[2] if current else None,
        "resistance_mode": condition[3] if current else None,
        "conditions_source": "athlete_reported" if current else None,
        "conditions_state": "stale"
        if condition and not current
        else "current"
        if current
        else "unknown",
    }


def selected(conn: sqlite3.Connection, start: str, end: str) -> list[dict[str, Any]]:
    """Read selected candidates under a caller-owned coherent snapshot."""
    cur = conn.execute(
        "SELECT s.*,a.date FROM movement_sets s JOIN activities a USING(activity_id) "
        "WHERE a.date BETWEEN ? AND ? AND a.gtype IN ('strength_training','hiit') "
        "ORDER BY a.date,a.start_local,s.activity_id,s.set_idx",
        (start, end),
    )
    names = [c[0] for c in cur.description]
    return [_candidate(conn, dict(zip(names, row, strict=True))) for row in cur]


def working_set(conn: sqlite3.Connection, activity_id: int, set_idx: int) -> dict[str, Any] | None:
    """Read one selected candidate under the caller's snapshot or write transaction."""
    day = conn.execute("SELECT date FROM activities WHERE activity_id=?", (activity_id,)).fetchone()
    rows = selected(conn, day[0], day[0]) if day else []
    return next(
        (row for row in rows if (row["activity_id"], row["set_idx"]) == (activity_id, set_idx)),
        None,
    )
