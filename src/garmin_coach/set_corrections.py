"""Reviewed recorded-set proposals; account transport is explicit and injected."""

from __future__ import annotations

import datetime as dt
import logging
import math
import sqlite3
from collections.abc import Callable
from typing import Any

from .core import exercise_names, recorded_sets
from .core.evidence import fingerprint as fingerprint
from .core.recorded_sets import WorkingSet
from .workouts.exercises import EXERCISE_MAP

logger = logging.getLogger(__name__)


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
    state = conn.execute(
        "SELECT captured_sha,manual_sha FROM exercise_correction_state WHERE activity_id=?",
        (activity_id,),
    ).fetchone()
    known = bool(state and state[1] == fingerprint(result["manual"]))
    result["baseline_state"] = "known" if known else "unknown"
    result["conflict"] = (
        state[0] != fingerprint(result["captured"]) if known and result["manual"] else None
    )
    result["weight_unit"] = "grams"
    return result


def _identity(name: Any) -> tuple[str, str | None]:
    canonical = exercise_names.normalize(name)
    identities = set(EXERCISE_MAP.values())
    direct = [pair for pair in identities if pair[1] == canonical]
    pair = EXERCISE_MAP.get(canonical.lower())
    if pair is not None:
        return pair[0], None if pair[0] == pair[1] else pair[1]
    if len(direct) == 1:
        category, variant = direct[0]
        return category, None if category == variant else variant
    raise ValueError(f"unresolved curated exercise identity: {canonical}")


def _reported_identity(row: dict[str, Any], index: int | None) -> tuple[str | None, str | None]:
    if "category" not in row:
        if "name" in row:
            return _identity(row["name"])
        if index is not None:
            return None, None
        raise ValueError("omitting name requires an explicit captured_index")
    category = exercise_names.normalize(row["category"])
    if category not in {pair[0] for pair in EXERCISE_MAP.values()}:
        raise ValueError("unresolved curated exercise category")
    name = exercise_names.normalize(row["name"]) if row.get("name") is not None else None
    if name == category:
        name = None
    if name is not None and (category, name) not in EXERCISE_MAP.values():
        raise ValueError("unresolved curated category/variant identity")
    return category, name


def _validated_row(row: Any) -> WorkingSet:
    if not isinstance(row, dict):
        raise ValueError("each working set must be an object")
    if set(row) - {"name", "category", "reps", "weight_kg", "captured_index"}:
        raise ValueError("unsupported working-set field")
    reps, weight, index = row.get("reps"), row.get("weight_kg"), row.get("captured_index")
    if reps is not None and (type(reps) is not int or reps < 0):
        raise ValueError("reps must be a nonnegative integer")
    if weight is not None and (
        type(weight) not in (int, float)
        or not math.isfinite(weight)
        or weight < 0
        or not math.isfinite(weight * 1000)
    ):
        raise ValueError("weight_kg must be a finite nonnegative kilogram value")
    if index is not None and (type(index) is not int or index < 0):
        raise ValueError("captured_index must be a nonnegative integer")
    category, name = _reported_identity(row, index)
    explicit = "captured_index" in row
    if explicit and index is None and (reps is None or weight is None):
        raise ValueError(
            "new working sets require explicit reps and weight_kg; zero weight means bodyweight"
        )
    return {
        "category": category,
        "name": name,
        "reps": reps,
        "weight": round(weight * 1000) if weight is not None else None,
        "captured_index": index,
        "pairing_explicit": explicit,
    }


def validate(rows: list[dict[str, Any]]) -> list[WorkingSet]:
    """Validate reported identities/measurements and complete explicit pairing before transport."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("a nonempty ordered working-set correction is required")
    result = [_validated_row(row) for row in rows]
    explicit = [row["pairing_explicit"] for row in result]
    if any(explicit) and not all(explicit):
        raise ValueError("provide captured_index for every working set or none")
    indices = [row["captured_index"] for row in result if row["captured_index"] is not None]
    if len(set(indices)) != len(indices):
        raise ValueError("duplicate captured_index in working-set pairing")
    return result


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
        if entry.get("startTime") is not None:
            try:
                dt.datetime.fromisoformat(entry["startTime"])
            except (TypeError, ValueError):
                raise ValueError("invalid captured startTime") from None
        for key in ("duration", "weight", "repetitionCount"):
            value = entry.get(key)
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value < 0
                and not (
                    key == "weight"
                    and entry["setType"] == "REST"
                    and value == recorded_sets.UNRECORDED_REST_WEIGHT
                )
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

    New sets use the operator-observed null timing contract and explicit measurements.
    The callback must defer account connection until called, after local validation.
    """
    local = saved(conn, activity_id)
    intended = validate(rows)
    payload = account_payload(activity_id, fetch())
    if any(not isinstance(entry.get("exercises"), list) for entry in payload["exerciseSets"]):
        raise ValueError("malformed captured exercise identities")
    proposal = recorded_sets.propose(payload, intended)
    evidence = {
        "activity_id": activity_id,
        "local": local,
        "account": payload,
        "rows": rows,
        **proposal,
    }
    return {
        **evidence,
        "approval": fingerprint(evidence),
        "weight_unit": "grams",
        "display_weight_unit": "kg",
        "add_delete_verified": True,
    }
