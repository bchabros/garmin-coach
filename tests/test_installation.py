"""Selecting the athlete installation through the CLI entry point (issue #83, ADR 0031)."""

from __future__ import annotations

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

    assert cli.main(["--home", str(home), "event", "list"]) == 0


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


def test_two_installations_keep_separate_garmin_logins(tmp_path, monkeypatch):
    logins = set()
    for name in ("first", "second"):
        monkeypatch.setenv("GARMIN_COACH_HOME", str(make_installation(tmp_path / name)))
        logins.add(get_settings().garmintokens)

    assert len(logins) == 2
