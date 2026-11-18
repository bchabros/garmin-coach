"""Reviewed recorded-set proposals through the shared application boundary."""

import copy

import pytest

from garmin_coach import set_corrections
from garmin_coach.core import db


@pytest.mark.parametrize(
    "weight,grams", [(16.1, 16100), (32.2, 32200), (64.1, 64100), (64.4, 64400)]
)
def test_decimal_kilograms_produce_exact_account_grams(conn, fixture, weight, grams):
    session(conn)
    proposal = set_corrections.preview(
        conn,
        900001,
        [
            {"name": "bench_press", "weight_kg": weight},
            {"name": "deadlift"},
            {"name": "bench_press"},
        ],
        lambda: fixture("exercise_sets_strength"),
    )
    assert proposal["proposed"]["exerciseSets"][0]["weight"] == grams


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


def test_repetition_only_correction_preserves_watch_candidates_and_exact_timing(conn, fixture):
    session(conn)
    captured = fixture("exercise_sets_candidates")
    original = copy.deepcopy(captured)
    proposal = set_corrections.preview(
        conn,
        900001,
        [{"captured_index": 0, "reps": 9}, {"captured_index": 1}, {"captured_index": 2}],
        lambda: captured,
    )
    expected = copy.deepcopy(original)
    expected["exerciseSets"][0]["repetitionCount"] = 9
    assert proposal["proposed"] == expected
    assert captured == original
    assert proposal["pairing"] == [0, 1, 2]
    assert proposal["changes"][0]["before"]["reps"] == 8
    assert proposal["changes"][0]["after"]["reps"] == 9
    assert len(proposal["changes"][0]["after"]["exercises"]) == 3


def test_added_set_uses_observed_nullable_format_and_retains_complete_source(conn, fixture):
    session(conn)
    original = fixture("exercise_sets_candidates")
    proposal = set_corrections.preview(
        conn,
        900001,
        [
            {"captured_index": 0},
            {"captured_index": 1},
            {"captured_index": 2},
            {"captured_index": None, "name": "lunge", "reps": 1, "weight_kg": 0},
        ],
        lambda: original,
    )
    assert proposal["proposed"] == fixture("exercise_sets_added_request")
    assert proposal["pairing"] == [0, 1, 2, None]
    assert proposal["deleted"] == []
    assert len(proposal["added"]) == 1
    assert proposal["added"][0]["before"] is None
    assert proposal["added"][0]["after"]["duration"] is None
    assert proposal["added"][0]["after"]["start_time"] is None
    assert proposal["added"][0]["after"]["timing_source"] == "unrecorded"
    assert original == fixture("exercise_sets_candidates")


def test_explicit_generic_identity_replaces_candidates_without_retiming_work(conn, fixture):
    session(conn)
    original = fixture("exercise_sets_candidates")
    proposal = set_corrections.preview(
        conn,
        900001,
        [{"captured_index": 0}, {"captured_index": 1, "category": "LUNGE"}, {"captured_index": 2}],
        lambda: original,
    )
    proposed = proposal["proposed"]["exerciseSets"]
    assert proposed[2]["exercises"] == [{"category": "LUNGE", "name": None, "probability": 100}]
    assert proposed[2]["duration"] == original["exerciseSets"][2]["duration"]
    assert proposed[2]["startTime"] == original["exerciseSets"][2]["startTime"]
    assert proposed[0] == original["exerciseSets"][0]
    assert proposed[1] == original["exerciseSets"][1]
    assert proposed[3:] == original["exerciseSets"][3:]


def test_auto_alignment_reserves_repeated_identities_before_pairing_new_work(conn, fixture):
    session(conn)
    original = fixture("exercise_sets_candidates")
    proposal = set_corrections.preview(
        conn,
        900001,
        [
            {"category": "LUNGE"},
            {"name": "deadlift", "reps": 5, "weight_kg": 20},
            {"category": "BENCH_PRESS"},
            {"category": "BENCH_PRESS"},
        ],
        lambda: original,
    )
    assert proposal["pairing"] == [0, None, 1, 2]
    active = [
        entry for entry in proposal["proposed"]["exerciseSets"] if entry["setType"] == "ACTIVE"
    ]
    assert [entry["duration"] for entry in active] == [60.125, None, 45.75, 30.625]
    assert active[1]["weight"] == 20000
    assert proposal["changes"][2]["pairing_basis"] == "identity_order_review_required"


@pytest.mark.parametrize("missing", ["reps", "weight_kg"])
def test_explicit_new_work_requires_measurements_before_source_access(conn, missing):
    session(conn)
    row = {"name": "lunge", "reps": 1, "weight_kg": 0, "captured_index": None}
    del row[missing]

    def forbidden():
        pytest.fail("unknown measurements must refuse before connecting")

    with pytest.raises(ValueError, match="explicit reps and weight_kg"):
        set_corrections.preview(conn, 900001, [row], forbidden)


def test_preview_retains_timing_rests_and_opaque_fields(conn, fixture):
    session(conn)
    payload = fixture("exercise_sets_strength")
    payload["opaque"] = {"keep": True}
    payload["exerciseSets"][0]["exercises"][0]["opaque_id"] = "keep"
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
    assert proposal["proposed"]["exerciseSets"][0]["exercises"][0]["opaque_id"] == "keep"
    assert payload == original
    assert proposal["approval"]


def test_invalid_identity_refuses_before_source_read(conn):
    session(conn)

    def forbidden():
        pytest.fail("invalid local proposal must not connect")

    with pytest.raises(ValueError, match="identity"):
        set_corrections.preview(conn, 900001, [{"name": "made_up"}], forbidden)


def test_deleting_added_work_removes_only_its_unrecorded_tail(conn, fixture):
    session(conn)
    original = fixture("exercise_sets_added")
    proposal = set_corrections.preview(
        conn,
        900001,
        [{"captured_index": 0}, {"captured_index": 1}, {"captured_index": 2}],
        lambda: original,
    )
    assert proposal["proposed"] == fixture("exercise_sets_candidates")
    assert proposal["added"] == []
    assert [change["captured_index"] for change in proposal["deleted"]] == [3]
    assert proposal["deleted"][0]["before"]["timing_source"] == "unrecorded"
    assert original == fixture("exercise_sets_added")


def test_pairing_override_changes_values_and_approval_without_reordering_timing(conn, fixture):
    session(conn)
    rows = [{"name": "bench_press"}, {"name": "deadlift"}, {"name": "bench_press"}]
    first = set_corrections.preview(conn, 900001, rows, lambda: fixture("exercise_sets_strength"))
    swapped = [{**row, "captured_index": idx} for row, idx in zip(rows, [1, 0, 2], strict=True)]
    second = set_corrections.preview(
        conn, 900001, swapped, lambda: fixture("exercise_sets_strength")
    )
    assert second["approval"] != first["approval"]
    assert second["proposed"]["exerciseSets"][0]["exercises"][0]["name"] == "BARBELL_DEADLIFT"
    assert second["proposed"]["exerciseSets"][0]["duration"] == 48


def test_saved_read_preserves_caller_transaction(conn):
    session(conn)
    conn.execute("BEGIN")
    set_corrections.saved(conn, 900001)
    assert conn.in_transaction
    conn.rollback()


def test_saved_read_sees_one_generation_during_concurrent_edit(tmp_path):
    path = str(tmp_path / "evidence.sqlite")
    reader = db.connect(path)
    db.bootstrap(reader)
    reader.execute("PRAGMA journal_mode=WAL")
    session(reader)
    db.replace_activity_sets(
        reader, 900001, [{"activity_id": 900001, "set_idx": 0, "subcategory": "BEFORE"}]
    )
    reader.commit()
    writer = db.connect(path)
    triggered = False

    def concurrent_edit(action, table, *args):
        nonlocal triggered
        import sqlite3

        if action == sqlite3.SQLITE_READ and table == "manual_activity_sets" and not triggered:
            triggered = True
            writer.execute("UPDATE activity_sets SET subcategory='AFTER'")
            db.replace_manual_activity_sets(
                writer, 900001, [{"activity_id": 900001, "set_idx": 0, "subcategory": "NEW_MANUAL"}]
            )
            writer.commit()
        return sqlite3.SQLITE_OK

    reader.set_authorizer(concurrent_edit)
    try:
        result = set_corrections.saved(reader, 900001)
        assert result["captured"][0]["subcategory"] == "BEFORE"
        assert result["manual"] == []
        assert triggered
    finally:
        reader.close()
        writer.close()


def test_cli_saved_read_uses_selected_installation_offline(athlete_home, capsys):
    import json
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as conn:
        db.bootstrap(conn)
        session(conn)
    assert cli.main(["--home", str(athlete_home), "activity-sets", "900001"]) == 0
    assert json.loads(capsys.readouterr().out)["activity_id"] == 900001


def test_mcp_saved_read_is_offline_and_preview_is_open_world():
    import asyncio
    from garmin_coach.mcp import server

    tools = {t.name: t for t in asyncio.run(server.server.list_tools())}
    assert tools["get_activity_sets"].annotations.openWorldHint is False
    assert tools["preview_set_correction"].annotations.openWorldHint is True
    assert tools["preview_set_correction"].annotations.readOnlyHint is True


def test_preview_explains_evidence_pairing_and_before_after(conn, fixture):
    session(conn)
    proposal = set_corrections.preview(
        conn,
        900001,
        [
            {"name": "deadlift", "weight_kg": 100},
            {"name": "bench_press", "weight_kg": 60},
            {"name": "bench_press"},
        ],
        lambda: fixture("exercise_sets_strength"),
    )
    assert proposal["pairing"] == [1, 0, 2]
    change = proposal["changes"][0]
    assert change["captured_index"] == 1
    assert change["pairing_basis"] == "identity_and_weight"
    assert change["name_matches"] is True
    assert change["weight_matches"] is True
    assert change["before"]["weight_kg"] == 100
    assert change["after"]["weight_kg"] == 100
    assert change["before"]["duration"] == change["after"]["duration"] == 52


def test_partial_pairing_is_refused_with_specific_error(conn, fixture):
    session(conn)
    with pytest.raises(ValueError, match="captured_index for every"):
        set_corrections.preview(
            conn,
            900001,
            [
                {"name": "bench_press", "captured_index": 1},
                {"name": "deadlift"},
                {"name": "bench_press"},
            ],
            lambda: fixture("exercise_sets_strength"),
        )


@pytest.mark.parametrize("rows", ["{", "{}", '[{"name":"made_up"}]'])
def test_cli_invalid_proposal_is_a_clean_refusal(athlete_home, capsys, rows):
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as conn:
        db.bootstrap(conn)
        session(conn)
    assert (
        cli.main(["--home", str(athlete_home), "activity-sets", "900001", "--rows-json", rows]) == 2
    )
    output = capsys.readouterr()
    assert "garmin-coach:" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize(
    "field,value",
    [
        ("exercises", None),
        ("exercises", [None]),
        ("weight", "heavy"),
        ("weight", -1),
        ("startTime", {}),
        ("startTime", "not-a-time"),
    ],
)
def test_malformed_source_refuses_before_pairing(conn, fixture, field, value):
    session(conn)
    payload = fixture("exercise_sets_strength")
    payload["exerciseSets"][0][field] = value
    with pytest.raises(ValueError, match="captured"):
        set_corrections.preview(conn, 900001, [{"name": "bench_press"}] * 3, lambda: payload)
