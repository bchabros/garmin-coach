"""Athlete-change operations: durable facts and honest derived-state outcomes."""

import datetime as dt
import re

import pytest

from garmin_coach import athlete_changes
from garmin_coach.core import db, events
from garmin_coach.marts import features

DATA_START = "2026-06-08"


def _strength(conn, aid=1, date=DATA_START):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 18:00:00",
            "date": date,
            "gtype": "strength_training",
            "discipline": "Siła",
            "dur_s": 4200,
            "aero_te": 1.4,
            "anaero_te": 0.3,
            "training_load": 22.0,
        },
    )
    conn.commit()


def _fail_late_mart(conn):
    conn.execute(
        "CREATE TEMP TRIGGER fail_snapshot BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
    )
    conn.commit()


def test_saved_rpe_survives_a_late_mart_failure_and_database_reopen(tmp_path):
    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _strength(conn)
    features.features(conn, data_start_date=DATA_START)
    previous = conn.execute(
        "SELECT load_strength FROM daily_metrics WHERE date=?", (DATA_START,)
    ).fetchone()[0]
    _fail_late_mart(conn)

    result = athlete_changes.log_session_rpe(conn, activity_id=1, rpe=9, data_start_date=DATA_START)
    conn.rollback()
    conn.close()

    assert result.saved is True
    assert result.recomputed is False
    assert result.data["activity_id"] == 1
    assert result.data["date"] == DATA_START
    assert "late mart failure" in result.error
    assert result.recovery_command == "garmin-coach features"
    with db.connect(str(path)) as reopened:
        assert (
            reopened.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
        )
        assert (
            reopened.execute(
                "SELECT load_strength FROM daily_metrics WHERE date=?", (DATA_START,)
            ).fetchone()[0]
            == previous
        )


def test_niggle_is_durable_without_a_mart_pass(conn):
    _strength(conn)
    _fail_late_mart(conn)
    result = athlete_changes.log_niggle(conn, body_part="kolano", severity=4, date=DATA_START)
    conn.rollback()

    assert result.saved is True
    assert result.recomputed is None
    assert result.error is None
    assert result.data["date"] == DATA_START
    assert conn.execute("SELECT severity FROM niggle WHERE body_part='kolano'").fetchone()[0] == 4


def test_manual_sets_survive_rebuild_failure_without_replacing_captured_evidence(tmp_path):
    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _strength(conn)
    db.replace_activity_sets(conn, 1, [{"activity_id": 1, "set_idx": 0, "subcategory": "UNKNOWN"}])
    conn.commit()
    features.features(conn, data_start_date=DATA_START)
    _fail_late_mart(conn)

    result = athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=["SLED_PULL", "WALL_WALK"], data_start_date=DATA_START
    )
    conn.close()

    assert result.saved is True
    assert result.recomputed is False
    assert result.data["n_sets"] == 2
    assert result.data["unmapped"] == ["WALL_WALK"]
    with db.connect(str(path)) as reopened:
        assert reopened.execute(
            "SELECT subcategory FROM manual_activity_sets ORDER BY set_idx"
        ).fetchall() == [("SLED_PULL",), ("WALL_WALK",)]
        assert reopened.execute("SELECT subcategory FROM activity_sets").fetchall() == [
            ("UNKNOWN",)
        ]


def test_goal_event_add_returns_saved_identity_and_refreshed_anchor(conn):
    _strength(conn)
    race = (dt.date.today() + dt.timedelta(days=28)).isoformat()
    result = athlete_changes.event_add(
        conn,
        date=race,
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
        data_start_date=DATA_START,
    )

    assert result.saved is True
    assert result.recomputed is True
    assert result.data["event"]["date"] == race
    assert result.data["block_before"] is None
    assert result.data["block_after"]["race_date"] == race
    assert result.data["block_after"]["anchor_event_id"] == result.data["event"]["id"]


def test_goal_update_failure_keeps_new_event_and_previous_marts_without_a_mixed_block(tmp_path):
    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _strength(conn)
    original = (dt.date.today() + dt.timedelta(days=28)).isoformat()
    changed = (dt.date.today() + dt.timedelta(days=56)).isoformat()
    event_id = events.add_goal_event(
        conn, date=original, type="hyrox", priority="A", status="confirmed", date_precision="exact"
    )
    features.features(conn, data_start_date=DATA_START)
    previous = conn.execute("SELECT * FROM plan_block ORDER BY week_start").fetchall()
    _fail_late_mart(conn)

    result = athlete_changes.event_update(
        conn, event_id=event_id, date=changed, data_start_date=DATA_START
    )
    conn.close()

    assert result.saved is True
    assert result.recomputed is False
    assert result.data["event"]["id"] == event_id
    assert result.data["event"]["date"] == changed
    assert result.data["block_before"]["race_date"] == original
    assert result.data["block_after"] is None
    with db.connect(str(path)) as reopened:
        assert db.list_goal_events(reopened)[0]["date"] == changed
        assert (
            reopened.execute("SELECT * FROM plan_block ORDER BY week_start").fetchall() == previous
        )
        features.features(reopened, data_start_date=DATA_START)
        from garmin_coach.marts import periodize

        assert periodize.current_plan(reopened, dt.date.today().isoformat())["race_date"] == changed
        assert len(db.list_goal_events(reopened)) == 1


def test_conflicting_goal_update_is_an_unsaved_readable_refusal(conn):
    first = events.add_goal_event(
        conn,
        date="2099-10-10",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
    )
    second = events.add_goal_event(
        conn,
        date="2099-10-17",
        type="hyrox",
        priority="B",
        status="tentative",
        date_precision="exact",
    )

    result = athlete_changes.event_update(
        conn, event_id=second, date="2099-10-10", data_start_date=DATA_START
    )

    assert result.saved is False
    assert result.recomputed is None
    assert "already" in result.error
    assert [(row["id"], row["date"]) for row in db.list_goal_events(conn)] == [
        (first, "2099-10-10"),
        (second, "2099-10-17"),
    ]


# Workflow regressions formerly exercised through CLI implementation helpers.


def test_log_session_rpe_rejects_unknown_activity(conn):
    result = athlete_changes.log_session_rpe(
        conn, activity_id=999, rpe=8, data_start_date=DATA_START
    )
    assert result.saved is False
    assert re.search("not found", result.error)


def test_log_session_rpe_writes_and_recomputes_load(conn):
    _strength(conn)
    date = athlete_changes.log_session_rpe(
        conn, activity_id=1, rpe=9, data_start_date=DATA_START
    ).data["date"]
    assert date == "2026-06-08"
    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
    load_strength = conn.execute(
        "SELECT load_strength FROM daily_metrics WHERE date='2026-06-08'"
    ).fetchone()[0]
    assert abs(load_strength - 189.0) < 1e-06


def test_log_session_rpe_validates_rpe_range(conn):
    _strength(conn)
    result = athlete_changes.log_session_rpe(
        conn, activity_id=1, rpe=11, data_start_date=DATA_START
    )
    assert result.saved is False
    assert re.search("rpe", result.error)


def test_log_niggle_writes_row(conn):
    day = athlete_changes.log_niggle(conn, body_part="kolano", severity=4, date="2026-06-14").data[
        "date"
    ]
    assert day == "2026-06-14"
    row = conn.execute(
        "SELECT severity FROM niggle WHERE date='2026-06-14' AND body_part='kolano'"
    ).fetchone()
    assert row[0] == 4


def test_log_niggle_validates_severity_range(conn):
    result = athlete_changes.log_niggle(conn, body_part="kolano", severity=6, date="2026-06-14")
    assert result.saved is False
    assert re.search("severity", result.error)


def test_log_session_rpe_survives_a_rollback_probe(conn):
    """The rollups no longer commit, but this entry point still persists everything."""
    _strength(conn)
    athlete_changes.log_session_rpe(conn, activity_id=1, rpe=9, data_start_date=DATA_START).data[
        "date"
    ]
    conn.rollback()
    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
    row = conn.execute("SELECT load_strength FROM daily_metrics WHERE date='2026-06-08'").fetchone()
    assert row is not None and row[0] is not None


def test_log_niggle_survives_a_rollback_probe(conn):
    """log_niggle owns its own transaction; the write must outlive a rollback."""
    athlete_changes.log_niggle(conn, body_part="kolano", severity=4, date="2026-06-14").data["date"]
    conn.rollback()
    row = conn.execute(
        "SELECT severity FROM niggle WHERE date='2026-06-14' AND body_part='kolano'"
    ).fetchone()
    assert row[0] == 4


def _hyrox(conn, aid=1, date="2026-07-28"):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 19:30:00",
            "date": date,
            "gtype": "hiit",
            "discipline": "Hyrox/HIIT",
            "training_load": 149.0,
            "dur_s": 3706,
        },
    )
    db.replace_activity_sets(
        conn,
        aid,
        [
            {
                "activity_id": aid,
                "set_idx": 0,
                "category": "UNKNOWN",
                "subcategory": "UNKNOWN",
                "reps": 4,
                "sets": None,
                "duration_s": 3706.0,
                "max_weight": None,
            }
        ],
    )


def test_log_activity_sets_rejects_unknown_activity(conn):
    result = athlete_changes.log_activity_sets(
        conn, activity_id=999, stations=["SLED_PULL"], data_start_date=DATA_START
    )
    assert result.saved is False
    assert re.search("not found", result.error)


def test_log_activity_sets_writes_one_row_per_station_and_recomputes(conn):
    _hyrox(conn)
    out = athlete_changes.log_activity_sets(
        conn,
        activity_id=1,
        stations=["SLED_PULL", "SANDBAG_CARRY", "PUSH_PRESS"],
        data_start_date=DATA_START,
    ).data
    assert out == {"activity_id": 1, "date": "2026-07-28", "n_sets": 3, "unmapped": []}
    rows = conn.execute(
        "SELECT set_idx, subcategory FROM manual_activity_sets WHERE activity_id=1 ORDER BY 1"
    ).fetchall()
    assert rows == [(0, "SLED_PULL"), (1, "SANDBAG_CARRY"), (2, "PUSH_PRESS")]
    assert conn.execute("SELECT COUNT(*) FROM activity_sets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM movement_sets").fetchone()[0] == 3
    assert conn.execute("SELECT 1 FROM daily_metrics WHERE date='2026-07-28'").fetchone()


def test_log_activity_sets_normalizes_names_and_reports_the_unmapped_ones(conn):
    _hyrox(conn)
    out = athlete_changes.log_activity_sets(
        conn,
        activity_id=1,
        stations=["sled pull", " Sandbag-Carry ", "wall_walk"],
        data_start_date=DATA_START,
    ).data
    names = [
        r[0] for r in conn.execute("SELECT subcategory FROM manual_activity_sets ORDER BY set_idx")
    ]
    assert names == ["SLED_PULL", "SANDBAG_CARRY", "WALL_WALK"]
    assert out["unmapped"] == ["WALL_WALK"]


def test_log_activity_sets_accepts_station_details(conn):
    _hyrox(conn)
    athlete_changes.log_activity_sets(
        conn,
        activity_id=1,
        stations=[{"subcategory": "SLED_PULL", "reps": 4, "duration_s": 160.0}, "V_UP"],
        data_start_date=DATA_START,
    ).data
    rows = conn.execute(
        "SELECT subcategory, reps, duration_s FROM manual_activity_sets ORDER BY set_idx"
    ).fetchall()
    assert rows == [("SLED_PULL", 4, 160.0), ("V_UP", None, None)]


def test_log_activity_sets_re_log_replaces_the_prior_stations(conn):
    _hyrox(conn)
    athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=["SLED_PULL"] * 3, data_start_date=DATA_START
    ).data
    athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=["V_UP"], data_start_date=DATA_START
    ).data
    assert conn.execute("SELECT subcategory FROM manual_activity_sets").fetchall() == [("V_UP",)]


@pytest.mark.parametrize(
    "stations, message",
    [
        ([], "at least one station"),
        (["SLED_PULL", ""], "empty"),
        (["SLED PULL!"], "letters, digits"),
        ([{"reps": 4}], "subcategory"),
        ([{"subcategory": "SLED_PULL", "reps": -1}], "reps"),
        ([{"subcategory": "SLED_PULL", "duration_s": "long"}], "duration_s"),
        ([{"subcategory": "SLED_PULL", "rep": 4}], "unsupported field.*rep"),
    ],
)
def test_log_activity_sets_rejects_malformed_input(conn, stations, message):
    _hyrox(conn)
    result = athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=stations, data_start_date=DATA_START
    )
    assert result.saved is False
    assert re.search(message, result.error)


def test_log_activity_sets_survives_a_rollback_probe(conn):
    """log_activity_sets owns its own transaction; the write must outlive a rollback."""
    _hyrox(conn)
    athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=["SLED_PULL"], data_start_date=DATA_START
    ).data
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 1


def test_log_sets_malformed_station_returns_error_without_writing(conn):
    _strength(conn, aid=1, date="2026-07-28")
    out = athlete_changes.log_activity_sets(
        conn, activity_id=1, stations=["SLED_PULL", ""], data_start_date=DATA_START
    ).as_data()
    assert "station 1" in out["error"]
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 0


RACE = (dt.date.today() + dt.timedelta(days=21)).isoformat()


def _seed_core_day(conn, date: str = "2026-07-13", activity_id: int = 900) -> None:
    """One stored run, so the mart rebuild the writers trigger has something to build."""
    db.upsert_activity(
        conn,
        {
            "activity_id": activity_id,
            "start_local": f"{date} 12:00:00",
            "date": date,
            "gtype": "running",
            "dur_s": 2400,
            "aero_te": 2.4,
            "anaero_te": 0.2,
            "training_load": 90.0,
        },
    )
    conn.commit()


def _add_race(conn, date: str = RACE, **over):
    fields = {
        "date": date,
        "type": "hyrox",
        "priority": "A",
        "status": "confirmed",
        "date_precision": "approx",
        "data_start_date": DATA_START,
    }
    fields.update(over)
    return athlete_changes.event_add(conn, **fields).as_data()


def test_event_add_reports_the_block_before_and_after(conn):
    """The athlete sees the periodization move, not just that a row was written (#72)."""
    _seed_core_day(conn)
    out = _add_race(conn)
    assert out["block_before"] is None
    assert out["block_after"]["block"] in ("base", "build", "peak", "taper", "race")
    assert out["block_after"]["weeks_to_event"] == 3
    assert out["block_after"]["anchor_event_id"] == out["event"]["id"]


def test_event_add_refuses_a_malformed_date_as_tool_text(conn):
    out = _add_race(conn, date="17/10/2026")
    assert "date" in out["error"]
    assert out["event"] is None
    assert db.list_goal_events(conn) == []


def test_event_add_refuses_a_race_already_recorded(conn):
    _seed_core_day(conn)
    _add_race(conn)
    out = _add_race(conn, priority="B")
    assert "already recorded" in out["error"]
    assert len(db.list_goal_events(conn)) == 1


def test_event_update_refuses_an_unknown_event(conn):
    out = athlete_changes.event_update(
        conn, event_id=999, status="confirmed", data_start_date=DATA_START
    ).as_data()
    assert "999" in out["error"]


def test_event_update_refuses_an_empty_change(conn):
    _seed_core_day(conn)
    _add_race(conn)
    event_id = db.list_goal_events(conn)[0]["id"]
    out = athlete_changes.event_update(
        conn, event_id=event_id, data_start_date=DATA_START
    ).as_data()
    assert "nothing to update" in out["error"]


def test_event_add_reports_the_row_it_stored_whatever_shape_the_date_came_in(conn):
    """The writer normalises the date, so matching on what was typed would miss it."""
    _seed_core_day(conn)
    compact = (dt.date.today() + dt.timedelta(days=21)).strftime("%Y%m%d")
    out = athlete_changes.event_add(
        conn,
        date=compact,
        type="run_race",
        priority="B",
        status="tentative",
        date_precision="exact",
        data_start_date=DATA_START,
    ).as_data()
    assert out["error"] is None
    assert out["event"] is not None
    assert out["event"]["date"] == RACE


def test_database_write_failure_is_not_reported_as_a_saved_partial_change(conn):
    _strength(conn)
    conn.execute(
        "CREATE TEMP TRIGGER block_rpe BEFORE INSERT ON session_rpe BEGIN SELECT RAISE(ABORT, 'record write blocked'); END"
    )
    conn.commit()

    result = athlete_changes.log_session_rpe(conn, activity_id=1, rpe=8, data_start_date=DATA_START)

    assert result.saved is False
    assert result.recomputed is None
    assert "record write blocked" in result.error
    assert result.recovery_command is None
    assert conn.execute("SELECT count(*) FROM session_rpe").fetchone()[0] == 0


def test_goal_event_write_failure_is_an_unsaved_result(conn):
    conn.execute(
        "CREATE TEMP TRIGGER block_event BEFORE INSERT ON goal_event BEGIN SELECT RAISE(ABORT, 'event write blocked'); END"
    )
    conn.commit()

    result = athlete_changes.event_add(
        conn,
        date="2099-10-10",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
        data_start_date=DATA_START,
    )

    assert result.saved is False
    assert result.recomputed is None
    assert "event write blocked" in result.error
    assert result.recovery_command is None
    assert db.list_goal_events(conn) == []


def test_saved_event_is_acknowledged_when_reading_its_context_fails(conn):
    _strength(conn)
    db.insert_goal_event(
        conn,
        {
            "date": "bad-date",
            "type": "run_race",
            "priority": "B",
            "status": "tentative",
            "date_precision": "exact",
        },
    )
    conn.commit()
    race = (dt.date.today() + dt.timedelta(days=28)).isoformat()

    result = athlete_changes.event_add(
        conn,
        date=race,
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
        data_start_date=DATA_START,
    )

    assert result.saved is True
    assert result.data["event_id"] > 0
    assert "record saved" in result.error
    assert "could not read updated event context" in result.error
    assert len(db.list_goal_events(conn)) == 2
