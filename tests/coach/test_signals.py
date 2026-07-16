"""Phase 5 signal tests: ``deload_advised`` as a pure function.

Seam: the pure signal boundary (weekly rows + thresholds -> signal dict | None),
mirroring the other functions in signals.py. No DB, no Garmin.
"""

from __future__ import annotations

from garmin_coach.coach import signals, thresholds
from garmin_coach.coach.signals import deload_advised

THRESHOLDS = {
    "deload_min_history_weeks": 3,
    "deload_load_rise_weeks": 3,
    "acwr_risk_high": 1.5,
    "monotony_high": 2.0,
}


def _week(load_total, acwr_end, monotony):
    return {"load_total": load_total, "acwr_end": acwr_end, "monotony": monotony}


def test_fires_on_rising_load_into_hot_acwr():
    rows = [
        _week(400, 1.0, 1.0),
        _week(600, 1.2, 1.0),
        _week(800, 1.6, 1.0),  # hot ACWR > 1.5
    ]
    sig = deload_advised(rows, THRESHOLDS)
    assert sig is not None
    assert sig["code"] == "DELOAD_ADVISED"
    assert sig["severity"] == "warn"


def test_silent_when_history_too_short():
    rows = [_week(600, 1.2, 1.0), _week(800, 1.6, 1.0)]  # only 2 weeks
    assert deload_advised(rows, THRESHOLDS) is None


def test_fires_on_rising_load_into_high_monotony():
    rows = [
        _week(400, 1.0, 1.0),
        _week(600, 1.0, 1.5),
        _week(800, 1.0, 2.5),  # ACWR calm but monotony > 2.0
    ]
    assert deload_advised(rows, THRESHOLDS)["code"] == "DELOAD_ADVISED"


def test_silent_when_load_not_strictly_rising():
    rows = [
        _week(800, 1.6, 1.0),
        _week(600, 1.6, 1.0),  # dip breaks the rise
        _week(700, 1.6, 1.0),
    ]
    assert deload_advised(rows, THRESHOLDS) is None


# --- Phase 8: movement-overlap signals ---------------------------------------

from garmin_coach.coach.signals import muscle_overlap, pattern_stack  # noqa: E402

OVERLAP_THR = {"pattern_overlap_high": 40}


def _ov(date, dim, key, overlap):
    return {"date": date, "dim": dim, "key": key, "overlap": overlap}


def test_pattern_stack_fires_on_latest_day_and_lists_keys():
    rows = [
        _ov("2026-07-11", "pattern", "hinge", 63.0),
        _ov("2026-07-11", "pattern", "pull", 45.0),
        _ov("2026-07-11", "muscle", "posterior", 63.0),  # other axis, ignored here
    ]
    sig = pattern_stack(rows, OVERLAP_THR, "2026-07-11")
    assert sig["code"] == "PATTERN_STACK"
    assert sig["severity"] == "warn"
    assert sig["facts"]["keys"] == "hinge,pull"
    assert sig["facts"]["overlap_max"] == 63.0
    assert sig["facts"]["date"] == "2026-07-11"


def test_muscle_overlap_reads_the_muscle_axis():
    rows = [
        _ov("2026-07-11", "muscle", "grip", 63.0),
        _ov("2026-07-11", "muscle", "posterior", 52.0),
    ]
    sig = muscle_overlap(rows, OVERLAP_THR, "2026-07-11")
    assert sig["code"] == "MUSCLE_OVERLAP"
    assert sig["facts"]["keys"] == "grip,posterior"


def test_overlap_silent_below_threshold():
    rows = [_ov("2026-07-11", "pattern", "hinge", 30.0)]  # below 40
    assert pattern_stack(rows, OVERLAP_THR, "2026-07-11") is None


def test_overlap_silent_when_stack_not_on_latest_day():
    rows = [_ov("2026-07-10", "pattern", "hinge", 63.0)]  # cleared by a rest day
    assert pattern_stack(rows, OVERLAP_THR, "2026-07-11") is None


# --- Phase 9: TAPER_ACTIVE + RACE_PROXIMITY ---

TH9 = thresholds.merge()


def _plan_row(block="taper", weeks_to_event=1, is_deload=0):
    """Mirrors periodize.current_plan, which derives taper_active for its consumers."""
    return {
        "week_start": "2026-10-05",
        "block": block,
        "weeks_to_event": weeks_to_event,
        "is_deload": is_deload,
        "taper_active": 1 if block == "taper" else 0,
        "race_date": "2026-10-17",
        "race_type": "hyrox",
        "race_status": "confirmed",
        "race_date_precision": "approx",
    }


def _goal(date, *, priority="A", status="confirmed", date_precision="exact", type="hyrox"):
    return {
        "id": 1,
        "date": date,
        "type": type,
        "priority": priority,
        "status": status,
        "date_precision": date_precision,
        "target_s": 3600,
        "note": None,
    }


def test_taper_active_fires_in_a_taper_week():
    signal = signals.taper_active(_plan_row())

    assert signal["code"] == "TAPER_ACTIVE"
    assert signal["facts"]["weeks_to_event"] == 1
    assert signal["facts"]["race_date"] == "2026-10-17"


def test_taper_active_is_silent_outside_the_taper():
    assert signals.taper_active(_plan_row(block="build", weeks_to_event=6)) is None


def test_taper_active_is_silent_without_a_plan():
    assert signals.taper_active(None) is None


def test_race_proximity_fires_inside_the_window():
    signal = signals.race_proximity([_goal("2026-10-17")], TH9, "2026-10-05")

    assert signal["code"] == "RACE_PROXIMITY"
    assert signal["facts"]["weeks_to_event"] == 1
    assert signal["facts"]["type"] == "hyrox"


def test_race_proximity_is_silent_outside_the_window():
    assert signals.race_proximity([_goal("2026-10-17")], TH9, "2026-07-14") is None


def test_race_proximity_fires_for_a_nearer_tentative_b_race():
    """Any priority, any status - proximity is information, not anchoring."""
    events = [_goal("2026-10-17"), _goal("2026-09-05", priority="B", status="tentative")]

    signal = signals.race_proximity(events, TH9, "2026-08-24")

    assert signal["facts"]["priority"] == "B"
    assert signal["facts"]["needs_decision"] is True


def test_race_proximity_asks_to_pin_an_approx_date():
    signal = signals.race_proximity(
        [_goal("2026-10-17", date_precision="approx")], TH9, "2026-10-05"
    )

    assert signal["facts"]["needs_date_pinned"] is True
    assert signal["facts"]["needs_decision"] is False


def test_race_proximity_ignores_a_race_already_run():
    assert signals.race_proximity([_goal("2026-06-20")], TH9, "2026-07-14") is None


# --- Phase 10: subjective hard-RPE trigger ------------------------------------

from garmin_coach.coach.signals import hard_rpe_yesterday  # noqa: E402

HARD_RPE_THR = {"hard_rpe": 8}


def _rated(activity_id, rpe, date="2026-06-14"):
    return {"activity_id": activity_id, "rpe": rpe, "date": date}


def test_hard_rpe_fires_at_floor():
    sig = hard_rpe_yesterday([_rated(1, 8)], HARD_RPE_THR)
    assert sig["code"] == "HARD_RPE_YESTERDAY"
    assert sig["severity"] == "warn"
    assert sig["facts"] == {"activity_id": 1, "rpe": 8, "date": "2026-06-14"}


def test_hard_rpe_silent_below_floor():
    assert hard_rpe_yesterday([_rated(1, 7)], HARD_RPE_THR) is None


def test_hard_rpe_picks_the_max_rpe_of_the_day():
    sig = hard_rpe_yesterday([_rated(1, 6), _rated(2, 9)], HARD_RPE_THR)
    assert sig["facts"]["activity_id"] == 2
    assert sig["facts"]["rpe"] == 9


def test_hard_rpe_silent_without_any_rated_session():
    assert hard_rpe_yesterday([], HARD_RPE_THR) is None
