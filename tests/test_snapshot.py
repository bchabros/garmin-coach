"""Phase 6b athlete-snapshot (mart) tests.

Primary seam: the pure ``snapshot.build(conn, through_date=...)`` compose that reads
finished marts + core and returns one ``athlete_status`` row dict. Total and
deterministic - never Garmin. A separate test exercises the ``snapshot.rollup``
conn-seam that persists the singleton row, and ``features`` wiring lives in
``test_features``.
"""

from __future__ import annotations

import pytest

from garmin_coach import db, snapshot


def _seed_daily(conn, date, *, load_day, low, high, anaero,
                hrv_baseline=None, hrv_sd=None, acwr=None, n_chronic=None,
                sleep_debt_h=None):
    db.upsert_daily(conn, "daily_metrics", {
        "date": date, "load_day": load_day, "load_low": low, "load_high": high,
        "load_anaerobic": anaero, "hrv_baseline": hrv_baseline, "hrv_sd": hrv_sd,
        "acwr": acwr, "n_chronic": n_chronic, "sleep_debt_h": sleep_debt_h,
    })


def _seed_full_standing(conn):
    """A coherent post-onboarding standing through 2026-07-08 (Wednesday, dow=2)."""
    # 7 days of load: each load_day=100 split 60/30/10 -> shares 0.6/0.3/0.1, load_7d=700.
    for i, date in enumerate(
        ["2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05",
         "2026-07-06", "2026-07-07", "2026-07-08"]
    ):
        _seed_daily(conn, date, load_day=100, low=60, high=30, anaero=10)
    # latest-day headline values live on 07-06 and 07-08 (distinct, for the as-of test)
    _seed_daily(conn, "2026-07-06", load_day=100, low=60, high=30, anaero=10,
                hrv_baseline=66, hrv_sd=10, acwr=1.10, n_chronic=28, sleep_debt_h=2.0)
    _seed_daily(conn, "2026-07-08", load_day=100, low=60, high=30, anaero=10,
                hrv_baseline=68, hrv_sd=11, acwr=1.21, n_chronic=30, sleep_debt_h=3.5)

    db.upsert_zones(conn, {
        "id": 1, "lthr_bpm": 175, "z1_hi_bpm": 140, "z2_hi_bpm": 156,
        "z3_hi_bpm": 164, "z4_hi_bpm": 173, "threshold_pace_s_per_km": 257.1,
        "z2_pace_ceiling_s_per_km": 334.23, "source": "regression+lthr",
        "lthr_detected_on": "2026-07-02", "computed_at": "2026-07-08", "stale": 0,
    })
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-06-20", "vo2max_running": 51.0})
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-07-07", "vo2max_running": 52.0})
    db.upsert_daily(conn, "weight_log", {"date": "2026-06-28", "weight_g": 74500})
    db.upsert_daily(conn, "weight_log", {"date": "2026-07-07", "weight_g": 74200})
    # weekly-average HRV series (Garmin's smoothed series drives the HRV trend)
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-06-25", "weekly_avg": 64})
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-07-08", "weekly_avg": 69})
    db.upsert_daily(conn, "race_predictions", {
        "date": "2026-07-06", "t_5k_s": 1170, "t_10k_s": 2460,
        "t_half_s": 5400, "t_marathon_s": 11400})
    db.upsert_daily(conn, "training_readiness",
                    {"date": "2026-07-06", "score": 60, "level": "MODERATE"})
    db.upsert_daily(conn, "training_readiness",
                    {"date": "2026-07-08", "score": 72, "level": "HIGH"})
    db.upsert_daily(conn, "training_status_daily", {
        "date": "2026-07-08", "heat_accl_pct": 40,
        "heat_trend": "ACCLIMATIZED", "altitude_accl": 0})


def test_full_standing_composes_every_direct_read_field(conn):
    _seed_full_standing(conn)
    s = snapshot.build(conn, through_date="2026-07-08")

    assert s["computed_at"] == "2026-07-08"
    # fitness markers (latest <= through) with a signed trend over the available window
    assert s["vo2max"] == 52.0
    assert s["vo2max_delta"] == pytest.approx(1.0)  # 52 - 51 (06-20 baseline)
    assert s["vo2max_span_days"] == 17  # 06-20 .. 07-07
    assert s["weight_kg"] == pytest.approx(74.2)
    assert s["weight_delta"] == pytest.approx(-0.3)  # 74.2 - 74.5 (06-28 baseline)
    assert s["weight_span_days"] == 9  # 06-28 .. 07-07
    assert (s["t_5k_s"], s["t_10k_s"], s["t_half_s"], s["t_marathon_s"]) == (
        1170, 2460, 5400, 11400)
    # HRV band (our numbers) + trend of Garmin's weekly-average HRV
    assert s["hrv_baseline"] == 68 and s["hrv_sd"] == 11
    assert s["hrv_delta"] == pytest.approx(5.0)  # 69 - 64 (06-25 weekly_avg)
    assert s["hrv_span_days"] == 13  # 06-25 .. 07-08
    # load / ACWR
    assert s["acwr"] == pytest.approx(1.21)
    assert s["n_chronic"] == 30
    assert s["acwr_reliable"] == 1  # 30 >= 28
    assert s["load_7d"] == pytest.approx(700.0)
    assert s["low_share"] == pytest.approx(0.6)
    assert s["high_share"] == pytest.approx(0.3)
    assert s["anaero_share"] == pytest.approx(0.1)
    # recovery
    assert s["readiness_score"] == 72 and s["readiness_level"] == "HIGH"
    assert s["sleep_debt_h"] == pytest.approx(3.5)
    assert s["heat_accl_pct"] == 40
    assert s["heat_trend"] == "ACCLIMATIZED"
    assert s["altitude_accl"] == 0
    # zones: full mirror of athlete_zones
    assert s["lthr_bpm"] == 175
    assert (s["z1_hi_bpm"], s["z2_hi_bpm"], s["z3_hi_bpm"], s["z4_hi_bpm"]) == (
        140, 156, 164, 173)
    assert s["threshold_pace_s_per_km"] == pytest.approx(257.1)
    assert s["z2_pace_ceiling_s_per_km"] == pytest.approx(334.23)
    assert s["zones_source"] == "regression+lthr"
    assert s["lthr_detected_on"] == "2026-07-02"
    assert s["zones_stale"] == 0
    # plan: placeholders NULL until Phase 9; today's intent from plan_template (Wed)
    assert s["block"] is None
    assert s["weeks_to_event"] is None
    assert s["taper_active"] is None
    assert s["planned_intent_today"] == "easy"
    assert s["planned_label_today"] == "bieganie easy/long"


def test_as_of_reproduces_the_then_current_standing(conn):
    _seed_full_standing(conn)
    s = snapshot.build(conn, through_date="2026-07-06")  # Monday, dow=0

    assert s["computed_at"] == "2026-07-06"
    assert s["vo2max"] == 51.0  # 07-07 reading is in the future of this as-of
    assert s["weight_kg"] == pytest.approx(74.5)  # 07-07 weight not yet seen
    assert s["readiness_score"] == 60  # 07-06 row, not the later 72
    assert s["acwr"] == pytest.approx(1.10)
    assert s["n_chronic"] == 28
    assert s["planned_intent_today"] == "rest"  # Monday


def test_trend_delta_is_null_below_the_min_span(conn):
    # Two VO2max readings only 4 days apart -> span 4 < min_span 7 -> no delta yet.
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-07-04", "vo2max_running": 51.0})
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-07-08", "vo2max_running": 52.0})
    s = snapshot.build(conn, through_date="2026-07-08")
    assert s["vo2max"] == 52.0  # value still shown
    assert s["vo2max_delta"] is None  # span 4 < 7
    assert s["vo2max_span_days"] is None


def test_trend_span_is_the_available_history_not_the_lookback(conn):
    # 24 days of VO2max history, well under the 90-day lookback -> span reflects reality.
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-06-14", "vo2max_running": 50.0})
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-07-08", "vo2max_running": 52.0})
    s = snapshot.build(conn, through_date="2026-07-08")
    assert s["vo2max_delta"] == pytest.approx(2.0)
    assert s["vo2max_span_days"] == 24  # not 90


def test_no_zones_anchor_degrades_without_raising(conn):
    # daily standing but no athlete_zones row at all
    _seed_daily(conn, "2026-07-08", load_day=100, low=60, high=30, anaero=10,
                acwr=1.0, n_chronic=10)
    s = snapshot.build(conn, through_date="2026-07-08")

    assert s["lthr_bpm"] is None
    assert s["z2_pace_ceiling_s_per_km"] is None
    assert s["zones_source"] is None
    assert s["zones_stale"] == 1  # missing anchor reads as stale/unknown
    assert s["acwr"] == pytest.approx(1.0)
    assert s["acwr_reliable"] == 0  # 10 < 28


def test_empty_db_degrades_to_nulls(conn):
    s = snapshot.build(conn, through_date="2026-07-08")
    assert s["computed_at"] == "2026-07-08"
    assert s["acwr"] is None
    assert s["acwr_reliable"] is None
    assert s["load_7d"] is None
    assert s["vo2max"] is None
    assert s["zones_stale"] == 1


# --- conn seam: snapshot.rollup persists the singleton row ---

def test_rollup_writes_single_status_row(conn):
    _seed_full_standing(conn)
    snapshot.rollup(conn, through_date="2026-07-08")
    rows = conn.execute(
        "SELECT computed_at, vo2max, acwr, z2_hi_bpm, planned_intent_today "
        "FROM athlete_status"
    ).fetchall()
    assert len(rows) == 1
    computed_at, vo2max, acwr, z2_hi, intent = rows[0]
    assert computed_at == "2026-07-08"
    assert vo2max == 52.0
    assert acwr == pytest.approx(1.21)
    assert z2_hi == 156
    assert intent == "easy"


def test_rollup_is_idempotent(conn):
    _seed_full_standing(conn)
    snapshot.rollup(conn, through_date="2026-07-08")
    snapshot.rollup(conn, through_date="2026-07-08")
    assert conn.execute("SELECT COUNT(*) FROM athlete_status").fetchone()[0] == 1
