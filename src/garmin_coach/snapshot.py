"""Athlete snapshot (mart): compose the current standing into one athlete_status row.

Pure and total. ``build`` reads finished marts + core (``daily_metrics``,
``athlete_zones``, ``fitness_markers``, ``weight_log``, ``race_predictions``,
``training_readiness``, ``training_status_daily``, ``plan_template``) and returns a
single "where do I stand right now" dict; ``rollup`` upserts the singleton row. A
same-run copy of finished data - never calls Garmin. Runs as the tail of ``features``
after weekly + zones. See docs/prd/phase-6b/PRD.md.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from . import db, signals as _signals, thresholds as _thresholds

LOAD_WINDOW_DAYS = 7

# Full mirror of the athlete_zones bounds: (source column, snapshot key).
_ZONE_MIRROR = (
    ("lthr_bpm", "lthr_bpm"),
    ("z1_hi_bpm", "z1_hi_bpm"),
    ("z2_hi_bpm", "z2_hi_bpm"),
    ("z3_hi_bpm", "z3_hi_bpm"),
    ("z4_hi_bpm", "z4_hi_bpm"),
    ("threshold_pace_s_per_km", "threshold_pace_s_per_km"),
    ("z2_pace_ceiling_s_per_km", "z2_pace_ceiling_s_per_km"),
    ("source", "zones_source"),
    ("lthr_detected_on", "lthr_detected_on"),
    ("stale", "zones_stale"),
)


def rollup(conn: sqlite3.Connection, *, through_date: str | None = None) -> None:
    """Recompute the singleton ``athlete_status`` row from finished marts + core.

    A mart-from-mart step - never calls Garmin. Runs as the tail of ``features``,
    after ``weekly.rollup`` and ``zones.rollup``, so it copies their fresh rows.
    """
    cutoff = through_date or _latest_mart_date(conn)
    if cutoff is None:
        return
    db.upsert_status(conn, {"id": 1, **build(conn, through_date=cutoff)})
    conn.commit()


def read(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Read the persisted singleton ``athlete_status`` row as a dict, or None."""
    cur = conn.execute("SELECT * FROM athlete_status WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        return None
    status = dict(zip([d[0] for d in cur.description], row))
    status.pop("id", None)
    return status


def build(
    conn: sqlite3.Connection,
    *,
    through_date: str,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compose the current-standing dict as of ``through_date``.

    Every "latest" read is scoped to ``date <= through_date`` so a past ``through_date``
    reproduces that day's standing. ``planned_*_today`` uses that date's weekday.
    Total: a missing source degrades to ``None`` rather than raising.
    """
    thr = thresholds if thresholds is not None else _thresholds.read(conn)
    return {
        "computed_at": through_date,
        **_markers(conn, through_date),
        **_hrv(conn, through_date),
        **_load_acwr(conn, through_date, thr),
        **_recovery(conn, through_date),
        **_zones_mirror(conn),
        **_plan(conn, through_date),
    }


def _latest_mart_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    return row[0] if row else None


def _latest_row(
    conn: sqlite3.Connection, table: str, cols: str, through_date: str, where: str = ""
) -> dict[str, Any] | None:
    """Latest row of ``table`` at or before ``through_date`` as a dict, or None."""
    cur = conn.execute(
        f"SELECT {cols} FROM {table} WHERE date <= ? {where} ORDER BY date DESC LIMIT 1",
        (through_date,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _markers(conn: sqlite3.Connection, through_date: str) -> dict[str, Any]:
    """VO2max, body weight, and race predictions (trend deltas filled by ticket 02)."""
    vo2 = _latest_row(
        conn, "fitness_markers", "vo2max_running", through_date,
        "AND vo2max_running IS NOT NULL",
    )
    weight = _latest_row(conn, "weight_log", "weight_g", through_date)
    preds = _latest_row(
        conn, "race_predictions", "t_5k_s, t_10k_s, t_half_s, t_marathon_s", through_date
    ) or {}
    weight_g = weight["weight_g"] if weight else None
    return {
        "vo2max": vo2["vo2max_running"] if vo2 else None,
        "vo2max_delta": None,
        "vo2max_span_days": None,
        "weight_kg": weight_g / 1000 if weight_g is not None else None,
        "weight_delta": None,
        "weight_span_days": None,
        "t_5k_s": preds.get("t_5k_s"),
        "t_10k_s": preds.get("t_10k_s"),
        "t_half_s": preds.get("t_half_s"),
        "t_marathon_s": preds.get("t_marathon_s"),
    }


def _hrv(conn: sqlite3.Connection, through_date: str) -> dict[str, Any]:
    """HRV baseline and SD from the latest daily row (trend delta filled by ticket 02)."""
    latest = _latest_row(
        conn, "daily_metrics", "hrv_baseline, hrv_sd", through_date
    ) or {}
    return {
        "hrv_baseline": latest.get("hrv_baseline"),
        "hrv_sd": latest.get("hrv_sd"),
        "hrv_delta": None,
        "hrv_span_days": None,
    }


def _load_acwr(
    conn: sqlite3.Connection, through_date: str, thr: dict[str, float]
) -> dict[str, Any]:
    """Latest ACWR + reliability and the trailing-7-day load total and shares.

    Reuses the digest headline approach: ``load_7d`` sums ``load_day`` over the
    window and ``signals.load_shares`` yields the easy/hard split.
    """
    latest = _latest_row(conn, "daily_metrics", "acwr, n_chronic", through_date)
    recent = _recent_daily(conn, through_date)
    n_chronic = latest["n_chronic"] if latest else None
    reliable = None if n_chronic is None else int(n_chronic >= thr["acwr_min_chronic_days"])
    load_7d = sum((r["load_day"] or 0) for r in recent) if recent else None
    low_share, high_share, total = _signals.load_shares(recent)
    anaero_share = (
        None if low_share is None or high_share is None else 1 - low_share - high_share
    )
    return {
        "acwr": latest["acwr"] if latest else None,
        "n_chronic": n_chronic,
        "acwr_reliable": reliable,
        "load_7d": load_7d,
        "low_share": low_share,
        "high_share": high_share,
        "anaero_share": anaero_share,
    }


def _recent_daily(conn: sqlite3.Connection, through_date: str) -> list[dict[str, Any]]:
    """The trailing ``LOAD_WINDOW_DAYS`` daily rows ending at ``through_date``."""
    start = (
        _dt.date.fromisoformat(through_date) - _dt.timedelta(days=LOAD_WINDOW_DAYS - 1)
    ).isoformat()
    cur = conn.execute(
        "SELECT load_day, load_low, load_high, load_anaerobic FROM daily_metrics "
        "WHERE date >= ? AND date <= ? ORDER BY date",
        (start, through_date),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _recovery(conn: sqlite3.Connection, through_date: str) -> dict[str, Any]:
    """Readiness, sleep debt, and heat/altitude acclimation from their latest rows."""
    readiness = _latest_row(
        conn, "training_readiness", "score, level", through_date
    ) or {}
    sleep = _latest_row(conn, "daily_metrics", "sleep_debt_h", through_date) or {}
    accl = _latest_row(
        conn, "training_status_daily",
        "heat_accl_pct, heat_trend, altitude_accl", through_date,
    ) or {}
    return {
        "readiness_score": readiness.get("score"),
        "readiness_level": readiness.get("level"),
        "sleep_debt_h": sleep.get("sleep_debt_h"),
        "heat_accl_pct": accl.get("heat_accl_pct"),
        "heat_trend": accl.get("heat_trend"),
        "altitude_accl": accl.get("altitude_accl"),
    }


def _zones_mirror(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full copy of the singleton athlete_zones bounds; missing anchor reads as stale."""
    cols = ", ".join(src for src, _ in _ZONE_MIRROR)
    row = conn.execute(f"SELECT {cols} FROM athlete_zones WHERE id = 1").fetchone()
    if row is None:
        return {key: (1 if key == "zones_stale" else None) for _, key in _ZONE_MIRROR}
    return {key: row[i] for i, (_, key) in enumerate(_ZONE_MIRROR)}


def _plan(conn: sqlite3.Connection, through_date: str) -> dict[str, Any]:
    """Today's planned intent from plan_template; block fields are Phase 9 placeholders."""
    dow = _dt.date.fromisoformat(through_date).weekday()
    row = conn.execute(
        "SELECT planned, intent FROM plan_template WHERE dow = ?", (dow,)
    ).fetchone()
    return {
        "block": None,
        "weeks_to_event": None,
        "taper_active": None,
        "planned_label_today": row[0] if row else None,
        "planned_intent_today": row[1] if row else None,
    }
