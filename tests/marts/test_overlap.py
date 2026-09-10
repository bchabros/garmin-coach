"""Movement-overlap mart seam: pattern_overlap materialization from core."""

from __future__ import annotations

import logging

from garmin_coach.core import db
from garmin_coach.marts import overlap


def _add_session(conn, activity_id, date, subcats, dur_s=4200.0):
    """Insert a Siła session (blended load = 0.3*7*min) with the given exercise sets."""
    conn.execute(
        "INSERT INTO activities(activity_id, start_local, date, gtype, discipline, "
        "dur_s, training_load) VALUES (?,?,?,?,?,?,?)",
        (activity_id, f"{date} 17:00:00", date, "strength_training", "Siła", dur_s, None),
    )
    rows = [
        {
            "activity_id": activity_id,
            "set_idx": i,
            "category": None,
            "subcategory": sub,
            "reps": 10,
            "sets": None,
            "duration_s": 40.0,
            "max_weight": None,
        }
        for i, sub in enumerate(subcats)
    ]
    db.replace_activity_sets(conn, activity_id, rows)


def _overlap(conn, dim, key, date):
    row = conn.execute(
        "SELECT load_d, load_prev, overlap FROM pattern_overlap WHERE dim=? AND key=? AND date=?",
        (dim, key, date),
    ).fetchone()
    return row


# A full-session Siła of 70 minutes blends to 0.3*7*70 = 147 load units.
FULL = 147.0


def test_overlap_min_and_set_share(conn):
    # Day 1: four deadlifts -> hinge share 1.0 -> hinge load 147
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    # Day 2: two deadlifts + two bench -> hinge share 0.5 -> hinge load 73.5
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 2 + ["BARBELL_BENCH_PRESS"] * 2)
    overlap.rollup(conn)

    hinge = _overlap(conn, "pattern", "hinge", "2026-06-13")
    assert hinge is not None
    load_d, load_prev, ov = hinge
    assert abs(load_d - FULL / 2) < 0.5  # set-share split
    assert abs(load_prev - FULL) < 0.5
    assert abs(ov - FULL / 2) < 0.5  # overlap is the min of the two days
    # push loaded only on day 2 -> no stack, no row
    assert _overlap(conn, "pattern", "push", "2026-06-13") is None
    # the muscle axis stacks too (posterior via the deadlifts)
    assert _overlap(conn, "muscle", "posterior", "2026-06-13") is not None


def test_same_day_sessions_sum(conn):
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    # two separate hinge sessions on day 2 -> summed daily load ~294
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 4)
    _add_session(conn, 3, "2026-06-13", ["KETTLEBELL_SWING"] * 4)
    overlap.rollup(conn)

    load_d, load_prev, ov = _overlap(conn, "pattern", "hinge", "2026-06-13")
    assert abs(load_d - 2 * FULL) < 1.0
    assert abs(ov - FULL) < 1.0  # min(294, 147)


def test_rest_day_clears_stack(conn):
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    # gap on 06-13; next hinge session on 06-14 -> no adjacent-day overlap
    _add_session(conn, 2, "2026-06-14", ["BARBELL_DEADLIFT"] * 4)
    overlap.rollup(conn)

    assert _overlap(conn, "pattern", "hinge", "2026-06-14") is None


def test_below_floor_does_not_stack(conn):
    # 5-minute sessions blend to ~10.5 load each, below pattern_load_floor (20)
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4, dur_s=300.0)
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 4, dur_s=300.0)
    overlap.rollup(conn)

    assert _overlap(conn, "pattern", "hinge", "2026-06-13") is None


def test_unmapped_excluded_and_counted(conn):
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT", "MYSTERY_LIFT"])
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT", "MYSTERY_LIFT"])
    overlap.rollup(conn)

    # denominator is mapped sets only -> hinge share is 1/1, load = full session
    load_d, _, _ = _overlap(conn, "pattern", "hinge", "2026-06-13")
    assert abs(load_d - FULL) < 0.5
    cov = overlap.coverage(conn)
    assert cov["sets_total"] == 4
    assert cov["sets_unmapped"] == 2
    assert cov["unmapped"] == ["MYSTERY_LIFT"]


def test_cardio_pseudo_set_excluded_not_unmapped(conn):
    # A Hyrox-style session: one nameless CARDIO leg + real carry sets. The CARDIO
    # set is a known non-movement, so it neither dilutes the carry share nor shows
    # up as unmapped drift.
    _add_session(conn, 1, "2026-06-12", ["CARDIO"] + ["FARMERS_WALK"] * 3)
    _add_session(conn, 2, "2026-06-13", ["CARDIO"] + ["FARMERS_WALK"] * 3)
    overlap.rollup(conn)

    # carry share is 3/3 (CARDIO excluded from the denominator) -> full session load
    load_d, _, _ = _overlap(conn, "pattern", "carry", "2026-06-13")
    assert abs(load_d - FULL) < 0.5
    cov = overlap.coverage(conn)
    assert cov["sets_total"] == 8  # every ACTIVE set is still captured
    assert cov["sets_unmapped"] == 0  # CARDIO is known, not drift
    assert cov["unmapped"] == []


def test_rollup_is_idempotent(conn):
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 4)
    overlap.rollup(conn)
    overlap.rollup(conn)

    n = conn.execute("SELECT COUNT(*) FROM pattern_overlap").fetchone()[0]
    # one pattern row (hinge) + one muscle row (posterior) on 06-13
    assert n == 2


def test_as_of_drift_warning_ignores_future_sets(conn, caplog):
    # The unmapped set exists only after the cutoff: an as-of recompute must not
    # warn about drift the report's horizon cannot see.
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    _add_session(conn, 2, "2026-06-14", ["MYSTERY_LIFT"])

    with caplog.at_level(logging.WARNING, logger="garmin_coach.marts.overlap"):
        overlap.rollup(conn, through_date="2026-06-13")

    assert "unmapped" not in caplog.text


def test_rollup_as_of_reproduces_past_day(conn):
    # A later stacking session on 06-14 must not leak into a 06-13 recompute.
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 4)
    _add_session(conn, 3, "2026-06-14", ["BARBELL_DEADLIFT"] * 4)

    # Full recompute, then capture the 06-13 hinge stack.
    overlap.rollup(conn)
    full = _overlap(conn, "pattern", "hinge", "2026-06-13")
    assert full is not None

    # Recompute as-of 06-13: reproduces that day and excludes 06-14 entirely.
    overlap.rollup(conn, through_date="2026-06-13")
    assert _overlap(conn, "pattern", "hinge", "2026-06-13") == full
    assert _overlap(conn, "pattern", "hinge", "2026-06-14") is None


# --- Issue #60: hand-logged stations supersede the watch's nameless round ------------

# The ten stations of the 2026-07-28 circuit, as the box published them.
CIRCUIT = [
    "SLED_PULL",
    "SANDBAG_CARRY",
    "PUSH_PRESS",
    "LUNGE",
    "BATTLE_ROPE",
    "SPANISH_SQUAT",
    "BOX_STEP_OVER",
    "SKI_ERG",
    "KETTLEBELL_SWING",
    "V_UP",
]


def _log_stations(conn, activity_id, subcats):
    rows = [
        {
            "activity_id": activity_id,
            "set_idx": i,
            "category": None,
            "subcategory": sub,
            "reps": None,
            "sets": None,
            "duration_s": None,
            "max_weight": None,
        }
        for i, sub in enumerate(subcats)
    ]
    db.replace_manual_activity_sets(conn, activity_id, rows)


def test_manual_rows_alone_feed_the_overlap(conn):
    # No captured sets at all (the enrichment fetch failed); the overlay still counts.
    _add_session(conn, 1, "2026-06-12", [])
    _log_stations(conn, 1, ["SLED_PULL"] * 4)
    _add_session(conn, 2, "2026-06-13", [])
    _log_stations(conn, 2, ["SLED_PULL"] * 4)
    overlap.rollup(conn)

    load_d, _, _ = _overlap(conn, "pattern", "carry", "2026-06-13")
    assert abs(load_d - FULL) < 0.5


def test_manual_rows_supersede_the_captured_rows_of_the_same_activity(conn):
    # The watch says hinge on both days; the athlete's log says carry. Manual wins
    # wholesale - the captured rows are ignored, not merged.
    _add_session(conn, 1, "2026-06-12", ["BARBELL_DEADLIFT"] * 4)
    _log_stations(conn, 1, ["SLED_PULL"] * 4)
    _add_session(conn, 2, "2026-06-13", ["BARBELL_DEADLIFT"] * 4)
    _log_stations(conn, 2, ["SLED_PULL"] * 4)
    overlap.rollup(conn)

    assert _overlap(conn, "pattern", "carry", "2026-06-13") is not None
    assert _overlap(conn, "pattern", "hinge", "2026-06-13") is None


def test_coverage_counts_the_overlay_rows_not_the_superseded_ones(conn):
    _add_session(conn, 1, "2026-06-12", ["UNKNOWN"])
    _log_stations(conn, 1, ["SLED_PULL", "MYSTERY_STATION"])

    cov = overlap.coverage(conn)

    assert cov["sets_total"] == 2
    assert cov["sets_unmapped"] == 1
    assert cov["unmapped"] == ["MYSTERY_STATION"]


def test_the_hyrox_stations_are_seeded_in_the_movement_map(conn):
    mapped = dict(
        (sub, (pat, mus))
        for sub, pat, mus in conn.execute(
            "SELECT subcategory, pattern, muscle_group FROM exercise_pattern"
        )
    )
    assert mapped["SPANISH_SQUAT"] == ("squat", "quads")
    assert mapped["BOX_STEP_OVER"] == ("squat", "quads")
    assert mapped["SKI_ERG"] == ("hinge", "posterior")
    assert mapped["BATTLE_ROPE"] == (None, "shoulders")  # muscle axis only
    assert mapped["V_UP"] == (None, "core")
    # every station of the standard circuit joins the map
    assert set(CIRCUIT) <= set(mapped)


def test_logged_circuit_stacks_carry_and_grip_on_the_day_after_a_carry_session(conn):
    # Adjacent-day acceptance case for issue #60: once the ten stations are logged,
    # the carry / grip axes stack against the previous day's carry work.
    _add_session(conn, 1, "2026-07-27", ["FARMERS_WALK"] * 4)
    _add_session(conn, 2, "2026-07-28", ["UNKNOWN"])
    _log_stations(conn, 2, CIRCUIT)
    overlap.rollup(conn)

    carry = _overlap(conn, "pattern", "carry", "2026-07-28")
    grip = _overlap(conn, "muscle", "grip", "2026-07-28")
    assert carry is not None and grip is not None
    # two of ten stations carry -> 0.2 of the session load on the carry axis
    assert abs(carry[0] - FULL * 0.2) < 0.5
    assert overlap.coverage(conn)["sets_unmapped"] == 0
