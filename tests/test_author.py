"""Phase 11 author tests: ``author`` and ``to_garmin`` as pure functions.

Seam 1: the pure authoring boundary (a workout request + finished-mart context
-> a workout spec, and the spec -> Garmin workout JSON). No DB, no Garmin. Later
tickets add tempo/quality structure, athlete requests, and hybrid validation.
"""

from __future__ import annotations

import pytest

from garmin_coach.author import (
    DeferredSportError,
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


def test_hiit_sport_defers_to_the_spike():
    with pytest.raises(DeferredSportError, match="hiit"):
        author(_request(origin="athlete") | {"sport": "hiit"}, _context())


def test_strength_sport_defers_to_the_spike():
    with pytest.raises(DeferredSportError, match="strength"):
        author(_request(origin="athlete") | {"sport": "strength"}, _context())


def test_hyrox_session_requires_a_split_decision():
    with pytest.raises(HyroxSplitRequired):
        author(_request(session_type="hyrox"), _context())


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
