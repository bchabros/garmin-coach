"""Coach threshold policy: defaults, DB reads, and explicit overrides."""

from __future__ import annotations

import sqlite3

DEFAULTS: dict[str, float] = {
    "hrv_low_k_sd": 1,
    "acwr_risk_low": 0.8,
    "acwr_sweet_hi": 1.3,
    "acwr_risk_high": 1.5,
    "acwr_min_chronic_days": 28,
    "hard_te_load": 150,
    "aero_low_target_share": 0.60,
    "aero_high_target_share": 0.40,
    "hrv_sleep_r_min": 0.5,
    "hrv_sleep_min_pairs": 7,
    "monotony_high": 2.0,
    "deload_load_rise_weeks": 3,
    "deload_min_history_weeks": 3,
    "deload_drop_pct": 0.40,
    "z1_hi_pct_lthr": 0.80,
    "z2_hi_pct_lthr": 0.89,
    "z3_hi_pct_lthr": 0.94,
    "z4_hi_pct_lthr": 0.99,
    "z2_pace_fallback_mult": 1.30,
    "zones_regression_min_runs": 12,
    "zones_regression_min_r2": 0.30,
    "zones_heat_temp_c": 22,
    "zones_stale_days": 28,
    "snapshot_vo2max_lookback_days": 90,
    "snapshot_weight_lookback_days": 28,
    "snapshot_hrv_lookback_days": 28,
    "snapshot_trend_min_span_days": 7,
    "srpe_load_scale": 0.3,
    "sila_default_rpe": 7,
    "hard_rpe": 8,
    "niggle_active_days": 7,
    "niggle_reduced_mode_severity": 3,
    "pattern_load_floor": 20,
    "pattern_overlap_high": 40,
    "taper_weeks": 2,
    "peak_weeks": 3,
    "build_weeks": 5,
    "deload_every_n_weeks": 4,
    "race_proximity_weeks": 3,
    "replan_missed_sessions": 2,
}


def merge(overrides: dict[str, float] | None = None) -> dict[str, float]:
    """Return effective thresholds with explicit overrides applied."""
    return {**DEFAULTS, **(overrides or {})}


def read_raw(conn: sqlite3.Connection) -> dict[str, float]:
    """Read non-null threshold rows from ``coach_thresholds``."""
    return {
        key: value
        for key, value in conn.execute("SELECT key, value FROM coach_thresholds")
        if value is not None
    }


def read(conn: sqlite3.Connection) -> dict[str, float]:
    """Return effective thresholds from code defaults plus DB seed rows."""
    return merge(read_raw(conn))
