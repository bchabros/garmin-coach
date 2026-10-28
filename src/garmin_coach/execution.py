"""Verified stored execution shared by descriptive metrics and manual reports."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from .core import laps

RUNNING_TYPES = frozenset(("running", "trail_running", "treadmill_running"))
_ROLES = {
    "INTERVAL_ACTIVE": "ACTIVE",
    "INTERVAL_RECOVERY": "RECOVERY",
    "INTERVAL_WARMUP": "WARMUP",
    "INTERVAL_COOLDOWN": "COOLDOWN",
}


def _start(row: dict[str, Any]) -> dt.datetime | None:
    value = row.get("start_gmt")
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(dt.timezone.utc)
            return parsed.replace(tzinfo=None)
        except (ValueError, OverflowError):
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


@dataclass(frozen=True)
class Execution:
    """Verified groups and the version of work a manual report may describe."""

    activity: dict[str, Any] | None
    groups: list[dict[str, Any]] | None
    unavailable_reason: str | None
    binding: str | None

    @property
    def work(self) -> list[dict[str, Any]]:
        """Observed work in chronological order, never planned repetitions."""
        return [g for g in self.groups or [] if g["source_type"] == "INTERVAL_ACTIVE"]

    def as_data(self) -> dict[str, Any]:
        """Expose numbered work references and the binding required by a local write."""
        return {
            "binding": self.binding,
            "unavailable_reason": self.unavailable_reason
            or ("execution_timing_unavailable" if self.binding is None else None),
            "work_count": len(self.work) if self.groups is not None else None,
            "work_repetitions": [
                {
                    "repetition": i,
                    "split_idx": g["row_idx"],
                    "lap_indices": g["lap_indices"],
                    "start_gmt": g["start_gmt"],
                    "dur_s": g["dur_s"],
                }
                for i, g in enumerate(self.work, 1)
            ],
        }


def _timing(row: dict[str, Any]) -> dict[str, Any] | None:
    start, duration = _start(row), row["dur_s"]
    if (
        start is None
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        return None
    return {"start_utc": start.isoformat(timespec="microseconds"), "duration_s": float(duration)}


def _binding(
    activity: dict[str, Any], groups: list[dict[str, Any]], captured: list[dict[str, Any]]
) -> str | None:
    by_index = {r["lap_index"]: r for r in captured}
    work = []
    for group in groups:
        if group["source_type"] != "INTERVAL_ACTIVE":
            continue
        timing = _timing(group)
        if timing is None:
            return None
        members = []
        for index in group["lap_indices"]:
            lap = by_index[index]
            member = _timing(lap)
            if member is None:
                return None
            members.append(
                {**member, **{k: lap[k] for k in ("intensity_type", "wkt_index", "wkt_step_index")}}
            )
        work.append({**timing, "role": group["source_type"], "members": members})
    if not work:
        return None
    evidence = {
        "version": 1,
        "activity_id": activity["activity_id"],
        "gtype": activity["gtype"],
        "work": work,
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "v1:" + hashlib.sha256(encoded.encode()).hexdigest()


def read(conn: sqlite3.Connection, activity_id: int) -> Execution:
    """Verify one activity's captured execution without consulting cached summaries."""
    cur = conn.execute(
        "SELECT activity_id,date,gtype,discipline,name FROM activities WHERE activity_id=?",
        (activity_id,),
    )
    row = cur.fetchone()
    if row is None:
        return Execution(None, None, "activity_not_found", None)
    info = dict(zip([c[0] for c in cur.description], row))
    captured = laps.rows(conn, "activity_laps", activity_id)
    if not captured:
        return Execution(info, None, "no_captured_laps", None)
    if info["gtype"] not in RUNNING_TYPES:
        return Execution(info, None, "activity_type_not_supported_for_running_summary", None)
    groups = _execution_groups(
        captured,
        laps.rows(conn, "activity_splits", activity_id),
        laps.rows(conn, "activity_split_laps", activity_id),
    )
    if groups is None:
        return Execution(info, None, "execution_roles_or_grouping_unavailable", None)
    if not any(g["source_type"] == "INTERVAL_ACTIVE" for g in groups):
        return Execution(info, groups, "no_observed_work_repetitions", None)
    return Execution(info, groups, None, _binding(info, groups, captured))
