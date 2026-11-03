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


def test_unverified_add_delete_timing_is_explicitly_refused(conn, fixture):
    session(conn)
    with pytest.raises(ValueError, match="add/delete"):
        set_corrections.preview(
            conn, 900001, [{"name": "bench_press"}], lambda: fixture("exercise_sets_strength")
        )


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
