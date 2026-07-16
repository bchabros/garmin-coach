"""Movement-overlap mart: the same pattern/muscle loaded on adjacent days.

Pure aggregation over core (``activities``, ``activity_sets``, ``exercise_pattern``)
plus the blended session load. Each session's load is split across its
movement patterns and muscle groups by set-share, summed per day, then compared to
the day before: a key loaded on both D-1 and D (each above ``pattern_load_floor``)
stacks, and ``overlap = min`` of the two days lands in ``pattern_overlap``. A single
rest day clears the stack. Only overlap>0 rows are materialized; the mart is safe to
drop and rebuild from core. See docs/adr/0011-phase-8-movement-overlap.md.
"""

from __future__ import annotations

import datetime as _dt
import logging
import sqlite3
from collections import Counter
from typing import Any

from . import load
from ..coach import thresholds
from ..core import db

logger = logging.getLogger(__name__)

DIMS = ("pattern", "muscle")

# Shared FROM/JOIN for reading captured sets against the movement map. A set's
# ``subcategory`` (real exercise name, or the category fallback) joins to
# ``exercise_pattern``; an unmatched row means an exercise not yet in the map.
_SETS_JOIN_MAP = (
    "FROM activity_sets s LEFT JOIN exercise_pattern p ON s.subcategory = p.subcategory"
)


def _session_load(
    conn: sqlite3.Connection,
    *,
    scale: float,
    sila_default_rpe: float,
    through_date: str | None,
) -> dict[int, tuple[str, float]]:
    """Map each activity to (date, blended load), bounded by through_date."""
    rpe_by_activity = dict(conn.execute("SELECT activity_id, rpe FROM session_rpe"))
    loads: dict[int, tuple[str, float]] = {}
    for aid, date, discipline, dur_s, garmin_load in conn.execute(
        "SELECT activity_id, date(start_local), discipline, dur_s, training_load FROM activities"
    ):
        if through_date is not None and date > through_date:
            continue
        loads[aid] = (
            date,
            load.activity_load(
                discipline,
                garmin_load,
                rpe_by_activity.get(aid),
                dur_s,
                scale=scale,
                sila_default_rpe=sila_default_rpe,
            ),
        )
    return loads


def _daily_key_load(
    conn: sqlite3.Connection, session_load: dict[int, tuple[str, float]]
) -> dict[tuple[str, str, str], float]:
    """Sum blended load per (date, dim, key), split by set-share of mapped sets.

    The denominator is movement sets only: a set mapped to neither a pattern nor a
    muscle group (e.g. a ``CARDIO`` pseudo-set inside a Hyrox session) is excluded,
    so it never dilutes the load attributed to the real strength movements.
    """
    mapped: dict[int, list[tuple[str | None, str | None]]] = {}
    for aid, pattern, muscle in conn.execute(
        "SELECT s.activity_id, p.pattern, p.muscle_group "
        f"{_SETS_JOIN_MAP} "
        "WHERE p.pattern IS NOT NULL OR p.muscle_group IS NOT NULL"
    ):
        mapped.setdefault(aid, []).append((pattern, muscle))

    daily: dict[tuple[str, str, str], float] = {}
    for aid, sets in mapped.items():
        if aid not in session_load:
            continue
        date, sess_load = session_load[aid]
        n_total = len(sets)
        counts = {
            "pattern": Counter(p for p, _ in sets if p),
            "muscle": Counter(m for _, m in sets if m),
        }
        for dim in DIMS:
            for key, n in counts[dim].items():
                slot = (date, dim, key)
                daily[slot] = daily.get(slot, 0.0) + (n / n_total) * sess_load
    return daily


def _overlap_rows(daily: dict[tuple[str, str, str], float], floor: float) -> list[dict[str, Any]]:
    """Adjacent-day stacks: a key loaded above the floor on both D-1 and D."""
    series: dict[tuple[str, str], dict[str, float]] = {}
    for (date, dim, key), value in daily.items():
        series.setdefault((dim, key), {})[date] = value

    rows: list[dict[str, Any]] = []
    for (dim, key), by_date in series.items():
        for date, load_d in by_date.items():
            prev = (_dt.date.fromisoformat(date) - _dt.timedelta(days=1)).isoformat()
            load_prev = by_date.get(prev, 0.0)
            if load_d > floor and load_prev > floor:
                rows.append(
                    {
                        "date": date,
                        "dim": dim,
                        "key": key,
                        "load_d": load_d,
                        "load_prev": load_prev,
                        "overlap": min(load_d, load_prev),
                    }
                )
    return rows


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Movement-map coverage over all captured sets (for the digest + drift warning)."""
    total = 0
    unmapped_count = 0
    unmapped: set[str] = set()
    for sub, mapped_sub in conn.execute(f"SELECT s.subcategory, p.subcategory {_SETS_JOIN_MAP}"):
        total += 1
        if mapped_sub is None:
            unmapped_count += 1
            if sub is not None:
                unmapped.add(sub)
    return {
        "sets_total": total,
        "sets_unmapped": unmapped_count,
        "unmapped": sorted(unmapped),
    }


def rollup(conn: sqlite3.Connection, *, through_date: str | None = None) -> None:
    """Recompute the ``pattern_overlap`` mart from core and upsert it wholesale.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        through_date: Latest day to consider; activities after it are ignored so a
            past recompute reproduces that day's overlap.
    """
    thr = thresholds.read(conn)
    session_load = _session_load(
        conn,
        scale=thr["srpe_load_scale"],
        sila_default_rpe=thr["sila_default_rpe"],
        through_date=through_date,
    )
    daily = _daily_key_load(conn, session_load)
    rows = _overlap_rows(daily, thr["pattern_load_floor"])
    db.replace_pattern_overlap(conn, rows)

    cov = coverage(conn)
    if cov["sets_unmapped"]:
        logger.warning(
            "overlap: %d unmapped exercise subcategories (excluded from overlap): %s",
            cov["sets_unmapped"],
            cov["unmapped"],
        )
    conn.commit()
