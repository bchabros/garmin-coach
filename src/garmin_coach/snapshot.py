"""Athlete snapshot (mart): compose the current standing into one athlete_status row.

Pure and total. ``build`` reads finished marts + core (``daily_metrics``,
``athlete_zones``, ``weight_log``, ``race_predictions``, ``training_readiness``,
``training_status_daily``, ``plan_template``) and returns a single "where do I stand
right now" dict; ``rollup`` upserts the singleton row. A
same-run copy of finished data - never calls Garmin. Runs as the tail of ``features``
after weekly + zones. See docs/prd/phase-6b/PRD.md.
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sqlite3
from typing import Any

from . import db, periodize as _periodize, signals as _signals, thresholds as _thresholds

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


def write_json(status: dict[str, Any], out_dir: pathlib.Path) -> pathlib.Path:
    """Serialize a standing dict to ``snapshot.json`` in ``out_dir``; return the path."""
    path = out_dir / "snapshot.json"
    path.write_text(json.dumps(status, indent=2))
    return path


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
        **_markers(conn, through_date, thr),
        **_hrv(conn, through_date, thr),
        **_load_acwr(conn, through_date, thr),
        **_recovery(conn, through_date),
        **_zones_mirror(conn),
        **_plan(conn, through_date),
    }


def _latest_mart_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    return row[0] if row else None


def _latest_row(
    conn: sqlite3.Connection, table: str, cols: str, through_date: str
) -> dict[str, Any] | None:
    """Latest row of ``table`` at or before ``through_date`` as a dict, or None."""
    cur = conn.execute(
        f"SELECT {cols} FROM {table} WHERE date <= ? ORDER BY date DESC LIMIT 1",
        (through_date,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _series(
    conn: sqlite3.Connection, table: str, col: str, through_date: str
) -> list[tuple[str, float]]:
    """Non-null ``(date, value)`` readings of ``col`` at or before ``through_date``."""
    return [
        (date, value)
        for date, value in conn.execute(
            f"SELECT date, {col} FROM {table} "
            f"WHERE date <= ? AND {col} IS NOT NULL ORDER BY date",
            (through_date,),
        )
    ]


def _trend(
    series: list[tuple[str, float]],
    through_date: str,
    lookback_days: float,
    min_span_days: float,
) -> tuple[float | None, int | None]:
    """Signed change of a marker over the available window, plus the span in days.

    Current value is the latest reading; the baseline is the earliest reading on or
    after ``through_date - lookback_days`` (falling back to the earliest reading when
    history is younger than the window). Returns ``(None, None)`` when the available
    span is below ``min_span_days`` - honest while history is still short, never faked
    from a single point.
    """
    if not series:
        return None, None
    cur_date, cur_val = series[-1]
    floor = (
        _dt.date.fromisoformat(through_date) - _dt.timedelta(days=int(lookback_days))
    ).isoformat()
    base_date, base_val = next(((d, v) for d, v in series if d >= floor), series[0])
    span = (_dt.date.fromisoformat(cur_date) - _dt.date.fromisoformat(base_date)).days
    if span < min_span_days:
        return None, None
    return cur_val - base_val, span


def _markers(
    conn: sqlite3.Connection, through_date: str, thr: dict[str, float]
) -> dict[str, Any]:
    """VO2max and body weight (value + trend) and the latest race predictions."""
    vo2 = _series(conn, "training_status_daily", "vo2max", through_date)
    vo2_delta, vo2_span = _trend(
        vo2, through_date, thr["snapshot_vo2max_lookback_days"],
        thr["snapshot_trend_min_span_days"],
    )
    weight = [(d, g / 1000) for d, g in _series(conn, "weight_log", "weight_g", through_date)]
    weight_delta, weight_span = _trend(
        weight, through_date, thr["snapshot_weight_lookback_days"],
        thr["snapshot_trend_min_span_days"],
    )
    preds = _latest_row(
        conn, "race_predictions", "t_5k_s, t_10k_s, t_half_s, t_marathon_s", through_date
    ) or {}
    return {
        "vo2max": vo2[-1][1] if vo2 else None,
        "vo2max_delta": vo2_delta,
        "vo2max_span_days": vo2_span,
        "weight_kg": weight[-1][1] if weight else None,
        "weight_delta": weight_delta,
        "weight_span_days": weight_span,
        "t_5k_s": preds.get("t_5k_s"),
        "t_10k_s": preds.get("t_10k_s"),
        "t_half_s": preds.get("t_half_s"),
        "t_marathon_s": preds.get("t_marathon_s"),
    }


def _hrv(
    conn: sqlite3.Connection, through_date: str, thr: dict[str, float]
) -> dict[str, Any]:
    """HRV baseline + SD (our numbers) and the trend of Garmin's weekly-average HRV.

    The trend rides on ``hrv_nightly.weekly_avg`` rather than ``daily_metrics``'s own
    baseline: ``features`` recomputes the whole mart nightly, so the stored baseline is
    a constant across rows and cannot trend. The weekly average is a real, smoothed,
    read-only series.
    """
    latest = _latest_row(
        conn, "daily_metrics", "hrv_baseline, hrv_sd", through_date
    ) or {}
    weekly = _series(conn, "hrv_nightly", "weekly_avg", through_date)
    hrv_delta, hrv_span = _trend(
        weekly, through_date, thr["snapshot_hrv_lookback_days"],
        thr["snapshot_trend_min_span_days"],
    )
    return {
        "hrv_baseline": latest.get("hrv_baseline"),
        "hrv_sd": latest.get("hrv_sd"),
        "hrv_delta": hrv_delta,
        "hrv_span_days": hrv_span,
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
    """Today's planned intent from plan_template, plus this week's periodization block.

    The block fields mirror the ``plan_block`` week containing ``through_date``. All
    three stay NULL when there is no anchor race: the system says it does not know
    what the athlete is training for rather than inventing a phase.
    """
    dow = _dt.date.fromisoformat(through_date).weekday()
    row = conn.execute(
        "SELECT planned, intent FROM plan_template WHERE dow = ?", (dow,)
    ).fetchone()
    block = _periodize.current_plan(conn, through_date)
    return {
        "block": block["block"] if block else None,
        "weeks_to_event": block["weeks_to_event"] if block else None,
        "taper_active": block["taper_active"] if block else None,
        "planned_label_today": row[0] if row else None,
        "planned_intent_today": row[1] if row else None,
    }
