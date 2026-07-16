"""Normalizer seam: pure payload dict -> row dict functions."""

from __future__ import annotations

import pytest

from garmin_coach.core import models


def test_normalize_activity_maps_core_fields(fixture):
    payload = fixture("activities_range")[0]
    row = models.normalize_activity(payload)

    assert row["activity_id"] == 23176570790
    assert row["start_local"] == "2026-06-08 16:52:46"
    assert row["date"] == "2026-06-08"  # derived from start_local
    assert row["gtype"] == "running"
    assert row["discipline"] == "Bieganie"
    assert row["name"] == "Warsaw - Tempo 10"
    assert row["dur_s"] == 2651.559
    assert row["distance_m"] == 10041.96
    assert row["avg_hr"] == 165
    assert row["max_hr"] == 194
    assert row["aero_te"] == 5.0
    assert row["anaero_te"] == 2.6
    assert row["te_label"] == "VO2MAX"
    assert row["training_load"] == 285.2075500488281
    assert row["hr_z1_s"] == 804.576
    assert row["hr_z5_s"] == 0.0
    assert row["workout_id"] == 1594525147
    assert row["is_pr"] == 1


def test_normalize_exercise_sets_maps_active_sets(fixture):
    rows = models.normalize_exercise_sets(900001, fixture("exercise_sets_strength"))

    # REST sets are dropped; only the three ACTIVE work sets survive, re-indexed 0..2
    assert [r["set_idx"] for r in rows] == [0, 1, 2]
    assert all(r["activity_id"] == 900001 for r in rows)
    bench = rows[0]
    assert bench["category"] == "BENCH_PRESS"
    assert bench["subcategory"] == "BARBELL_BENCH_PRESS"
    assert bench["reps"] == 10
    assert bench["duration_s"] == 48.0
    assert bench["max_weight"] == 60000.0
    assert [r["subcategory"] for r in rows] == [
        "BARBELL_BENCH_PRESS",
        "BARBELL_DEADLIFT",
        "CABLE_ROW",
    ]


def test_normalize_exercise_sets_falls_back_to_category_when_name_missing(fixture):
    rows = models.normalize_exercise_sets(900002, fixture("exercise_sets_hyrox"))

    # name=None under a known parent -> subcategory falls back to the category
    assert rows[0]["subcategory"] == "CARDIO"
    assert rows[1]["subcategory"] == "FARMERS_WALK"


def test_normalize_exercise_sets_empty_payload_is_empty():
    assert models.normalize_exercise_sets(1, None) == []
    assert models.normalize_exercise_sets(1, {"exerciseSets": []}) == []


def test_normalize_activity_missing_fields_become_none(fixture):
    row = models.normalize_activity(
        {
            "activityId": 1,
            "startTimeLocal": "2026-06-09 07:00:00",
            "activityType": {"typeKey": "strength_training"},
        }
    )
    assert row["activity_id"] == 1
    assert row["discipline"] == "Siła"
    assert row["avg_hr"] is None
    assert row["training_load"] is None
    assert row["is_pr"] == 0


def test_normalize_activity_weather_fahrenheit_to_celsius():
    # Garmin weather `temp` is Fahrenheit even on a metric account: 67F -> 19.4C.
    row = models.normalize_activity(
        {
            "activityId": 1,
            "startTimeLocal": "2026-07-04 08:00:00",
            "activityType": {"typeKey": "running"},
        },
        weather={"temp": 67},
    )
    assert row["temp_c"] == pytest.approx(19.44, abs=0.1)


def test_normalize_activity_without_weather_has_null_temp():
    row = models.normalize_activity(
        {
            "activityId": 1,
            "startTimeLocal": "2026-07-04 08:00:00",
            "activityType": {"typeKey": "running"},
        },
    )
    assert row["temp_c"] is None


def test_normalize_lactate_maps_hr_and_pace(fixture):
    row = models.normalize_lactate(fixture("lactate_threshold_latest"))
    assert row["date"] == "2026-07-02"  # from calendarDate
    assert row["lactate_thr_hr"] == 175
    # speed 0.3888878 is scaled x10 vs true m/s (3.889 m/s); pace = 1000/(speed*10)
    assert row["lactate_thr_pace"] == pytest.approx(257.1, abs=0.5)  # ~4:17/km


def test_normalize_lactate_onboarding_is_all_none(fixture):
    row = models.normalize_lactate(fixture("lactate_threshold_onboarding"))
    assert row["date"] is None
    assert row["lactate_thr_hr"] is None
    assert row["lactate_thr_pace"] is None


def test_normalize_lactate_owns_only_lthr_columns(fixture):
    # Must not emit sibling fitness_markers columns (vo2max/etc): an upsert of
    # this row would otherwise clobber values written by another marker source.
    row = models.normalize_lactate(fixture("lactate_threshold_latest"))
    assert set(row) == {"date", "lactate_thr_hr", "lactate_thr_pace"}


def test_normalize_lactate_range_joins_hr_and_speed_by_date(fixture):
    rows = models.normalize_lactate_range(fixture("lactate_threshold_range"))
    by_date = {r["date"]: r for r in rows}
    # history is backfilled: one row per detection day with HR and/or speed
    assert by_date["2026-06-08"]["lactate_thr_hr"] == 179
    assert isinstance(by_date["2026-06-08"]["lactate_thr_hr"], int)  # not 179.0
    assert by_date["2026-07-02"]["lactate_thr_hr"] == 175
    assert by_date["2026-07-02"]["lactate_thr_pace"] == pytest.approx(257.1, abs=0.5)
    # a power-only date carries no threshold HR/speed -> no fitness_markers row
    assert "2026-06-15" not in by_date


def test_normalize_lactate_range_tolerates_empty_or_missing():
    assert models.normalize_lactate_range({}) == []
    assert models.normalize_lactate_range({"heart_rate": None, "speed": None}) == []


def test_map_discipline_trail_promotion():
    assert models.map_discipline("running", "Trail Bieszczady", 800) == "Trail"
    assert models.map_discipline("running", "Easy run", 1200) == "Trail"  # elevation
    assert models.map_discipline("running", "Warsaw tempo", 30) == "Bieganie"
    assert models.map_discipline("hiit") == "Hyrox/HIIT"
    assert models.map_discipline(None) is None


def test_normalize_sleep_maps_summary(fixture):
    row = models.normalize_sleep("2026-06-10", fixture("sleep_day"))
    assert row["date"] == "2026-06-10"
    assert row["total_s"] == 23880
    assert row["deep_s"] == 6600
    assert row["rem_s"] == 5400
    assert row["light_s"] == 11880
    assert row["awake_s"] == 5880
    assert row["score"] == 66  # sleepScores.overall.value
    assert row["score_feedback"] == "NEGATIVE_DISCONTINUOUS"
    assert row["resting_hr"] == 48  # top-level restingHeartRate
    assert row["avg_sleep_hr"] == 53
    assert row["avg_overnight_hrv"] == 68
    assert row["awake_count"] == 3


def test_normalize_hrv_maps_summary(fixture):
    row = models.normalize_hrv("2026-06-10", fixture("hrv_day"))
    assert row["date"] == "2026-06-10"
    assert row["avg_hrv"] == 68  # hrvSummary.lastNightAvg
    assert row["night_high_5min"] == 120
    assert row["weekly_avg"] is None
    assert row["garmin_baseline"] is None
    assert row["status"] == "NONE"
    assert row["feedback_phrase"] == "ONBOARDING_1"


def test_normalize_hrv_baseline_is_scalar_after_onboarding(fixture):
    # After onboarding, hrvSummary.baseline becomes a band object, not null.
    # garmin_baseline must stay a scalar (schema column is INTEGER).
    row = models.normalize_hrv("2026-07-01", fixture("hrv_balanced_day"))
    assert row["avg_hrv"] == 68
    assert row["status"] == "BALANCED"
    assert row["garmin_baseline"] == 57  # balancedLow (lower edge of balanced band)
    assert not isinstance(row["garmin_baseline"], dict)


def test_normalize_wellness_populated(fixture):
    row = models.normalize_wellness("2026-06-10", fixture("wellness_day"))
    assert row["date"] == "2026-06-10"
    assert row["has_data"] == 1
    assert row["rhr"] == 48
    assert row["steps"] == 13062
    assert row["stress_avg"] == 30
    assert row["stress_max"] == 94
    assert row["bb_min"] == 19
    assert row["bb_max"] == 89
    assert row["resp_avg"] == 14


def test_normalize_wellness_empty_day_sets_has_data_zero(fixture):
    row = models.normalize_wellness("2026-05-20", fixture("wellness_empty"))
    assert row["date"] == "2026-05-20"
    assert row["has_data"] == 0
    assert row["rhr"] is None
    assert row["steps"] is None


def test_normalize_readiness_from_list(fixture):
    row = models.normalize_readiness("2026-06-10", fixture("readiness_day"))
    assert row["date"] == "2026-06-10"
    assert row["level"] == "NONE"
    assert row["score"] is None
    assert row["recovery_time_min"] == 3412
    assert row["sleep_factor_pct"] == 0
    assert row["stress_factor_pct"] == 0  # stressHistoryFactorPercent
    assert row["valid_sleep"] == 1


def test_normalize_status_picks_single_device(fixture):
    row = models.normalize_status("2026-06-10", fixture("status_day"))
    assert row["date"] == "2026-06-10"
    assert row["status"] == "NO_STATUS_1"  # feedback phrase, not numeric code
    assert row["garmin_acute_load"] == 329
    assert row["garmin_chronic_load"] == 146
    assert row["garmin_acwr"] is None
    assert row["ml_aero_low"] == 1.6338348
    assert row["ml_aero_low_min"] == 49
    assert row["vo2max"] == 51.7
