"""Hardness measurement: a spec's steps and a zone ladder -> where the session sits.

The authoring seam's classifier half (issue #62). Pure - no DB, no Garmin, no spec
authoring - so the boundaries can be pinned against the athlete's own zone numbers.
"""

from __future__ import annotations

from garmin_coach.workouts import hardness


def _zones(z2_ceiling=330, thr=270, z2_hi=155, lthr=172):
    return {
        "z2_pace_ceiling_s_per_km": z2_ceiling,
        "z2_hi_bpm": z2_hi,
        "lthr_bpm": lthr,
    } | {"threshold_pace_s_per_km": thr}


def _pace(fast, slow):
    return [
        {
            "kind": "work",
            "target": {"type": "pace_band", "fast_s_per_km": fast, "slow_s_per_km": slow},
        }
    ]


def _hr(low, high):
    return [{"kind": "work", "target": {"type": "hr_band", "low_bpm": low, "high_bpm": high}}]


_NO_ZONES = object()


def _measure(steps, zones=_NO_ZONES, untargeted_work=None):
    return hardness.measure(
        steps,
        _zones() if zones is _NO_ZONES else zones,
        threshold_tolerance_s=5,
        untargeted_work=untargeted_work,
    )


def _hardness(steps, zones=_NO_ZONES, untargeted_work=None):
    measured = _measure(steps, zones, untargeted_work)
    return measured.hardness if measured else None


def test_a_pace_band_at_the_z2_ceiling_is_easy():
    assert _hardness(_pace(330, 370)) == "easy"


def test_a_pace_band_faster_than_the_z2_ceiling_is_threshold():
    assert _hardness(_pace(331, 371)) == "easy"
    assert _hardness(_pace(320, 340)) == "threshold"
    assert _hardness(_pace(300, 320)) == "threshold"


def test_the_2026_09_03_band_ranks_easy_against_the_athletes_own_zones():
    """5:20-5:40/km on the day's real ladder: a Z2 ceiling of 5:14 and threshold at 4:02."""
    zones = _zones(z2_ceiling=314, thr=241, z2_hi=158, lthr=178)
    assert _hardness(_pace(320, 340), zones=zones) == "easy"
    assert _hardness(_hr(140, 150), zones=zones) == "easy"
    # 3:55/km is faster than the stored threshold pace of 4:01, so it ranks above it -
    # the stored anchor is a fallback multiplier, not a measurement (issue #13).
    assert _hardness(_pace(235, 245), zones=zones) == "hard"
    assert _hardness(_hr(170, 178), zones=zones) == "threshold"


def test_the_default_tempo_band_ranks_threshold_by_its_own_margin():
    assert _hardness(_pace(265, 275)) == "threshold"


def test_a_pace_band_faster_than_threshold_is_hard():
    assert _hardness(_pace(220, 240)) == "hard"


def test_a_heart_rate_band_is_judged_by_its_upper_edge():
    assert _hardness(_hr(140, 150)) == "easy"
    assert _hardness(_hr(160, 172)) == "threshold"
    assert _hardness(_hr(168, 178)) == "hard"


def test_the_hardest_step_decides_the_session():
    steps = [
        {"kind": "warmup", "target": {"type": "hr_band", "low_bpm": 168, "high_bpm": 178}},
        *_pace(330, 370),
    ]
    assert _hardness(steps) == "hard"


def test_steps_inside_a_repeat_group_are_walked():
    steps = [{"kind": "repeat", "reps": 4, "steps": _pace(220, 240)}]
    assert _hardness(steps) == "hard"


def test_an_untargeted_non_work_step_contributes_nothing():
    steps = [{"kind": "warmup", "target": {"type": "none"}}, *_pace(330, 370)]
    assert _hardness(steps) == "easy"


def test_an_untargeted_work_step_contributes_its_types_default_chain():
    steps = [{"kind": "work", "target": {"type": "none"}}]
    assert _hardness(steps, untargeted_work="threshold") == "threshold"


def test_nothing_is_measured_without_zones():
    assert _hardness(_pace(220, 240), zones=None) is None


def test_nothing_is_measured_when_the_ladder_lacks_the_bound_it_needs():
    assert _hardness(_pace(220, 240), zones=_zones(thr=None)) is None


def test_a_spec_with_no_targeted_step_measures_nothing():
    assert _hardness([{"kind": "work", "target": {"type": "none"}}]) is None


def test_the_measurement_names_the_step_that_decided_it():
    steps = [
        {"kind": "warmup", "target": {"type": "none"}},
        *_pace(220, 240),
        {"kind": "cooldown", "target": {"type": "none"}},
    ]
    assert _measure(steps).step["kind"] == "work"


def test_an_untargeted_work_step_is_named_as_the_decider_too():
    """The refusal has to point somewhere even when the chain, not a band, ranked it."""
    steps = [{"kind": "work", "target": {"type": "none"}}]
    measured = _measure(steps, untargeted_work="threshold")
    assert measured.hardness == "threshold"
    assert hardness.describe_step(measured.step) == "the work step at its default target"
