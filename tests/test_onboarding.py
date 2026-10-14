"""First-run setup through the CLI, on temporary athlete installations only."""

from __future__ import annotations

import json

import pytest

from garmin_coach import cli
from garmin_coach.core.config import get_settings
from garmin_coach.core import db


def answer(monkeypatch, *answers):
    responses = iter(answers)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))


def test_new_athlete_configures_an_empty_installation_before_login(tmp_path, monkeypatch, capsys):
    home = tmp_path / "new athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes")

    assert cli.main(["--home", str(home), "setup", "--configure-only"]) == 0

    settings = get_settings()
    assert settings.data_start_date == "2026-09-01"
    assert settings.garmintokens == str(home / ".garminconnect")
    assert settings.weekly_template == "none"
    assert not (home / "data/garmin.db").exists()
    assert all((home / name).is_dir() for name in ("data", "plans", "reports", "logs", "memory"))
    profile = (home / "memory/athlete-profile.md").read_text()
    assert "## Goals" in profile
    assert "## Decyzje" in profile
    assert "Every edit" in (home / "memory/README.md").read_text()
    assert "GARMIN_PASSWORD" not in (home / ".env").read_text()
    assert "Configured" in capsys.readouterr().out


def test_a_fresh_database_has_rules_but_no_other_athletes_measurements_or_plan(tmp_path):
    conn = db.connect(str(tmp_path / "fresh.db"))
    db.bootstrap(conn)

    assert conn.execute("SELECT * FROM plan_template").fetchall() == []
    assert (
        conn.execute(
            "SELECT * FROM coach_thresholds WHERE key IN ('hrv_baseline_ms', 'hrv_sd_ms')"
        ).fetchall()
        == []
    )
    assert conn.execute(
        "SELECT value FROM coach_thresholds WHERE key='acwr_min_chronic_days'"
    ).fetchone() == (28,)
    conn.close()


def test_first_login_imports_the_chosen_range_then_builds_an_honest_read(
    tmp_path, monkeypatch, capsys, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "rest,easy,strength,rest,easy,tempo,rest", "yes", "yes")

    transport = fake_client(by_day={"hrv": {"2026-09-01": {"hrvSummary": {"lastNightAvg": 40}}}})
    logins = []

    def login(settings):
        logins.append(settings)
        assert not (home / "data/garmin.db").exists()
        return transport

    monkeypatch.setattr("garmin_coach.etl.client.login", login)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0

    assert len(logins) == 1
    assert logins[0].garmintokens == str(home / ".garminconnect")
    assert ("activities", "2026-09-01..2026-09-02") in transport.calls
    assert ("sleep", "2026-09-02") in transport.calls
    assert ("lactate", "2026-09-01..2026-09-02") in transport.calls
    conn = db.connect(str(home / "data/garmin.db"))
    assert conn.execute("SELECT intent FROM plan_template ORDER BY dow").fetchall() == [
        (intent,) for intent in ("rest", "easy", "strength", "rest", "easy", "tempo", "rest")
    ]
    assert conn.execute("SELECT * FROM sync_state").fetchall() == []
    conn.close()
    reading = json.loads(
        next((home / "reports/2026-09-02").glob("setup-*/digest.json")).read_text()
    )
    assert reading["headline"]["hrv_baseline"] == 40
    assert reading["headline"]["hrv_sd"] is None
    assert reading["onboarding"]["missing_streams"] == [
        "activities",
        "sleep",
        "wellness",
        "readiness",
        "status",
    ]
    assert "First coach read" in capsys.readouterr().out


def test_without_an_agreed_plan_the_first_read_does_not_report_missed_training(
    tmp_path, monkeypatch, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-08-31", "none", "yes", "yes")
    transport = fake_client(by_day={"hrv": {"2026-09-06": {"hrvSummary": {"lastNightAvg": 40}}}})
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: transport)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-06"]) == 0

    reading = json.loads(
        next((home / "reports/2026-09-06").glob("setup-*/digest.json")).read_text()
    )
    assert reading["weekly"]["plan_adherence"] is None
    assert reading["recommendation"]["intended_type"] is None
    assert reading["recommendation"]["replan"] is None
    gap = next(signal for signal in reading["signals"] if signal["code"] == "PLAN_MISSING")
    assert gap["facts"]["source"] is None


@pytest.mark.parametrize("failure", [RuntimeError("429 secret-token"), KeyboardInterrupt()])
def test_interrupted_or_rate_limited_history_keeps_completed_days_and_can_be_repeated(
    tmp_path, monkeypatch, fake_client, capsys, failure
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")

    class Interrupted(fake_client):
        def get_sleep(self, date):
            if date == "2026-09-02":
                raise failure
            return super().get_sleep(date)

    days = {"hrv": {"2026-09-01": {"hrvSummary": {"lastNightAvg": 40}}}}
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: Interrupted(by_day=days))
    args = ["--home", str(home), "setup", "--to", "2026-09-02"]

    assert cli.main(args) == 2

    output = capsys.readouterr()
    assert "import incomplete" in output.err
    assert "secret-token" not in output.err + output.out
    conn = db.connect(str(home / "data/garmin.db"))
    assert conn.execute("SELECT date, avg_hrv FROM hrv_nightly").fetchall() == [("2026-09-01", 40)]
    assert conn.execute("SELECT * FROM sync_state").fetchall() == []
    assert conn.execute("SELECT count(*) FROM daily_metrics").fetchone() == (0,)
    conn.close()
    first = next((home / "reports/2026-09-02").glob("setup-*/status.json"))
    status = json.loads(first.read_text())
    assert status["onboarding"]["import_complete"] is False
    assert "headline" not in status
    assert "recommendation" not in status
    assert not (first.parent / "snapshot.json").exists()
    before = first.read_bytes()
    answer(monkeypatch, "yes")
    resumed = fake_client(by_day=days)
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: resumed)

    assert cli.main(args) == 0

    assert ("sleep", "2026-09-01") not in resumed.calls
    assert ("sleep", "2026-09-02") in resumed.calls
    assert first.read_bytes() == before
    readings = list((home / "reports/2026-09-02").glob("setup-*/digest.json"))
    assert len(readings) == 1
    assert any(json.loads(path.read_text())["onboarding"]["import_complete"] for path in readings)
    conn = db.connect(str(home / "data/garmin.db"))
    assert conn.execute("SELECT count(*) FROM hrv_nightly").fetchone() == (1,)
    assert conn.execute("SELECT * FROM sync_state").fetchall() == []
    conn.close()


def test_an_empty_import_reports_unknown_measurements(tmp_path, monkeypatch, fake_client, capsys):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client())

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0

    reading = json.loads(
        next((home / "reports/2026-09-02").glob("setup-*/digest.json")).read_text()
    )
    assert reading["headline"]["hrv_baseline"] is None
    assert reading["headline"]["load_7d"] is None
    assert "evidence is missing" in capsys.readouterr().out


def test_adoption_preserves_personal_files_measurements_and_configuration(
    tmp_path, monkeypatch, fake_client, capsys
):
    home = tmp_path / "existing"
    home.mkdir()
    (home / ".env").write_text("DATA_START_DATE=2026-09-01\nDB_PATH=private/history.db\n")
    (home / "private").mkdir()
    conn = db.connect(str(home / "private/history.db"))
    db.bootstrap(conn)
    conn.execute("INSERT INTO coach_thresholds VALUES ('hrv_baseline_ms',68,'historical override')")
    conn.execute("INSERT INTO plan_template VALUES (0,'my Monday','strength')")
    conn.commit()
    conn.close()
    files = {
        ".env": (home / ".env").read_bytes(),
        "memory/athlete-profile.md": b"Private profile",
        "memory/README.md": b"Existing profile rules",
        "plans/notes.md": b"Authored plans",
        "reports/2026-09-02/push.json": b"Private receipt",
        "reports/2026-09-02/digest.json": b"Existing report",
    }
    for name, content in files.items():
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    answer(monkeypatch, "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client())

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0

    for name, content in files.items():
        assert (home / name).read_bytes() == content
    conn = db.connect(str(home / "private/history.db"))
    assert conn.execute("SELECT * FROM plan_template").fetchall() == [(0, "my Monday", "strength")]
    assert conn.execute(
        "SELECT value FROM coach_thresholds WHERE key='hrv_baseline_ms'"
    ).fetchone() == (68,)
    conn.close()
    assert "Existing athlete installation" in capsys.readouterr().out


@pytest.mark.parametrize(
    "start, template, confirm",
    [
        ("not a date", "none", "yes"),
        ("2026-09-03", "none", "yes"),
        ("2026-09-01", "easy", "yes"),
        ("2026-09-01", "none", "no"),
    ],
)
def test_invalid_or_declined_setup_never_creates_state(
    tmp_path, monkeypatch, start, template, confirm
):
    home = tmp_path / "athlete"
    answer(monkeypatch, start, template, confirm)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 2
    assert not home.exists()


def test_setup_refuses_an_unattended_run_before_writing(tmp_path, monkeypatch, capsys):
    home = tmp_path / "athlete"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert cli.main(["--home", str(home), "setup"]) == 2
    assert not home.exists()
    assert "interactive terminal" in capsys.readouterr().err


def test_failed_login_retains_configuration_without_exposing_secrets(tmp_path, monkeypatch, capsys):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")

    def login(settings):
        raise RuntimeError("password=secret token=private-token")

    monkeypatch.setattr("garmin_coach.etl.client.login", login)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 2
    output = capsys.readouterr()
    assert "login failed" in output.err
    assert "secret" not in output.err + output.out
    assert (home / ".env").exists()
    assert not (home / "data/garmin.db").exists()


def test_existing_data_after_the_import_end_does_not_enter_the_first_read(
    tmp_path, monkeypatch, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes")
    assert cli.main(["--home", str(home), "setup", "--configure-only"]) == 0
    from garmin_coach.core import installation

    conn = installation.open_database(get_settings(), create=True)
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-09-01", "avg_hrv": 40})
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-09-03", "avg_hrv": 100})
    conn.commit()
    conn.close()
    answer(monkeypatch, "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client())

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0

    reading = json.loads(
        next((home / "reports/2026-09-02").glob("setup-*/digest.json")).read_text()
    )
    assert reading["window"]["to"] == "2026-09-02"
    assert reading["headline"]["hrv_baseline"] == 40


def test_empty_payloads_do_not_become_measurements_or_zero_training(
    tmp_path, monkeypatch, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")
    transport = fake_client(
        by_day={
            name: {"2026-09-01": {}} for name in ("sleep", "hrv", "wellness", "readiness", "status")
        }
    )
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: transport)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0

    reading = json.loads(
        next((home / "reports/2026-09-02").glob("setup-*/digest.json")).read_text()
    )
    assert len(reading["onboarding"]["missing_streams"]) == 6
    assert reading["headline"]["load_7d"] is None


@pytest.mark.parametrize(
    "variable,value",
    [
        ("DB_PATH", "/another/history.db"),
        ("GARMINTOKENS", "/another/login"),
        ("GARMIN_EMAIL", "other@example.com"),
        ("GARMIN_PASSWORD", "private-secret"),
        ("DATA_START_DATE", "2026-06-08"),
        ("WEEKLY_TEMPLATE", "none"),
    ],
)
def test_new_setup_refuses_inherited_personal_settings_before_creating_anything(
    tmp_path, monkeypatch, capsys, variable, value
):
    home = tmp_path / "athlete"
    answer(monkeypatch)
    monkeypatch.setenv(variable, value)

    assert cli.main(["--home", str(home), "setup"]) == 2
    assert not home.exists()
    error = capsys.readouterr().err
    assert variable in error
    assert value not in error


def test_setup_needs_an_explicit_destination_even_from_a_source_checkout(monkeypatch, capsys):
    answer(monkeypatch)

    assert cli.main(["setup"]) == 2
    assert "--home" in capsys.readouterr().err


def test_unrecognized_nonempty_folder_is_preserved(tmp_path, monkeypatch):
    home = tmp_path / "athlete"
    home.mkdir()
    (home / "notes.md").write_text("keep")
    answer(monkeypatch)

    assert cli.main(["--home", str(home), "setup"]) == 2
    assert [path.name for path in home.iterdir()] == ["notes.md"]
    assert (home / "notes.md").read_text() == "keep"


def test_template_write_failure_does_not_leave_a_half_created_installation(tmp_path, monkeypatch):
    import pathlib

    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes")
    original = pathlib.Path.open

    def fail_profile(path, *args, **kwargs):
        if path.name == "athlete-profile.md" and args and args[0] == "x":
            raise OSError("disk full")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", fail_profile)

    assert cli.main(["--home", str(home), "setup"]) == 2
    assert not home.exists()
    assert list(tmp_path.iterdir()) == []


def test_completed_setup_can_be_repeated_without_another_login(tmp_path, monkeypatch, fake_client):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client())
    args = ["--home", str(home), "setup", "--to", "2026-09-02"]
    assert cli.main(args) == 0
    answer(monkeypatch, "yes")

    def no_login(settings):
        pytest.fail("a completed initial import must not log in again")

    monkeypatch.setattr("garmin_coach.etl.client.login", no_login)

    assert cli.main(args) == 0


def test_a_checkpoint_for_a_replaced_database_never_skips_the_import(
    tmp_path, monkeypatch, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client())
    args = ["--home", str(home), "setup", "--to", "2026-09-02"]
    assert cli.main(args) == 0
    (home / "data/garmin.db").rename(home / "data/previous.db")
    resumed = fake_client()
    answer(monkeypatch, "yes")
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: resumed)

    assert cli.main(args) == 0
    assert ("sleep", "2026-09-01") in resumed.calls


def test_setup_uses_terminal_credentials_and_mfa_in_the_installations_own_login(
    tmp_path, monkeypatch, fake_client, capsys
):
    import secrets
    from garmin_coach.etl import client

    home = tmp_path / "athlete"
    password = secrets.token_urlsafe(16)
    mfa_code = secrets.token_hex(3)
    saved = []

    class Garmin:
        def __init__(self, email=None, password=None, prompt_mfa=None):
            self.email, self.password, self.prompt_mfa = email, password, prompt_mfa

        def login(self, tokenstore):
            assert self.email == "new@example.com"
            assert self.password == password
            assert self.prompt_mfa() == mfa_code
            saved.append(tokenstore)

    answer(monkeypatch, "2026-09-01", "none", "yes", "yes", "new@example.com", mfa_code)
    monkeypatch.setattr(client.getpass, "getpass", lambda prompt: password)
    monkeypatch.setattr(client, "Garmin", Garmin)
    monkeypatch.setattr(client, "GarminTransport", lambda api: fake_client())

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0
    assert saved == [str(home / ".garminconnect")]
    output = capsys.readouterr()
    for secret in (password, mfa_code):
        assert secret not in output.out + output.err
        assert all(secret not in path.read_text() for path in home.rglob("*.json"))
        assert secret not in (home / ".env").read_text()


def test_authored_plan_files_take_precedence_in_the_first_read(tmp_path, monkeypatch, fake_client):
    from garmin_coach.core import plan

    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "rest,rest,rest,rest,rest,rest,rest", "yes")
    assert cli.main(["--home", str(home), "setup", "--configure-only"]) == 0
    text = plan.render_week("2026-09-07", [{"planned": "my easy run", "intent": "easy"}] * 7)
    path = home / "plans/2026-09-07_week.md"
    path.write_text(text)
    answer(monkeypatch, "yes")
    transport = fake_client(by_day={"hrv": {"2026-09-06": {"hrvSummary": {"lastNightAvg": 40}}}})
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: transport)

    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-06"]) == 0

    reading = json.loads(
        next((home / "reports/2026-09-06").glob("setup-*/digest.json")).read_text()
    )
    assert reading["recommendation"]["planned_intent"] == "easy"
    assert path.read_text() == text


def test_an_incomplete_import_preserves_an_existing_generation_of_marts(
    tmp_path, monkeypatch, fake_client
):
    home = tmp_path / "athlete"
    answer(monkeypatch, "2026-09-01", "none", "yes", "yes")
    days = {"hrv": {"2026-09-01": {"hrvSummary": {"lastNightAvg": 40}}}}
    monkeypatch.setattr("garmin_coach.etl.client.login", lambda settings: fake_client(by_day=days))
    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-02"]) == 0
    conn = db.connect(str(home / "data/garmin.db"))
    tables = ("daily_metrics", "weekly_metrics", "athlete_status", "athlete_zones")
    before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    conn.close()

    class Interrupted(fake_client):
        def get_sleep(self, date):
            if date == "2026-09-02":
                raise RuntimeError("rate limited")
            return super().get_sleep(date)

    answer(monkeypatch, "yes")
    changed = {"hrv": {"2026-09-01": {"hrvSummary": {"lastNightAvg": 80}}}}
    monkeypatch.setattr(
        "garmin_coach.etl.client.login", lambda settings: Interrupted(by_day=changed)
    )
    assert cli.main(["--home", str(home), "setup", "--to", "2026-09-03"]) == 2

    conn = db.connect(str(home / "data/garmin.db"))
    assert {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables} == before
    assert conn.execute("SELECT avg_hrv FROM hrv_nightly").fetchone() == (80,)
    conn.close()
