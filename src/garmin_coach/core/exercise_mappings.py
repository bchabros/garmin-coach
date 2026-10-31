"""Exercise classifications and selected-set evidence, independent of transport."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from . import db, exercise_names

PATTERNS = ("push", "pull", "hinge", "squat", "carry")
MUSCLE_GROUPS = ("chest", "back", "posterior", "quads", "shoulders", "grip", "core")


def _binding(name: str, pattern: str | None, muscle_group: str | None) -> str:
    payload = json.dumps([name, pattern, muscle_group], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def read(
    conn: sqlite3.Connection, *, name: str | None = None, local_only: bool = False
) -> dict[str, Any]:
    """Read installed assignments and their differences from packaged defaults."""
    defaults = db.reference_exercise_mappings()
    selected = exercise_names.normalize(name) if name is not None else None
    rows = []
    for exercise, pattern, muscle in conn.execute(
        "SELECT subcategory, pattern, muscle_group FROM exercise_pattern ORDER BY subcategory"
    ):
        if selected is not None and selected != exercise:
            continue
        packaged = defaults.get(exercise)
        local = packaged != (pattern, muscle)
        if local_only and not local:
            continue
        rows.append(
            {
                "name": exercise,
                "pattern": pattern,
                "muscle_group": muscle,
                "binding": _binding(exercise, pattern, muscle),
                "packaged": (
                    {"pattern": packaged[0], "muscle_group": packaged[1]} if packaged else None
                ),
                "local": local,
            }
        )
    return {"mappings": rows, "patterns": list(PATTERNS), "muscle_groups": list(MUSCLE_GROUPS)}


def assign(
    conn: sqlite3.Connection,
    *,
    name: str,
    pattern: str | None,
    muscle_group: str | None,
    expected_current: str | None = None,
) -> dict[str, Any]:
    """Stage an assignment; a different existing value requires its reviewed binding."""
    if not isinstance(name, str):
        raise ValueError("exercise name must be a string")
    name = exercise_names.normalize(name)
    if name == "UNKNOWN":
        raise ValueError("UNKNOWN needs actual exercise names; log the session's stations")
    if pattern is not None and pattern not in PATTERNS:
        raise ValueError(f"pattern must be one of {', '.join(PATTERNS)}, or null")
    if muscle_group is not None and muscle_group not in MUSCLE_GROUPS:
        raise ValueError(f"muscle_group must be one of {', '.join(MUSCLE_GROUPS)}, or null")
    previous = conn.execute(
        "SELECT pattern, muscle_group FROM exercise_pattern WHERE subcategory=?", (name,)
    ).fetchone()
    data = {
        "name": name,
        "pattern": pattern,
        "muscle_group": muscle_group,
        "previous": {"pattern": previous[0], "muscle_group": previous[1]} if previous else None,
        "changed": previous != (pattern, muscle_group),
    }
    if not data["changed"]:
        return data
    current_binding = _binding(name, *previous) if previous else None
    if expected_current != current_binding:
        raise ValueError("assignment changed or was not reviewed; read get_exercise_mappings again")
    conn.execute(
        "INSERT INTO exercise_pattern(subcategory, pattern, muscle_group) VALUES (?, ?, ?) "
        "ON CONFLICT(subcategory) DO UPDATE SET pattern=excluded.pattern, "
        "muscle_group=excluded.muscle_group",
        (name, pattern, muscle_group),
    )
    return data


def affected_activities(
    conn: sqlite3.Connection, name: str, *, data_start_date: str
) -> list[dict[str, Any]]:
    """Count selected sets by eligible activity, excluding hidden captured rows."""
    rows = conn.execute(
        "SELECT a.activity_id, a.date, COUNT(*) FROM movement_sets s "
        "JOIN activities a ON a.activity_id=s.activity_id "
        "WHERE s.subcategory=? AND a.date>=? "
        "GROUP BY a.activity_id, a.date ORDER BY a.date, a.activity_id",
        (name, data_start_date),
    )
    return [{"activity_id": aid, "date": date, "sets": count} for aid, date, count in rows]
