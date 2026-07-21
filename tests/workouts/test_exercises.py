"""Contract tests for the exercise whitelist (issue #16, T3).

Every whitelist entry must be one of Garmin's own (category, exerciseName)
pairs. The reference is the taxonomy fixture snapshotted from Garmin Connect's
public exercise list (the athlete's logged sets carry no enums - the watch
records whole sessions as UNKNOWN - so the taxonomy is the mining source).
"""

from __future__ import annotations

import json
import pathlib

from garmin_coach.workouts.author import author
from garmin_coach.workouts.exercises import EXERCISE_MAP, resolve

_TAXONOMY = json.loads(
    (
        pathlib.Path(__file__).parent.parent / "fixtures" / "garmin_exercise_taxonomy.json"
    ).read_text()
)


def test_every_entry_is_a_garmin_pair():
    for key, (category, name) in EXERCISE_MAP.items():
        assert category in _TAXONOMY, f"{key}: unknown category {category}"
        assert name in _TAXONOMY[category], f"{key}: {name} not in {category}"


def test_every_key_is_normalized():
    for key in EXERCISE_MAP:
        assert resolve(key) == EXERCISE_MAP[key], f"{key} does not resolve to itself"


def test_resolve_normalizes_spacing_and_case():
    assert resolve("Wall Balls") == ("SQUAT", "WALL_BALL")
    assert resolve("sled push") == ("SLED", "PUSH")
    assert resolve("KB-Swing") == ("HIP_RAISE", "KETTLEBELL_SWING")


def test_the_athlete_vocabulary_authors_without_warnings():
    request = {
        "sport": "strength",
        "origin": "athlete",
        "date": "2026-07-17",
        "session_type": "strength",
        "structure": {
            "exercises": [
                {"exercise": "back squat", "sets": 3, "reps": 5, "weight_kg": 100},
                {"exercise": "deadlift", "sets": 3, "reps": 5, "weight_kg": 120},
                {"exercise": "bench press", "sets": 3, "reps": 8, "weight_kg": 80},
                {"exercise": "overhead press", "sets": 3, "reps": 8, "weight_kg": 50},
                {"exercise": "pull up", "sets": 3, "reps": 10},
                {"exercise": "wall balls", "sets": 3, "reps": 20},
                {"exercise": "sled push", "sets": 2, "time": {"s": 45}},
                {"exercise": "sled pull", "sets": 2, "time": {"s": 45}},
                {"exercise": "kettlebell swing", "sets": 3, "reps": 15, "weight_kg": 24},
                {"exercise": "farmers carry", "sets": 2, "time": {"s": 60}},
                {"exercise": "walking lunge", "sets": 2, "reps": 20},
                {"exercise": "burpee", "sets": 2, "reps": 15},
                {"exercise": "box jump", "sets": 3, "reps": 10},
                {"exercise": "thrusters", "sets": 3, "reps": 12, "weight_kg": 40},
                {"exercise": "goblet squat", "sets": 3, "reps": 10, "weight_kg": 24},
                {"exercise": "row erg", "sets": 1, "time": {"min": 5}},
            ]
        },
    }
    spec = author(request, {"zones": None, "today": "2026-07-15"})
    assert spec["warnings"] == []
    labeled = [s for s in spec["steps"] if s["kind"] == "work"]
    assert all("exercise" in s for s in labeled)
