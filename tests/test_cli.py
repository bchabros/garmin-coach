"""CLI seam: parser wiring and the transport-free `log-rpe` writers."""

from __future__ import annotations

import argparse
import datetime as _dt
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


def _week_file(intents):
    """A plan file for the week of 2026-07-13, in the athlete's own table format."""
    monday = _dt.date.fromisoformat("2026-07-13")
    rows = "\n".join(
        f"| {abbr} | {(monday + _dt.timedelta(days=i)).strftime('%d.%m')} | sesja | {intent} | plan |"
        for i, (abbr, intent) in enumerate(
            zip(("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"), intents)
        )
    )
    return (
        "| Dzień | Data | Plan | Zamiar (dla silnika) | Status |\n"
        "|---|---|---|---|---|\n" + rows + "\n"
    )


def _seeded_db(tmp_path, dates=("2026-07-13",)):
    """A DB file with one running activity per date, so the marts have something to build."""
    path = tmp_path / "t.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    for i, date in enumerate(dates):
        db.upsert_activity(
            conn,
            {
                "activity_id": 900 + i,
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
    conn.close()
    return path


def test_cmd_event_add_rebuilds_the_block_calendar(tmp_path, monkeypatch, capsys):
    """A race recorded from the terminal dates the periodization at once (#72)."""
    path = _seeded_db(tmp_path)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(path), data_start_date=DATA_START),
    )
    args = argparse.Namespace(
        event_command="add",
        date="2026-10-10",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
        target="1:00:00",
        note=None,
    )

    assert cli._cmd_event(args) == 0

    conn = db.connect(str(path))
    blocks = conn.execute("SELECT count(*) FROM plan_block").fetchone()[0]
    conn.close()
    assert blocks > 0


def test_cmd_event_update_rebuilds_the_block_calendar(tmp_path, monkeypatch):
    """Pinning the race date moves every week's block without a second command (#72)."""
    path = _seeded_db(tmp_path)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(path), data_start_date=DATA_START),
    )
    add = argparse.Namespace(
        event_command="add",
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
        target=None,
        note=None,
    )
    cli._cmd_event(add)
    conn = db.connect(str(path))
    event_id = db.list_goal_events(conn)[0]["id"]
    before = conn.execute(
        "SELECT weeks_to_event FROM plan_block WHERE week_start = '2026-07-13'"
    ).fetchone()[0]
    conn.close()

    update = argparse.Namespace(
        event_command="update",
        event_id=event_id,
        date="2026-10-10",
        type=None,
        priority=None,
        status=None,
        date_precision="exact",
        target=None,
        note=None,
    )
    assert cli._cmd_event(update) == 0

    conn = db.connect(str(path))
    after = conn.execute(
        "SELECT weeks_to_event FROM plan_block WHERE week_start = '2026-07-13'"
    ).fetchone()[0]
    conn.close()
    assert after == before - 1


def test_cmd_plan_import_rebuilds_plan_versus_actual(tmp_path, monkeypatch, capsys):
    """An imported week is compared against what was actually done straight away (#72)."""
    week = [
        (_dt.date.fromisoformat("2026-07-13") + _dt.timedelta(days=i)).isoformat() for i in range(7)
    ]
    path = _seeded_db(tmp_path, dates=tuple(week))
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir()
    (plans_dir / "2026-07-13_week.md").write_text(
        _week_file(["easy", "quality", "rest", "quality", "easy", "rest", "quality"]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(path), data_start_date=DATA_START),
    )
    args = argparse.Namespace(
        week=None, plans_dir=str(plans_dir), reports_dir=str(tmp_path / "reports")
    )

    assert cli._cmd_plan(args) == 0

    conn = db.connect(str(path))
    rows = conn.execute(
        "SELECT count(*) FROM weekly_plan_actual WHERE week_start = '2026-07-13'"
    ).fetchone()[0]
    conn.close()
    assert rows == 7


def test_plan_import_reports_a_pushed_workout_the_new_plan_invalidates(
    tmp_path, monkeypatch, capsys
):
    """A mid-week revision surfaces the conflict at import time, not on the watch
    on the morning of (#22)."""
    plans_dir, reports_dir = tmp_path / "plans", tmp_path / "reports"
    plans_dir.mkdir()
    (plans_dir / "2026-07-13_week.md").write_text(
        _week_file(["easy", "quality", "rest", "quality", "easy", "rest", "quality"]),
        encoding="utf-8",
    )
    day_dir = reports_dir / PUSH_DATE  # the Friday the plan drops to easy
    day_dir.mkdir(parents=True)
    (day_dir / "push.json").write_text(
        json.dumps(
            {"workout_id": 1000, "session_type": "quality", "pushed_at": "2026-07-15T17:28:00"}
        )
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(tmp_path / "t.db"), data_start_date=DATA_START),
    )
    args = argparse.Namespace(week=None, plans_dir=str(plans_dir), reports_dir=str(reports_dir))

    code = cli._cmd_plan(args)

    out = capsys.readouterr().out
    assert code == 0
    assert PUSH_DATE in out
    assert "quality" in out and "easy" in out


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


# --- Issue #60: log-sets writes the manual set overlay for a circuit ---------------


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


def test_parser_accepts_log_sets_with_a_station_list():
    args = build_parser().parse_args(
        ["log-sets", "--activity", "23767493130", "SLED_PULL", "SANDBAG_CARRY"]
    )

    assert args.command == "log-sets"
    assert args.activity_id == 23767493130
    assert args.stations == ["SLED_PULL", "SANDBAG_CARRY"]


def test_log_activity_sets_rejects_unknown_activity(conn):
    with pytest.raises(ValueError, match="not found"):
        cli.log_activity_sets(
            conn, activity_id=999, stations=["SLED_PULL"], data_start_date=DATA_START
        )


def test_log_activity_sets_writes_one_row_per_station_and_recomputes(conn):
    _hyrox(conn)

    out = cli.log_activity_sets(
        conn,
        activity_id=1,
        stations=["SLED_PULL", "SANDBAG_CARRY", "PUSH_PRESS"],
        data_start_date=DATA_START,
    )

    assert out == {"date": "2026-07-28", "n_sets": 3, "unmapped": []}
    rows = conn.execute(
        "SELECT set_idx, subcategory FROM manual_activity_sets WHERE activity_id=1 ORDER BY 1"
    ).fetchall()
    assert rows == [(0, "SLED_PULL"), (1, "SANDBAG_CARRY"), (2, "PUSH_PRESS")]
    # the captured round is untouched, the mart now reads the stations
    assert conn.execute("SELECT COUNT(*) FROM activity_sets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM movement_sets").fetchone()[0] == 3
    # features ran from the activity's date
    assert conn.execute("SELECT 1 FROM daily_metrics WHERE date='2026-07-28'").fetchone()


def test_log_activity_sets_normalizes_names_and_reports_the_unmapped_ones(conn):
    _hyrox(conn)

    out = cli.log_activity_sets(
        conn,
        activity_id=1,
        stations=["sled pull", " Sandbag-Carry ", "wall_walk"],
        data_start_date=DATA_START,
    )

    names = [
        r[0] for r in conn.execute("SELECT subcategory FROM manual_activity_sets ORDER BY set_idx")
    ]
    assert names == ["SLED_PULL", "SANDBAG_CARRY", "WALL_WALK"]
    assert out["unmapped"] == ["WALL_WALK"]  # logged, but not yet in exercise_pattern


def test_log_activity_sets_accepts_station_details(conn):
    _hyrox(conn)

    cli.log_activity_sets(
        conn,
        activity_id=1,
        stations=[{"subcategory": "SLED_PULL", "reps": 4, "duration_s": 160.0}, "V_UP"],
        data_start_date=DATA_START,
    )

    rows = conn.execute(
        "SELECT subcategory, reps, duration_s FROM manual_activity_sets ORDER BY set_idx"
    ).fetchall()
    assert rows == [("SLED_PULL", 4, 160.0), ("V_UP", None, None)]


def test_log_activity_sets_re_log_replaces_the_prior_stations(conn):
    _hyrox(conn)
    cli.log_activity_sets(
        conn, activity_id=1, stations=["SLED_PULL"] * 3, data_start_date=DATA_START
    )

    cli.log_activity_sets(conn, activity_id=1, stations=["V_UP"], data_start_date=DATA_START)

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
    with pytest.raises(ValueError, match=message):
        cli.log_activity_sets(conn, activity_id=1, stations=stations, data_start_date=DATA_START)


def test_log_activity_sets_survives_a_rollback_probe(conn):
    """log_activity_sets owns its own transaction; the write must outlive a rollback."""
    _hyrox(conn)
    cli.log_activity_sets(conn, activity_id=1, stations=["SLED_PULL"], data_start_date=DATA_START)

    conn.rollback()

    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 1


def test_cmd_log_sets_reports_a_failure_with_exit_code_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(tmp_path / "t.db"), data_start_date=DATA_START),
    )
    args = argparse.Namespace(activity_id=999, stations=["SLED_PULL"])

    assert cli._cmd_log_sets(args) == 2
    assert "log-sets failed" in capsys.readouterr().out


# --- daily: a login nobody can answer is a failed run with a readable line (#71) --


def _run_cli_daily(tmp_path, monkeypatch, login_error):
    """Drive _cmd_daily with a login that raises, exercising the real wiring."""
    log_path = tmp_path / "daily.log"
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(
            db_path=str(tmp_path / "t.db"),
            log_path=str(log_path),
            log_max_bytes=1_000_000,
            log_backup_count=1,
        ),
    )

    def _login(_settings):
        raise login_error

    monkeypatch.setattr(cli.client, "login", _login)
    return cli._cmd_daily(argparse.Namespace(to_date=None)), log_path


def test_daily_reports_an_unanswerable_login_as_a_failed_run_without_a_traceback(
    tmp_path, monkeypatch, capsys
):
    error = cli.client.LoginUnavailableError("no terminal is attached; run sync from a terminal")

    exit_code, log_path = _run_cli_daily(tmp_path, monkeypatch, error)

    assert exit_code == 2
    assert "no terminal is attached; run sync from a terminal" in capsys.readouterr().out
    logged = log_path.read_text()
    assert "ERROR" in logged and "no terminal is attached" in logged
    assert "Traceback" not in logged


def test_daily_keeps_the_traceback_for_any_other_login_failure(tmp_path, monkeypatch):
    exit_code, log_path = _run_cli_daily(tmp_path, monkeypatch, RuntimeError("garmin is down"))

    assert exit_code == 2
    assert "Traceback" in log_path.read_text()


def test_a_terminal_command_prints_the_unanswerable_login_and_exits_non_zero(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "get_settings", lambda: types.SimpleNamespace(db_path=str(tmp_path / "t.db"))
    )

    def _login(_settings):
        raise cli.client.LoginUnavailableError("no terminal is attached")

    monkeypatch.setattr(cli.client, "login", _login)

    assert cli.main(["sync"]) == 2
    assert "no terminal is attached" in capsys.readouterr().out
