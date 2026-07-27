"""CLI seam: parser wiring and the transport-free `log-rpe` writers."""

from __future__ import annotations

import argparse
import json
import types

import pytest

from garmin_coach import cli
from garmin_coach.core import db, plan
from garmin_coach.cli import build_parser
from garmin_coach.workouts import publish
from tests.conftest import FakePublisher, run_spec

DATA_START = "2026-06-08"


# --- push: the CLI resolves the account the same way the MCP path does (#40) --

PUSH_DATE = "2026-07-17"


def _run_cli_push(tmp_path, monkeypatch, pub, spec, *, replace=False):
    """Drive _cmd_push with a fake account, exercising the real wiring."""
    day_dir = tmp_path / PUSH_DATE
    day_dir.mkdir(exist_ok=True)
    (day_dir / "workout.json").write_text(json.dumps(spec))
    monkeypatch.setattr(
        cli, "get_settings", lambda: types.SimpleNamespace(db_path=str(tmp_path / "t.db"))
    )
    monkeypatch.setattr(cli.publish, "connect_publisher", lambda settings: pub)
    args = argparse.Namespace(
        date=PUSH_DATE, reports_dir=str(tmp_path), confirm=True, replace=replace
    )
    return cli._cmd_push(args), day_dir


def test_cli_push_does_not_duplicate_a_workout_renamed_in_connect(tmp_path, monkeypatch):
    """The CLI must resolve a renamed workout exactly as push_confirm does (#40)."""
    pub = FakePublisher()
    spec = run_spec(date=PUSH_DATE)
    _run_cli_push(tmp_path, monkeypatch, pub, spec)
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"
    pub.calls.clear()

    _run_cli_push(tmp_path, monkeypatch, pub, spec)

    assert len(pub.workouts) == 1
    assert pub.calls == []


def test_cli_push_passes_the_receipts_workout_id_as_the_lookup_candidate(tmp_path, monkeypatch):
    """Rename plus a changed spec: only the receipt's id still finds the workout."""
    pub = FakePublisher()
    _, day_dir = _run_cli_push(tmp_path, monkeypatch, pub, run_spec(date=PUSH_DATE))
    assert publish.receipt_workout_id(day_dir) == 1000
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"

    code, _ = _run_cli_push(tmp_path, monkeypatch, pub, run_spec(date=PUSH_DATE, work_s=2400))

    assert code == 1  # refused rather than creating a second copy
    assert len(pub.workouts) == 1


def test_cli_push_refuses_a_spec_harder_than_the_plan_of_record(tmp_path, monkeypatch):
    """The guard is not an MCP-only feature - the CLI writes to the same account (#22)."""
    conn = db.connect(str(tmp_path / "t.db"))
    db.bootstrap(conn)
    plan.upsert_week(
        conn,
        [
            {"week_start": "2026-07-13", "dow": dow, "planned": "easy 10 km", "intent": "easy"}
            for dow in range(7)
        ],
    )
    conn.close()
    pub = FakePublisher()

    code, day_dir = _run_cli_push(tmp_path, monkeypatch, pub, run_spec(date=PUSH_DATE))

    assert code == 1
    assert pub.calls == []
    assert not (day_dir / "push.json").exists()


def test_parser_accepts_sync_command_with_optional_to_date():
    args = build_parser().parse_args(["sync", "--to", "2026-06-11"])

    assert args.command == "sync"
    assert args.to_date == "2026-06-11"


def test_parser_accepts_log_rpe_activity_mode():
    args = build_parser().parse_args(["log-rpe", "--activity", "1", "--rpe", "8"])

    assert args.command == "log-rpe"
    assert args.activity_id == 1
    assert args.rpe == 8


def _sila(conn, aid=1, date="2026-06-08"):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 18:00:00",
            "date": date,
            "gtype": "strength_training",
            "discipline": "Siła",
            "aero_te": 1.4,
            "anaero_te": 0.3,
            "training_load": 22.0,
            "dur_s": 4200,
        },
    )


def test_log_session_rpe_rejects_unknown_activity(conn):
    with pytest.raises(ValueError, match="not found"):
        cli.log_session_rpe(conn, activity_id=999, rpe=8, data_start_date=DATA_START)


def test_log_session_rpe_writes_and_recomputes_load(conn):
    _sila(conn)

    date = cli.log_session_rpe(conn, activity_id=1, rpe=9, data_start_date=DATA_START)

    assert date == "2026-06-08"
    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
    load_strength = conn.execute(
        "SELECT load_strength FROM daily_metrics WHERE date='2026-06-08'"
    ).fetchone()[0]
    assert abs(load_strength - 189.0) < 1e-6  # 0.3 * 9 * 70, recomputed by features


def test_log_session_rpe_validates_rpe_range(conn):
    _sila(conn)
    with pytest.raises(ValueError, match="rpe"):
        cli.log_session_rpe(conn, activity_id=1, rpe=11, data_start_date=DATA_START)


def test_parser_accepts_log_rpe_niggle_mode():
    args = build_parser().parse_args(["log-rpe", "--niggle", "kolano", "--severity", "4"])

    assert args.body_part == "kolano"
    assert args.severity == 4


def test_parser_rejects_both_activity_and_niggle():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["log-rpe", "--activity", "1", "--niggle", "kolano", "--severity", "3"]
        )


def test_log_niggle_writes_row(conn):
    day = cli.log_niggle(conn, body_part="kolano", severity=4, date="2026-06-14")

    assert day == "2026-06-14"
    row = conn.execute(
        "SELECT severity FROM niggle WHERE date='2026-06-14' AND body_part='kolano'"
    ).fetchone()
    assert row[0] == 4


def test_log_niggle_validates_severity_range(conn):
    with pytest.raises(ValueError, match="severity"):
        cli.log_niggle(conn, body_part="kolano", severity=6, date="2026-06-14")


# --- Issue #35: standalone recompute entry points still commit their work ---


def test_log_session_rpe_survives_a_rollback_probe(conn):
    """The rollups no longer commit, but this entry point still persists everything."""
    _sila(conn)
    cli.log_session_rpe(conn, activity_id=1, rpe=9, data_start_date=DATA_START)

    conn.rollback()

    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
    row = conn.execute("SELECT load_strength FROM daily_metrics WHERE date='2026-06-08'").fetchone()
    assert row is not None and row[0] is not None


def test_log_niggle_survives_a_rollback_probe(conn):
    """log_niggle owns its own transaction; the write must outlive a rollback."""
    cli.log_niggle(conn, body_part="kolano", severity=4, date="2026-06-14")

    conn.rollback()

    row = conn.execute(
        "SELECT severity FROM niggle WHERE date='2026-06-14' AND body_part='kolano'"
    ).fetchone()
    assert row[0] == 4


# --- `event` command (Phase 9): record what the athlete is training for ---


def test_parser_accepts_event_add_with_both_uncertainty_axes():
    args = build_parser().parse_args(
        [
            "event",
            "add",
            "--date",
            "2026-10-17",
            "--type",
            "hyrox",
            "--priority",
            "A",
            "--status",
            "confirmed",
            "--date-precision",
            "approx",
            "--target",
            "1:00:00",
        ]
    )

    assert args.command == "event"
    assert args.event_command == "add"
    assert args.status == "confirmed"
    assert args.date_precision == "approx"


def test_parser_rejects_an_unknown_event_status():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "event",
                "add",
                "--date",
                "2026-10-17",
                "--type",
                "hyrox",
                "--priority",
                "A",
                "--status",
                "maybe",
            ]
        )


def test_parse_target_s_reads_hours_minutes_seconds():
    assert cli.parse_target_s("1:00:00") == 3600
    assert cli.parse_target_s("1:30:00") == 5400


def test_parse_target_s_reads_minutes_seconds_and_bare_seconds():
    assert cli.parse_target_s("61:46") == 3706
    assert cli.parse_target_s("3600") == 3600


def test_parse_target_s_rejects_nonsense():
    with pytest.raises(ValueError, match="target"):
        cli.parse_target_s("under an hour")


def test_add_goal_event_records_the_race(conn):
    cli.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
        target="1:00:00",
    )

    events = db.list_goal_events(conn)
    assert len(events) == 1
    assert events[0]["target_s"] == 3600


def test_parse_target_s_rejects_out_of_range_minutes_and_seconds():
    with pytest.raises(ValueError, match="target"):
        cli.parse_target_s("1:99")
    with pytest.raises(ValueError, match="target"):
        cli.parse_target_s("0:0:75")


def test_add_goal_event_rejects_a_malformed_date(conn):
    """A date typo must be refused at entry: it would otherwise poison every later read."""
    with pytest.raises(ValueError, match="date"):
        cli.add_goal_event(
            conn,
            date="17/10/2026",
            type="run_race",
            priority="B",
            status="tentative",
            date_precision="exact",
        )

    assert db.list_goal_events(conn) == []


def test_update_goal_event_rejects_a_malformed_date(conn):
    cli.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    with pytest.raises(ValueError, match="date"):
        cli.update_goal_event(conn, event_id, date="24.10.2026")

    assert db.list_goal_events(conn)[0]["date"] == "2026-10-17"


def test_update_goal_event_rejects_an_unknown_id(conn):
    with pytest.raises(ValueError, match="999"):
        cli.update_goal_event(conn, 999, status="confirmed")


def test_update_goal_event_rejects_an_empty_update(conn):
    cli.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    with pytest.raises(ValueError, match="nothing to update"):
        cli.update_goal_event(conn, event_id)


def test_update_goal_event_commits_a_tentative_race(conn):
    cli.add_goal_event(
        conn,
        date="2026-09-05",
        type="run_race",
        priority="B",
        status="tentative",
        date_precision="exact",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    cli.update_goal_event(conn, event_id, status="confirmed")

    assert db.list_goal_events(conn)[0]["status"] == "confirmed"


def test_add_goal_event_rejects_an_unknown_type(conn):
    with pytest.raises(ValueError, match="type"):
        cli.add_goal_event(
            conn,
            date="2026-10-17",
            type="triathlon",
            priority="A",
            status="confirmed",
            date_precision="approx",
        )


def test_add_goal_event_refuses_to_clobber_an_existing_race(conn):
    """Re-adding without --target/--note would silently erase them; it must fail instead."""
    cli.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
        target="1:00:00",
        note="PB 1:01:46",
    )

    with pytest.raises(ValueError, match="already recorded"):
        cli.add_goal_event(
            conn,
            date="2026-10-17",
            type="hyrox",
            priority="A",
            status="confirmed",
            date_precision="approx",
        )

    stored = db.list_goal_events(conn)[0]
    assert stored["target_s"] == 3600
    assert stored["note"] == "PB 1:01:46"


def test_parser_accepts_plan_import_with_week_and_dir():
    args = build_parser().parse_args(
        ["plan", "import", "--week", "2026-07-13", "--plans-dir", "/tmp/plans"]
    )

    assert args.command == "plan"
    assert args.week == "2026-07-13"
    assert args.plans_dir == "/tmp/plans"


def test_parser_plan_import_defaults_to_all_weeks():
    args = build_parser().parse_args(["plan", "import"])

    assert args.week is None
