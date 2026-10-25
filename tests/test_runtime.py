"""A selected installation keeps configuration stable and observations live."""

from contextlib import closing
import json
import sqlite3
from pathlib import Path
import subprocess

import pytest

from garmin_coach.core.config import get_settings


def test_context_keeps_configuration_until_a_new_run(athlete_home):
    from garmin_coach.runtime import InstallationContext

    config = athlete_home / ".env"
    config.write_text("SYNC_RECHECK_DAYS=5\nPLANS_DIR=./agreed-plans\n")
    context = InstallationContext.select(athlete_home)
    with context.use():
        assert get_settings().sync_recheck_days == 5
        config.write_text("SYNC_RECHECK_DAYS=7\nPLANS_DIR=./new-plans\n")
        assert get_settings().sync_recheck_days == 5
        assert context.plans_dir == athlete_home / "agreed-plans"
    restarted = InstallationContext.select(athlete_home)
    with restarted.use():
        assert get_settings().sync_recheck_days == 7
        assert restarted.plans_dir == athlete_home / "new-plans"


def test_cli_uses_the_context_captured_before_dispatch(athlete_home, capsys):
    from garmin_coach import cli
    from garmin_coach.core import installation
    from garmin_coach.runtime import InstallationContext

    with installation.open_database(installation.load(athlete_home), create=True) as conn:
        conn.execute("INSERT INTO athlete_status(id, computed_at) VALUES (1, '2026-07-03')")

    def select(home):
        context = InstallationContext.select(home)
        (athlete_home / ".env").write_text("DB_PATH=./missing.sqlite\n")
        return context

    assert cli.main(["snapshot"], context_factory=select) == 0
    assert "as of 2026-07-03" in capsys.readouterr().out
    assert list((athlete_home / "reports").glob("*/snapshot.json"))
    assert not (athlete_home / "missing.sqlite").exists()


def test_context_opens_fresh_connections_and_reads_new_observations(athlete_home):
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with closing(context.open_database(create=True)) as first:
        first.execute("INSERT INTO daily_metrics(date, load_day) VALUES ('2026-07-03', 80)")
        first.commit()
    with closing(context.open_database()) as second:
        assert second is not first
        assert second.execute("SELECT load_day FROM daily_metrics").fetchone()[0] == 80
        second.execute("UPDATE daily_metrics SET load_day=100")
        second.commit()
    with closing(context.open_database()) as third:
        assert third.execute("SELECT load_day FROM daily_metrics").fetchone()[0] == 100


def test_import_and_publisher_factories_are_lazy_and_read_current_token_files(athlete_home):
    from garmin_coach.runtime import InstallationContext

    tokens = athlete_home / ".garminconnect"
    tokens.mkdir()
    token = tokens / "saved-token"
    token.write_text("first")
    calls = []

    def connect_import(settings):
        calls.append(
            ("import", settings.db_path, (Path(settings.garmintokens) / "saved-token").read_text())
        )
        return object()

    def connect_publisher(settings):
        calls.append(
            (
                "publisher",
                settings.db_path,
                (Path(settings.garmintokens) / "saved-token").read_text(),
            )
        )
        return object()

    context = InstallationContext.select(
        athlete_home, import_factory=connect_import, publisher_factory=connect_publisher
    )
    assert calls == []
    first = context.connect_import()
    token.write_text("second")
    second = context.connect_import()
    publisher = context.connect_publisher()
    assert first is not second and publisher is not second
    assert calls == [
        ("import", context.settings.db_path, "first"),
        ("import", context.settings.db_path, "second"),
        ("publisher", context.settings.db_path, "second"),
    ]


def test_guarded_context_refuses_bootstrap_and_preserves_old_storage(athlete_home):
    from garmin_coach.runtime import InstallationContext

    database = athlete_home / "legacy.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE legacy_note(value TEXT)")
        conn.execute("INSERT INTO legacy_note VALUES ('preserve')")
    (athlete_home / ".env").write_text("DB_PATH=./legacy.sqlite\n")
    before = database.read_bytes()
    context = InstallationContext.select(athlete_home, existing_db_only=True)
    with pytest.raises(sqlite3.Error):
        context.open_database()
    assert database.read_bytes() == before


def test_cli_diagnosis_uses_the_same_captured_database_as_other_commands(
    athlete_home, tmp_path, capsys
):
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with closing(context.open_database(create=True)) as conn:
        conn.execute("INSERT INTO daily_metrics(date) VALUES ('2026-07-03')")
        conn.execute("INSERT INTO athlete_status(id,computed_at) VALUES (1,'2026-07-03')")
        conn.commit()

    def select(home):
        (athlete_home / ".env").write_text("DB_PATH=./missing.sqlite\n")
        return context

    assert (
        cli.main(["doctor", "--runtime-root", str(tmp_path / "program")], context_factory=select)
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["database"]["state"] == "ready"
    assert not (athlete_home / "missing.sqlite").exists()


def test_explicit_login_diagnosis_keeps_the_captured_token_path(
    athlete_home, tmp_path, monkeypatch, capsys
):
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    (athlete_home / ".env").write_text(
        "GARMIN_EMAIL=private@example.test\nGARMIN_PASSWORD=secret-value\n"
    )
    tokens = athlete_home / ".garminconnect"
    tokens.mkdir()
    (tokens / "saved-token").write_text("fake")
    context = InstallationContext.select(athlete_home)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    def select(home):
        (athlete_home / ".env").write_text("GARMINTOKENS=./other-tokens\n")
        return context

    monkeypatch.setattr(subprocess, "run", run)
    assert (
        cli.main(
            ["doctor", "--live-auth", "--runtime-root", str(tmp_path / "program")],
            context_factory=select,
        )
        == 1
    )
    assert len(calls) == 1
    assert calls[0][-1] == str(tokens)
    assert "private@example.test" not in str(calls) and "secret-value" not in str(calls)
    assert json.loads(capsys.readouterr().out)["checks"]["login"]["state"] == "unverified"
