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


# --- issue #21: PLAN_MISSING -------------------------------------------------


def test_plan_missing_fires_when_the_week_runs_on_the_template():
    signal = signals.plan_missing(has_plan=False, week_start="2026-07-13")

    assert signal["code"] == "PLAN_MISSING"
    assert signal["severity"] == "info"
    assert signal["facts"] == {"week_start": "2026-07-13", "source": "plan_template"}


def test_plan_missing_is_silent_once_the_week_is_authored():
    assert signals.plan_missing(has_plan=True, week_start="2026-07-13") is None


def test_plan_missing_needs_a_week():
    assert signals.plan_missing(has_plan=False, week_start=None) is None


# --- AEROBIC_LOW_SHORTAGE follows Garmin's own lower bound (issue #70) ----------

# Garmin's balance on 2026-09-20: lower limit for low-aerobic load 698 of a 3076 total.
GARMIN_BALANCE = {
    "ml_aero_low_min": 698.0,
    "ml_aero_low": 990.0,
    "ml_aero_high": 1460.0,
    "ml_anaerobic": 626.0,
}
LOW_BOUND = 698.0 / 3076.0  # 0.2269...


def _load_day(low, high, anaerobic):
    return {"load_low": low, "load_high": high, "load_anaerobic": anaerobic}


def test_shortage_fires_when_the_easy_share_is_below_garmins_lower_bound():
    rows = [_load_day(low=20, high=50, anaerobic=30)]

    signal = signals.aerobic_low_shortage(
        rows, thresholds.DEFAULTS, "AEROBIC_LOW_SHORTAGE", garmin_balance=GARMIN_BALANCE
    )

    assert signal["code"] == "AEROBIC_LOW_SHORTAGE"
    assert signal["facts"]["low_share"] == 0.2
    assert abs(signal["facts"]["target_low_share"] - LOW_BOUND) < 1e-9
    assert signal["facts"]["target_source"] == "garmin"
    assert signal["garmin_agrees"] is True


def test_shortage_is_silent_above_garmins_bound_whatever_the_hard_share():
    rows = [_load_day(low=30, high=60, anaerobic=10)]  # the old 60/40 rule fired here

    assert (
        signals.aerobic_low_shortage(rows, thresholds.DEFAULTS, None, garmin_balance=GARMIN_BALANCE)
        is None
    )


def test_shortage_fires_on_a_low_easy_share_even_when_hard_work_is_low_too():
    rows = [_load_day(low=20, high=10, anaerobic=70)]  # the old rule needed hard > 40%

    signal = signals.aerobic_low_shortage(
        rows, thresholds.DEFAULTS, None, garmin_balance=GARMIN_BALANCE
    )

    assert signal is not None and signal["facts"]["high_share"] == 0.1


def test_shortage_uses_the_fixed_floor_when_garmin_publishes_no_bound():
    short = [_load_day(low=20, high=50, anaerobic=30)]
    enough = [_load_day(low=30, high=50, anaerobic=20)]

    signal = signals.aerobic_low_shortage(short, thresholds.DEFAULTS, None)

    assert signal["facts"]["target_low_share"] == 0.25
    assert signal["facts"]["target_source"] == "fallback"
    assert signals.aerobic_low_shortage(enough, thresholds.DEFAULTS, None) is None
    no_total = dict(GARMIN_BALANCE, ml_aero_low=None, ml_aero_high=None, ml_anaerobic=None)
    assert (
        signals.aerobic_low_shortage(short, thresholds.DEFAULTS, None, garmin_balance=no_total)[
            "facts"
        ]["target_source"]
        == "fallback"
    )


def test_shortage_is_silent_with_no_load_at_all():
    assert signals.aerobic_low_shortage([_load_day(0, 0, 0)], thresholds.DEFAULTS, None) is None


# --- how far our load split is from Garmin's own balance (issue #70) ------------


def test_balance_gap_is_the_mean_share_difference_in_percentage_points():
    """Ours 33 / 49 / 18 against Garmin's 990 / 1460 / 626 of 3076 (32.2 / 47.5 / 20.4):
    differences of 0.8, 1.5 and 2.4 points, mean 1.6."""
    rows = [_load_day(low=33, high=49, anaerobic=18)]

    gap = signals.garmin_balance_gap(rows, GARMIN_BALANCE)

    assert round(gap, 1) == 1.6


def test_balance_gap_is_none_when_either_side_is_empty():
    rows = [_load_day(low=33, high=49, anaerobic=18)]

    assert signals.garmin_balance_gap([_load_day(0, 0, 0)], GARMIN_BALANCE) is None
    assert signals.garmin_balance_gap(rows, None) is None
    assert signals.garmin_balance_gap(rows, dict(GARMIN_BALANCE, ml_aero_high=None)) is None
