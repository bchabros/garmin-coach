"""Phase 11 author tests: ``author`` and ``to_garmin`` as pure functions.

Seam 1: the pure authoring boundary (a workout request + finished-mart context
-> a workout spec, and the spec -> Garmin workout JSON). No DB, no Garmin. Later
tickets add tempo/quality structure, athlete requests, and hybrid validation.
"""

from __future__ import annotations

import pytest

from garmin_coach.workouts.author import (
    HyroxSplitRequired,
    author,
    request_from_recommendation,
    to_garmin,
)


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


def _context(zones=_UNSET, today="2026-07-15"):
    return {"zones": _zones() if zones is _UNSET else zones, "today": today}


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


def test_strength_unknown_exercise_warns_and_stays_unlabeled():
    spec = author(
        _strength_request([{"exercise": "sled push", "sets": 1, "reps": 10}]),
        _context(),
    )
    assert any("unknown exercise 'sled push'" in w for w in spec["warnings"])
    assert "exercise" not in spec["steps"][0]


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


def test_to_garmin_strength_unknown_exercise_step_has_no_labels():
    spec = author(
        _strength_request([{"exercise": "sled push", "sets": 1, "reps": 10}]),
        _context(),
    )
    step = to_garmin(spec)["workoutSegments"][0]["workoutSteps"][0]
    assert "category" not in step
    assert "exerciseName" not in step
    assert "weightValue" not in step


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


def test_role_not_valid_for_session_type_is_refused():
    req = _request(session_type="tempo", pace=270, origin="athlete")
    req["structure"] = {"recovery_end": {"min": 2}}  # tempo has no recovery role
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
