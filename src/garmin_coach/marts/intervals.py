"""Descriptive repetition facts from explicitly captured execution groups only."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

from .. import execution
from ..execution import RUNNING_TYPES as RUNNING_TYPES


def _number(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return float(value)
    return None


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


def _result(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    observed = execution.read(conn, activity_id)
    summary = (
        _summarize(observed.groups, observed.activity["gtype"])
        if observed.groups is not None
        and observed.activity is not None
        and observed.unavailable_reason is None
        else None
    )
    return {"summary": summary, "unavailable_reason": observed.unavailable_reason}


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
    for (activity_id,) in conn.execute(
        "SELECT activity_id FROM activities WHERE date BETWEEN ? AND ?",
        (data_start_date, through_date),
    ).fetchall():
        conn.execute(
            "INSERT INTO activity_interval_summaries VALUES (?,?)",
            (activity_id, json.dumps(_result(conn, activity_id))),
        )
