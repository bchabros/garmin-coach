"""Coach threshold policy tests."""

from __future__ import annotations

from garmin_coach import thresholds


def test_read_merges_code_defaults_with_db_seed_rows(conn):
    """Effective thresholds include code defaults and DB seed overrides."""
    values = thresholds.read(conn)

    assert values["aero_low_target_share"] == 0.60
    assert values["hard_te_load"] == 150

    conn.execute(
        "UPDATE coach_thresholds SET value = ? WHERE key = ?",
        (175, "hard_te_load"),
    )

    assert thresholds.read(conn)["hard_te_load"] == 175


def test_personal_zone_thresholds_present_and_placeholder_retired(conn):
    """Phase 6 seeds the %LTHR band + guard keys and drops hr_z2_upper_bpm."""
    values = thresholds.read(conn)

    assert values["z2_hi_pct_lthr"] == 0.89
    assert values["zones_heat_temp_c"] == 22
    assert values["zones_stale_days"] == 28
    assert "hr_z2_upper_bpm" not in values


def test_snapshot_trend_thresholds_present(conn):
    """Phase 6b seeds the snapshot trend lookback windows and the min-span floor."""
    values = thresholds.read(conn)

    assert values["snapshot_vo2max_lookback_days"] == 90
    assert values["snapshot_weight_lookback_days"] == 28
    assert values["snapshot_hrv_lookback_days"] == 28
    assert values["snapshot_trend_min_span_days"] == 7


def test_merge_applies_explicit_overrides():
    """Tests and callers can still pass explicit threshold overrides."""
    values = thresholds.merge({"acwr_sweet_hi": 1.0})

    assert values["acwr_sweet_hi"] == 1.0
    assert values["acwr_risk_high"] == 1.5
