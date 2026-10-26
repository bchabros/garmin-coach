"""Selecting the athlete installation through the CLI entry point (issue #83, ADR 0031)."""

from __future__ import annotations

import datetime as _dt
import json
import pathlib

import pytest

from garmin_coach import cli
from garmin_coach.core import db, events
from garmin_coach.core.config import get_settings


def make_installation(path: pathlib.Path, env: str = "", race_on: str | None = None):
    """A temporary athlete installation: a `.env` and an existing database."""
    path.mkdir(parents=True)
    (path / ".env").write_text(env)
    (path / "data").mkdir()
    conn = db.connect(str(path / "data" / "garmin.db"))
    db.bootstrap(conn)
    if race_on:
        events.add_goal_event(
            conn,
            date=race_on,
            type="hyrox",
            priority="A",
            status="confirmed",
            date_precision="exact",
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """An unrelated starting folder that is not an installation."""
    start = tmp_path / "elsewhere"
    start.mkdir()
    monkeypatch.chdir(start)
    return start


def test_home_flag_selects_the_installation_from_an_unrelated_folder(tmp_path, elsewhere, capsys):
    home = make_installation(tmp_path / "athlete", race_on="2026-11-08")

    assert cli.main(["--home", str(home), "event", "list"]) == 0

    assert "2026-11-08" in capsys.readouterr().out
    assert list(elsewhere.iterdir()) == []


def test_the_home_variable_selects_the_installation(tmp_path, elsewhere, monkeypatch, capsys):
    home = make_installation(tmp_path / "athlete", race_on="2026-11-08")
    monkeypatch.setenv("GARMIN_COACH_HOME", str(home))

    assert cli.main(["event", "list"]) == 0

    assert "2026-11-08" in capsys.readouterr().out


def test_without_a_selection_the_starting_folder_is_used_when_it_holds_a_dot_env(
    tmp_path, monkeypatch, capsys
):
    home = make_installation(tmp_path / "athlete", race_on="2026-11-08")
    monkeypatch.chdir(home)

    assert cli.main(["event", "list"]) == 0

    assert "2026-11-08" in capsys.readouterr().out


def test_a_named_installation_wins_over_the_starting_folder(tmp_path, monkeypatch, capsys):
    current = make_installation(tmp_path / "current", race_on="2026-09-01")
    named = make_installation(tmp_path / "named", race_on="2026-11-08")
    monkeypatch.chdir(current)

    assert cli.main(["--home", str(named), "event", "list"]) == 0

    output = capsys.readouterr().out
    assert "2026-11-08" in output
    assert "2026-09-01" not in output


def _snapshot(*folders: pathlib.Path) -> dict[pathlib.Path, bytes]:
    return {
        path: path.read_bytes()
        for folder in folders
        for path in folder.rglob("*")
        if path.is_file()
    }


def test_no_installation_is_refused_without_creating_anything(elsewhere, capsys):
    assert cli.main(["features"]) == 2

    error = capsys.readouterr().err
    assert "No athlete installation selected" in error
    assert "--home" in error
    assert "GARMIN_COACH_HOME" in error
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("how", ["flag", "variable"])
def test_a_named_folder_without_dot_env_is_refused(tmp_path, elsewhere, monkeypatch, capsys, how):
    folder = tmp_path / "not an installation"
    folder.mkdir()
    (folder / "notes.md").write_text("keep")
    args = ["features"]
    if how == "flag":
        args = ["--home", str(folder), *args]
    else:
        monkeypatch.setenv("GARMIN_COACH_HOME", str(folder))

    assert cli.main(args) == 2

    assert "Not an athlete installation" in capsys.readouterr().err
    assert [path.name for path in folder.iterdir()] == ["notes.md"]


def test_a_mistyped_home_is_refused_and_never_created(tmp_path, elsewhere, capsys):
    typo = tmp_path / "athelte"

    assert cli.main(["--home", str(typo), "features"]) == 2

    assert "Not an athlete installation" in capsys.readouterr().err
    assert not typo.exists()


def test_flag_and_variable_naming_different_folders_are_refused(
    tmp_path, elsewhere, monkeypatch, capsys
):
    first = make_installation(tmp_path / "first")
    second = make_installation(tmp_path / "second")
    before = _snapshot(first, second)
    monkeypatch.setenv("GARMIN_COACH_HOME", str(second))

    assert cli.main(["--home", str(first), "features"]) == 2

    error = capsys.readouterr().err
    assert "Installation conflict" in error
    assert "GARMIN_COACH_HOME" in error
    assert _snapshot(first, second) == before


def test_flag_and_variable_naming_the_same_folder_agree(tmp_path, elsewhere, monkeypatch, capsys):
    home = make_installation(tmp_path / "athlete", race_on="2026-11-08")
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("GARMIN_COACH_HOME", str(alias))

    assert cli.main(["--home", str(home), "event", "list"]) == 0

    assert "2026-11-08" in capsys.readouterr().out


@pytest.mark.parametrize(
    "variable, value",
    [
        ("DB_PATH", "{other}/data/garmin.db"),
        ("PLANS_DIR", "{other}/plans"),
        ("LOG_PATH", "{other}/logs/daily.log"),
        ("GARMINTOKENS", "{other}/.garminconnect"),
        ("GARMIN_EMAIL", "someone.else@example.com"),
    ],
)
def test_an_environment_value_contradicting_the_installation_is_refused(
    tmp_path, elsewhere, monkeypatch, capsys, variable, value
):
    home = make_installation(tmp_path / "athlete", env="GARMIN_EMAIL=athlete@example.com\n")
    other = make_installation(tmp_path / "other")
    before = _snapshot(home, other)
    monkeypatch.setenv(variable, value.format(other=other))

    assert cli.main(["--home", str(home), "features"]) == 2

    error = capsys.readouterr().err
    assert "Installation conflict" in error
    assert variable in error
    assert "unset" in error
    assert _snapshot(home, other) == before


def test_an_environment_value_agreeing_with_the_installation_is_accepted(
    tmp_path, elsewhere, monkeypatch, capsys
):
    home = make_installation(tmp_path / "athlete", env="GARMIN_EMAIL=Athlete@Example.com\n")
    monkeypatch.setenv("DB_PATH", str(home / "data" / ".." / "data" / "garmin.db"))
    monkeypatch.setenv("GARMIN_EMAIL", "athlete@example.com")
    monkeypatch.setenv("garmin_email", "ATHLETE@example.com")

    assert cli.main(["--home", str(home), "event", "list"]) == 0


@pytest.mark.parametrize("conflict_first", [False, True])
def test_conflicting_case_variants_of_the_account_are_refused(
    tmp_path, elsewhere, monkeypatch, capsys, conflict_first
):
    home = make_installation(tmp_path / "athlete", env="GARMIN_EMAIL=athlete@example.test\n")
    values = [("GARMIN_EMAIL", "athlete@example.test"), ("garmin_email", "other@example.test")]
    for name, value in reversed(values) if conflict_first else values:
        monkeypatch.setenv(name, value)
    before = _snapshot(home)

    assert cli.main(["--home", str(home), "event", "list"]) == 2

    assert "Installation conflict: GARMIN_EMAIL" in capsys.readouterr().err
    assert _snapshot(home) == before
    assert list(elsewhere.iterdir()) == []


def test_an_environment_path_through_a_symlink_names_the_same_file(
    tmp_path, elsewhere, monkeypatch
):
    home = make_installation(tmp_path / "athlete")
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setenv("DB_PATH", str(alias / "data" / "garmin.db"))

    assert cli.main(["--home", str(home), "event", "list"]) == 0


def test_a_relative_environment_path_is_read_from_where_the_run_starts(
    tmp_path, monkeypatch, capsys
):
    """As a shell user would read it: the same text means the same file only from inside."""
    home = make_installation(tmp_path / "athlete")
    monkeypatch.setenv("DB_PATH", "data/garmin.db")

    monkeypatch.chdir(home)
    assert cli.main(["--home", str(home), "event", "list"]) == 0

    monkeypatch.chdir(tmp_path)
    assert cli.main(["--home", str(home), "event", "list"]) == 2
    assert "Installation conflict: DB_PATH" in capsys.readouterr().err


def test_settings_unrelated_to_location_stay_overridable_from_the_environment(
    tmp_path, monkeypatch
):
    home = make_installation(tmp_path / "athlete", env="SYNC_RECHECK_DAYS=3\n")
    monkeypatch.setenv("GARMIN_COACH_HOME", str(home))
    monkeypatch.setenv("SYNC_RECHECK_DAYS", "5")

    assert get_settings().sync_recheck_days == 5


def test_relative_paths_in_the_installation_resolve_against_the_installation(
    tmp_path, elsewhere, capsys
):
    home = tmp_path / "athlete"
    make_installation(home / "nested", race_on="2026-11-08")
    (home / ".env").write_text("DB_PATH=./nested/data/garmin.db\n")

    assert cli.main(["--home", str(home), "event", "list"]) == 0

    assert "2026-11-08" in capsys.readouterr().out
    assert list(elsewhere.iterdir()) == []


def test_the_saved_garmin_login_defaults_to_the_installation(tmp_path, monkeypatch):
    home = make_installation(tmp_path / "athlete")
    monkeypatch.setenv("GARMIN_COACH_HOME", str(home))

    assert pathlib.Path(get_settings().garmintokens) == (home / ".garminconnect").resolve()


def test_an_explicitly_configured_garmin_login_location_is_kept(tmp_path, monkeypatch):
    """The owner's installation names the shared home-folder login in its `.env`."""
    user_home = tmp_path / "user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    home = make_installation(tmp_path / "athlete", env="GARMINTOKENS=~/.garminconnect\n")
    monkeypatch.setenv("GARMIN_COACH_HOME", str(home))

    assert pathlib.Path(get_settings().garmintokens) == (user_home / ".garminconnect").resolve()


@pytest.mark.parametrize(
    "command",
    [
        ["features"],
        ["report"],
        ["snapshot"],
        ["event", "list"],
        ["plan", "import"],
        ["log-rpe", "--activity", "1", "--rpe", "5"],
        ["log-sets", "--activity", "1", "SkiErg"],
        ["author", "--date", "2026-10-14", "--from-recommendation"],
        ["push", "--date", "2026-10-14"],
        ["refresh-today"],
        ["daily"],
        ["reports", "prune"],
        ["reports", "prune", "--confirm"],
    ],
    ids=lambda command: " ".join(command[:2]),
)
def test_only_a_manual_import_may_create_the_database(
    tmp_path, elsewhere, monkeypatch, capsys, command
):
    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("")
    logins = []
    monkeypatch.setattr(cli.client, "login", lambda settings: logins.append(settings))

    assert cli.main(["--home", str(home), *command]) == 2

    error = capsys.readouterr().err
    assert "No database" in error
    assert "backfill" in error
    assert [path.name for path in home.iterdir()] == [".env"]
    assert logins == []
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("confirm", [False, True])
def test_report_pruning_refuses_a_missing_database_without_deleting_reports(
    tmp_path, elsewhere, capsys, confirm
):
    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("")
    report = home / "reports" / "2026-01-01" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("preserve the synthetic report")
    before = _snapshot(home)
    command = ["reports", "prune", *(["--confirm"] if confirm else [])]

    assert cli.main(["--home", str(home), *command]) == 2

    assert "No database" in capsys.readouterr().err
    assert _snapshot(home) == before
    assert not (home / "data").exists()
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("command", [["backfill", "--to", "2026-06-09"], ["sync"]])
def test_a_manual_import_creates_the_database_inside_the_installation(
    tmp_path, elsewhere, monkeypatch, command
):
    from tests.conftest import FakeGarminClient

    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("DATA_START_DATE=2026-06-08\n")
    monkeypatch.setattr(cli.client, "login", lambda settings: FakeGarminClient())

    assert cli.main(["--home", str(home), *command]) == 0

    assert (home / "data" / "garmin.db").is_file()
    assert list(elsewhere.iterdir()) == []


# --- every personal file lands in the selected installation (T3) -------------

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
WORKOUT_DAY = (_dt.date.today() + _dt.timedelta(days=2)).isoformat()
WORKOUT_WEEK = (
    _dt.date.fromisoformat(WORKOUT_DAY)
    - _dt.timedelta(days=_dt.date.fromisoformat(WORKOUT_DAY).weekday())
).isoformat()


def _seed_standing(home: pathlib.Path) -> None:
    conn = db.connect(str(home / "data" / "garmin.db"))
    for date in ("2026-07-21", "2026-07-22"):
        db.upsert_daily(
            conn,
            "daily_metrics",
            {"date": date, "load_day": 80, "hrv": 61, "hrv_baseline": 60, "hrv_sd": 5, "acwr": 1.0},
        )
    conn.commit()
    conn.close()


def _plan_crossfit_week(home: pathlib.Path) -> None:
    """Plan the workout's week so the plan of record allows a crossfit session."""
    (home / "plans").mkdir(exist_ok=True)
    (home / "plans" / f"{WORKOUT_WEEK}_week.md").write_text(
        _week_file(WORKOUT_WEEK, "crossfit"), encoding="utf-8"
    )
    assert cli.main(["--home", str(home), "plan", "import"]) == 0


def _week_file(monday: str, intent: str = "easy") -> str:
    """A plan file in the athlete's own table format, one intent for every day."""
    start = _dt.date.fromisoformat(monday)
    rows = "\n".join(
        f"| {abbr} | {(start + _dt.timedelta(days=i)).strftime('%d.%m')} | sesja | {intent} | plan |"
        for i, abbr in enumerate(("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"))
    )
    return "| Dzień | Data | Plan | Zamiar (dla silnika) | Status |\n|---|---|---|---|---|\n" + rows


def test_a_report_lands_in_the_installation(tmp_path, elsewhere):
    home = make_installation(tmp_path / "athlete")
    _seed_standing(home)

    assert cli.main(["--home", str(home), "report"]) == 0

    today = _dt.date.today().isoformat()
    assert (home / "reports" / today / "digest.json").is_file()
    assert list(elsewhere.iterdir()) == []


def test_an_authored_workout_and_its_push_receipt_land_in_the_installation(
    tmp_path, elsewhere, monkeypatch
):
    from garmin_coach.workouts import publish
    from tests.conftest import FakePublisher

    home = make_installation(tmp_path / "athlete")
    _plan_crossfit_week(home)
    account = FakePublisher()
    monkeypatch.setattr(publish, "connect_publisher", lambda settings: account)
    request = FIXTURES / "hiit_request.json"

    assert (
        cli.main(["--home", str(home), "author", "--date", WORKOUT_DAY, "--request", str(request)])
        == 0
    )
    assert cli.main(["--home", str(home), "push", "--date", WORKOUT_DAY, "--confirm"]) == 0

    day = home / "reports" / WORKOUT_DAY
    assert (day / "workout.json").is_file()
    assert json.loads((day / "push.json").read_text())["applied"] is True
    assert list(elsewhere.iterdir()) == []


def test_the_reports_dir_flag_still_overrides_one_command(tmp_path, elsewhere):
    home = make_installation(tmp_path / "athlete")
    _plan_crossfit_week(home)
    chosen = tmp_path / "chosen reports"
    request = FIXTURES / "hiit_request.json"

    assert (
        cli.main(
            [
                "--home",
                str(home),
                "author",
                "--date",
                WORKOUT_DAY,
                "--request",
                str(request),
                "--reports-dir",
                str(chosen),
            ]
        )
        == 0
    )

    assert (chosen / WORKOUT_DAY / "workout.json").is_file()
    assert not (home / "reports").exists()


def test_plans_are_imported_from_the_installation(tmp_path, elsewhere, capsys):
    home = make_installation(tmp_path / "athlete")
    (home / "plans").mkdir()
    (home / "plans" / "2026-07-13_week.md").write_text(_week_file("2026-07-13"), encoding="utf-8")

    assert cli.main(["--home", str(home), "plan", "import"]) == 0

    assert "1 week(s)" in capsys.readouterr().out
    conn = db.connect(str(home / "data" / "garmin.db"))
    weeks = conn.execute("SELECT count(DISTINCT week_start) FROM plan_week").fetchone()[0]
    conn.close()
    assert weeks == 1
    assert list(elsewhere.iterdir()) == []


def test_the_nightly_log_lands_in_the_installation(tmp_path, elsewhere, monkeypatch):
    from tests.conftest import FakeGarminClient

    yesterday = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    home = make_installation(tmp_path / "athlete", env=f"DATA_START_DATE={yesterday}\n")
    monkeypatch.setattr(cli.client, "login", lambda settings: FakeGarminClient())

    assert cli.main(["--home", str(home), "daily"]) in (0, 1)

    assert "daily: run complete" in (home / "logs" / "daily.log").read_text()
    assert list(elsewhere.iterdir()) == []


def test_report_pruning_acts_on_the_installation(tmp_path, elsewhere, capsys):
    home = make_installation(tmp_path / "athlete")
    old = (_dt.date.today() - _dt.timedelta(days=120)).isoformat()
    (home / "reports" / old).mkdir(parents=True)
    (home / "reports" / old / "report.md").write_text("report")

    assert cli.main(["--home", str(home), "reports", "prune"]) == 0

    assert f"{old}: age=120 days" in capsys.readouterr().out


def test_two_installations_keep_separate_garmin_logins(tmp_path, monkeypatch):
    logins = set()
    for name in ("first", "second"):
        monkeypatch.setenv("GARMIN_COACH_HOME", str(make_installation(tmp_path / name)))
        logins.add(get_settings().garmintokens)

    assert len(logins) == 2
