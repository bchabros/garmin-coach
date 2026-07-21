"""Phase 6 personal-zones (mart) tests.

Primary seam: the pure ``zones.compute(lthr, runs, thresholds, today)`` function
that turns the LTHR anchor plus aerobic runs into one ``athlete_zones`` row dict.
Total and deterministic - no DB, no Garmin. A separate test exercises the
``features`` conn-seam that persists the row.
"""

from __future__ import annotations

import pytest

from garmin_coach.core import db
from garmin_coach.marts import zones

# %LTHR band multipliers + guards, mirroring the coach_thresholds seed.
THRESHOLDS = {
    "z1_hi_pct_lthr": 0.80,
    "z2_hi_pct_lthr": 0.89,
    "z3_hi_pct_lthr": 0.94,
    "z4_hi_pct_lthr": 0.99,
    "z2_pace_fallback_mult": 1.30,
    "zones_regression_min_runs": 12,
    "zones_regression_min_r2": 0.30,
    "zones_heat_temp_c": 22,
    "zones_stale_days": 28,
}

LTHR = {"lthr_bpm": 175, "threshold_pace_s_per_km": 257.1, "detected_on": "2026-07-02"}


def test_hr_bands_are_pct_of_lthr():
    row = zones.compute(LTHR, runs=[], thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert row["lthr_bpm"] == 175
    assert row["z1_hi_bpm"] == 140  # 0.80 * 175
    assert row["z2_hi_bpm"] == 156  # 0.89 * 175 = 155.75
    assert row["z3_hi_bpm"] == 164  # 0.94 * 175 = 164.5
    assert row["z4_hi_bpm"] == 173  # 0.99 * 175 = 173.25


def test_pace_ceiling_falls_back_when_history_is_thin():
    # Only 3 runs (< min 12) -> fallback to threshold_pace * mult, no regression.
    runs = [
        {"avg_hr": 145, "pace_s_per_km": 320.0, "temp_c": 15.0},
        {"avg_hr": 150, "pace_s_per_km": 300.0, "temp_c": 16.0},
        {"avg_hr": 165, "pace_s_per_km": 264.0, "temp_c": 14.0},
    ]
    row = zones.compute(LTHR, runs=runs, thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert row["source"] == "threshold_pace_fallback+lthr"
    assert row["z2_pace_ceiling_s_per_km"] == pytest.approx(334.23, abs=0.5)  # 257.1*1.3


def _line_runs(hrs, temp_c=15.0):
    # pace exactly on the line pace = 340 - (83/35)*(hr-140); OLS recovers it.
    return [
        {"avg_hr": hr, "pace_s_per_km": 340 - (83 / 35) * (hr - 140), "temp_c": temp_c}
        for hr in hrs
    ]


def test_pace_ceiling_uses_regression_with_enough_clean_runs():
    hrs = [140, 143, 146, 149, 152, 155, 158, 161, 164, 167, 170, 173]  # 12 runs
    row = zones.compute(LTHR, runs=_line_runs(hrs), thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert row["source"] == "regression+lthr"
    # predicted pace at Z2 HR ceiling 156: 340 - (83/35)*16 = 302.06
    assert row["z2_pace_ceiling_s_per_km"] == pytest.approx(302.06, abs=1.0)


def test_hot_runs_are_excluded_and_can_drop_below_regression_minimum():
    hrs = [140, 143, 146, 149, 152, 155, 158, 161, 164, 167, 170, 173]  # 12 total
    runs = _line_runs(hrs)
    for r in runs[:4]:  # 4 hot runs -> only 8 clean, below min 12
        r["temp_c"] = 26.0
    row = zones.compute(LTHR, runs=runs, thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert row["source"] == "threshold_pace_fallback+lthr"
    assert row["z2_pace_ceiling_s_per_km"] == pytest.approx(334.23, abs=0.5)


def test_staleness_tracks_detection_age():
    fresh = zones.compute(LTHR, runs=[], thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert fresh["lthr_detected_on"] == "2026-07-02"
    assert fresh["stale"] == 0  # 6 days < 28

    old = zones.compute(LTHR, runs=[], thresholds=THRESHOLDS, cutoff="2026-08-15")
    assert old["stale"] == 1  # 44 days > 28


def test_no_lthr_anchor_degrades_without_raising():
    row = zones.compute(None, runs=[], thresholds=THRESHOLDS, cutoff="2026-07-08")
    assert row["lthr_bpm"] is None
    assert row["z2_hi_bpm"] is None
    assert row["z2_pace_ceiling_s_per_km"] is None
    assert row["source"] == "no_anchor"
    assert row["stale"] == 1  # no fresh anchor at all


# --- conn seam: zones.rollup reads core + thresholds and writes the mart row ---


def _seed_lthr(conn, date="2026-07-02", hr=175, pace=257.1):
    db.upsert_daily(
        conn, "fitness_markers", {"date": date, "lactate_thr_hr": hr, "lactate_thr_pace": pace}
    )


def _seed_run(conn, aid, date, speed_mps, hr, temp_c=15.0):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 08:00:00",
            "date": date,
            "gtype": "running",
            "discipline": "Bieganie",
            "avg_hr": hr,
            "avg_speed_mps": speed_mps,
            "temp_c": temp_c,
        },
    )


def test_rollup_writes_single_zones_row_from_core(conn):
    _seed_lthr(conn)
    _seed_run(conn, 1, "2026-07-01", 3.0, 150)
    zones.rollup(conn, through_date="2026-07-08")
    rows = conn.execute("SELECT lthr_bpm, z2_hi_bpm, source, stale FROM athlete_zones").fetchall()
    assert len(rows) == 1
    lthr_bpm, z2_hi_bpm, source, stale = rows[0]
    assert lthr_bpm == 175
    assert z2_hi_bpm == 156
    assert source == "threshold_pace_fallback+lthr"  # 1 run < min
    assert stale == 0


def test_rollup_is_idempotent(conn):
    _seed_lthr(conn)
    zones.rollup(conn, through_date="2026-07-08")
    zones.rollup(conn, through_date="2026-07-08")
    assert conn.execute("SELECT COUNT(*) FROM athlete_zones").fetchone()[0] == 1


# --- Issue #36: the as-of cutoff bounds the sources, not just computed_at ---


def test_rollup_ignores_an_lthr_detected_after_the_cutoff(conn):
    """A detection from the future of the horizon must not anchor an as-of recompute."""
    _seed_lthr(conn, date="2026-06-10", hr=170)
    _seed_lthr(conn, date="2026-07-10", hr=190)  # after the cutoff
    _seed_run(conn, 1, "2026-06-12", 3.0, 150)

    zones.rollup(conn, through_date="2026-06-15")

    lthr_bpm, detected_on = conn.execute(
        "SELECT lthr_bpm, lthr_detected_on FROM athlete_zones WHERE id = 1"
    ).fetchone()
    assert detected_on == "2026-06-10"
    assert lthr_bpm == 170


def test_rollup_ignores_runs_after_the_cutoff(conn):
    """The pace<->HR fit sees only runs that existed at the horizon."""
    _seed_lthr(conn, date="2026-06-10")
    # 12 clean runs before the cutoff would reach the regression minimum...
    for i, hr in enumerate([140, 143, 146, 149, 152, 155, 158, 161, 164, 167, 170, 173]):
        _seed_run(conn, 100 + i, "2026-07-01", 1000 / (340 - (83 / 35) * (hr - 140)), hr)

    zones.rollup(conn, through_date="2026-06-15")

    source = conn.execute("SELECT source FROM athlete_zones WHERE id = 1").fetchone()[0]
    assert source == "threshold_pace_fallback+lthr"  # no run existed yet at the horizon


def test_rollup_still_sees_everything_up_to_the_cutoff(conn):
    """The bound is inclusive - a same-day detection still anchors the recompute."""
    _seed_lthr(conn, date="2026-06-15", hr=180)

    zones.rollup(conn, through_date="2026-06-15")

    assert conn.execute("SELECT lthr_bpm FROM athlete_zones WHERE id = 1").fetchone()[0] == 180
