"""Exercise vocabulary: athlete exercise names -> Garmin (category, exerciseName) pairs.

The category/exerciseName enumeration is undocumented in the API docs; the
reference is Garmin Connect's own public exercise taxonomy
(https://connect.garmin.com/web-data/exercises/Exercises.json, snapshotted to
``tests/fixtures/garmin_exercise_taxonomy.json`` on 2026-07-21). The athlete's
logged sets carry no enums (the watch records whole sessions as a single
UNKNOWN set), so the taxonomy is the mining source; the contract tests hold
every entry to it. Resolution is warn-never-block: an unknown name authors an
unlabeled step, it never refuses the session.

Curation notes: Hyrox's burpee broad jump has no taxonomy entry - the closest
honest label is TOTAL_BODY/BURPEE; the ski erg has none at all and stays
unmapped (the step goes out unlabeled).
"""

from __future__ import annotations

# Athlete name (normalized: lowercase, underscores) -> (category, exerciseName).
EXERCISE_MAP: dict[str, tuple[str, str]] = {
    # Main lifts. BARBELL_BACK_SQUAT proved to round-trip by the phase-11 probe.
    "back_squat": ("SQUAT", "BARBELL_BACK_SQUAT"),
    "front_squat": ("SQUAT", "BARBELL_FRONT_SQUAT"),
    "goblet_squat": ("SQUAT", "GOBLET_SQUAT"),
    "deadlift": ("DEADLIFT", "BARBELL_DEADLIFT"),
    "romanian_deadlift": ("DEADLIFT", "ROMANIAN_DEADLIFT"),
    "bench_press": ("BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "overhead_press": ("SHOULDER_PRESS", "BARBELL_SHOULDER_PRESS"),
    "barbell_row": ("ROW", "BARBELL_ROW"),
    "hip_thrust": ("HIP_RAISE", "BARBELL_HIP_THRUST_WITH_BENCH"),
    # Bodyweight staples.
    "pull_up": ("PULL_UP", "PULL_UP"),
    "push_up": ("PUSH_UP", "PUSH_UP"),
    "lunge": ("LUNGE", "LUNGE"),
    "walking_lunge": ("LUNGE", "WALKING_LUNGE"),
    "burpee": ("TOTAL_BODY", "BURPEE"),
    "box_jump": ("PLYO", "BOX_JUMP"),
    # Hyrox stations and conditioning.
    "wall_balls": ("SQUAT", "WALL_BALL"),
    "sled_push": ("SLED", "PUSH"),
    "sled_pull": ("SLED", "ROW"),
    "farmers_carry": ("CARRY", "FARMERS_CARRY"),
    "sandbag_lunge": ("SANDBAG", "LUNGE"),
    "row_erg": ("ROW", "INDOOR_ROW"),
    "thrusters": ("SQUAT", "THRUSTERS"),
    # Kettlebell work.
    "kettlebell_swing": ("HIP_RAISE", "KETTLEBELL_SWING"),
    "kettlebell_deadlift": ("DEADLIFT", "KETTLEBELL_DEADLIFT"),
}

# Spelling variants folded onto canonical map keys before lookup.
_ALIASES = {
    "squat": "back_squat",
    "ohp": "overhead_press",
    "military_press": "overhead_press",
    "bench": "bench_press",
    "rdl": "romanian_deadlift",
    "wall_ball": "wall_balls",
    "kb_swing": "kettlebell_swing",
    "chin_up": "pull_up",
    "burpees": "burpee",
    # No broad-jump variant exists in the taxonomy; BURPEE is the closest label.
    "burpee_broad_jump": "burpee",
    "rower": "row_erg",
}


def resolve(name: str) -> tuple[str, str] | None:
    """The Garmin (category, exerciseName) pair for an exercise name, or None if unmapped."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    return EXERCISE_MAP.get(_ALIASES.get(key, key))
