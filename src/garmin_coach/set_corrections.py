"""Reviewed recorded-set proposals; account transport is explicit and injected."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import sqlite3
from collections.abc import Callable
from typing import Any, TypedDict

from .core import exercise_names
from .workouts.exercises import EXERCISE_MAP

logger = logging.getLogger(__name__)


class WorkingSet(TypedDict):
    """Validated identity, optional measurements and reviewed captured pairing."""

    category: str
    name: str
    reps: int | None
    weight: float | None
    captured_index: int | None


def fingerprint(value: Any) -> str:
    """Identify complete reviewed evidence, independent of JSON key ordering."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def saved(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Read one coherent saved generation, preserving caller-owned transactions."""
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        return _saved(conn, activity_id)
    finally:
        if started:
            conn.rollback()


def _saved(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Read captured/manual exercise evidence without opening an account session."""
    info = conn.execute(
        "SELECT date,gtype FROM activities WHERE activity_id=?", (activity_id,)
    ).fetchone()
    if info is None or info[1] not in {"strength_training", "hiit"}:
        raise ValueError("an existing strength/HIIT activity is required")
    result: dict[str, Any] = {"activity_id": activity_id, "date": info[0]}
    for label, table in [("captured", "activity_sets"), ("manual", "manual_activity_sets")]:
        cur = conn.execute(
            f"SELECT * FROM {table} WHERE activity_id=? ORDER BY set_idx", (activity_id,)
        )
        names = [c[0] for c in cur.description]
        result[label] = [dict(zip(names, row, strict=True)) for row in cur]
    result["baseline_state"] = "unknown"
    result["weight_unit"] = "grams"
    return result


def _identity(name: Any) -> tuple[str, str]:
    canonical = exercise_names.normalize(name)
    identities = set(EXERCISE_MAP.values())
    direct = [pair for pair in identities if pair[1] == canonical]
    pair = EXERCISE_MAP.get(canonical.lower())
    if pair is not None:
        return pair
    if len(direct) == 1:
        return direct[0]
    raise ValueError(f"unresolved curated exercise identity: {canonical}")


def validate(rows: list[dict[str, Any]]) -> list[WorkingSet]:
    """Validate one explicit row per intended working set before any account read."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("a nonempty ordered working-set correction is required")
    result: list[WorkingSet] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each working set must be an object")
        if set(row) - {"name", "reps", "weight_kg", "captured_index"}:
            raise ValueError("unsupported working-set field")
        category, name = _identity(row.get("name"))
        reps, weight = row.get("reps"), row.get("weight_kg")
        if reps is not None and (type(reps) is not int or reps < 0):
            raise ValueError("reps must be a nonnegative integer")
        if weight is not None and (
            type(weight) not in (int, float)
            or not math.isfinite(weight)
            or weight < 0
            or not math.isfinite(weight * 1000)
        ):
            raise ValueError("weight_kg must be a finite nonnegative kilogram value")
        index = row.get("captured_index")
        if index is not None and (type(index) is not int or index < 0):
            raise ValueError("captured_index must be a nonnegative integer")
        result.append(
            {
                "category": category,
                "name": name,
                "reps": reps,
                "weight": round(weight * 1000) if weight is not None else None,
                "captured_index": index,
            }
        )
    indices = [r["captured_index"] for r in result]
    if any(i is not None for i in indices):
        if any(i is None for i in indices):
            raise ValueError("provide captured_index for every working set or none")
        if len(set(indices)) != len(indices):
            raise ValueError("duplicate captured_index in working-set pairing")
    return result


def _matches(row: WorkingSet, entry: dict[str, Any]) -> bool:
    return any(
        e.get("category") == row["category"] and e.get("name") == row["name"]
        for e in entry.get("exercises", [])
        if isinstance(e, dict)
    )


def _pair(intended: list[WorkingSet], active: list[dict[str, Any]]) -> list[tuple[int, str]]:
    if len(active) != len(intended):
        raise ValueError("add/delete format and unrecorded timing require operator verification")
    explicit = [r["captured_index"] for r in intended if r["captured_index"] is not None]
    if len(explicit) == len(intended):
        if sorted(explicit) != list(range(len(active))):
            raise ValueError("captured_index must refer to an existing active set")
        return [(i, "explicit") for i in explicit]
    paired: dict[int, tuple[int, str]] = {}
    available = set(range(len(active)))
    for with_weight in (True, False):
        candidates = {
            i: [
                j
                for j in sorted(available)
                if _matches(row, active[j])
                and (
                    not with_weight
                    or row["weight"] is not None
                    and row["weight"] == active[j].get("weight")
                )
            ]
            for i, row in enumerate(intended)
            if i not in paired
        }
        for i, matches in candidates.items():
            if len(matches) == 1 and sum(matches[0] in m for m in candidates.values()) == 1:
                j = matches[0]
                paired[i] = (j, "identity_and_weight" if with_weight else "identity")
                available.remove(j)
    for i in range(len(intended)):
        if i not in paired:
            j = min(available)
            paired[i] = (j, "order_fallback_review_required")
            available.remove(j)
    return [paired[i] for i in range(len(intended))]


def _display(entry: dict[str, Any]) -> dict[str, Any]:
    weight = entry.get("weight")
    return {
        "exercises": [
            {k: e.get(k) for k in ("category", "name")} for e in entry.get("exercises", [])
        ],
        "reps": entry.get("repetitionCount"),
        "weight_kg": weight / 1000 if weight is not None else None,
        "start_time": entry.get("startTime"),
        "duration": entry.get("duration"),
    }


def account_payload(activity_id: int, payload: Any) -> dict[str, Any]:
    """Refuse malformed/wrong-activity evidence instead of treating it as empty."""
    if not isinstance(payload, dict) or payload.get("activityId") != activity_id:
        raise ValueError("missing or wrong-activity account evidence")
    entries = payload.get("exerciseSets")
    if not isinstance(entries, list) or any(
        not isinstance(e, dict) or e.get("setType") not in {"ACTIVE", "REST"} for e in entries
    ):
        raise ValueError("malformed account exercise sets")
    for entry in entries:
        for key in ("duration", "weight", "repetitionCount"):
            value = entry.get(key)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"invalid captured {key}")
        exercises = entry.get("exercises")
        if exercises is not None and (
            not isinstance(exercises, list) or any(not isinstance(e, dict) for e in exercises)
        ):
            raise ValueError("malformed captured exercise identities")
    return payload


def preview(
    conn: sqlite3.Connection, activity_id: int, rows: list[dict[str, Any]], fetch: Callable[[], Any]
) -> dict[str, Any]:
    """Read the account explicitly and bind a timing-preserving set proposal.

    Add/delete transport is refused until operator evidence establishes its format.
    The callback must defer account connection until called, after local validation.
    """
    local = saved(conn, activity_id)
    intended = validate(rows)
    payload = account_payload(activity_id, fetch())
    proposed = copy.deepcopy(payload)
    active = [entry for entry in proposed["exerciseSets"] if entry["setType"] == "ACTIVE"]
    if any(not isinstance(e.get("exercises"), list) or len(e["exercises"]) != 1 for e in active):
        raise ValueError("exactly one captured exercise per working set is required")
    paired = _pair(intended, active)
    changes = []
    for row, (idx, basis) in zip(intended, paired, strict=True):
        entry = active[idx]
        change = {
            "captured_index": idx,
            "pairing_basis": basis,
            "name_matches": _matches(row, entry),
            "weight_matches": row["weight"] == entry.get("weight")
            if row["weight"] is not None
            else None,
            "before": _display(entry),
        }
        exercises = entry.get("exercises")
        exercises[0].update(category=row["category"], name=row["name"])
        for key, value in [("repetitionCount", row["reps"]), ("weight", row["weight"])]:
            if value is not None:
                entry[key] = value
        changes.append({**change, "after": _display(entry)})
    evidence = {
        "activity_id": activity_id,
        "local": local,
        "account": payload,
        "rows": rows,
        "pairing": [idx for idx, _ in paired],
        "changes": changes,
        "proposed": proposed,
    }
    return {
        **evidence,
        "approval": fingerprint(evidence),
        "weight_unit": "grams",
        "display_weight_unit": "kg",
        "add_delete_verified": False,
    }
