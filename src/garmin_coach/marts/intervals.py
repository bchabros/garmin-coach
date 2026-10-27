"""Descriptive repetition facts from explicitly captured execution groups only."""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from typing import Any

from ..core import laps

RUNNING_TYPES = frozenset(("running", "trail_running", "treadmill_running"))
_ROLES = {
    "INTERVAL_ACTIVE": "ACTIVE",
    "INTERVAL_RECOVERY": "RECOVERY",
    "INTERVAL_WARMUP": "WARMUP",
    "INTERVAL_COOLDOWN": "COOLDOWN",
}


def _number(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return float(value)
    return None


def _start(row: dict[str, Any]) -> dt.datetime | None:
    value = row.get("start_gmt")
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(dt.timezone.utc)
            return parsed.replace(tzinfo=None)
        except ValueError:
            pass
    return None


def _members(
    group: dict[str, Any],
    refs: list[dict[str, Any]],
    captured: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    indices = [r["lap_index"] for r in refs if r["split_idx"] == group["row_idx"]]
    if not indices or len(set(indices)) != len(indices):
        return None
    result = [r for r in captured if r["lap_index"] in indices]
    if len(result) != len(indices) or [r["lap_index"] for r in result] != indices:
        return None
    positions = [captured.index(r) for r in result]
    if positions != list(range(positions[0], positions[-1] + 1)):
        return None
    role = _ROLES.get(group["source_type"])
    if role is None or any(r["intensity_type"] != role for r in result):
        return None
    if role in ("ACTIVE", "RECOVERY"):
        steps = {(r["wkt_index"], r["wkt_step_index"]) for r in result}
        if len(steps) != 1 or any(not isinstance(v, int) or v < 0 for v in next(iter(steps))):
            return None
    return result


def _execution_groups(
    captured: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    indices = [r["lap_index"] for r in captured]
    if any(not isinstance(i, int) or i < 1 for i in indices) or len(set(indices)) != len(indices):
        return None
    by_start: dict[dt.datetime, dict[str, Any]] = {}
    for row in captured:
        start = _start(row)
        if start is None or start in by_start:
            return None
        by_start[start] = row
    chronological = [by_start[start] for start in sorted(by_start)]
    execution = []
    used: list[int] = []
    for group in groups:
        kind = group["source_type"]
        if isinstance(kind, str) and kind.startswith("RWD_"):
            continue  # Overlapping run/walk classifications are not execution steps.
        members = _members(group, refs, chronological)
        if members is None:
            return None
        member_indices = [r["lap_index"] for r in members]
        used.extend(member_indices)
        execution.append(
            {
                **group,
                "lap_indices": member_indices,
                "first_position": chronological.index(members[0]),
            }
        )
    if sorted(used) != sorted(indices):
        return None
    execution.sort(key=lambda r: r["first_position"])
    # All-ACTIVE captures also occur for lap-ended station sessions recorded as
    # running. Their source does not establish work/recovery interval boundaries.
    if not any(
        g["source_type"] in ("INTERVAL_RECOVERY", "INTERVAL_WARMUP", "INTERVAL_COOLDOWN")
        for g in execution
    ):
        return None
    return execution


def _difference(last: Any, first: Any) -> float | None:
    a, b = _number(last), _number(first)
    return a - b if a is not None and b is not None else None


def _repetition(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "split_idx": group["row_idx"],
        "source_message_index": group["message_index"],
        "lap_indices": group["lap_indices"],
        "source_type": group["source_type"],
        **{
            key: _number(group[key])
            for key in ("dur_s", "distance_m", "avg_speed_mps", "avg_hr", "max_hr")
        },
    }


def _pairs(execution: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs = []
    for i, work in enumerate(execution):
        if work["source_type"] != "INTERVAL_ACTIVE":
            continue
        following = execution[i + 1] if i + 1 < len(execution) else None
        recovery = (
            following if following and following["source_type"] == "INTERVAL_RECOVERY" else None
        )
        difference = _difference(work["max_hr"], recovery["avg_hr"]) if recovery else None
        pairs.append(
            {
                "work_split_idx": work["row_idx"],
                "work_lap_indices": work["lap_indices"],
                "work_max_hr_bpm": _number(work["max_hr"]),
                "recovery_split_idx": recovery["row_idx"] if recovery else None,
                "recovery_lap_indices": recovery["lap_indices"] if recovery else [],
                "recovery_avg_hr_bpm": _number(recovery["avg_hr"]) if recovery else None,
                "recovery_dur_s": _number(recovery["dur_s"]) if recovery else None,
                "recovery_type": recovery["source_type"] if recovery else None,
                "difference_bpm": difference,
            }
        )
    return pairs


def _pace(work: list[dict[str, Any]], gtype: str) -> dict[str, Any] | None:
    if gtype == "treadmill_running":
        return None
    values = []
    for group in work:
        duration, distance = _number(group["dur_s"]), _number(group["distance_m"])
        if duration is not None and distance is not None:
            values.append(duration * 1000 / distance)
    return {
        "fastest_s_per_km": min(values) if values else None,
        "slowest_s_per_km": max(values) if values else None,
        "spread_s_per_km": max(values) - min(values) if values else None,
        "valid_repetition_count": len(values),
        "total_work_count": len(work),
        "source": "captured typed-group duration / distance",
    }


def _summarize(execution: list[dict[str, Any]], gtype: str) -> dict[str, Any] | None:
    work = [g for g in execution if g["source_type"] == "INTERVAL_ACTIVE"]
    if not work:
        return None
    pairs = _pairs(execution)
    values = [p["difference_bpm"] for p in pairs if p["difference_bpm"] is not None]
    first, last = work[0], work[-1]
    return {
        "source": "Garmin get_activity_typed_splits with verified captured-lap membership",
        "work_count": len(work),
        "work_repetitions": [_repetition(g) for g in work],
        "first_work": _repetition(first),
        "last_work": _repetition(last),
        "hr_change_first_to_last_bpm": {
            "average": _difference(last["avg_hr"], first["avg_hr"]) if len(work) > 1 else None,
            "maximum": _difference(last["max_hr"], first["max_hr"]) if len(work) > 1 else None,
        },
        "hr_change_label": "change in HR between the first and last repetition (last minus first)",
        "work_recovery_difference": {
            "label": "approximation: work maximum HR minus following recovery average HR; lap aggregates, not HRR60",
            "pairs": pairs,
            "valid_pair_count": len(values),
            "total_work_count": len(work),
            "mean_bpm": sum(values) / len(values) if values else None,
        },
        "outdoor_pace": _pace(work, gtype),
    }


def _result(conn: sqlite3.Connection, activity_id: int, gtype: str) -> dict[str, Any]:
    captured = laps.rows(conn, "activity_laps", activity_id)
    reason = None
    summary = None
    if not captured:
        reason = "no_captured_laps"
    elif gtype not in RUNNING_TYPES:
        reason = "activity_type_not_supported_for_running_summary"
    else:
        execution = _execution_groups(
            captured,
            laps.rows(conn, "activity_splits", activity_id),
            laps.rows(conn, "activity_split_laps", activity_id),
        )
        if execution is None:
            reason = "execution_roles_or_grouping_unavailable"
        else:
            summary = _summarize(execution, gtype)
            if summary is None:
                reason = "no_observed_work_repetitions"
    return {"summary": summary, "unavailable_reason": reason}


def rollup(conn: sqlite3.Connection, *, data_start_date: str, through_date: str) -> None:
    """Replace summaries within the inclusive activity-date window, without committing.

    A historical rebuild preserves summaries outside that window. The features
    transaction still owns both deletion and replacement of the selected rows.
    """
    conn.execute(
        "DELETE FROM activity_interval_summaries WHERE activity_id IN "
        "(SELECT activity_id FROM activities WHERE date BETWEEN ? AND ?)",
        (data_start_date, through_date),
    )
    for activity_id, gtype in conn.execute(
        "SELECT activity_id,gtype FROM activities WHERE date BETWEEN ? AND ?",
        (data_start_date, through_date),
    ).fetchall():
        conn.execute(
            "INSERT INTO activity_interval_summaries VALUES (?,?)",
            (activity_id, json.dumps(_result(conn, activity_id, gtype))),
        )
