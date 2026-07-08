"""Personal training zones (mart): LTHR anchor + aerobic runs -> athlete_zones row.

Pure and total. ``compute`` derives HR bands as fixed fractions of the
watch-detected Lactate Threshold heart rate and a Zone-2 pace ceiling from a
pace<->HR regression over aerobic runs (falling back to a fraction of threshold
pace when history is thin). Reads nothing - the caller extracts core rows and
persists the result. See docs/prd/phase-6.md.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from . import db, thresholds as _thresholds

# Disciplines whose runs carry a meaningful pace<->HR relationship. Public so the
# digest's personal-Z2 query shares one source of truth (no duplicated literal).
RUN_DISCIPLINES = ("Bieganie", "Trail")


def rollup(conn: sqlite3.Connection, *, through_date: str | None = None) -> None:
    """Recompute the singleton ``athlete_zones`` row from core + thresholds.

    Reads the latest LTHR from ``fitness_markers`` and the aerobic runs from
    ``activities``, runs :func:`compute`, and upserts the single mart row. A
    mart-from-core step - never calls Garmin. Runs as the tail of ``features``.
    """
    thresholds = _thresholds.read(conn)
    cutoff = through_date or _latest_core_date(conn)
    if cutoff is None:
        return
    row = compute(_latest_lthr(conn), _aerobic_runs(conn), thresholds, cutoff)
    db.upsert_zones(conn, {"id": 1, **row})
    conn.commit()


def _latest_core_date(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT MAX(date) FROM activities").fetchone()
    return r[0] if r else None


def _latest_lthr(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Most recent non-null LTHR heart-rate row from ``fitness_markers``."""
    r = conn.execute(
        "SELECT date, lactate_thr_hr, lactate_thr_pace FROM fitness_markers "
        "WHERE lactate_thr_hr IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if r is None:
        return None
    return {"lthr_bpm": r[1], "threshold_pace_s_per_km": r[2], "detected_on": r[0]}


def _aerobic_runs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Runs with the fields the pace<->HR fit needs (HR, pace, temperature)."""
    placeholders = ",".join("?" for _ in RUN_DISCIPLINES)
    rows = conn.execute(
        f"SELECT avg_hr, avg_speed_mps, temp_c FROM activities "
        f"WHERE discipline IN ({placeholders}) "
        f"AND avg_hr IS NOT NULL AND avg_speed_mps > 0",
        RUN_DISCIPLINES,
    ).fetchall()
    return [
        {"avg_hr": hr, "pace_s_per_km": 1000.0 / spd, "temp_c": temp}
        for hr, spd, temp in rows
    ]


def compute(
    lthr: dict[str, Any] | None,
    runs: list[dict[str, Any]],
    thresholds: dict[str, float],
    cutoff: str,
) -> dict[str, Any]:
    """Build the single ``athlete_zones`` row from the LTHR anchor and runs.

    ``cutoff`` is the as-of date the row is computed for (stored as
    ``computed_at``); during a backfill it can be a past date, so it drives
    staleness rather than wall-clock "now".
    """
    lthr_bpm = lthr.get("lthr_bpm") if lthr else None
    thr_pace = lthr.get("threshold_pace_s_per_km") if lthr else None
    detected_on = lthr.get("detected_on") if lthr else None
    z2_hi = _pct(lthr_bpm, thresholds["z2_hi_pct_lthr"])
    if lthr_bpm is None:
        ceiling, source = None, "no_anchor"
    else:
        # source records the pace-ceiling method and the HR-band provenance
        # (bands are %LTHR-derived), e.g. "regression+lthr".
        ceiling, method = _z2_pace_ceiling(z2_hi, thr_pace, runs, thresholds)
        source = f"{method}+lthr"
    return {
        "lthr_bpm": lthr_bpm,
        "z1_hi_bpm": _pct(lthr_bpm, thresholds["z1_hi_pct_lthr"]),
        "z2_hi_bpm": z2_hi,
        "z3_hi_bpm": _pct(lthr_bpm, thresholds["z3_hi_pct_lthr"]),
        "z4_hi_bpm": _pct(lthr_bpm, thresholds["z4_hi_pct_lthr"]),
        "threshold_pace_s_per_km": thr_pace,
        "z2_pace_ceiling_s_per_km": ceiling,
        "source": source,
        "lthr_detected_on": detected_on,
        "computed_at": cutoff,
        "stale": _stale(detected_on, cutoff, thresholds["zones_stale_days"]),
    }


def _stale(detected_on: str | None, cutoff: str, stale_days: float) -> int:
    """1 when the anchor is missing or its detection is older than the cadence."""
    if detected_on is None:
        return 1
    age = (_dt.date.fromisoformat(cutoff) - _dt.date.fromisoformat(detected_on)).days
    return 1 if age > stale_days else 0


def _z2_pace_ceiling(
    z2_hi_bpm: int | None,
    thr_pace: float | None,
    runs: list[dict[str, Any]],
    thresholds: dict[str, float],
) -> tuple[float | None, str]:
    """Z2 pace ceiling and its provenance.

    Prefer a pace<->HR regression over aerobic runs once there are enough
    heat-clean runs; otherwise fall back to a fraction of threshold pace.
    """
    clean = [
        r
        for r in runs
        if r.get("avg_hr") is not None
        and r.get("pace_s_per_km") is not None
        and (r.get("temp_c") is None or r["temp_c"] <= thresholds["zones_heat_temp_c"])
    ]
    if z2_hi_bpm is not None and len(clean) >= thresholds["zones_regression_min_runs"]:
        fit = _ols_predict(
            xs=[r["avg_hr"] for r in clean],
            ys=[r["pace_s_per_km"] for r in clean],
            at=z2_hi_bpm,
        )
        if fit is not None and fit[1] >= thresholds["zones_regression_min_r2"]:
            return fit[0], "regression"
    if thr_pace is None:
        return None, "threshold_pace_fallback"
    return thr_pace * thresholds["z2_pace_fallback_mult"], "threshold_pace_fallback"


def _ols_predict(
    xs: list[float], ys: list[float], at: float
) -> tuple[float, float] | None:
    """Ordinary least squares of ``y`` on ``x``; return (prediction at ``at``, R^2).

    None when the fit is degenerate (no spread in x or in y).
    """
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    mean_y = sy / n
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    if ss_tot == 0:
        return None
    ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot
    return intercept + slope * at, r2


def _pct(lthr_bpm: int | None, frac: float) -> int | None:
    """A zone edge as a rounded percentage of LTHR, or None without an anchor."""
    return round(lthr_bpm * frac) if lthr_bpm is not None else None
