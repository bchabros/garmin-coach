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


def test_merge_applies_explicit_overrides():
    """Tests and callers can still pass explicit threshold overrides."""
    values = thresholds.merge({"acwr_sweet_hi": 1.0})

    assert values["acwr_sweet_hi"] == 1.0
    assert values["acwr_risk_high"] == 1.5
