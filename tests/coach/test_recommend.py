"""Phase 10 recommender tests: ``recommend`` as a pure function.

Seam: the pure recommendation boundary (a finished digest + tomorrow's planned
intent + thresholds -> a ``recommendation`` block). No DB, no Garmin. The
``avoid`` list and ``replan`` menu are stubs here (later tickets fill them).
"""

from __future__ import annotations

from garmin_coach.coach.recommend import recommend

THRESHOLDS = {"hard_rpe": 8, "replan_missed_sessions": 2}


def _digest(signals=None, zones=None, to="2026-06-14", weekly=None, plan=None):
    return {
        "window": {"to": to},
        "signals": signals or [],
        "zones": zones,
        "weekly": weekly,
        "plan": plan,
    }


def _week(plan_rows, week_start="2026-07-06"):
    """A weekly section carrying a plan-vs-actual grid from (planned, matched) tuples."""
    pva = [
        {"dow": i, "date": week_start, "planned": planned, "actual": "x", "match": matched}
        for i, (planned, matched) in enumerate(plan_rows)
    ]
    return {"week_start": week_start, "plan_vs_actual": pva}


def _zones(source="regression+lthr", z2=330, thr=270):
    return {
        "source": source,
        "z2_pace_ceiling_s_per_km": z2,
        "threshold_pace_s_per_km": thr,
    }


def _sig(code, **facts):
    return {"code": code, "severity": "warn", "facts": facts}


def test_green_day_keeps_the_planned_session():
    rec = recommend(_digest(zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "quality"
    assert rec["downgraded"] is False
    assert rec["rationale"] == []
    assert rec["intensity_cap"] is None
    assert rec["pace_target_s_per_km"] == 270  # threshold pace for a quality target
    assert rec["target_date"] == "2026-06-15"
    assert rec["avoid"] == []
    assert rec["replan"] is None


def test_hot_acwr_caps_to_easy_z2_and_cites():
    signals = [_sig("ACWR_OUT_OF_RANGE", acwr=1.6, sweet_hi=1.3)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["pace_target_s_per_km"] == 330  # z2 ceiling
    assert rec["downgraded"] is True
    assert rec["rationale"] == ["ACWR_OUT_OF_RANGE"]


def test_acwr_below_sweet_spot_does_not_downgrade():
    signals = [_sig("ACWR_OUT_OF_RANGE", acwr=0.7, sweet_hi=1.3)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "quality"
    assert rec["rationale"] == []


def test_hrv_low_caps_to_easy_z2():
    signals = [_sig("HRV_LOW_MORNING")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["rationale"] == ["HRV_LOW_MORNING"]


def test_aerobic_shortage_forces_z2_pace_on_an_easy_day():
    signals = [_sig("AEROBIC_LOW_SHORTAGE")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "easy", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] is None  # aerobic sets no HR cap of its own
    assert rec["pace_target_s_per_km"] == 330  # forced to the z2 ceiling
    assert rec["rationale"] == ["AEROBIC_LOW_SHORTAGE"]


def test_deload_advised_caps_to_easy_z2():
    signals = [_sig("DELOAD_ADVISED")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["rationale"] == ["DELOAD_ADVISED"]


def test_taper_caps_intensity_without_forcing_easy():
    signals = [_sig("TAPER_ACTIVE", block="taper")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "quality"  # taper alone does not force easy
    assert rec["intensity_cap"] == "Z4"  # no all-out
    assert rec["pace_target_s_per_km"] == 270  # threshold for the quality target
    assert rec["downgraded"] is False
    assert rec["rationale"] == ["TAPER_ACTIVE"]


def test_hard_rpe_yesterday_caps_to_easy():
    signals = [_sig("HARD_RPE_YESTERDAY", rpe=9)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["downgraded"] is True
    assert rec["rationale"] == ["HARD_RPE_YESTERDAY"]


def test_two_hard_days_only_downgrades_when_trailing():
    trailing = [_sig("TWO_HARD_DAYS", trailing=True)]
    assert recommend(_digest(signals=trailing), "quality", THRESHOLDS)["intended_type"] == "easy"
    historic = [_sig("TWO_HARD_DAYS", trailing=False)]
    rec = recommend(_digest(signals=historic), "quality", THRESHOLDS)
    assert rec["intended_type"] == "quality"
    assert rec["rationale"] == []


def test_most_conservative_wins_and_cites_every_driver():
    signals = [
        _sig("HRV_LOW_MORNING"),
        _sig("TWO_HARD_DAYS", trailing=True),
    ]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert set(rec["rationale"]) == {"HRV_LOW_MORNING", "TWO_HARD_DAYS"}


def test_pace_is_null_on_a_fallback_multiplier():
    signals = [_sig("HRV_LOW_MORNING")]
    zones = _zones(source="threshold_pace_fallback+lthr")
    rec = recommend(_digest(signals=signals, zones=zones), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["pace_target_s_per_km"] is None


def test_pace_is_null_without_a_zones_row():
    rec = recommend(_digest(zones=None), "quality", THRESHOLDS)
    assert rec["pace_target_s_per_km"] is None


def test_aerobic_not_cited_when_a_z2_cap_already_set_the_pace():
    signals = [_sig("HRV_LOW_MORNING"), _sig("AEROBIC_LOW_SHORTAGE")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["pace_target_s_per_km"] == 330
    assert rec["rationale"] == ["HRV_LOW_MORNING"]  # aerobic changed nothing


def test_taper_adds_no_ceiling_to_an_easy_session():
    signals = [_sig("TAPER_ACTIVE", block="taper"), _sig("HARD_RPE_YESTERDAY", rpe=9)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] is None  # no Z4 ceiling on an easy day
    assert rec["rationale"] == ["HARD_RPE_YESTERDAY"]  # taper cited only when it caps


def test_niggle_reduced_mode_caps_to_easy_z2():
    signals = [_sig("NIGGLE_REDUCED_MODE", body_part="kolano", severity=4)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"
    assert rec["intensity_cap"] == "Z2"
    assert rec["rationale"] == ["NIGGLE_REDUCED_MODE"]


def test_avoid_lists_the_stacked_movement_patterns():
    signals = [_sig("PATTERN_STACK", keys="hinge,squat", overlap_max=63.0)]
    rec = recommend(_digest(signals=signals), "quality", THRESHOLDS)
    assert rec["avoid"] == ["hinge", "squat"]


def test_avoid_merges_patterns_and_muscles_deduped_and_sorted():
    signals = [
        _sig("PATTERN_STACK", keys="squat,hinge"),
        _sig("MUSCLE_OVERLAP", keys="grip,posterior"),
    ]
    rec = recommend(_digest(signals=signals), "quality", THRESHOLDS)
    assert rec["avoid"] == ["grip", "hinge", "posterior", "squat"]


def test_avoid_is_empty_without_a_stack_signal():
    assert recommend(_digest(), "quality", THRESHOLDS)["avoid"] == []


def test_niggle_downgrades_without_fabricating_an_avoid_pattern():
    signals = [_sig("NIGGLE_REDUCED_MODE", body_part="kolano", severity=4)]
    rec = recommend(_digest(signals=signals, zones=_zones()), "quality", THRESHOLDS)
    assert rec["intended_type"] == "easy"  # niggle still softens the session
    assert rec["avoid"] == []  # but invents no movement pattern


def test_replan_menu_fires_on_a_missed_week():
    weekly = _week([("quality", False), ("easy", False), ("rest", True), ("quality", True)])
    plan = {"block": "build", "weeks_to_event": 14}
    rp = recommend(_digest(weekly=weekly, plan=plan), "quality", THRESHOLDS)["replan"]
    assert rp["missed"] == 2
    assert rp["week_start"] == "2026-07-06"
    assert rp["recommended"] == "extend"
    assert [o["id"] for o in rp["options"]] == ["extend", "rebuild", "continue"]


def test_replan_is_null_below_the_threshold():
    weekly = _week([("quality", False), ("rest", True), ("easy", True)])  # one miss
    rec = recommend(
        _digest(weekly=weekly, plan={"block": "build", "weeks_to_event": 14}),
        "quality",
        THRESHOLDS,
    )
    assert rec["replan"] is None


def test_replan_recommends_continue_near_the_race():
    weekly = _week([("quality", False), ("quality", False)])
    plan = {"block": "taper", "weeks_to_event": 2}
    rp = recommend(_digest(weekly=weekly, plan=plan), "easy", THRESHOLDS)["replan"]
    assert rp["recommended"] == "continue"


def test_replan_rebuild_is_flagged_manual():
    weekly = _week([("quality", False), ("easy", False)])
    plan = {"block": "build", "weeks_to_event": 14}
    rp = recommend(_digest(weekly=weekly, plan=plan), "quality", THRESHOLDS)["replan"]
    rebuild = next(o for o in rp["options"] if o["id"] == "rebuild")
    assert "manual" in rebuild["cite"]


def test_replan_does_not_count_a_skipped_rest_day_as_a_miss():
    weekly = _week([("rest", False), ("rest", False), ("quality", False)])  # one real miss
    rec = recommend(
        _digest(weekly=weekly, plan={"block": "build", "weeks_to_event": 14}),
        "quality",
        THRESHOLDS,
    )
    assert rec["replan"] is None


def test_replan_is_null_without_a_complete_week():
    assert recommend(_digest(), "quality", THRESHOLDS)["replan"] is None


def test_downgrade_never_disturbs_a_rest_day():
    signals = [_sig("HRV_LOW_MORNING")]
    rec = recommend(_digest(signals=signals, zones=_zones()), "rest", THRESHOLDS)
    assert rec["intended_type"] == "rest"
    assert rec["intensity_cap"] is None
    assert rec["pace_target_s_per_km"] is None
    assert rec["downgraded"] is False
    assert rec["rationale"] == []


# --- issue #21: the widened plan vocabulary ----------------------------------


def test_intent_rank_covers_the_whole_plan_vocabulary():
    """Guard: recommend() indexes _INTENT_RANK directly, so any intent a plan file
    may legally carry must have a rank or the recommendation KeyErrors."""
    from garmin_coach.coach.recommend import _INTENT_RANK
    from garmin_coach.core import plan

    assert set(_INTENT_RANK) == set(plan.INTENTS)


def test_taper_caps_a_planned_crossfit_day_like_any_other_quality_day():
    digest = _digest(signals=[_sig("TAPER_ACTIVE")], zones=_zones())
    rec = recommend(digest, "crossfit", THRESHOLDS)

    assert rec["intended_type"] == "crossfit"
    assert rec["intensity_cap"] == "Z4"


def test_a_planned_strength_day_gets_no_running_pace_target():
    """Strength is hard work, but a threshold *pace* is meaningless for lifting."""
    rec = recommend(_digest(zones=_zones()), "strength", THRESHOLDS)

    assert rec["intended_type"] == "strength"
    assert rec["pace_target_s_per_km"] is None


def test_a_stress_signal_still_softens_a_planned_strength_day():
    digest = _digest(signals=[_sig("HRV_LOW_MORNING")], zones=_zones())
    rec = recommend(digest, "strength", THRESHOLDS)

    assert rec["intended_type"] == "easy"
    assert rec["downgraded"] is True
