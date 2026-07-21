"""Exercise vocabulary: athlete exercise names -> Garmin (category, exerciseName) pairs.

The category/exerciseName enumeration is undocumented by Garmin. Entries here are
proven by live probes or mined from the athlete's own logged exercise sets (issue
#16, ticket T3). Resolution is warn-never-block: an unknown name authors an
unlabeled step, it never refuses the session.
"""

from __future__ import annotations

# Athlete name (normalized: lowercase, underscores) -> (category, exerciseName).
EXERCISE_MAP: dict[str, tuple[str, str]] = {
    # Proven to round-trip by the phase-11 strength probe (2026-07-15).
    "back_squat": ("SQUAT", "BARBELL_BACK_SQUAT"),
}


def resolve(name: str) -> tuple[str, str] | None:
    """The Garmin (category, exerciseName) pair for an exercise name, or None if unmapped."""
    return EXERCISE_MAP.get(name.strip().lower().replace(" ", "_").replace("-", "_"))
