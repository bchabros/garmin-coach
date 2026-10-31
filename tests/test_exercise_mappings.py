"""Exercise mappings through bootstrap and the shared athlete-change interface."""

from __future__ import annotations

import sqlite3

import pytest

from garmin_coach import athlete_changes
from garmin_coach.core import db
from garmin_coach.marts import features, overlap


AGREED = {
    "BACK_SQUAT": ("squat", "quads"),
    "BENCH_PRESS": ("push", "chest"),
    "BOX_JUMP": ("squat", "quads"),
    "DUMBBELL_BICEPS_CURL": ("pull", "grip"),
    "INDOOR_ROW": ("hinge", "posterior"),
    "JUMPING_JACKS": (None, None),
    "ROW": ("hinge", "posterior"),
    "SIT_UP": (None, "core"),
    "SQUAT": ("squat", "quads"),
    "DEADLIFT": ("hinge", "posterior"),
    "DUMBBELL_REVERSE_LUNGE": ("squat", "quads"),
    "DUMBBELL_LATERAL_RAISE": ("push", "shoulders"),
    "JUMP_ROPE": (None, None),
    "BURPEE": (None, "core"),
}


def test_fresh_database_contains_the_fourteen_agreed_assignments(conn):
    installed = {
        name: (pattern, muscle)
        for name, pattern, muscle in conn.execute(
            "SELECT subcategory, pattern, muscle_group FROM exercise_pattern"
        )
    }

    assert {name: installed.get(name) for name in AGREED} == AGREED
    assert set(AGREED) <= installed.keys()


def test_explicit_bootstrap_adds_missing_defaults_and_preserves_local_choices(conn):
    conn.executemany(
        "DELETE FROM exercise_pattern WHERE subcategory=?", [(name,) for name in AGREED]
    )
    conn.executemany(
        "INSERT INTO exercise_pattern VALUES (?, ?, ?)",
        [("BENCH_PRESS", "push", "shoulders"), ("LOCAL_LIFT", "pull", "back")],
    )
    conn.commit()
    with pytest.raises(sqlite3.DatabaseError, match="exercise_pattern"):
        db.validate_schema(conn)

    db.bootstrap(conn)

    db.validate_schema(conn)
    assert conn.execute(
        "SELECT pattern, muscle_group FROM exercise_pattern WHERE subcategory='BENCH_PRESS'"
    ).fetchone() == ("push", "shoulders")
    assert conn.execute(
        "SELECT pattern, muscle_group FROM exercise_pattern WHERE subcategory='LOCAL_LIFT'"
    ).fetchone() == ("pull", "back")
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    db.replace_activity_sets(
        conn,
        1,
        [
            {"activity_id": 1, "set_idx": i, "subcategory": name}
            for i, name in enumerate([*AGREED, "UNKNOWN"])
        ],
    )
    assert overlap.coverage(conn) == {
        "sets_total": 15,
        "sets_unmapped": 1,
        "unmapped": ["UNKNOWN"],
    }


def _session(conn, aid, day, names):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "date": day,
            "start_local": f"{day} 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
            "dur_s": 4200,
        },
    )
    db.replace_activity_sets(
        conn,
        aid,
        [{"activity_id": aid, "set_idx": i, "subcategory": name} for i, name in enumerate(names)],
    )
    conn.commit()


def test_mapping_an_unknown_exercise_rebuilds_its_selected_historical_sets(conn):
    _session(conn, 1, "2026-06-12", ["LOCAL_LIFT", "BENCH_PRESS"])
    _session(conn, 2, "2026-06-13", ["LOCAL_LIFT", "BENCH_PRESS"])
    features.features(conn, data_start_date="2026-06-08")
    assert overlap.coverage(conn)["sets_unmapped"] == 2

    result = athlete_changes.map_exercise(
        conn, name="local lift", pattern="pull", muscle_group="back", data_start_date="2026-06-08"
    )

    assert result.saved is True
    assert result.recomputed is True
    assert result.data["name"] == "LOCAL_LIFT"
    assert result.data["date"] == "2026-06-12"
    assert result.data["affected_sets"] == 2
    assert result.data["newly_mapped_sets"] == 2
    assert overlap.coverage(conn)["sets_unmapped"] == 0
    assert conn.execute(
        "SELECT overlap FROM pattern_overlap WHERE dim='pattern' AND key='pull'"
    ).fetchall() == [(73.5,)]


def test_mapping_counts_respect_manual_selection_and_history_cutoff(conn):
    _session(conn, 1, "2026-06-07", ["LOCAL_LIFT"])
    _session(conn, 2, "2026-06-12", ["LOCAL_LIFT"] * 3)
    _session(conn, 3, "2026-06-13", ["UNKNOWN"])
    athlete_changes.log_activity_sets(
        conn, activity_id=2, stations=["BENCH_PRESS"], data_start_date="2026-06-08"
    )
    athlete_changes.log_activity_sets(
        conn, activity_id=3, stations=["LOCAL_LIFT"] * 2, data_start_date="2026-06-08"
    )

    result = athlete_changes.map_exercise(
        conn, name="LOCAL_LIFT", pattern="pull", muscle_group="back", data_start_date="2026-06-08"
    )

    assert result.data["activities"] == [{"activity_id": 3, "date": "2026-06-13", "sets": 2}]
    assert result.data["affected_sets"] == 2
    assert result.data["date"] == "2026-06-13"


def test_mapping_survives_reopen_when_derived_refresh_fails(tmp_path):
    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _session(conn, 1, "2026-06-12", ["LOCAL_LIFT", "BENCH_PRESS"])
    _session(conn, 2, "2026-06-13", ["LOCAL_LIFT", "BENCH_PRESS"])
    features.features(conn, data_start_date="2026-06-08")
    before = conn.execute("SELECT * FROM pattern_overlap ORDER BY date, dim, key").fetchall()
    conn.execute(
        "CREATE TEMP TRIGGER fail_snapshot BEFORE INSERT ON athlete_status "
        "BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
    )
    conn.commit()

    result = athlete_changes.map_exercise(
        conn, name="LOCAL_LIFT", pattern="pull", muscle_group="back", data_start_date="2026-06-08"
    )
    conn.close()

    assert result.saved is True
    assert result.recomputed is False
    assert "late mart failure" in result.error
    assert result.recovery_command == "garmin-coach features"
    with db.connect(str(path)) as reopened:
        assert reopened.execute(
            "SELECT pattern, muscle_group FROM exercise_pattern WHERE subcategory='LOCAL_LIFT'"
        ).fetchone() == ("pull", "back")
        assert (
            reopened.execute("SELECT * FROM pattern_overlap ORDER BY date, dim, key").fetchall()
            == before
        )


@pytest.mark.parametrize(
    "name,pattern,muscle",
    [("", "push", "chest"), ("UNKNOWN", "pull", "back"), ("X", "run", None), ("X", None, "leg")],
)
def test_invalid_mapping_is_an_unsaved_refusal(conn, name, pattern, muscle):
    before = conn.execute("SELECT * FROM exercise_pattern ORDER BY subcategory").fetchall()
    result = athlete_changes.map_exercise(
        conn, name=name, pattern=pattern, muscle_group=muscle, data_start_date="2026-06-08"
    )
    assert result.saved is False
    assert result.recomputed is None
    assert result.error
    assert conn.execute("SELECT * FROM exercise_pattern ORDER BY subcategory").fetchall() == before


def test_mapping_a_known_exclusion_without_activity_needs_no_refresh(conn):
    result = athlete_changes.map_exercise(
        conn, name="LOCAL_CARDIO", pattern=None, muscle_group=None, data_start_date="2026-06-08"
    )
    assert result.saved is True
    assert result.recomputed is None
    assert result.data["affected_sets"] == 0
    _session(conn, 1, "2026-06-12", ["LOCAL_CARDIO"])
    assert overlap.coverage(conn)["sets_unmapped"] == 0


def test_coach_can_read_mapping_vocabulary_and_save_a_local_assignment(conn, athlete_home):
    from garmin_coach.mcp import tools
    from garmin_coach.runtime import InstallationContext

    with InstallationContext.select(str(athlete_home)).use():
        read = tools.get_exercise_mappings(conn, name="bench press")
        written = tools.map_exercise(
            conn,
            name="LOCAL_LIFT",
            pattern="pull",
            muscle_group="back",
            data_start_date="2026-06-08",
        )
        local = tools.get_exercise_mappings(conn, local_only=True)
    assert read["data"]["mappings"][0]["pattern"] == "push"
    assert read["data"]["mappings"][0]["local"] is False
    assert "carry" in read["data"]["patterns"]
    assert written["data"]["saved"] is True
    assert [row["name"] for row in local["data"]["mappings"]] == ["LOCAL_LIFT"]
    assert local["data"]["mappings"][0]["packaged"] is None
    assert "freshness" in local


def test_terminal_can_save_and_list_mapping_in_the_selected_installation(athlete_home, capsys):
    import json

    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(str(athlete_home))
    context.open_database(create=True).close()
    assert (
        cli.main(
            [
                "--home",
                str(athlete_home),
                "exercise-map",
                "set",
                "local lift",
                "--pattern",
                "pull",
                "--muscle-group",
                "back",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["saved"] is True
    assert cli.main(["--home", str(athlete_home), "exercise-map", "list", "--local-only"]) == 0
    rows = json.loads(capsys.readouterr().out)["mappings"]
    assert [row["name"] for row in rows] == ["LOCAL_LIFT"]


def test_existing_mapping_changes_only_against_the_reviewed_current_value(conn, athlete_home):
    from garmin_coach.mcp import tools
    from garmin_coach.runtime import InstallationContext

    _session(conn, 1, "2026-06-12", ["BENCH_PRESS"])
    with InstallationContext.select(str(athlete_home)).use():
        reviewed = tools.get_exercise_mappings(conn, name="BENCH_PRESS")["data"]["mappings"][0]
        result = tools.map_exercise(
            conn,
            name="BENCH_PRESS",
            pattern="pull",
            muscle_group="back",
            expected_current=reviewed["binding"],
            data_start_date="2026-06-08",
        )
        local = tools.get_exercise_mappings(conn, local_only=True)["data"]["mappings"]

    assert result["data"]["saved"] is True
    assert result["data"]["previous"] == {"pattern": "push", "muscle_group": "chest"}
    assert result["data"]["affected_sets"] == 1
    assert result["data"]["newly_mapped_sets"] == 0
    assert local[0]["name"] == "BENCH_PRESS"
    assert local[0]["packaged"] == {"pattern": "push", "muscle_group": "chest"}
    stale = athlete_changes.map_exercise(
        conn,
        name="BENCH_PRESS",
        pattern="push",
        muscle_group="shoulders",
        expected_current=reviewed["binding"],
        data_start_date="2026-06-08",
    )
    assert stale.saved is False
    assert "changed" in stale.error


def test_identical_retry_does_not_rebuild_and_implicit_replacement_is_refused(conn):
    _session(conn, 1, "2026-06-12", ["LOCAL_LIFT"])
    first = athlete_changes.map_exercise(
        conn, name="LOCAL_LIFT", pattern="pull", muscle_group="back", data_start_date="2026-06-08"
    )
    assert first.recomputed is True
    conn.execute(
        "CREATE TEMP TRIGGER fail_snapshot BEFORE INSERT ON athlete_status "
        "BEGIN SELECT RAISE(ABORT, 'must not rebuild'); END"
    )
    conn.commit()

    retry = athlete_changes.map_exercise(
        conn, name="LOCAL_LIFT", pattern="pull", muscle_group="back", data_start_date="2026-06-08"
    )
    assert retry.saved is True
    assert retry.recomputed is None
    assert retry.data["changed"] is False
    assert retry.data["affected_sets"] == 0
    refusal = athlete_changes.map_exercise(
        conn, name="LOCAL_LIFT", pattern="push", muscle_group="chest", data_start_date="2026-06-08"
    )
    assert refusal.saved is False
    assert refusal.recomputed is None
    assert "reviewed" in refusal.error


def test_registered_mapping_tools_use_selected_storage_without_transport(athlete_home):
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    def forbidden_transport(settings):
        raise AssertionError("mapping must remain offline")

    context = InstallationContext.select(
        athlete_home, import_factory=forbidden_transport, publisher_factory=forbidden_transport
    )
    context.open_database(create=True).close()
    with context.use():
        saved = server.map_exercise("LOCAL_LIFT", "pull", "back")
        reviewed = server.get_exercise_mappings("LOCAL_LIFT")["data"]["mappings"][0]
        replaced = server.map_exercise("LOCAL_LIFT", None, "core", reviewed["binding"])
        after = server.get_exercise_mappings("LOCAL_LIFT")["data"]["mappings"][0]
    assert saved["data"]["saved"] is True
    assert replaced["data"]["saved"] is True
    assert after["pattern"] is None
    assert after["muscle_group"] == "core"


@pytest.mark.parametrize("surface", ["cli_read", "cli_write", "mcp_read", "mcp_write"])
def test_mapping_adapters_require_explicit_migration_of_older_storage(
    athlete_home, capsys, surface
):
    import pathlib

    from garmin_coach import cli
    from garmin_coach.core import installation
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    conn = context.open_database(create=True)
    conn.execute("DELETE FROM exercise_pattern WHERE subcategory='BENCH_PRESS'")
    _session(conn, 1, "2026-06-12", ["BENCH_PRESS"])
    _session(conn, 2, "2026-06-13", ["BENCH_PRESS"])
    features.features(conn, data_start_date="2026-06-08")
    conn.close()
    path = pathlib.Path(context.settings.db_path)
    before = path.read_bytes()

    if surface.startswith("cli"):
        command = (
            ["list", "--name", "BENCH_PRESS"]
            if surface == "cli_read"
            else ["set", "BENCH_PRESS", "--pattern", "push", "--muscle-group", "chest"]
        )
        assert cli.main(["--home", str(athlete_home), "exercise-map", *command]) == 2
        assert "features" in capsys.readouterr().out
    else:
        with context.use(), pytest.raises(installation.InstallationError, match="features"):
            if surface == "mcp_read":
                server.get_exercise_mappings("BENCH_PRESS")
            else:
                server.map_exercise("BENCH_PRESS", "push", "chest")
    assert path.read_bytes() == before
    if surface == "cli_read":
        assert cli.main(["--home", str(athlete_home), "features"]) == 0
        with db.connect(str(path)) as migrated:
            assert overlap.coverage(migrated)["sets_unmapped"] == 0
            assert migrated.execute(
                "SELECT overlap FROM pattern_overlap WHERE dim='pattern' AND key='push'"
            ).fetchall() == [(147.0,)]
        assert cli.main(["--home", str(athlete_home), "exercise-map", "list"]) == 0
