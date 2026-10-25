"""Interrupted maintenance preserves the last recoverable installation state."""

import subprocess

import pytest

from garmin_coach import cli, clients, distribution
from tests.test_lifecycle import NativeClients, make_home, managed_program


def test_removing_a_client_updates_the_installers_saved_selection(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path / "athlete")
    root, _, _ = managed_program(tmp_path)
    state = distribution.read_state(root)
    state.update(selected_home=str(home), selected_clients=["codex", "claude-code"])
    distribution.write_state(root, state)
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(subprocess, "run", NativeClients())
    clients.configure(home, root, ["codex", "claude-code"])

    assert (
        cli.main(
            [
                "--home",
                str(home),
                "program",
                "remove",
                "--runtime-root",
                str(root),
                "--client",
                "claude-code",
                "--confirm",
            ]
        )
        == 0
    )

    capsys.readouterr()
    assert clients.status(home, root)["selected_clients"] == ["codex"]
    assert distribution.read_state(root)["selected_clients"] == ["codex"]


def update_args(home, root, manifest):
    return [
        "--home",
        str(home),
        "program",
        "update",
        "--runtime-root",
        str(root),
        "--manifest",
        str(manifest),
    ]


def test_unchanged_update_never_rebuilds_the_active_release(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)

    def forbidden(*args):
        raise AssertionError("An unchanged update must not rebuild its active runtime")

    monkeypatch.setattr(distribution, "prepare_release", forbidden)
    assert cli.main(update_args(home, root, old / "manifest.json")) == 0
    assert '"unchanged"' in capsys.readouterr().out
    assert (root / "current").resolve() == old


def test_interrupted_preparation_is_recoverable_on_retry(tmp_path, monkeypatch, capsys):
    import pytest

    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)
    calls = 0

    def prepare(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SystemExit("Simulate process termination after durable journal")
        return new

    monkeypatch.setattr(distribution, "prepare_release", prepare)
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0)
    )
    args = update_args(home, root, new / "manifest.json")
    with pytest.raises(SystemExit):
        cli.main(args)
    assert (root / "current").resolve() == old
    assert distribution.read_state(root)["operation"]["phase"] == "preparing"

    assert cli.main(args) == 0
    capsys.readouterr()
    assert (root / "current").resolve() == new
    assert distribution.read_state(root).get("operation") is None


def test_keyboard_interrupt_rolls_back_with_relevant_guidance(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)

    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(distribution, "prepare_release", interrupt)
    assert cli.main(update_args(home, root, new / "manifest.json")) == 2
    output = capsys.readouterr().out
    assert '"rolled_back"' in output
    assert '"failed_phase": "preparing"' in output
    assert '"retry_command"' in output
    assert "schema" not in output.lower()
    assert (root / "current").resolve() == old
    assert distribution.read_state(root).get("operation") is None


def test_missing_selected_client_does_not_fail_a_runtime_update(tmp_path, monkeypatch, capsys):
    import json

    home = make_home(tmp_path / "athlete")
    root, _, new = managed_program(tmp_path)
    monkeypatch.setattr(clients.shutil, "which", lambda name: None)
    clients.configure(home, root, ["claude-code"])
    monkeypatch.setattr(distribution, "prepare_release", lambda *args: new)

    def native(command, **kwargs):
        result = {"clients": {"claude-code": {"state": "client_missing"}}}
        return subprocess.CompletedProcess(command, 0, json.dumps(result))

    monkeypatch.setattr(subprocess, "run", native)
    assert cli.main(update_args(home, root, new / "manifest.json")) == 0
    assert '"updated"' in capsys.readouterr().out
    assert (root / "current").resolve() == new


@pytest.mark.parametrize(
    "content",
    [
        "invalid private receipt contents",
        '{"schema_version": 1, "release": {"private": "private receipt contents"}}',
        '{"schema_version": 1, "source_commit": {"private": "private receipt contents"}}',
    ],
)
def test_doctor_reports_a_corrupt_program_receipt_without_hiding_diagnosis(
    tmp_path, capsys, content
):
    import json

    home = make_home(tmp_path / "athlete")
    root = tmp_path / "program"
    root.mkdir()
    (root / "install-state.json").write_text(content)
    assert cli.main(["--home", str(home), "doctor", "--runtime-root", str(root)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["runtime"]["state"] == "receipt_unreadable"
    assert result["checks"]["database"]["state"] == "ready"
    assert "private receipt contents" not in json.dumps(result)


def test_live_auth_with_no_saved_tokens_never_starts_a_login_process(tmp_path, monkeypatch, capsys):
    import json

    home = make_home(tmp_path / "athlete")

    def forbidden(*args, **kwargs):
        raise AssertionError("Missing saved tokens are a local state, not a network probe")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert (
        cli.main(
            [
                "--home",
                str(home),
                "doctor",
                "--live-auth",
                "--runtime-root",
                str(tmp_path / "program"),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["login"]["state"] == "missing"


@pytest.mark.parametrize(
    "change",
    [
        "DROP INDEX ix_activities_date",
        "DELETE FROM coach_thresholds WHERE key='acwr_risk_high'",
    ],
)
def test_doctor_refuses_missing_required_indexes_and_policy_rows(tmp_path, capsys, change):
    import json
    import sqlite3

    home = make_home(tmp_path / "athlete")
    with sqlite3.connect(home / "data/garmin.db") as conn:
        conn.execute(change)
    assert (
        cli.main(["--home", str(home), "doctor", "--runtime-root", str(tmp_path / "program")]) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["database"]["state"] == "incompatible"


def test_removal_stops_the_owned_job_even_when_its_plist_is_missing(tmp_path, monkeypatch, capsys):
    import hashlib

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    label = "com.garmincoach.daily." + hashlib.sha256(str(home).encode()).hexdigest()[:12]
    state = distribution.read_state(root)
    state["scheduler"] = {
        "label": label,
        "status": "enabled",
        "plist_path": str(tmp_path / (label + ".plist")),
        "sha256": "a" * 64,
    }
    distribution.write_state(root, state)
    calls = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kw: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    assert (
        cli.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 0
    )
    capsys.readouterr()
    assert any(command[1] == "bootout" and command[-1].endswith("/" + label) for command in calls)
    assert not old.exists()


def test_removal_preview_lists_owned_resources_and_retained_external_storage(tmp_path, capsys):
    import json

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    external = tmp_path / "external.sqlite"
    (home / "data/garmin.db").rename(external)
    with (home / ".env").open("a") as settings:
        settings.write(f"DB_PATH={external}\n")
    assert cli.main(["--home", str(home), "program", "remove", "--runtime-root", str(root)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert str(old) in result["resources"]["releases"]
    assert str(external) in result["retained_paths"]
    assert str(home / "backups") in result["retained_paths"]
    assert old.exists() and external.exists()


@pytest.mark.parametrize("edited_launcher", [False, True])
def test_terminated_activation_can_retry_without_overwriting_an_edited_launcher(
    tmp_path, monkeypatch, capsys, edited_launcher
):
    import json

    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)
    monkeypatch.setattr(distribution, "prepare_release", lambda *args: new)
    terminated = False

    def native(command, **kwargs):
        nonlocal terminated
        if str(root / "current") in command[0] and "read_database" in " ".join(command):
            if not terminated:
                terminated = True
                raise SystemExit("Terminate after activating candidate")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", native)
    args = update_args(home, root, new / "manifest.json")
    with pytest.raises(SystemExit):
        cli.main(args)
    assert (root / "current").resolve() == new
    journal = distribution.read_state(root)["operation"]
    assert journal["previous"] == str(old) and journal["candidate"] == str(new)
    assert journal["home"] == str(home)
    launcher = root / "bin/garmin-coach"
    if edited_launcher:
        launcher.write_text("preserve manual launcher replacement")

    assert cli.main(args) == (2 if edited_launcher else 0)
    output = capsys.readouterr()
    if edited_launcher:
        assert launcher.read_text() == "preserve manual launcher replacement"
        assert distribution.read_state(root)["operation"]["phase"] == "recovery_required"
    else:
        assert json.loads(output.out)["state"] == "updated"
        assert distribution.read_state(root).get("operation") is None


def test_local_schema_check_preserves_personal_policy_overrides(tmp_path, capsys):
    import json
    import sqlite3

    home = make_home(tmp_path / "athlete")
    with sqlite3.connect(home / "data/garmin.db") as connection:
        connection.execute("UPDATE coach_thresholds SET value=1.9 WHERE key='acwr_risk_high'")
    assert (
        cli.main(["--home", str(home), "doctor", "--runtime-root", str(tmp_path / "program")]) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["database"]["state"] == "ready"
    with sqlite3.connect(home / "data/garmin.db") as connection:
        assert (
            connection.execute(
                "SELECT value FROM coach_thresholds WHERE key='acwr_risk_high'"
            ).fetchone()[0]
            == 1.9
        )


def test_invalid_initial_weekly_template_is_an_actionable_cli_error_before_login(
    tmp_path, monkeypatch, capsys
):
    from garmin_coach.etl import client

    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("DATA_START_DATE=2026-10-01\nWEEKLY_TEMPLATE=invalid\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid local setup must be refused before login")

    monkeypatch.setattr(client, "login", forbidden)
    assert cli.main(["--home", str(home), "backfill", "--from", "2026-10-01"]) == 2
    assert "WEEKLY_TEMPLATE" in capsys.readouterr().err
    assert not (home / "data/garmin.db").exists()


def test_doctor_warns_about_a_legacy_daily_job_without_adopting_it(tmp_path, monkeypatch, capsys):
    import json

    user_home = tmp_path / "isolated-user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    home = make_home(tmp_path / "athlete")
    legacy = user_home / "Library/LaunchAgents/com.garmincoach.daily.plist"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy job: preserve exactly")
    assert (
        cli.main(["--home", str(home), "doctor", "--runtime-root", str(tmp_path / "program")]) == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["scheduling"]["state"] == "legacy_conflict"
    assert legacy.read_text() == "legacy job: preserve exactly"


@pytest.mark.parametrize("phase", ["preparing", "activating"])
def test_legacy_update_journal_restores_clients_detached_before_the_phase_checkpoint(
    tmp_path, monkeypatch, capsys, phase
):
    import json
    from tests.test_lifecycle import NativeClients

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    native = NativeClients()
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(subprocess, "run", native)
    clients.configure(home, root, ["claude-code"])
    state = distribution.read_state(root)
    state.update(selected_home=str(home), selected_clients=["claude-code"])
    distribution.write_state(root, state)
    assert cli.main(["--home", str(home), "program", "backup", "--runtime-root", str(root)]) == 0
    backup = json.loads(capsys.readouterr().out)["backup"]
    clients.remove(home, root, confirm=True)
    distribution.write_state(
        root,
        dict(
            state,
            operation={
                "kind": "update",
                "phase": phase,
                "backup": backup,
            },
        ),
    )
    assert clients.status(home, root)["selected_clients"] == []
    assert native.plugins["claude"] == []

    assert cli.main(update_args(home, root, old / "manifest.json")) == 0
    capsys.readouterr()
    assert clients.status(home, root)["selected_clients"] == ["claude-code"]
    assert native.plugins["claude"]
    assert not distribution.read_state(root).get("operation")


def test_unknown_update_intent_version_is_preserved_before_any_native_operation(
    tmp_path, monkeypatch, capsys
):
    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    state = distribution.read_state(root)
    state["operation"] = {"schema_version": 999, "kind": "update", "phase": "preparing"}
    distribution.write_state(root, state)
    before = (root / "install-state.json").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Unknown intent must be refused before native operations")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert cli.main(update_args(home, root, old / "manifest.json")) == 2
    assert "Unknown interrupted operation" in capsys.readouterr().err
    assert (root / "install-state.json").read_bytes() == before
    assert (root / "current").resolve() == old
