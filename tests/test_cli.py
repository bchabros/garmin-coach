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
from garmin_coach.workouts import publish, push
from tests.conftest import FakePublisher, run_spec

DATA_START = "2026-06-08"


def _existing_db(tmp_path) -> str:
    """The installation's database, already imported: only an import may create one."""
    path = tmp_path / "t.db"
    if not path.exists():
        conn = db.connect(str(path))
        db.bootstrap(conn)
        conn.commit()
        conn.close()
    return str(path)


# --- push: the CLI resolves the account the same way the MCP path does (#40) --

PUSH_DATE = "2026-07-17"


def _run_cli_push(tmp_path, monkeypatch, pub, spec, *, replace=False):
    """Drive _cmd_push with a fake account, exercising the real wiring."""
    day_dir = tmp_path / PUSH_DATE
    day_dir.mkdir(exist_ok=True)
    (day_dir / "workout.json").write_text(json.dumps(spec))
    monkeypatch.setattr(
        cli, "get_settings", lambda: types.SimpleNamespace(db_path=_existing_db(tmp_path))
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
    assert push.receipt_workout_id(day_dir) == 1000
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


def test_cli_push_prints_the_receipt_it_wrote(tmp_path, monkeypatch, capsys):
    pub = FakePublisher()

    code, day_dir = _run_cli_push(tmp_path, monkeypatch, pub, run_spec(date=PUSH_DATE))

    assert code == 0
    assert (day_dir / "push.json").exists()
    assert f"push complete: {day_dir / 'push.json'}" in capsys.readouterr().out


def test_cli_push_refuses_a_different_date_before_login(
    tmp_path, monkeypatch, capsys, athlete_home
):
    day_dir = tmp_path / PUSH_DATE
    day_dir.mkdir()
    (day_dir / "workout.json").write_text(json.dumps(run_spec(date="2026-07-18")))
    monkeypatch.setattr(
        cli, "get_settings", lambda: types.SimpleNamespace(db_path=_existing_db(tmp_path))
    )
    connections = []
    pub = FakePublisher()

    def connect(settings):
        connections.append(settings)
        return pub

    monkeypatch.setattr(publish, "connect_publisher", connect)

    code = cli.main(["push", "--date", PUSH_DATE, "--reports-dir", str(tmp_path), "--confirm"])

    assert code == 1
    assert connections == []
    assert pub.workouts == {}
    assert not (day_dir / "push.json").exists()
    assert "targets 2026-07-18" in capsys.readouterr().out


def test_parser_accepts_sync_command_with_optional_to_date():
    args = build_parser().parse_args(["sync", "--to", "2026-06-11"])

    assert args.command == "sync"
    assert args.to_date == "2026-06-11"


def test_parser_accepts_log_rpe_activity_mode():
    args = build_parser().parse_args(["log-rpe", "--activity", "1", "--rpe", "8"])

    assert args.command == "log-rpe"
    assert args.activity_id == 1
    assert args.rpe == 8


def test_parser_accepts_log_rpe_niggle_mode():
    args = build_parser().parse_args(["log-rpe", "--niggle", "kolano", "--severity", "4"])

    assert args.body_part == "kolano"
    assert args.severity == 4


def test_parser_rejects_both_activity_and_niggle():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["log-rpe", "--activity", "1", "--niggle", "kolano", "--severity", "3"]
        )


# --- Issue #35: standalone recompute entry points still commit their work ---


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
        lambda: types.SimpleNamespace(db_path=_existing_db(tmp_path), data_start_date=DATA_START),
    )
    args = argparse.Namespace(week=None, plans_dir=str(plans_dir), reports_dir=str(reports_dir))

    code = cli._cmd_plan(args)

    out = capsys.readouterr().out
    assert code == 0
    assert PUSH_DATE in out
    assert "quality" in out and "easy" in out


def test_cli_partial_plan_import_names_the_saved_week_and_rebuilds_it(
    tmp_path, monkeypatch, capsys, athlete_home
):
    path = _seeded_db(tmp_path, dates=("2026-07-13", "2026-07-19"))
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-07-13_week.md").write_text(_week_file(["easy"] * 7), encoding="utf-8")
    (plans / "2026-07-20_week.md").write_text("invalid plan", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(path), data_start_date=DATA_START),
    )

    code = cli.main(
        ["plan", "import", "--plans-dir", str(plans), "--reports-dir", str(tmp_path / "reports")]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "plan import partial: 2026-07-13" in out
    assert "2026-07-20_week.md" in out
    conn = db.connect(str(path))
    try:
        rows = conn.execute(
            "SELECT planned FROM weekly_plan_actual WHERE week_start = '2026-07-13'"
        ).fetchall()
        assert [r[0] for r in rows] == ["easy"] * 7
    finally:
        conn.close()


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


def test_parser_accepts_log_sets_with_a_station_list():
    args = build_parser().parse_args(
        ["log-sets", "--activity", "23767493130", "SLED_PULL", "SANDBAG_CARRY"]
    )

    assert args.command == "log-sets"
    assert args.activity_id == 23767493130
    assert args.stations == ["SLED_PULL", "SANDBAG_CARRY"]


def test_cmd_log_sets_reports_a_failure_with_exit_code_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=_existing_db(tmp_path), data_start_date=DATA_START),
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
            db_path=_existing_db(tmp_path),
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
    tmp_path, monkeypatch, capsys, athlete_home
):
    monkeypatch.setattr(
        cli, "get_settings", lambda: types.SimpleNamespace(db_path=_existing_db(tmp_path))
    )

    def _login(_settings):
        raise cli.client.LoginUnavailableError("no terminal is attached")

    monkeypatch.setattr(cli.client, "login", _login)

    assert cli.main(["sync"]) == 2
    assert "no terminal is attached" in capsys.readouterr().out


def test_cli_author_requires_a_hyrox_variant_without_reporting_success(
    tmp_path, monkeypatch, capsys
):
    settings = types.SimpleNamespace(db_path=_existing_db(tmp_path), sync_recheck_days=3)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"sport": "run", "origin": "athlete", "session_type": "hyrox"}))
    args = build_parser().parse_args(
        [
            "author",
            "--date",
            "2099-07-17",
            "--request",
            str(request),
            "--reports-dir",
            str(tmp_path),
        ]
    )

    assert args.func(args) == 1
    assert "hyrox" in capsys.readouterr().out.lower()
    assert not (tmp_path / "2099-07-17" / "workout.json").exists()


def test_cli_repeat_matches_conversation_and_preserves_the_saved_workout(
    tmp_path, monkeypatch, athlete_home
):
    from garmin_coach.mcp import tools

    settings = types.SimpleNamespace(db_path=_existing_db(tmp_path), sync_recheck_days=3)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    source, target = "2099-07-17", "2099-07-18"
    day = tmp_path / source
    day.mkdir()
    spec = run_spec(date=source)
    spec["name"] = "GC reusable tempo"
    path = day / "workout.json"
    path.write_text(json.dumps(spec))
    original = path.read_bytes()
    args = build_parser().parse_args(
        ["author", "--date", target, "--reuse-from", source, "--reports-dir", str(tmp_path)]
    )

    assert args.func(args) == 0
    cli_spec = json.loads((tmp_path / target / "workout.json").read_text())
    conn = db.connect(settings.db_path)
    try:
        conversational = tools.author_workout(
            conn, target, reuse_from=source, reports_dir=str(tmp_path)
        )
    finally:
        conn.close()
    assert cli_spec == conversational["data"]["spec"]
    assert cli_spec["date"] == target
    assert cli_spec["steps"] == spec["steps"]
    assert cli_spec["name"] == "GC reusable tempo"
    assert path.read_bytes() == original
    assert not (tmp_path / target / "push.json").exists()


def test_cli_author_refuses_a_non_object_request_as_a_plain_error(
    tmp_path, monkeypatch, capsys, athlete_home
):
    settings = types.SimpleNamespace(db_path=_existing_db(tmp_path), sync_recheck_days=3)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    request = tmp_path / "request.json"
    request.write_text("[]")
    args = build_parser().parse_args(["author", "--date", "2099-07-17", "--request", str(request)])

    assert args.func(args) == 1
    assert "JSON object" in capsys.readouterr().out


def test_event_command_shows_current_block_transition_and_anchor_date(
    tmp_path, monkeypatch, capsys
):
    race = (_dt.date.today() + _dt.timedelta(days=28)).isoformat()
    path = _seeded_db(tmp_path)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(path), data_start_date=DATA_START),
    )
    args = build_parser().parse_args(
        [
            "event",
            "add",
            "--date",
            race,
            "--type",
            "hyrox",
            "--priority",
            "A",
            "--status",
            "confirmed",
            "--date-precision",
            "exact",
        ]
    )

    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert "current block: none ->" in output
    assert f"anchor {race}" in output


def test_event_command_reports_saved_record_and_recovery_after_failed_rebuild(athlete_home, capsys):
    from garmin_coach.marts import features

    (athlete_home / ".env").write_text(f"DB_PATH=t.db\nDATA_START_DATE={DATA_START}\n")
    path = _seeded_db(athlete_home)
    conn = db.connect(str(path))
    features.features(conn, data_start_date=DATA_START)
    conn.execute(
        "CREATE TRIGGER fail_snapshot BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
    )
    conn.commit()
    conn.close()
    race = (_dt.date.today() + _dt.timedelta(days=28)).isoformat()

    code = cli.main(
        [
            "--home",
            str(athlete_home),
            "event",
            "add",
            "--date",
            race,
            "--type",
            "hyrox",
            "--priority",
            "A",
            "--status",
            "confirmed",
            "--date-precision",
            "exact",
        ]
    )

    output = capsys.readouterr().out
    assert code == 2
    assert "event partial: record saved; recomputation failed" in output
    assert f"event 1 on {race}" in output
    assert "garmin-coach features" in output
    assert "current block:" not in output
    with db.connect(str(path)) as reopened:
        assert db.list_goal_events(reopened)[0]["date"] == race
