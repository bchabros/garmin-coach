"""Phase 11 author tests: ``author`` and ``to_garmin`` as pure functions.

Seam 1: the pure authoring boundary (a workout request + finished-mart context
-> a workout spec, and the spec -> Garmin workout JSON). No DB, no Garmin. Later
tickets add tempo/quality structure, athlete requests, and hybrid validation.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from garmin_coach.workouts.author import (
    HyroxSplitRequired,
    author,
    copy_to_date,
    request_from_recommendation,
    to_garmin,
)

_TODAY = _dt.date.today().isoformat()


def _zones(source="regression+lthr", z2_ceiling=330, thr=270, z1_hi=140, z2_hi=155):
    return {
        "source": source,
        "z2_pace_ceiling_s_per_km": z2_ceiling,
        "threshold_pace_s_per_km": thr,
        "z1_hi_bpm": z1_hi,
        "z2_hi_bpm": z2_hi,
        "z3_hi_bpm": 168,
        "z4_hi_bpm": 178,
        "lthr_bpm": 172,
    }


def _request(session_type="easy", date="2026-07-17", pace=330, cap="Z2", origin="recommender"):
    return {
        "sport": "run",
        "origin": origin,
        "date": date,
        "session_type": session_type,
        "intensity_cap": cap,
        "pace_target_s_per_km": pace,
        "structure": None,
    }


def _kinds(steps):
    return [s["kind"] for s in steps]


_UNSET = object()


def _context(zones=_UNSET, today="2026-07-15", planned_intent=None):
    return {
        "zones": _zones() if zones is _UNSET else zones,
        "today": today,
        "planned_intent": planned_intent,
    }


# --- easy expansion ---------------------------------------------------------


def test_easy_expands_to_one_work_step_with_a_pace_band():
    spec = author(_request(), _context())
    assert spec is not None
    assert spec["sport"] == "run"
    assert spec["session_type"] == "easy"
    assert spec["date"] == "2026-07-17"
    assert len(spec["steps"]) == 1
    step = spec["steps"][0]
    assert step["kind"] == "work"
    assert step["end"] == {"type": "time", "seconds": 2700}
    assert step["target"]["type"] == "pace_band"
    # easy: never faster than the Z2 ceiling, a margin slower is allowed
    assert step["target"]["fast_s_per_km"] == 330
    assert step["target"]["slow_s_per_km"] == 370
    assert spec["warnings"] == []


def test_spec_name_is_gc_prefixed():
    spec = author(_request(), _context())
    assert spec["name"] == "GC 2026-07-17 easy"


# --- target degradation: pace -> HR -> none ---------------------------------


def test_no_measured_pace_degrades_to_hr_band():
    req = _request(pace=None, cap=None)
    spec = author(req, _context(zones=_zones(source="threshold_pace_fallback")))
    step = spec["steps"][0]
    assert step["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}
    assert any("heart rate" in w for w in spec["warnings"])


def test_no_pace_and_no_hr_band_degrades_to_none():
    req = _request(pace=None, cap=None)
    zones = _zones()
    zones["z2_hi_bpm"] = None
    spec = author(req, _context(zones=zones))
    step = spec["steps"][0]
    assert step["target"] == {"type": "none"}
    assert any("no target" in w for w in spec["warnings"])


def test_missing_zones_entirely_degrades_to_none():
    req = _request(pace=None, cap=None)
    spec = author(req, _context(zones=None))
    assert spec["steps"][0]["target"] == {"type": "none"}


# --- date guards ------------------------------------------------------------


def test_past_date_is_refused():
    with pytest.raises(ValueError, match="past"):
        author(_request(date="2026-07-10"), _context(today="2026-07-15"))


def test_today_is_allowed_with_a_warning():
    spec = author(_request(date="2026-07-15"), _context(today="2026-07-15"))
    assert spec is not None
    assert any("today" in w for w in spec["warnings"])


# --- plan guard (issue #22) --------------------------------------------------


def test_a_session_harder_than_the_plan_of_record_is_refused():
    """The 2026-07-17 case: the plan had been downgraded to easy hours before a
    quality session was authored against it."""
    with pytest.raises(ValueError, match="planned as easy"):
        author(_request(session_type="quality", pace=270), _context(planned_intent="easy"))


def test_a_session_softer_than_the_plan_of_record_is_allowed():
    """The downgrade contract: softening is what the recommender does all day."""
    spec = author(_request(session_type="easy"), _context(planned_intent="quality"))

    assert spec["session_type"] == "easy"


def test_the_planned_session_itself_is_allowed():
    spec = author(_request(session_type="quality", pace=270), _context(planned_intent="quality"))

    assert spec["session_type"] == "quality"


def test_nothing_is_guarded_when_the_context_carries_no_plan():
    spec = author(_request(session_type="quality", pace=270), _context())

    assert spec["session_type"] == "quality"


def test_the_plan_guard_outranks_the_hyrox_split_question():
    """A refusal must not be reachable only after answering a follow-up question."""
    with pytest.raises(ValueError, match="planned as easy"):
        author(_request(session_type="hyrox"), _context(planned_intent="easy"))


# --- rest -------------------------------------------------------------------


def test_rest_produces_no_spec():
    assert author(_request(session_type="rest"), _context()) is None


# --- tempo structure --------------------------------------------------------


def test_tempo_expands_to_warmup_work_cooldown():
    spec = author(_request(session_type="tempo", pace=270), _context())
    assert _kinds(spec["steps"]) == ["warmup", "work", "cooldown"]
    warmup, work, cooldown = spec["steps"]
    assert warmup["target"] == {"type": "none"}
    assert cooldown["target"] == {"type": "none"}
    # work targets a band around threshold pace
    assert work["target"]["type"] == "pace_band"
    assert work["target"]["fast_s_per_km"] == 265
    assert work["target"]["slow_s_per_km"] == 275


def test_tempo_degrades_to_z4_hr_band_without_measured_pace():
    spec = author(
        _request(session_type="tempo", pace=None, cap=None),
        _context(zones=_zones(source="threshold_pace_fallback")),
    )
    work = spec["steps"][1]
    assert work["target"] == {"type": "hr_band", "low_bpm": 168, "high_bpm": 178}
    assert any("heart rate" in w for w in spec["warnings"])


# --- quality intervals ------------------------------------------------------


def test_quality_expands_to_warmup_repeat_cooldown():
    spec = author(_request(session_type="quality", pace=270), _context())
    assert _kinds(spec["steps"]) == ["warmup", "repeat", "cooldown"]
    repeat = spec["steps"][1]
    assert repeat["reps"] == 4
    assert _kinds(repeat["steps"]) == ["work", "recovery"]
    assert repeat["steps"][0]["target"]["type"] == "pace_band"
    assert repeat["steps"][1]["target"] == {"type": "none"}


def test_to_garmin_translates_a_repeat_group():
    spec = author(_request(session_type="quality", pace=270), _context())
    payload = to_garmin(spec)
    steps = payload["workoutSegments"][0]["workoutSteps"]
    assert steps[1]["type"] == "RepeatGroupDTO"
    assert steps[1]["numberOfIterations"] == 4
    nested = steps[1]["workoutSteps"]
    assert nested[0]["stepType"]["stepTypeKey"] == "interval"
    assert nested[1]["stepType"]["stepTypeKey"] == "recovery"


def test_to_garmin_estimated_duration_counts_repeat_iterations():
    spec = author(_request(session_type="quality", pace=270), _context())
    payload = to_garmin(spec)
    # warmup 600 + 4 * (work 180 + recovery 120) + cooldown 600 = 2400
    assert payload["estimatedDurationInSecs"] == 2400


# --- request_from_recommendation --------------------------------------------


def test_request_from_recommendation_maps_the_block():
    rec = {
        "target_date": "2026-07-17",
        "intended_type": "easy",
        "intensity_cap": "Z2",
        "pace_target_s_per_km": 330,
    }
    req = request_from_recommendation(rec)
    assert req["sport"] == "run"
    assert req["origin"] == "recommender"
    assert req["date"] == "2026-07-17"
    assert req["session_type"] == "easy"
    assert req["pace_target_s_per_km"] == 330
    # a recommendation-built request authors end to end
    spec = author(req, _context())
    assert spec["name"] == "GC 2026-07-17 easy"


def test_request_from_recommendation_auto_maps_strength():
    rec = {"target_date": "2026-07-17", "intended_type": "strength"}
    req = request_from_recommendation(rec)
    assert req["sport"] == "strength"
    assert req["session_type"] == "strength"


def test_request_from_recommendation_auto_maps_crossfit_to_hiit():
    rec = {"target_date": "2026-07-17", "intended_type": "crossfit"}
    req = request_from_recommendation(rec)
    assert req["sport"] == "hiit"


def test_request_from_recommendation_hyrox_stays_run_and_asks():
    rec = {"target_date": "2026-07-17", "intended_type": "hyrox"}
    req = request_from_recommendation(rec)
    assert req["sport"] == "run"
    with pytest.raises(HyroxSplitRequired):
        author(req, _context())


def test_request_from_recommendation_explicit_sport_wins():
    rec = {"target_date": "2026-07-17", "intended_type": "hyrox"}
    assert request_from_recommendation(rec, sport="hiit")["sport"] == "hiit"


# --- athlete request: schema validation -------------------------------------


def test_athlete_request_authors_like_any_other():
    spec = author(_request(session_type="tempo", pace=270, origin="athlete"), _context())
    assert _kinds(spec["steps"]) == ["warmup", "work", "cooldown"]
    assert spec["origin"] == "athlete"


def test_malformed_request_is_rejected():
    bad = _request()
    del bad["session_type"]
    with pytest.raises(ValueError, match="session_type"):
        author(bad, _context())


def test_unknown_sport_is_rejected():
    with pytest.raises(ValueError, match="sport"):
        author(_request() | {"sport": "swim"}, _context())


# --- sport gating -----------------------------------------------------------


def test_hiit_sport_rejects_run_session_types():
    # hiit no longer defers; it authors, but only its own session types
    with pytest.raises(ValueError, match="not valid for sport"):
        author(_request(origin="athlete") | {"sport": "hiit"}, _context())


def test_strength_sport_rejects_a_run_session_type():
    # strength no longer defers; it authors, but only its own session type
    with pytest.raises(ValueError, match="not valid for sport"):
        author(_request(origin="athlete") | {"sport": "strength"}, _context())


def test_hyrox_session_requires_a_split_decision():
    with pytest.raises(HyroxSplitRequired):
        author(_request(session_type="hyrox"), _context())


# --- strength authoring ------------------------------------------------------


def _strength_request(exercises=None, date="2026-07-17", origin="athlete"):
    if exercises is None:
        exercises = [{"exercise": "back_squat", "sets": 2, "reps": 5, "weight_kg": 100}]
    return {
        "sport": "strength",
        "origin": origin,
        "date": date,
        "session_type": "strength",
        "structure": {"exercises": exercises},
    }


def test_strength_expands_exercise_entries_into_per_set_steps():
    spec = author(_strength_request(), _context())
    assert spec["sport"] == "strength"
    assert spec["session_type"] == "strength"
    assert spec["name"] == "GC 2026-07-17 strength"
    # 2 sets -> work, rest, work: one flat step per set, the trailing rest skipped
    assert _kinds(spec["steps"]) == ["work", "rest", "work"]
    work = spec["steps"][0]
    assert work["end"] == {"type": "reps", "count": 5}
    assert work["exercise"] == {"category": "SQUAT", "name": "BARBELL_BACK_SQUAT"}
    assert work["weight_kg"] == 100
    assert spec["steps"][1]["end"] == {"type": "time", "seconds": 90}
    assert spec["warnings"] == []


def test_strength_rest_override_and_lap():
    spec = author(
        _strength_request(
            [
                {"exercise": "back_squat", "sets": 2, "reps": 5, "rest": {"s": 120}},
                {"exercise": "back_squat", "sets": 2, "reps": 5, "rest": "lap"},
            ]
        ),
        _context(),
    )
    assert _kinds(spec["steps"]) == ["work", "rest", "work", "rest", "work", "rest", "work"]
    assert spec["steps"][1]["end"] == {"type": "time", "seconds": 120}
    # the second entry's rests end on the lap button; the trailing one is skipped
    assert spec["steps"][5]["end"] == {"type": "lap"}


def test_strength_unknown_exercise_warns_and_becomes_the_step_notes():
    # the ski erg has no entry in Garmin's taxonomy - the canonical unmapped case
    spec = author(
        _strength_request([{"exercise": "ski erg", "sets": 1, "reps": 10}]),
        _context(),
    )
    assert any(
        "'ski erg' is not a Garmin exercise; shown as the step's notes" in w
        for w in spec["warnings"]
    )
    assert "exercise" not in spec["steps"][0]
    assert spec["steps"][0]["label"] == "ski erg"


def test_a_resolved_exercise_carries_no_notes():
    spec = author(_strength_request(), _context())
    assert "label" not in spec["steps"][0]


def test_an_unknown_exercise_keeps_the_athlete_words_as_written():
    """An EMOM block is a sentence, not a name: it goes out as typed (issue #76)."""
    block = "EMOM 1: 14 kcal row, 20 WBS, 10 burpees"
    spec = author(_strength_request([{"exercise": block, "sets": 1, "reps": 1}]), _context())
    assert spec["steps"][0]["label"] == block


def test_strength_time_ended_entry():
    spec = author(
        _strength_request([{"exercise": "back_squat", "sets": 1, "time": {"s": 45}}]),
        _context(),
    )
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 45}


def test_strength_requires_exercises():
    request = _strength_request()
    request["structure"] = None
    with pytest.raises(ValueError, match="exercises"):
        author(request, _context())


def test_strength_entry_needs_exactly_one_of_reps_or_time():
    entries = [{"exercise": "back_squat", "sets": 1, "reps": 5, "time": {"s": 45}}]
    with pytest.raises(ValueError, match="reps or time"):
        author(_strength_request(entries), _context())


def test_strength_structure_rejects_run_keys():
    request = _strength_request()
    request["structure"]["work_min"] = 20
    with pytest.raises(ValueError, match="unknown structure keys"):
        author(request, _context())


# --- hiit authoring ----------------------------------------------------------


def _hiit_request(session_type="crossfit", exercises=None, date="2026-07-17"):
    if exercises is None:
        exercises = [{"exercise": "back_squat", "sets": 2, "time": {"s": 40}}]
    return {
        "sport": "hiit",
        "origin": "athlete",
        "date": date,
        "session_type": session_type,
        "structure": {"exercises": exercises},
    }


def test_hiit_expands_stations_with_its_own_rest_default():
    spec = author(_hiit_request(), _context())
    assert spec["sport"] == "hiit"
    assert spec["name"] == "GC 2026-07-17 crossfit"
    assert _kinds(spec["steps"]) == ["work", "rest", "work"]
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 40}
    # hiit rests default to 60 s, not strength's 90
    assert spec["steps"][1]["end"] == {"type": "time", "seconds": 60}


def test_hiit_hyrox_session_authors_as_stations():
    spec = author(
        _hiit_request(
            session_type="hyrox", exercises=[{"exercise": "wall balls", "sets": 1, "reps": 30}]
        ),
        _context(),
    )
    assert spec["name"] == "GC 2026-07-17 hyrox"
    assert spec["steps"][0]["end"] == {"type": "reps", "count": 30}


def test_to_garmin_hiit_payload_uses_sport_type_9():
    payload = to_garmin(author(_hiit_request(), _context()))
    assert payload["sportType"] == {"sportTypeId": 9, "sportTypeKey": "hiit", "displayOrder": 9}
    assert payload["workoutSegments"][0]["sportType"]["sportTypeKey"] == "hiit"


# --- hybrid validation ------------------------------------------------------


def _context_with_rec(intended_type, rationale, **kw):
    ctx = _context(**kw)
    ctx["recommendation"] = {"intended_type": intended_type, "rationale": rationale}
    return ctx


def test_athlete_overriding_advice_gets_a_cited_warning():
    ctx = _context_with_rec("easy", ["HRV_LOW_MORNING", "ACWR_OUT_OF_RANGE"])
    spec = author(_request(session_type="tempo", pace=270, origin="athlete"), ctx)
    warning = next(w for w in spec["warnings"] if "recommender" in w)
    assert "tempo" in warning and "easy" in warning
    assert "HRV_LOW_MORNING" in warning


def test_athlete_matching_advice_gets_no_override_warning():
    ctx = _context_with_rec("tempo", [])
    spec = author(_request(session_type="tempo", pace=270, origin="athlete"), ctx)
    assert not any("recommender advises" in w for w in spec["warnings"])


def test_athlete_strength_against_easy_advice_warns():
    ctx = _context_with_rec("easy", ["HRV_LOW_MORNING"])
    spec = author(_strength_request(), ctx)
    warning = next(w for w in spec["warnings"] if "recommender" in w)
    assert "strength" in warning and "easy" in warning


def test_athlete_crossfit_against_strength_advice_warns():
    ctx = _context_with_rec("strength", [])
    spec = author(_hiit_request(), ctx)
    assert any("recommender advises strength" in w for w in spec["warnings"])


def test_athlete_strength_against_quality_advice_does_not_warn():
    ctx = _context_with_rec("quality", [])
    spec = author(_strength_request(), ctx)
    assert not any("recommender advises" in w for w in spec["warnings"])


def test_recommender_origin_is_never_hybrid_validated():
    ctx = _context_with_rec("easy", ["HRV_LOW_MORNING"])
    spec = author(_request(session_type="tempo", pace=270, origin="recommender"), ctx)
    assert not any("recommender advises" in w for w in spec["warnings"])


# --- structure override -----------------------------------------------------


def test_structure_override_sets_quality_reps_and_durations():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"reps": 6, "work_min": 4, "recovery_min": 1}
    spec = author(req, _context())
    repeat = spec["steps"][1]
    assert repeat["reps"] == 6
    assert repeat["steps"][0]["end"]["seconds"] == 240
    assert repeat["steps"][1]["end"]["seconds"] == 60


def test_structure_override_sets_easy_duration():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"duration_min": 60}
    spec = author(req, _context())
    assert spec["steps"][0]["end"]["seconds"] == 3600


# --- to_garmin --------------------------------------------------------------


def test_to_garmin_produces_a_running_workout_shape():
    spec = author(_request(), _context())
    payload = to_garmin(spec)
    assert payload["workoutName"] == "GC 2026-07-17 easy"
    assert payload["sportType"]["sportTypeKey"] == "running"
    segments = payload["workoutSegments"]
    assert len(segments) == 1
    steps = segments[0]["workoutSteps"]
    assert len(steps) == 1
    assert steps[0]["stepType"]["stepTypeKey"] == "interval"
    assert steps[0]["endCondition"]["conditionTypeKey"] == "time"
    assert steps[0]["endConditionValue"] == 2700.0


def test_to_garmin_encodes_a_pace_band_as_a_speed_range():
    spec = author(_request(), _context())
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    # slow bound -> lower speed (targetValueOne), fast bound -> higher speed (targetValueTwo)
    assert step["targetValueOne"] == pytest.approx(1000 / 370)
    assert step["targetValueTwo"] == pytest.approx(1000 / 330)


def test_to_garmin_encodes_an_hr_band():
    req = _request(pace=None, cap=None)
    spec = author(req, _context(zones=_zones(source="threshold_pace_fallback")))
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert step["targetValueOne"] == 140
    assert step["targetValueTwo"] == 155


def test_to_garmin_no_target_step_has_no_target_type():
    req = _request(pace=None, cap=None)
    spec = author(req, _context(zones=None))
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert step["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_to_garmin_strength_payload_matches_the_probe_shape():
    spec = author(_strength_request(), _context())
    payload = to_garmin(spec)
    assert payload["workoutName"] == "GC 2026-07-17 strength"
    assert payload["sportType"] == {
        "sportTypeId": 5,
        "sportTypeKey": "strength_training",
        "displayOrder": 5,
    }
    segment = payload["workoutSegments"][0]
    assert segment["sportType"]["sportTypeKey"] == "strength_training"
    steps = segment["workoutSteps"]
    assert [s["stepOrder"] for s in steps] == [1, 2, 3]
    work = steps[0]
    assert work["type"] == "ExecutableStepDTO"
    assert work["stepType"]["stepTypeKey"] == "interval"
    assert work["endCondition"]["conditionTypeKey"] == "reps"
    assert work["endConditionValue"] == 5.0
    assert work["category"] == "SQUAT"
    assert work["exerciseName"] == "BARBELL_BACK_SQUAT"
    assert work["weightValue"] == 100.0
    assert work["weightUnit"]["unitKey"] == "kilogram"
    rest = steps[1]
    assert rest["stepType"]["stepTypeKey"] == "rest"
    assert rest["endCondition"]["conditionTypeKey"] == "time"
    assert rest["endConditionValue"] == 90.0


def test_to_garmin_strength_unknown_exercise_step_carries_only_its_notes():
    spec = author(
        _strength_request([{"exercise": "ski erg", "sets": 1, "reps": 10}]),
        _context(),
    )
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert "category" not in step
    assert "exerciseName" not in step
    assert "weightValue" not in step
    assert step["description"] == "ski erg"


def test_to_garmin_resolved_exercise_step_carries_no_description():
    """Every exercise session pushed before #76 must still go up byte for byte."""
    steps = to_garmin(author(_strength_request(), _context()))["workoutSegments"][0]
    assert all("description" not in step for step in steps["workoutSteps"])


def test_to_garmin_strength_lap_rest_and_estimated_duration():
    spec = author(
        _strength_request(
            [
                {"exercise": "back_squat", "sets": 2, "reps": 5, "rest": "lap"},
                {"exercise": "back_squat", "sets": 1, "time": {"s": 45}},
            ]
        ),
        _context(),
    )
    payload = to_garmin(spec)
    lap_rest = payload["workoutSegments"][0]["workoutSteps"][1]
    assert lap_rest["endCondition"]["conditionTypeKey"] == "lap.button"
    assert lap_rest["endConditionValue"] is None
    # rep-ended sets are unknowable (0 s); only the 45 s time block counts
    assert payload["estimatedDurationInSecs"] == 45


def test_to_garmin_encodes_a_distance_ended_step():
    # the spec vocabulary allows a distance end condition; the translator honours it
    spec = {
        "sport": "run",
        "date": "2026-07-17",
        "session_type": "quality",
        "hardness": "threshold",
        "name": "GC 2026-07-17 quality",
        "steps": [
            {
                "kind": "work",
                "end": {"type": "distance", "metres": 1000},
                "target": {"type": "none"},
            }
        ],
        "warnings": [],
    }
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert step["endCondition"]["conditionTypeKey"] == "distance"
    assert step["endConditionValue"] == 1000.0


# --- Phase 11a: uniform end conditions (lap / distance from a request) -------


def test_lap_ended_warmup_and_cooldown():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": "lap", "cooldown_end": "lap"}
    spec = author(req, _context())
    warmup, work, cooldown = spec["steps"]
    assert warmup["end"] == {"type": "lap"}
    assert cooldown["end"] == {"type": "lap"}
    # work still time-ended by default
    assert work["end"]["type"] == "time"


def test_lap_ended_recovery_in_quality():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"recovery_end": "lap"}
    spec = author(req, _context())
    recovery = spec["steps"][1]["steps"][1]
    assert recovery["end"] == {"type": "lap"}


def test_distance_ended_work_interval_from_request():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"reps": 8, "work_end": {"distance_m": 1000}}
    spec = author(req, _context())
    repeat = spec["steps"][1]
    assert repeat["reps"] == 8
    assert repeat["steps"][0]["end"] == {"type": "distance", "metres": 1000}


def test_distance_ended_easy_run():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"work_end": {"distance_m": 8000}}
    spec = author(req, _context())
    assert spec["steps"][0]["end"] == {"type": "distance", "metres": 8000}


def test_minutes_via_end_descriptor_matches_the_old_min_key():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": {"min": 5}}
    spec = author(req, _context())
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 300}


def test_fractional_minutes_round_rather_than_truncate():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": {"min": 2.5}}
    spec = author(req, _context())
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 150}


def test_fractional_minutes_round_to_the_nearest_second():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": {"min": 1.5}}
    spec = author(req, _context())
    assert spec["steps"][0]["end"]["seconds"] == 90


def test_fractional_recovery_min_alias_matches_the_end_descriptor():
    end_req = _request(session_type="quality", pace=270, origin="athlete")
    end_req["structure"] = {"recovery_end": {"min": 2.5}}
    alias_req = _request(session_type="quality", pace=270, origin="athlete")
    alias_req["structure"] = {"recovery_min": 2.5}
    end_recovery = author(end_req, _context())["steps"][1]["steps"][1]["end"]
    alias_recovery = author(alias_req, _context())["steps"][1]["steps"][1]["end"]
    assert end_recovery["seconds"] == 150
    assert alias_recovery == end_recovery


def test_to_garmin_encodes_a_lap_button_end():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": "lap"}
    spec = author(req, _context())
    warmup = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert warmup["endCondition"]["conditionTypeKey"] == "lap.button"
    assert warmup["endCondition"]["conditionTypeId"] == 1
    assert warmup.get("endConditionValue") is None


# --- Phase 11a: structure validation ----------------------------------------


def test_lap_on_a_work_step_is_refused():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"work_end": "lap"}
    with pytest.raises(ValueError, match="work"):
        author(req, _context())


def test_end_and_min_clash_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": {"min": 5}, "warmup_min": 10}
    with pytest.raises(ValueError, match="warmup"):
        author(req, _context())


def test_easy_work_end_and_duration_clash_is_refused():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"work_end": {"distance_m": 8000}, "duration_min": 60}
    with pytest.raises(ValueError, match="work"):
        author(req, _context())


def test_non_positive_distance_is_refused():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"work_end": {"distance_m": 0}}
    with pytest.raises(ValueError, match="distance"):
        author(req, _context())


def test_non_positive_minutes_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmup_end": {"min": -3}}
    with pytest.raises(ValueError, match="min"):
        author(req, _context())


# --- Phase 11a: custom pace band --------------------------------------------


def test_explicit_pace_band_is_used_verbatim_on_quality_work():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"work_pace_band": [220, 240]}
    spec = author(req, _context())
    work = spec["steps"][1]["steps"][0]
    assert work["target"] == {"type": "pace_band", "fast_s_per_km": 220, "slow_s_per_km": 240}


def test_explicit_band_on_tempo_work():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"work_pace_band": [220, 240]}
    spec = author(req, _context())
    assert spec["steps"][1]["target"] == {
        "type": "pace_band",
        "fast_s_per_km": 220,
        "slow_s_per_km": 240,
    }


def test_explicit_band_on_easy_work():
    req = _request(session_type="easy", pace=330, origin="athlete")
    req["structure"] = {"work_pace_band": [300, 320]}
    spec = author(req, _context())
    assert spec["steps"][0]["target"] == {
        "type": "pace_band",
        "fast_s_per_km": 300,
        "slow_s_per_km": 320,
    }


def test_explicit_band_overrides_recommender_pace_and_suppresses_degradation():
    # no zones at all: without a band this would degrade to "none" with a warning
    req = _request(session_type="tempo", pace=None, cap=None, origin="athlete")
    req["structure"] = {"work_pace_band": [220, 240]}
    spec = author(req, _context(zones=None))
    assert spec["steps"][1]["target"] == {
        "type": "pace_band",
        "fast_s_per_km": 220,
        "slow_s_per_km": 240,
    }
    assert not any("no target" in w or "heart rate" in w for w in spec["warnings"])


def test_inverted_pace_band_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"work_pace_band": [240, 220]}
    with pytest.raises(ValueError, match="work_pace_band"):
        author(req, _context())


def test_non_positive_pace_band_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"work_pace_band": [0, 240]}
    with pytest.raises(ValueError, match="work_pace_band"):
        author(req, _context())


def test_pace_band_harder_than_recommendation_warns_cited():
    ctx = _context_with_rec("tempo", ["ACWR_OUT_OF_RANGE"])
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"work_pace_band": [220, 240]}
    spec = author(req, ctx)
    warning = next(w for w in spec["warnings"] if "faster" in w)
    assert "220-240" in warning
    assert "ACWR_OUT_OF_RANGE" in warning


def test_pace_band_fast_bound_beyond_recommendation_warns():
    # fast bound 200 is well beyond the suggested 230 even though the band straddles it
    ctx = _context_with_rec("tempo", ["HRV_LOW_MORNING"])
    req = _request(session_type="tempo", pace=230, origin="athlete")
    req["structure"] = {"work_pace_band": [200, 240]}
    spec = author(req, ctx)
    assert any("faster than the recommended" in w for w in spec["warnings"])


def test_pace_band_near_recommendation_does_not_warn():
    # fast bound 225 is within the small margin of the suggested 230 -> no warning
    req = _request(session_type="tempo", pace=230, origin="athlete")
    req["structure"] = {"work_pace_band": [225, 240]}
    spec = author(req, _context())
    assert not any("faster than the recommended" in w for w in spec["warnings"])


def test_unknown_structure_key_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"warmp_end": "lap"}  # typo for warmup_end
    with pytest.raises(ValueError, match="unknown structure"):
        author(req, _context())


def test_a_pause_without_a_repeat_count_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"recovery_end": {"min": 2}}  # a recovery runs between repeats
    with pytest.raises(ValueError, match="recovery_end"):
        author(req, _context())


# --- Phase 11a: duration estimate -------------------------------------------


def test_estimate_counts_distance_reps_by_band_midpoint():
    req = _request(session_type="quality", origin="athlete")
    req["structure"] = {
        "reps": 8,
        "warmup_end": "lap",
        "work_end": {"distance_m": 1000},
        "work_pace_band": [220, 240],
        "recovery_end": {"min": 2},
        "cooldown_end": "lap",
    }
    payload = to_garmin(author(req, _context()))
    # 8 * (1km @ midpoint 230 s/km = 230s + 120s recovery) = 2800; lap warmup/cooldown 0
    assert payload["estimatedDurationInSecs"] == 2800


def test_estimate_ignores_lap_and_paceless_distance():
    spec = {
        "sport": "run",
        "date": "2026-07-17",
        "session_type": "quality",
        "hardness": "threshold",
        "name": "GC 2026-07-17 quality",
        "steps": [
            {"kind": "warmup", "end": {"type": "lap"}, "target": {"type": "none"}},
            {
                "kind": "work",
                "end": {"type": "distance", "metres": 1000},
                "target": {"type": "none"},
            },
        ],
        "warnings": [],
    }
    assert to_garmin(spec)["estimatedDurationInSecs"] == 0


# --- Phase 11a: canonical tempo fixture (end to end) ------------------------


def test_canonical_tempo_fixture_authors_end_to_end(fixture):
    req = fixture("tempo_request")
    spec = author(req, _context(today="2026-07-15"))
    assert _kinds(spec["steps"]) == ["warmup", "repeat", "cooldown"]
    warmup, repeat, cooldown = spec["steps"]
    assert warmup["end"] == {"type": "lap"}
    assert cooldown["end"] == {"type": "lap"}
    assert repeat["reps"] == 8
    work, recovery = repeat["steps"]
    assert work["end"] == {"type": "distance", "metres": 1000}
    assert work["target"] == {"type": "pace_band", "fast_s_per_km": 220, "slow_s_per_km": 240}
    assert recovery["end"] == {"type": "time", "seconds": 120}
    # typed JSON: lap warmup, distance work with a pace target
    steps = to_garmin(spec)["workoutSegments"][0]["workoutSteps"]
    assert steps[0]["endCondition"]["conditionTypeKey"] == "lap.button"
    nested_work = steps[1]["workoutSteps"][0]
    assert nested_work["endCondition"]["conditionTypeKey"] == "distance"
    assert nested_work["endConditionValue"] == 1000.0
    assert nested_work["targetType"]["workoutTargetTypeKey"] == "pace.zone"


# --- issue #45: default targets pinned role by role -------------------------
#
# The whole of issue #24 rests on one promise: a request that names no target
# authors exactly what it authored before. These pin the full spec so a later
# ticket cannot quietly move a default while the behavioural tests still pass.


def test_default_easy_spec_is_unchanged():
    assert author(_request(session_type="easy"), _context()) == {
        "sport": "run",
        "origin": "recommender",
        "date": "2026-07-17",
        "session_type": "easy",
        "hardness": "easy",
        "name": "GC 2026-07-17 easy",
        "steps": [
            {
                "kind": "work",
                "end": {"type": "time", "seconds": 2700},
                "target": {"type": "pace_band", "fast_s_per_km": 330, "slow_s_per_km": 370},
            }
        ],
        "warnings": [],
    }


def test_default_tempo_spec_is_unchanged():
    assert author(_request(session_type="tempo"), _context()) == {
        "sport": "run",
        "origin": "recommender",
        "date": "2026-07-17",
        "session_type": "tempo",
        "hardness": "threshold",
        "name": "GC 2026-07-17 tempo",
        "steps": [
            {
                "kind": "warmup",
                "end": {"type": "time", "seconds": 600},
                "target": {"type": "none"},
            },
            {
                "kind": "work",
                "end": {"type": "time", "seconds": 1200},
                "target": {"type": "pace_band", "fast_s_per_km": 325, "slow_s_per_km": 335},
            },
            {
                "kind": "cooldown",
                "end": {"type": "time", "seconds": 600},
                "target": {"type": "none"},
            },
        ],
        "warnings": [],
    }


def test_default_quality_spec_is_unchanged():
    assert author(_request(session_type="quality"), _context()) == {
        "sport": "run",
        "origin": "recommender",
        "date": "2026-07-17",
        "session_type": "quality",
        "hardness": "threshold",
        "name": "GC 2026-07-17 quality",
        "steps": [
            {
                "kind": "warmup",
                "end": {"type": "time", "seconds": 600},
                "target": {"type": "none"},
            },
            {
                "kind": "repeat",
                "reps": 4,
                "steps": [
                    {
                        "kind": "work",
                        "end": {"type": "time", "seconds": 180},
                        "target": {
                            "type": "pace_band",
                            "fast_s_per_km": 325,
                            "slow_s_per_km": 335,
                        },
                    },
                    {
                        "kind": "recovery",
                        "end": {"type": "time", "seconds": 120},
                        "target": {"type": "none"},
                    },
                ],
            },
            {
                "kind": "cooldown",
                "end": {"type": "time", "seconds": 600},
                "target": {"type": "none"},
            },
        ],
        "warnings": [],
    }


def test_default_athlete_origin_spec_matches_the_recommender_one():
    # the pins above all run recommender-origin; an athlete request that sets no
    # target must author the same steps, so origin alone moves nothing.
    athlete = _request(session_type="quality", pace=270, origin="athlete")
    athlete["structure"] = {}
    recommender = _request(session_type="quality", pace=270)
    assert author(athlete, _context())["steps"] == author(recommender, _context())["steps"]


def test_default_work_degradation_chain_and_warning_are_unchanged():
    # no measured pace and no zones: the work role still degrades to no target,
    # still says so once, and the other roles stay silent.
    spec = author(_request(session_type="quality", pace=None, cap=None), _context(zones=None))
    assert spec["steps"][1]["steps"][0]["target"] == {"type": "none"}
    assert spec["warnings"] == [
        "no target: no measured pace or heart-rate band; time only",
        "no hardness measured: the zone ladder cannot rank this session's targets; "
        "the plan guard falls back to the session type",
    ]


# --- issue #46: explicit intensity bands on every step role ------------------


def _targets(session_type="quality", pace=270, **structure):
    """Author a session whose structure carries only the given target keys."""
    req = _request(session_type=session_type, pace=pace, origin="athlete")
    req["structure"] = structure
    return req


def test_explicit_hr_band_on_a_warmup():
    spec = author(_targets(warmup_target={"hr_band": [120, 145]}), _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 120, "high_bpm": 145}


def test_explicit_hr_band_on_a_cooldown():
    spec = author(_targets(cooldown_target={"hr_band": [110, 140]}), _context())
    assert spec["steps"][2]["target"] == {"type": "hr_band", "low_bpm": 110, "high_bpm": 140}


def test_explicit_hr_band_on_a_recovery_inside_the_repeat():
    spec = author(_targets(recovery_target={"hr_band": [115, 135]}), _context())
    recovery = spec["steps"][1]["steps"][1]
    assert recovery["target"] == {"type": "hr_band", "low_bpm": 115, "high_bpm": 135}


def test_explicit_pace_band_on_a_warmup():
    spec = author(
        _targets(session_type="tempo", warmup_target={"pace_band": [330, 360]}), _context()
    )
    assert spec["steps"][0]["target"] == {
        "type": "pace_band",
        "fast_s_per_km": 330,
        "slow_s_per_km": 360,
    }


def test_explicit_band_needs_no_zones_row():
    req = _targets(pace=None, warmup_target={"hr_band": [120, 145]})
    spec = author(req, _context(zones=None))
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 120, "high_bpm": 145}


def test_none_target_authors_no_target():
    spec = author(_targets(warmup_target="none"), _context())
    assert spec["steps"][0]["target"] == {"type": "none"}


def test_easy_gains_a_work_target():
    req = _targets(session_type="easy", pace=330, work_target={"hr_band": [130, 150]})
    spec = author(req, _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 130, "high_bpm": 150}


def test_work_target_pace_band_matches_the_older_spelling():
    band = author(_targets(work_target={"pace_band": [220, 240]}), _context())
    legacy = author(_targets(work_pace_band=[220, 240]), _context())
    assert band["steps"][1]["steps"][0]["target"] == legacy["steps"][1]["steps"][0]["target"]


def test_work_target_and_work_pace_band_clash_is_refused():
    req = _targets(work_target={"pace_band": [220, 240]}, work_pace_band=[220, 240])
    with pytest.raises(ValueError, match="both work_target and work_pace_band"):
        author(req, _context())


def test_work_target_pace_band_still_warns_when_faster_than_advised():
    ctx = _context_with_rec("quality", ["ACWR_OUT_OF_RANGE"])
    req = _targets(work_target={"pace_band": [220, 240]})
    spec = author(req, ctx)
    assert any("faster than the recommended" in w for w in spec["warnings"])


def test_a_resolved_explicit_target_adds_no_warning():
    spec = author(_targets(warmup_target={"hr_band": [120, 145]}), _context())
    assert spec["warnings"] == []


def test_a_pause_target_without_a_repeat_count_is_refused():
    req = _targets(session_type="tempo", recovery_target={"hr_band": [115, 135]})
    with pytest.raises(ValueError, match="recovery_target"):
        author(req, _context())


def test_unknown_band_kind_is_refused():
    with pytest.raises(ValueError, match="warmup_target must be"):
        author(_targets(warmup_target={"power_band": [200, 240]}), _context())


def test_target_band_with_wrong_arity_is_refused():
    with pytest.raises(ValueError, match="warmup_target hr_band must be"):
        author(_targets(warmup_target={"hr_band": [120]}), _context())


def test_non_positive_target_band_is_refused():
    with pytest.raises(ValueError, match="warmup_target hr_band must be"):
        author(_targets(warmup_target={"hr_band": [0, 145]}), _context())


def test_inverted_target_hr_band_is_refused():
    with pytest.raises(ValueError, match="low bound must be below"):
        author(_targets(warmup_target={"hr_band": [145, 120]}), _context())


def test_inverted_target_pace_band_is_refused():
    with pytest.raises(ValueError, match="fast bound must be faster"):
        author(_targets(warmup_target={"pace_band": [360, 330]}), _context())


def test_to_garmin_encodes_an_explicit_warmup_hr_band():
    spec = author(_targets(session_type="tempo", warmup_target={"hr_band": [120, 145]}), _context())
    warmup = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert warmup["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert warmup["targetValueOne"] == 120
    assert warmup["targetValueTwo"] == 145


def test_to_garmin_encodes_an_explicit_warmup_pace_band():
    spec = author(
        _targets(session_type="tempo", warmup_target={"pace_band": [330, 360]}), _context()
    )
    warmup = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert warmup["targetType"]["workoutTargetTypeKey"] == "pace.zone"


def test_to_garmin_encodes_an_explicit_cooldown_hr_band():
    spec = author(
        _targets(session_type="tempo", cooldown_target={"hr_band": [110, 140]}), _context()
    )
    cooldown = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][2]
    assert cooldown["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert cooldown["targetValueOne"] == 110
    assert cooldown["targetValueTwo"] == 140


def test_to_garmin_encodes_a_recovery_hr_band_inside_the_repeat():
    spec = author(_targets(recovery_target={"hr_band": [115, 135]}), _context())
    recovery = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"][1]
    assert recovery["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert recovery["targetValueOne"] == 115
    assert recovery["targetValueTwo"] == 135


def test_untargeted_steps_do_not_share_one_target():
    # every step's target is its own dict; a shared literal would let a later
    # stage editing one untargeted step silently retarget all the others.
    spec = author(_targets(session_type="tempo", pace=None), _context(zones=None))
    warmup, work, cooldown = spec["steps"]
    assert warmup["target"] == work["target"] == cooldown["target"] == {"type": "none"}
    assert warmup["target"] is not cooldown["target"]
    assert warmup["target"] is not work["target"]


# --- issue #47: zone names resolved from athlete zones -----------------------


def test_zone_two_resolves_to_the_athletes_z2_band():
    spec = author(_targets(warmup_target="z2"), _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}


def test_zone_three_resolves_to_the_pair_above_it():
    spec = author(_targets(warmup_target="z3"), _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 155, "high_bpm": 168}


def test_zone_four_resolves_to_the_top_nameable_pair():
    spec = author(_targets(warmup_target="z4"), _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 168, "high_bpm": 178}


def test_a_zone_name_works_on_every_role_the_session_type_has():
    req = _targets(warmup_target="z2", work_target="z4", recovery_target="z2", cooldown_target="z2")
    spec = author(req, _context())
    warmup, repeat, cooldown = spec["steps"]
    work, recovery = repeat["steps"]
    assert warmup["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}
    assert work["target"] == {"type": "hr_band", "low_bpm": 168, "high_bpm": 178}
    assert recovery["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}
    assert cooldown["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}


def test_a_resolved_zone_name_adds_no_warning():
    assert author(_targets(warmup_target="z2"), _context())["warnings"] == []


def test_zone_one_is_refused_and_points_at_an_explicit_band():
    with pytest.raises(ValueError, match="cannot name z1") as exc:
        author(_targets(warmup_target="z1"), _context())
    assert "hr_band" in str(exc.value)


def test_zone_five_is_refused_and_points_at_an_explicit_band():
    with pytest.raises(ValueError, match="cannot name z5") as exc:
        author(_targets(warmup_target="z5"), _context())
    assert "hr_band" in str(exc.value)


def test_an_unknown_zone_name_is_refused():
    with pytest.raises(ValueError, match="unknown zone"):
        author(_targets(warmup_target="z9"), _context())


def test_a_zone_name_is_read_whatever_its_case():
    # the athlete writes "Z2" as readily as "z2"; both name the same rung.
    spec = author(_targets(warmup_target="Z2", cooldown_target="NONE"), _context())
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}
    assert spec["steps"][2]["target"] == {"type": "none"}
    assert spec["warnings"] == []


def test_an_upper_case_edge_zone_is_refused_the_same_way():
    with pytest.raises(ValueError, match="cannot name z5"):
        author(_targets(warmup_target="Z5"), _context())


def test_a_zone_name_without_any_zones_row_warns_and_drops_the_target():
    spec = author(_targets(pace=None, warmup_target="z2"), _context(zones=None))
    assert spec["steps"][0]["target"] == {"type": "none"}
    warning = next(w for w in spec["warnings"] if "warmup_target" in w)
    assert "Z2" in warning


def test_a_zone_name_whose_bound_is_null_warns_the_same_way():
    # the row exists and Z2's lower bound is present; only the upper bound is missing
    zones = _zones()
    zones["z2_hi_bpm"] = None
    spec = author(_targets(warmup_target="z2"), _context(zones=zones))
    assert spec["steps"][0]["target"] == {"type": "none"}
    assert any("warmup_target" in w for w in spec["warnings"])


def test_an_unavailable_zone_target_never_raises():
    spec = author(_targets(warmup_target="z4"), _context(zones=None))
    assert spec is not None
    assert _kinds(spec["steps"]) == ["warmup", "repeat", "cooldown"]


def test_the_work_degradation_warning_keeps_its_own_wording():
    # a zone name on the warm-up and a degrading work step in one spec: two
    # different events, two different sentences.
    req = _targets(pace=None, warmup_target="z2")
    spec = author(req, _context(zones=None))
    assert "no target: no measured pace or heart-rate band; time only" in spec["warnings"]


def test_warnings_read_in_step_order_on_both_session_types():
    # a dropped warm-up zone and a degrading work step in one spec: the warm-up
    # runs first, so it must be reported first, on quality as well as tempo.
    zones = _zones()
    zones["z2_hi_bpm"] = None
    for session_type in ("tempo", "quality"):
        req = _targets(session_type=session_type, pace=None, warmup_target="z2")
        warnings = author(req, _context(zones=zones))["warnings"]
        assert warnings[0].startswith("warmup_target:")
        assert "heart rate" in warnings[1]


def test_the_2026_07_16_case_authors_z2_warmup_and_cooldown_on_the_lap_button():
    req = _targets(
        warmup_target="z2",
        warmup_end="lap",
        cooldown_target="z2",
        cooldown_end="lap",
    )
    spec = author(req, _context())
    warmup, _repeat, cooldown = spec["steps"]
    z2 = {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}
    assert warmup["target"] == z2
    assert cooldown["target"] == z2
    assert warmup["end"] == {"type": "lap"}
    assert cooldown["end"] == {"type": "lap"}
    steps = to_garmin(spec)["workoutSegments"][0]["workoutSteps"]
    assert steps[0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert steps[0]["endCondition"]["conditionTypeKey"] == "lap.button"
    assert steps[-1]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"


# The authored-shape projection deliberately has no seam of its own (issue #42):
# asserting it directly would assert the implementation, so it is exercised through
# get_workout_status against a fake account - see tests/mcp/test_tools.py.


# --- issue #61: a session type is a default shape, not a permitted shape --------------


def test_the_2026_09_03_case_authors_an_easy_run_with_lap_ended_edges():
    req = _request(session_type="easy", date="2026-09-03", origin="athlete", pace=None)
    req["structure"] = {
        "warmup_end": "lap",
        "work_end": {"distance_m": 8000},
        "work_pace_band": [320, 340],
        "cooldown_end": "lap",
    }
    zones = _zones(z2_ceiling=314, thr=241, z2_hi=158) | {"lthr_bpm": 178}
    spec = author(req, _context(zones=zones, today="2026-09-02", planned_intent="easy"))
    warmup, work, cooldown = spec["steps"]
    assert warmup == {"kind": "warmup", "end": {"type": "lap"}, "target": {"type": "none"}}
    assert work["end"] == {"type": "distance", "metres": 8000}
    assert work["target"] == {"type": "pace_band", "fast_s_per_km": 320, "slow_s_per_km": 340}
    assert cooldown == {"kind": "cooldown", "end": {"type": "lap"}, "target": {"type": "none"}}
    assert spec["session_type"] == "easy"
    assert spec["warnings"] == []


def test_tempo_with_reps_folds_work_and_recovery_into_a_repeat_block():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"reps": 4, "work_min": 8, "recovery_min": 2}
    spec = author(req, _context())
    assert _kinds(spec["steps"]) == ["warmup", "repeat", "cooldown"]
    repeat = spec["steps"][1]
    assert repeat["reps"] == 4
    assert _kinds(repeat["steps"]) == ["work", "recovery"]
    assert repeat["steps"][0]["end"] == {"type": "time", "seconds": 480}
    assert repeat["steps"][1]["end"] == {"type": "time", "seconds": 120}


def test_reps_alone_defaults_the_pause_to_a_two_minute_recovery():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"reps": 3}
    spec = author(req, _context())
    recovery = spec["steps"][1]["steps"][1]
    assert recovery == {
        "kind": "recovery",
        "end": {"type": "time", "seconds": 120},
        "target": {"type": "none"},
    }


def test_standing_rest_runs_between_repeats_instead_of_a_jog():
    req = _request(session_type="easy", pace=None, origin="athlete")
    req["structure"] = {"reps": 6, "work_end": {"distance_m": 1000}, "rest_end": {"min": 1.5}}
    spec = author(req, _context())
    repeat = spec["steps"][0]
    assert repeat["reps"] == 6
    assert _kinds(repeat["steps"]) == ["work", "rest"]
    assert repeat["steps"][1]["end"] == {"type": "time", "seconds": 90}


def test_a_jog_recovery_and_a_standing_rest_together_are_refused():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"reps": 4, "recovery_min": 2, "rest_min": 1}
    with pytest.raises(ValueError, match="recovery.*rest|rest.*recovery"):
        author(req, _context())


def test_to_garmin_encodes_a_standing_rest_inside_the_repeat_group():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"reps": 4, "rest_min": 1}
    spec = author(req, _context())
    nested = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][1]["workoutSteps"]
    assert nested[0]["stepType"]["stepTypeKey"] == "interval"
    assert nested[1]["stepType"]["stepTypeKey"] == "rest"
    assert nested[1]["endCondition"]["conditionTypeKey"] == "time"
    assert nested[1]["endConditionValue"] == 60.0


def test_an_asked_for_standing_rest_replaces_the_types_default_jog():
    req = _request(session_type="quality", pace=270, origin="athlete")
    req["structure"] = {"rest_min": 1}
    spec = author(req, _context())
    assert _kinds(spec["steps"]) == ["warmup", "repeat", "cooldown"]
    assert _kinds(spec["steps"][1]["steps"]) == ["work", "rest"]


def test_the_2026_09_09_case_authors_an_easy_run_with_a_heart_rate_band():
    req = _request(session_type="easy", date="2026-09-09", origin="athlete", pace=None)
    req["structure"] = {
        "warmup_end": "lap",
        "work_end": {"distance_m": 10000},
        "work_target": {"hr_band": [140, 150]},
        "cooldown_end": "lap",
    }
    zones = _zones(z2_ceiling=314, thr=241, z2_hi=158) | {"lthr_bpm": 178}
    spec = author(req, _context(zones=zones, today="2026-09-08", planned_intent="easy"))
    assert _kinds(spec["steps"]) == ["warmup", "work", "cooldown"]
    work = spec["steps"][1]
    assert work["end"] == {"type": "distance", "metres": 10000}
    assert work["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 150}
    assert spec["warnings"] == []


def test_a_role_summoned_by_its_target_alone_takes_the_shared_default_length():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"warmup_target": "z2"}
    spec = author(req, _context())
    warmup = spec["steps"][0]
    assert warmup["end"] == {"type": "time", "seconds": 600}
    assert warmup["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 155}


def test_work_min_is_the_spelling_on_every_session_type():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"work_min": 60}
    assert author(req, _context())["steps"][0]["end"] == {"type": "time", "seconds": 3600}


def test_easy_work_min_and_the_older_duration_min_clash_is_refused():
    req = _request(session_type="easy", origin="athlete")
    req["structure"] = {"work_min": 60, "duration_min": 45}
    with pytest.raises(ValueError, match="give only one"):
        author(req, _context())


# --- issue #62: the guard reads measured hardness, not the template name --------------


def test_the_default_sessions_carry_their_measured_hardness():
    assert author(_request(session_type="easy"), _context())["hardness"] == "easy"
    assert author(_request(session_type="tempo", pace=270), _context())["hardness"] == "threshold"
    assert author(_request(session_type="quality", pace=270), _context())["hardness"] == "threshold"


def test_the_2026_09_03_session_measures_easy_on_the_athletes_own_zones():
    req = _request(session_type="easy", date="2026-09-03", origin="athlete", pace=None)
    req["structure"] = {
        "warmup_end": "lap",
        "work_end": {"distance_m": 8000},
        "work_pace_band": [320, 340],
        "cooldown_end": "lap",
    }
    zones = _zones(z2_ceiling=314, thr=241, z2_hi=158)
    zones["lthr_bpm"] = 178
    spec = author(req, _context(zones=zones, today="2026-09-02", planned_intent="easy"))
    assert spec["hardness"] == "easy"


def test_an_easy_named_session_at_threshold_pace_is_refused_on_a_planned_easy_day():
    req = _request(session_type="easy", origin="athlete", pace=None)
    req["structure"] = {"work_pace_band": [265, 275]}
    with pytest.raises(ValueError, match="work step"):
        author(req, _context(planned_intent="easy"))


def test_threshold_repeats_named_quality_pass_on_a_planned_tempo_day():
    req = _request(session_type="quality", origin="athlete", pace=None)
    req["structure"] = {"reps": 4, "work_pace_band": [265, 275]}
    spec = author(req, _context(planned_intent="tempo"))
    assert spec["hardness"] == "threshold"


def test_a_session_faster_than_threshold_is_still_refused_on_a_planned_tempo_day():
    req = _request(session_type="quality", origin="athlete", pace=None)
    req["structure"] = {"reps": 8, "work_pace_band": [220, 240]}
    with pytest.raises(ValueError, match="planned as tempo"):
        author(req, _context(planned_intent="tempo"))


def test_a_targeted_warmup_alone_lifts_an_otherwise_easy_session():
    req = _request(session_type="easy", origin="athlete", pace=None)
    req["structure"] = {"warmup_target": "z3", "work_target": {"pace_band": [340, 360]}}
    spec = author(req, _context())
    assert spec["hardness"] == "threshold"


def test_nothing_is_measured_without_zones_and_the_spec_says_so():
    spec = author(_request(session_type="tempo", pace=None, cap=None), _context(zones=None))
    assert "hardness" not in spec
    assert any("hardness" in w for w in spec["warnings"])


def test_an_exercise_sport_spec_carries_no_measured_hardness():
    req = {
        "sport": "strength",
        "origin": "athlete",
        "date": "2026-07-17",
        "session_type": "strength",
        "structure": {"exercises": [{"exercise": "back squat", "sets": 3, "reps": 5}]},
    }
    assert "hardness" not in author(req, _context())


def test_the_refusal_names_an_untargeted_work_step_by_its_default_chain():
    """The chain ranked it, so the refusal must still point at the step, not the session."""
    req = _request(session_type="tempo", origin="athlete", pace=None)
    req["structure"] = {"work_target": "none"}
    with pytest.raises(ValueError, match="the work step at its default target is threshold"):
        author(req, _context(planned_intent="easy"))


# --- hyrox run-station sequence ----------------------------------------------


_STATIONS = ["SkiErg 1000 m", "Sled Push 50 m", "Wall Balls 100"]


def _hyrox_request(structure=None, date="2026-07-17", pace=None):
    return _request(session_type="hyrox", date=date, pace=pace, origin="athlete") | {
        "structure": {"stations": list(_STATIONS)} | (structure or {})
    }


def _hyrox_steps(payload):
    return payload["workoutSegments"][0]["workoutSteps"]


def test_hyrox_stations_expand_to_a_run_before_every_station():
    spec = author(_hyrox_request(), _context())
    assert spec["name"] == "GC 2026-07-17 hyrox"
    assert _kinds(spec["steps"]) == ["work", "station"] * 3
    assert spec["warnings"] == []


def test_hyrox_run_defaults_to_the_race_kilometre_and_no_target():
    spec = author(_hyrox_request(), _context())
    run = spec["steps"][0]
    assert run["end"] == {"type": "distance", "metres": 1000}
    assert run["target"] == {"type": "none"}


def test_hyrox_station_defaults_to_the_lap_button_and_carries_its_label():
    spec = author(_hyrox_request(), _context())
    stations = [s for s in spec["steps"] if s["kind"] == "station"]
    assert [s["label"] for s in stations] == _STATIONS
    assert all(s["end"] == {"type": "lap"} for s in stations)
    assert all(s["target"] == {"type": "none"} for s in stations)


def test_hyrox_run_target_band_is_on_every_run():
    spec = author(_hyrox_request({"run_target": {"pace_band": [235, 250]}}), _context())
    runs = [s for s in spec["steps"] if s["kind"] == "work"]
    assert len(runs) == 3
    assert all(
        s["target"] == {"type": "pace_band", "fast_s_per_km": 235, "slow_s_per_km": 250}
        for s in runs
    )


def test_hyrox_station_target_zone_resolves_from_the_ladder_once():
    spec = author(_hyrox_request({"station_target": "z4"}), _context())
    stations = [s for s in spec["steps"] if s["kind"] == "station"]
    assert all(
        s["target"] == {"type": "hr_band", "low_bpm": 168, "high_bpm": 178} for s in stations
    )
    assert spec["warnings"] == []


def test_hyrox_steps_are_not_aliased():
    spec = author(_hyrox_request({"run_target": {"pace_band": [235, 250]}}), _context())
    runs = [s for s in spec["steps"] if s["kind"] == "work"]
    runs[0]["target"]["fast_s_per_km"] = 1
    assert runs[1]["target"]["fast_s_per_km"] == 235


def test_hyrox_run_may_end_on_the_lap_button_for_race_day():
    spec = author(_hyrox_request({"run_end": "lap"}), _context())
    assert spec["steps"][0]["end"] == {"type": "lap"}


def test_hyrox_edges_are_authored_only_when_given_an_end():
    bare = author(_hyrox_request(), _context())
    assert _kinds(bare["steps"])[0] == "work"
    assert _kinds(bare["steps"])[-1] == "station"
    edged = author(_hyrox_request({"warmup_end": "lap", "cooldown_min": 5}), _context())
    assert _kinds(edged["steps"]) == ["warmup", *(["work", "station"] * 3), "cooldown"]
    assert edged["steps"][0]["end"] == {"type": "lap"}
    assert edged["steps"][-1]["end"] == {"type": "time", "seconds": 300}


def test_hyrox_station_entry_may_end_on_a_clock():
    structure = {"stations": ["SkiErg", {"label": "Sled Push", "end": {"min": 3}}]}
    spec = author(_hyrox_request(structure), _context())
    assert spec["steps"][3]["end"] == {"type": "time", "seconds": 180}
    assert spec["steps"][3]["label"] == "Sled Push"


def test_hyrox_station_cannot_end_on_a_distance():
    structure = {"stations": [{"label": "SkiErg", "end": {"distance_m": 1000}}]}
    with pytest.raises(ValueError, match="cannot end on a distance"):
        author(_hyrox_request(structure), _context())


@pytest.mark.parametrize("stations", [[], None, ["SkiErg", ""], [{"end": "lap"}], [3]])
def test_hyrox_malformed_stations_are_refused(stations):
    with pytest.raises(ValueError):
        author(_hyrox_request({"stations": stations}), _context())


def test_hyrox_structure_rejects_template_keys():
    with pytest.raises(ValueError, match="unknown structure keys for a station sequence: reps"):
        author(_hyrox_request({"reps": 4}), _context())


def test_hyrox_without_stations_still_asks_for_the_split():
    request = _hyrox_request() | {"structure": {"run_end": "lap"}}
    with pytest.raises(HyroxSplitRequired, match="structure.stations"):
        author(request, _context())


def test_hyrox_is_bounded_by_the_plan_of_record():
    with pytest.raises(ValueError, match="harder than the plan"):
        author(_hyrox_request(), _context(planned_intent="tempo"))


def test_hyrox_run_band_faster_than_the_recommendation_is_cited():
    request = _hyrox_request({"run_target": {"pace_band": [220, 240]}}, pace=250)
    spec = author(request, _context_with_rec("hyrox", ["HYROX_RACE_PREP"]))
    assert any("faster than the recommended 250" in w for w in spec["warnings"])
    assert any("HYROX_RACE_PREP" in w for w in spec["warnings"])


def test_to_garmin_hyrox_station_is_a_lap_ended_interval_with_its_label():
    spec = author(_hyrox_request({"station_target": "z4"}), _context())
    payload = to_garmin(spec)
    assert payload["sportType"]["sportTypeKey"] == "running"
    station = _hyrox_steps(payload)[1]
    assert station["stepType"]["stepTypeKey"] == "interval"
    assert station["endCondition"]["conditionTypeKey"] == "lap.button"
    assert "endConditionValue" not in station
    assert station["description"] == "SkiErg 1000 m"
    assert station["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (station["targetValueOne"], station["targetValueTwo"]) == (168, 178)


def test_to_garmin_hyrox_run_is_a_distance_ended_interval_without_notes():
    spec = author(_hyrox_request({"run_target": {"pace_band": [235, 250]}}), _context())
    run = _hyrox_steps(to_garmin(spec))[0]
    assert run["endCondition"]["conditionTypeKey"] == "distance"
    assert run["endConditionValue"] == 1000.0
    assert run["targetType"]["workoutTargetTypeKey"] == "pace.zone"
    assert "description" not in run


def test_to_garmin_hyrox_estimates_the_runs_and_nothing_for_lap_stations():
    spec = author(_hyrox_request({"run_target": {"pace_band": [230, 250]}}), _context())
    # three runs at the 240 s/km midpoint; the lap-ended stations are unknowable
    assert to_garmin(spec)["estimatedDurationInSecs"] == 3 * 240


def test_a_hyrox_sequence_carries_no_measured_hardness():
    """It expands from a list, not the role table: there is no chain to rank it against."""
    spec = author(_hyrox_request(), _context())
    assert "hardness" not in spec


def test_a_hyrox_sequence_is_still_guarded_by_its_session_type():
    with pytest.raises(ValueError, match="planned as easy"):
        author(_hyrox_request(), _context(planned_intent="easy"))


# --- warm-up and cool-down on the exercise sports (issue #65) ----------------


def _exercise_edges(**structure):
    """A strength request carrying warm-up / cool-down keys beside its exercises."""
    request = _strength_request()
    request["structure"] = {**request["structure"], **structure}
    return request


def test_strength_authors_a_warmup_before_the_first_set():
    """A FBB day that starts on the erg is one workout, not the lifting alone (#65)."""
    spec = author(_exercise_edges(warmup_min=10), _context())

    assert _kinds(spec["steps"]) == ["warmup", "work", "rest", "work"]
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 600}
    assert spec["steps"][0]["target"] == {"type": "none"}


def test_hiit_authors_a_cooldown_after_the_last_set():
    """The watch must not end the workout on the last set (#65)."""
    request = _hiit_request()
    request["structure"] = {**request["structure"], "cooldown_min": 8}

    spec = author(request, _context())

    assert _kinds(spec["steps"]) == ["work", "rest", "work", "cooldown"]
    assert spec["steps"][-1]["end"] == {"type": "time", "seconds": 480}


def test_an_exercise_sport_takes_both_edges_at_once():
    spec = author(_exercise_edges(warmup_min=10, cooldown_min=5), _context())

    assert _kinds(spec["steps"]) == ["warmup", "work", "rest", "work", "cooldown"]


def test_an_exercise_warmup_can_end_on_the_lap_button():
    """A self-timed warm-up: press lap when you are ready to lift."""
    spec = author(_exercise_edges(warmup_end="lap"), _context())

    assert spec["steps"][0]["end"] == {"type": "lap"}


def test_an_exercise_warmup_carries_the_heart_rate_ceiling_it_was_given():
    spec = author(_exercise_edges(warmup_target={"hr_band": [120, 145]}), _context())

    assert spec["steps"][0]["kind"] == "warmup"
    assert spec["steps"][0]["target"] == {"type": "hr_band", "low_bpm": 120, "high_bpm": 145}


def test_a_target_alone_summons_the_warmup_at_the_shared_default_length():
    """ "warm up under 140" needs no minutes: the run roles' default answers (#65)."""
    spec = author(_exercise_edges(warmup_target={"hr_band": [120, 140]}), _context())

    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 600}


def test_an_exercise_session_without_the_keys_authors_exactly_as_before():
    """The regression that matters: nothing changes for the sessions already pushed."""
    spec = author(_strength_request(), _context())

    assert _kinds(spec["steps"]) == ["work", "rest", "work"]


def test_an_untargeted_exercise_step_carries_no_target_in_the_payload():
    """The shape the live probes accepted has no targetType; adding one is untested risk."""
    spec = author(_exercise_edges(warmup_target={"hr_band": [120, 145]}), _context())

    steps = to_garmin(spec)["workoutSegments"][0]["workoutSteps"]

    assert "targetType" in steps[0]  # the warm-up was given a ceiling
    assert all("targetType" not in step for step in steps[1:])


def test_an_unknown_exercise_structure_key_is_still_refused():
    with pytest.raises(ValueError, match="unknown structure keys"):
        author(_exercise_edges(warmup_minutes=10), _context())


def test_an_exercise_warmup_refuses_two_lengths_at_once():
    with pytest.raises(ValueError, match="give only one"):
        author(_exercise_edges(warmup_end="lap", warmup_min=10), _context())


def test_an_exercise_warmup_refuses_a_target_it_cannot_express():
    with pytest.raises(ValueError, match="warmup_target must be"):
        author(_exercise_edges(warmup_target={"power_band": [200, 240]}), _context())


def test_the_garmin_payload_marks_the_edges_as_warmup_and_cooldown():
    """Garmin's own step types, so the watch shows a warm-up as a warm-up (#65)."""
    spec = author(_exercise_edges(warmup_min=10, cooldown_min=5), _context())

    steps = to_garmin(spec)["workoutSegments"][0]["workoutSteps"]

    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert steps[0]["stepType"]["stepTypeId"] == 1
    assert steps[-1]["stepType"]["stepTypeKey"] == "cooldown"
    assert steps[-1]["stepType"]["stepTypeId"] == 2
    assert steps[1]["stepType"]["stepTypeKey"] == "interval"


def test_the_exercise_edges_carry_their_heart_rate_target_into_the_payload():
    spec = author(_exercise_edges(warmup_target={"hr_band": [120, 145]}), _context())

    first = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]

    assert first["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (first["targetValueOne"], first["targetValueTwo"]) == (120, 145)


# --- the workout's name belongs to the athlete (issue #58) -------------------


def test_a_label_names_the_session_after_the_date():
    """On the watch, "4x2 km próg" beats "quality" (#58)."""
    request = {**_strength_request(), "label": "FBB A"}

    spec = author(request, _context())

    assert spec["name"] == "GC 2026-07-17 FBB A"


def test_a_name_is_used_whole():
    """A name the athlete asked for is theirs: no prefix, no date."""
    request = {**_strength_request(), "name": "FBB A"}

    spec = author(request, _context())

    assert spec["name"] == "FBB A"


def test_without_a_name_or_label_the_session_type_still_names_it():
    spec = author(_strength_request(), _context())

    assert spec["name"] == "GC 2026-07-17 strength"


def test_a_label_and_a_name_at_once_are_refused():
    request = {**_strength_request(), "label": "FBB A", "name": "FBB A"}

    with pytest.raises(ValueError, match="either label or name"):
        author(request, _context())


def test_a_name_is_trimmed():
    request = {**_strength_request(), "name": "  FBB A  "}

    assert author(request, _context())["name"] == "FBB A"


@pytest.mark.parametrize("value", ["", "   ", "FBB\nA", "FBB\tA"])
def test_a_malformed_name_is_refused(value):
    with pytest.raises(ValueError, match="name"):
        author({**_strength_request(), "name": value}, _context())


def test_a_label_that_repeats_the_prefix_is_refused():
    """Otherwise the watch shows "GC 2026-07-17 GC tempo"."""
    with pytest.raises(ValueError, match="GC"):
        author({**_strength_request(), "label": "GC tempo"}, _context())


def test_an_overlong_label_is_refused():
    with pytest.raises(ValueError, match="30"):
        author({**_strength_request(), "label": "x" * 31}, _context())


def test_an_overlong_name_is_refused():
    with pytest.raises(ValueError, match="80"):
        author({**_strength_request(), "name": "x" * 81}, _context())


def test_a_label_may_hold_the_polish_the_athlete_speaks():
    request = {**_strength_request(), "label": "4x2 km próg"}

    assert author(request, _context())["name"] == "GC 2026-07-17 4x2 km próg"


def test_a_run_session_takes_a_label_too():
    spec = author({**_targets(), "label": "próg 3x10"}, _context())

    assert spec["name"] == "GC 2026-07-17 próg 3x10"


# --- repeating a session on another day (issue #58) --------------------------


def _future(days: int = 2) -> str:
    return (_dt.date.today() + _dt.timedelta(days=days)).isoformat()


def _named_spec(name="GC FBB A", date=None, hardness=None):
    spec = {
        "sport": "strength",
        "origin": "athlete",
        "date": date or _future(1),
        "session_type": "strength",
        "name": name,
        "steps": [
            {"kind": "work", "end": {"type": "reps", "count": 5}, "target": {"type": "none"}}
        ],
        "warnings": ["an old warning from the first authoring"],
    }
    if hardness is not None:
        spec["hardness"] = hardness
    return spec


def test_copying_a_spec_moves_it_to_the_new_day():
    """ "Repeat FBB A from the 19th on Friday" keeps the steps and the name (#58)."""
    target = _future(3)

    copy = copy_to_date(_named_spec(), date=target, planned_intent="strength", today=_TODAY)

    assert copy["date"] == target
    assert copy["name"] == "GC FBB A"
    assert copy["steps"] == _named_spec()["steps"]


def test_a_copy_carries_only_the_warnings_of_its_own_day():
    """The first authoring's warnings were about that day, not this one."""
    copy = copy_to_date(_named_spec(), date=_TODAY, planned_intent="strength", today=_TODAY)

    assert copy["warnings"] == ["target date is today; the watch may not sync before the session"]


def test_a_copy_onto_a_past_day_is_refused():
    with pytest.raises(ValueError, match="past date"):
        copy_to_date(_named_spec(), date="2026-01-05", planned_intent="strength", today=_TODAY)


def test_a_copy_harder_than_the_new_days_plan_is_refused():
    """The plan guard runs for the day it lands on, not the day it came from (#58)."""
    with pytest.raises(ValueError, match="easy"):
        copy_to_date(_named_spec(), date=_future(3), planned_intent="easy", today=_TODAY)


def test_a_copy_of_a_measured_spec_is_judged_on_what_it_measured():
    with pytest.raises(ValueError, match="easy"):
        copy_to_date(
            _named_spec(hardness="quality"),
            date=_future(3),
            planned_intent="easy",
            today=_TODAY,
        )


# --- station sequence under sport hiit (issue #76) ----------------------------


_HIIT_STATIONS = ["EMOM 1: 14 kcal row, 20 WBS", "EMOM 2: 12 cal ski, 15 burpees", "Wall Balls 100"]


def _hiit_stations_request(structure=None, session_type="hyrox", date="2026-07-17"):
    return {
        "sport": "hiit",
        "origin": "athlete",
        "date": date,
        "session_type": session_type,
        "structure": {"stations": list(_HIIT_STATIONS)} | (structure or {}),
    }


def test_hiit_stations_expand_to_a_run_before_every_station_with_no_rests():
    spec = author(_hiit_stations_request(), _context())
    assert spec["name"] == "GC 2026-07-17 hyrox"
    assert _kinds(spec["steps"]) == ["work", "station"] * 3
    assert spec["warnings"] == []


def test_hiit_stations_author_under_crossfit_too():
    spec = author(_hiit_stations_request(session_type="crossfit"), _context())
    assert spec["name"] == "GC 2026-07-17 crossfit"
    assert _kinds(spec["steps"]) == ["work", "station"] * 3


def test_hiit_run_defaults_to_the_lap_button_and_no_target():
    """A HIIT activity measures no distance: the run ends when the athlete says so."""
    spec = author(_hiit_stations_request(), _context())
    run = spec["steps"][0]
    assert run["end"] == {"type": "lap"}
    assert run["target"] == {"type": "none"}


def test_hiit_run_may_end_on_a_clock():
    spec = author(_hiit_stations_request({"run_end": {"min": 8}}), _context())
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 480}


def test_hiit_station_defaults_to_the_lap_button_and_carries_its_label():
    spec = author(_hiit_stations_request(), _context())
    stations = [s for s in spec["steps"] if s["kind"] == "station"]
    assert [s["label"] for s in stations] == _HIIT_STATIONS
    assert all(s["end"] == {"type": "lap"} for s in stations)


def test_hiit_station_target_zone_resolves_from_the_ladder():
    spec = author(_hiit_stations_request({"station_target": "z4"}), _context())
    stations = [s for s in spec["steps"] if s["kind"] == "station"]
    assert all(
        s["target"] == {"type": "hr_band", "low_bpm": 168, "high_bpm": 178} for s in stations
    )


def test_hiit_run_hr_band_is_on_every_run():
    spec = author(_hiit_stations_request({"run_target": {"hr_band": [140, 160]}}), _context())
    runs = [s for s in spec["steps"] if s["kind"] == "work"]
    assert all(s["target"] == {"type": "hr_band", "low_bpm": 140, "high_bpm": 160} for s in runs)


@pytest.mark.parametrize("key", ["run_end", "warmup_end", "cooldown_end"])
def test_hiit_refuses_a_distance_end_and_points_to_sport_run(key):
    with pytest.raises(ValueError, match=f"{key} cannot be a distance under sport hiit.*sport run"):
        author(_hiit_stations_request({key: {"distance_m": 1000}}), _context())


@pytest.mark.parametrize("key", ["run_target", "station_target", "warmup_target"])
def test_hiit_refuses_a_pace_band(key):
    with pytest.raises(ValueError, match=f"{key} cannot be a pace band under sport hiit"):
        author(_hiit_stations_request({key: {"pace_band": [230, 250]}}), _context())


def test_hiit_refuses_stations_and_exercises_together():
    structure = {"exercises": [{"exercise": "wall balls", "sets": 1, "reps": 20}]}
    with pytest.raises(ValueError, match="structure.stations or structure.exercises, not both"):
        author(_hiit_stations_request(structure), _context())


def test_strength_does_not_take_a_station_list():
    request = _strength_request() | {"structure": {"stations": ["SkiErg"]}}
    with pytest.raises(ValueError, match="unknown structure keys for an exercise sport: stations"):
        author(request, _context())


def test_hiit_station_sequence_rejects_template_keys():
    with pytest.raises(ValueError, match="unknown structure keys for a station sequence: reps"):
        author(_hiit_stations_request({"reps": 4}), _context())


def test_a_hiit_station_sequence_carries_no_hardness_and_is_guarded_by_its_type():
    assert "hardness" not in author(_hiit_stations_request(), _context())
    with pytest.raises(ValueError, match="planned as easy"):
        author(_hiit_stations_request(), _context(planned_intent="easy"))


def test_to_garmin_hiit_station_sequence_is_lap_ended_labelled_intervals():
    payload = to_garmin(author(_hiit_stations_request({"station_target": "z4"}), _context()))
    assert payload["sportType"]["sportTypeKey"] == "hiit"
    steps = _hyrox_steps(payload)
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["interval"] * 6
    assert all(s["endCondition"]["conditionTypeKey"] == "lap.button" for s in steps)
    assert all(s["endConditionValue"] is None for s in steps)
    station = steps[1]
    assert station["description"] == _HIIT_STATIONS[0]
    assert "exerciseName" not in station
    assert station["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (station["targetValueOne"], station["targetValueTwo"]) == (168, 178)
    assert payload["estimatedDurationInSecs"] == 0


def test_the_2026_09_26_eight_wonders_session_authors_as_hiit(fixture):
    """Eight stations, a run before each, indoors: 16 lap-ended steps, a note on every station."""
    spec = author(fixture("hiit_stations_request"), _context(today="2026-09-24"))
    assert spec["name"] == "GC 2026-09-26 8 Wonders + 8x1 km"
    assert _kinds(spec["steps"]) == ["warmup", *["work", "station"] * 8]
    assert all(s["end"] == {"type": "lap"} for s in spec["steps"])
    payload = to_garmin(spec)
    assert payload["sportType"]["sportTypeKey"] == "hiit"
    steps = _hyrox_steps(payload)
    assert len(steps) == 17
    assert steps[0]["stepType"]["stepTypeKey"] == "warmup"
    assert [s["description"] for s in steps[2::2]] == fixture("hiit_stations_request")["structure"][
        "stations"
    ]
    assert not any(s["stepType"]["stepTypeKey"] == "rest" for s in steps)


# --- run_position: before / after / none (issue #76) --------------------------


def test_run_position_defaults_to_before_each_station():
    spec = author(_hiit_stations_request({"run_position": "before"}), _context())
    assert _kinds(spec["steps"]) == ["work", "station"] * 3


def test_run_position_after_puts_the_run_behind_each_station():
    spec = author(_hiit_stations_request({"run_position": "after"}), _context())
    assert _kinds(spec["steps"]) == ["station", "work"] * 3
    assert spec["steps"][0]["label"] == _HIIT_STATIONS[0]


def test_run_position_none_authors_a_pure_labelled_circuit():
    spec = author(_hiit_stations_request({"run_position": "none"}), _context())
    assert _kinds(spec["steps"]) == ["station"] * 3
    assert all(s["end"] == {"type": "lap"} for s in spec["steps"])


def test_run_position_none_keeps_the_edges():
    structure = {"run_position": "none", "warmup_end": "lap", "cooldown_min": 5}
    spec = author(_hiit_stations_request(structure), _context())
    assert _kinds(spec["steps"]) == ["warmup", "station", "station", "station", "cooldown"]


def test_run_position_after_works_on_a_run_hyrox_request_too():
    spec = author(_hyrox_request({"run_position": "after"}), _context())
    assert _kinds(spec["steps"]) == ["station", "work"] * 3


def test_run_position_none_is_refused_under_sport_run():
    with pytest.raises(ValueError, match='run_position "none".*sport hiit'):
        author(_hyrox_request({"run_position": "none"}), _context())


@pytest.mark.parametrize("value", ["between", "", 1, None])
def test_run_position_takes_only_its_three_words(value):
    with pytest.raises(ValueError, match='run_position must be "before", "after", or "none"'):
        author(_hiit_stations_request({"run_position": value}), _context())


def test_the_2026_09_23_emom_session_authors_as_blocks_then_runs(fixture):
    """Three EMOM blocks with a 2 km treadmill run after each, no rests (issue #76)."""
    request = fixture("hiit_emom_request")
    spec = author(request, _context(today="2026-09-22"))
    assert spec["name"] == "GC 2026-09-23 3x EMOM + 2 km"
    assert _kinds(spec["steps"]) == ["station", "work"] * 3
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 720}
    assert spec["steps"][1]["end"] == {"type": "time", "seconds": 600}
    steps = _hyrox_steps(to_garmin(spec))
    assert len(steps) == 6
    assert [s.get("description") for s in steps[::2]] == [
        e["label"] for e in request["structure"]["stations"]
    ]
    assert not any(s["stepType"]["stepTypeKey"] == "rest" for s in steps)


# --- labels on runs and edges (issue #76) ---------------------------------------


def test_hiit_runs_read_run_when_no_label_is_given():
    """Between labelled stations the run must not be the one blank step."""
    spec = author(_hiit_stations_request(), _context())
    runs = [s for s in spec["steps"] if s["kind"] == "work"]
    assert all(s["label"] == "Run" for s in runs)


def test_run_label_names_every_run_in_both_sports():
    for request in (_hiit_stations_request, _hyrox_request):
        spec = author(request({"run_label": "Run 1 km"}), _context())
        runs = [s for s in spec["steps"] if s["kind"] == "work"]
        assert [s["label"] for s in runs] == ["Run 1 km"] * 3


def test_a_run_sequence_run_stays_unlabelled_unless_asked():
    """Every run-station sequence pushed before #76 must still go up as it did."""
    spec = author(_hyrox_request(), _context())
    assert all("label" not in s for s in spec["steps"] if s["kind"] == "work")


def test_edge_labels_name_the_warmup_and_cooldown():
    structure = {
        "warmup_end": "lap",
        "warmup_label": "Rozgrzewka",
        "cooldown_min": 5,
        "cooldown_label": "Chłodzenie",
    }
    spec = author(_hiit_stations_request(structure), _context())
    assert spec["steps"][0]["label"] == "Rozgrzewka"
    assert spec["steps"][-1]["label"] == "Chłodzenie"


def test_an_edge_label_alone_summons_the_edge():
    """Any of a role's keys summons it (ADR 0023); a label is one of them."""
    spec = author(_hiit_stations_request({"warmup_label": "Rozgrzewka"}), _context())
    assert spec["steps"][0]["kind"] == "warmup"
    assert spec["steps"][0]["end"] == {"type": "time", "seconds": 600}


@pytest.mark.parametrize("key", ["run_label", "warmup_label", "cooldown_label"])
@pytest.mark.parametrize("value", ["", "   ", 3])
def test_a_label_must_be_a_non_empty_string(key, value):
    with pytest.raises(ValueError, match=f"{key} must be a non-empty string"):
        author(_hiit_stations_request({key: value}), _context())


def test_to_garmin_labels_ride_as_notes_on_runs_and_edges(fixture):
    spec = author(fixture("hiit_stations_request"), _context(today="2026-09-24"))
    steps = _hyrox_steps(to_garmin(spec))
    assert [s["description"] for s in steps[1::2]] == ["Run 1 km"] * 8
    assert "description" not in steps[0]
    run_spec = author(_hyrox_request({"run_label": "Run 1 km"}), _context())
    assert _hyrox_steps(to_garmin(run_spec))[0]["description"] == "Run 1 km"
