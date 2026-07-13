"""Phase 3 coach engine: build a compact ``digest`` from the mart.

Reads only ``daily_metrics`` (+ ``training_status_daily``) - never Garmin live -
and returns a small dict (headline + coach signals) that the coach skill consumes
instead of raw mart rows. Non-durable, like the mart; not a system of record.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

from . import overlap as _overlap
from . import signals as _signals
from . import thresholds as _thresholds
from . import weekly as _weekly
from . import zones as _zones

DISCLAIMER = (
    "This is a reading of your recorded data, not medical or coaching advice."
)

DEFAULT_THRESHOLDS = _thresholds.DEFAULTS

_WEEKLY_FACT_COLS = (
    "week_start", "load_total", "low_share", "high_share", "anaero_share",
    "z2_min", "threshold_min", "z5_min", "monotony", "strain", "acwr_end",
    "hrv_mean", "hrv_trend", "rhr_mean", "sleep_score_mean",
    "n_sessions", "n_quality", "n_rest_days", "max_consec_hard", "plan_adherence",
)

LOAD_HIGHLIGHT_DAYS = 7
WINDOW_DAYS = 28
_SEVERITY_ORDER = {"alert": 0, "warn": 1, "info": 2}


def merge_thresholds(thresholds: dict[str, float] | None) -> dict[str, float]:
    """Effective thresholds: code defaults overridden by the ``coach_thresholds`` table."""
    return _thresholds.merge(thresholds)


def _resolve_window(
    conn: sqlite3.Connection, from_date: str | None, to_date: str | None
) -> tuple[str | None, str | None]:
    """Fill missing bounds: default ``to`` = latest mart day, ``from`` = to - 27d."""
    if to_date is None:
        row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
        to_date = row[0] if row else None
    if from_date is None and to_date is not None:
        from_date = (
            _dt.date.fromisoformat(to_date) - _dt.timedelta(days=WINDOW_DAYS - 1)
        ).isoformat()
    return from_date, to_date


def _date_range_days(from_date: str, to_date: str) -> int:
    d0 = _dt.date.fromisoformat(from_date)
    d1 = _dt.date.fromisoformat(to_date)
    return (d1 - d0).days + 1


def read_mart(conn: sqlite3.Connection, from_date: str, to_date: str) -> list[dict]:
    """Read daily_metrics rows in ``[from_date, to_date]`` as dicts, ordered by date."""
    cur = conn.execute(
        "SELECT * FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
        (from_date, to_date),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _recent_rows(rows: list[dict], to_date: str) -> list[dict]:
    """The trailing LOAD_HIGHLIGHT_DAYS rows of the window (ending at to_date)."""
    cutoff = (
        _dt.date.fromisoformat(to_date) - _dt.timedelta(days=LOAD_HIGHLIGHT_DAYS - 1)
    ).isoformat()
    return [r for r in rows if r["date"] >= cutoff]


def _latest_balance_phrase(conn: sqlite3.Connection, from_date: str, to_date: str) -> str | None:
    row = conn.execute(
        "SELECT balance_phrase FROM training_status_daily "
        "WHERE date >= ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (from_date, to_date),
    ).fetchone()
    return row[0] if row else None


def enrich_hrv_band(rows: list[dict], thresholds: dict[str, float]) -> list[dict]:
    """Fill a missing per-row HRV band from the ``coach_thresholds`` fallback.

    The mart's own ``hrv_baseline``/``hrv_sd`` are the source of truth;
    ``hrv_baseline_ms``/``hrv_sd_ms`` only stand in for a row that has an HRV
    reading but no computed band (e.g. too few nights for Phase 2 to compute one).
    """
    fb_baseline = thresholds.get("hrv_baseline_ms")
    fb_sd = thresholds.get("hrv_sd_ms")
    if fb_baseline is None or fb_sd is None:
        return rows
    k = thresholds["hrv_low_k_sd"]
    enriched = []
    for r in rows:
        if r.get("hrv") is not None and r.get("hrv_baseline") is None:
            r = {**r, "hrv_baseline": fb_baseline, "hrv_sd": fb_sd}
            if r.get("hrv_low_flag") is None:
                r["hrv_low_flag"] = 1 if r["hrv"] < fb_baseline - k * fb_sd else 0
        enriched.append(r)
    return enriched


def _headline(rows: list[dict], recent: list[dict], thresholds: dict[str, float]) -> dict:
    """Latest-day ACWR/HRV band plus a trailing-7-day load total and shares."""
    if not rows:
        return {
            "acwr": None, "n_chronic": None, "acwr_reliable": None,
            "hrv_latest": None, "hrv_baseline": None, "hrv_sd": None,
            "load_7d": None, "load_low_share": None, "load_high_share": None,
        }
    latest = rows[-1]
    n_chronic = latest["n_chronic"]
    min_days = thresholds["acwr_min_chronic_days"]
    reliable = None if n_chronic is None else n_chronic >= min_days

    load_7d = sum((r["load_day"] or 0) for r in recent)
    low_share, high_share, _ = _signals.load_shares(recent)
    return {
        "acwr": latest["acwr"],
        "n_chronic": n_chronic,
        "acwr_reliable": reliable,
        "hrv_latest": latest["hrv"],
        "hrv_baseline": latest["hrv_baseline"],
        "hrv_sd": latest["hrv_sd"],
        "load_7d": load_7d,
        "load_low_share": low_share,
        "load_high_share": high_share,
    }


def _read_niggles(conn: sqlite3.Connection, to_date: str) -> list[dict]:
    """Read niggle entries on/before ``to_date``; the signal windows them itself."""
    cur = conn.execute(
        "SELECT date, body_part, severity FROM niggle WHERE date <= ? ORDER BY date",
        (to_date,),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _movement_coverage(conn: sqlite3.Connection) -> dict | None:
    """Per-set movement-map coverage, so unmapped-exercise drift stays visible.

    Returns None when no sets are captured yet, so the digest carries the fact only
    once there is per-set data to report on.
    """
    cov = _overlap.coverage(conn)
    return cov if cov["sets_total"] else None


def _read_overlap(conn: sqlite3.Connection, to_date: str) -> list[dict]:
    """Read pattern_overlap rows on/before ``to_date``; the signal picks the latest day."""
    cur = conn.execute(
        "SELECT date, dim, key, overlap FROM pattern_overlap WHERE date <= ? ORDER BY date",
        (to_date,),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _read_weekly(conn: sqlite3.Connection, through_date: str | None = None) -> list[dict]:
    """Read weekly rows in week order, optionally scoped to a report horizon."""
    if through_date is None:
        cur = conn.execute("SELECT * FROM weekly_metrics ORDER BY week_start")
    else:
        cur = conn.execute(
            "SELECT * FROM weekly_metrics "
            "WHERE date(week_start, '+6 days') <= date(?) "
            "ORDER BY week_start",
            (through_date,),
        )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _was_deload(weekly_rows: list[dict], drop_pct: float) -> bool | None:
    """Retrospective fact: did ``load_total`` drop by >= ``drop_pct`` vs the prior week.

    ``None`` when there's no predecessor week or its ``load_total`` is zero.
    """
    if len(weekly_rows) < 2:
        return None
    prev_load = weekly_rows[-2].get("load_total")
    if not prev_load:
        return None
    latest_load = weekly_rows[-1].get("load_total") or 0
    return (prev_load - latest_load) / prev_load >= drop_pct


def _weekly_section(
    conn: sqlite3.Connection, weekly_rows: list[dict], thr: dict[str, float]
) -> dict | None:
    """Facts for the latest complete week plus its per-day plan-vs-actual table."""
    if not weekly_rows:
        return None
    latest = weekly_rows[-1]
    section = {k: latest.get(k) for k in _WEEKLY_FACT_COLS}
    section["was_deload"] = _was_deload(weekly_rows, thr["deload_drop_pct"])
    section["plan_vs_actual"] = _weekly.plan_vs_actual(
        conn, latest["week_start"], thr["hard_te_load"]
    )
    return section


def build_digest(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict:
    """Build the coach digest for a window of the ``daily_metrics`` mart.

    Args:
        conn: Open SQLite connection with the mart populated.
        from_date: First day of the window (inclusive).
        to_date: Last day of the window (inclusive).
        thresholds: Coach thresholds; code defaults fill any missing key.

    Returns:
        A dict with ``window``, ``headline``, ``signals``, and ``disclaimer``.
    """
    thr = merge_thresholds(thresholds)
    from_date, to_date = _resolve_window(conn, from_date, to_date)
    weekly_rows = _read_weekly(conn, to_date)
    weekly_section = _weekly_section(conn, weekly_rows, thr)
    zones_section = _zones_section(conn)
    deload = _signals.deload_advised(weekly_rows, thr)
    overlap_rows = _read_overlap(conn, to_date) if to_date is not None else []
    niggle = pattern = muscle = None
    if to_date is not None:
        niggle = _signals.niggle_reduced_mode(_read_niggles(conn, to_date), thr, to_date)
        pattern = _signals.pattern_stack(overlap_rows, thr, to_date)
        muscle = _signals.muscle_overlap(overlap_rows, thr, to_date)
    movement = _movement_coverage(conn)
    if from_date is None or to_date is None:
        # Empty daily mart and no explicit range: only weekly rollups may report.
        return {
            "window": {"from": from_date, "to": to_date, "days": 0},
            "headline": _headline([], [], thr),
            "signals": [s for s in (deload, niggle, pattern, muscle) if s is not None],
            "weekly": weekly_section,
            "zones": zones_section,
            "movement": movement,
            "disclaimer": DISCLAIMER,
        }
    window = {
        "from": from_date,
        "to": to_date,
        "days": _date_range_days(from_date, to_date),
    }
    rows = enrich_hrv_band(read_mart(conn, from_date, to_date), thr)
    recent = _recent_rows(rows, to_date)
    balance_phrase = _latest_balance_phrase(conn, from_date, to_date)
    headline = _headline(rows, recent, thr)
    z2_hi_bpm = zones_section["z2_hi_bpm"] if zones_section else None
    personal_z2_share = _personal_z2_minute_share(conn, from_date, to_date, z2_hi_bpm)
    candidates = (
        _signals.aerobic_low_shortage(recent, thr, balance_phrase, personal_z2_share),
        _signals.acwr_out_of_range(rows, thr),
        _signals.hrv_low_morning(rows, thr),
        _signals.two_hard_days(rows, thr, to_date),
        _signals.hrv_sleep_confound(rows, thr),
        deload,
        niggle,
        pattern,
        muscle,
    )
    signals = sorted(
        (s for s in candidates if s is not None),
        key=lambda s: _SEVERITY_ORDER[s["severity"]],
    )
    return {
        "window": window,
        "headline": headline,
        "signals": signals,
        "weekly": weekly_section,
        "zones": zones_section,
        "movement": movement,
        "disclaimer": DISCLAIMER,
    }


def _zones_section(conn: sqlite3.Connection) -> dict | None:
    """The current personal-zones standing from the ``athlete_zones`` singleton."""
    cur = conn.execute(
        "SELECT lthr_bpm, z1_hi_bpm, z2_hi_bpm, z3_hi_bpm, z4_hi_bpm, "
        "threshold_pace_s_per_km, z2_pace_ceiling_s_per_km, source, "
        "lthr_detected_on, computed_at, stale FROM athlete_zones WHERE id = 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    section = dict(zip([d[0] for d in cur.description], row))
    section["lthr_age_days"] = _lthr_age_days(
        section["lthr_detected_on"], section["computed_at"]
    )
    return section


def _lthr_age_days(detected_on: str | None, computed_at: str | None) -> int | None:
    """Days between the LTHR detection and the recompute cutoff, or None."""
    if not detected_on or not computed_at:
        return None
    return (
        _dt.date.fromisoformat(computed_at) - _dt.date.fromisoformat(detected_on)
    ).days


def _personal_z2_minute_share(
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str,
    z2_hi_bpm: int | None,
) -> float | None:
    """Share of running minutes in the window at avg HR at/below the Z2 ceiling.

    None when there is no personal ceiling yet or no running minutes to weigh.
    """
    if z2_hi_bpm is None:
        return None
    placeholders = ",".join("?" for _ in _zones.RUN_DISCIPLINES)
    rows = conn.execute(
        f"SELECT avg_hr, dur_s FROM activities "
        f"WHERE date BETWEEN ? AND ? AND discipline IN ({placeholders}) "
        f"AND avg_hr IS NOT NULL AND dur_s IS NOT NULL",
        (from_date, to_date, *_zones.RUN_DISCIPLINES),
    ).fetchall()
    total = sum(dur for _, dur in rows)
    if total <= 0:
        return None
    under = sum(dur for hr, dur in rows if hr <= z2_hi_bpm)
    return under / total
