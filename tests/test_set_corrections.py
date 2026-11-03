"""Reviewed recorded-set proposals through the shared application boundary."""

import copy

import pytest

from garmin_coach import set_corrections
from garmin_coach.core import db


def session(conn):
    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    conn.commit()


def test_preview_retains_timing_rests_and_opaque_fields(conn, fixture):
    session(conn)
    payload = fixture("exercise_sets_strength")
    payload["opaque"] = {"keep": True}
    original = copy.deepcopy(payload)
    proposal = set_corrections.preview(
        conn,
        900001,
        [
            {"name": "bench_press", "reps": 8, "weight_kg": 65},
            {"name": "deadlift"},
            {"name": "bench_press"},
        ],
        lambda: payload,
    )
    assert proposal["proposed"]["exerciseSets"][0]["weight"] == 65000
    assert proposal["proposed"]["exerciseSets"][0]["repetitionCount"] == 8
    assert proposal["proposed"]["exerciseSets"][1] == original["exerciseSets"][1]
    assert proposal["proposed"]["exerciseSets"][0]["duration"] == 48
    assert proposal["proposed"]["opaque"] == {"keep": True}
    assert payload == original
    assert proposal["approval"]


def test_invalid_identity_refuses_before_source_read(conn):
    session(conn)

    def forbidden():
        pytest.fail("invalid local proposal must not connect")

    with pytest.raises(ValueError, match="identity"):
        set_corrections.preview(conn, 900001, [{"name": "made_up"}], forbidden)


def test_unverified_add_delete_timing_is_explicitly_refused(conn, fixture):
    session(conn)
    with pytest.raises(ValueError, match="add/delete"):
        set_corrections.preview(
            conn, 900001, [{"name": "bench_press"}], lambda: fixture("exercise_sets_strength")
        )
