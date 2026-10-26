"""Lifecycle commands use only temporary athlete/program state and fake transport."""

from __future__ import annotations

import json
import hashlib
import fcntl
import pathlib
import plistlib
import sqlite3
import subprocess
import sys
import shlex

import pytest

from garmin_coach.core import db


def runtime_success(command):
    """Model a successful runtime's documented database-evidence response."""
    output = ""
    if command[1:3] == ["-I", "-c"] and len(command) == 5:
        request = json.loads(command[4])
        if request.get("command") == "evidence":
            output = json.dumps(
                {
                    "schema": "compatible",
                    "data_through": "2026-10-16",
                    "snapshot_computed_at": "2026-10-16",
                }
            )
    return subprocess.CompletedProcess(command, 0, output)


class NativeClients:
    """Fake only documented client inventory and mutation commands."""

    def __init__(self):
        self.plugins = {"codex": [], "claude": []}
        self.marketplaces = {"codex": [], "claude": []}

    def __call__(self, command, **kwargs):
        host = pathlib.Path(command[0]).name
        args = command[1:]
        if host not in self.plugins:
            return runtime_success(command)
        if args[:3] == ["plugin", "marketplace", "list"]:
            value = self.marketplaces[host]
            return subprocess.CompletedProcess(
                command, 0, json.dumps(value if host == "claude" else {"marketplaces": value})
            )
        if args[:2] == ["plugin", "list"]:
            value = self.plugins[host]
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(value if host == "claude" else {"installed": value, "available": []}),
            )
        if args[:3] == ["plugin", "marketplace", "add"]:
            root = pathlib.Path(args[3])
            name = "garmin-coach-" + root.name
            self.marketplaces[host] = [
                {"name": name, "source": "directory", "path": str(root), "root": str(root)}
            ]
        elif args[:2] in (["plugin", "add"], ["plugin", "install"], ["plugin", "update"]):
            self.plugins[host] = [
                {"id": args[2], "pluginId": args[2], "scope": "user", "enabled": True}
            ]
        elif args[:2] in (["plugin", "remove"], ["plugin", "uninstall"]):
            self.plugins[host] = []
        elif args[:3] == ["plugin", "marketplace", "remove"]:
            self.marketplaces[host] = []
        else:
            raise AssertionError(f"Unexpected client command: {command}")
        return subprocess.CompletedProcess(command, 0, "{}")


@pytest.fixture
def native_clients():
    return NativeClients()


def make_home(path: pathlib.Path) -> pathlib.Path:
    path.mkdir()
    (path / ".env").write_text("GARMIN_EMAIL=private@example.test\nGARMIN_PASSWORD=secret-value\n")
    (path / "data").mkdir()
    conn = db.connect(str(path / "data" / "garmin.db"))
    db.bootstrap(conn)
    conn.execute("INSERT INTO daily_metrics(date) VALUES ('2026-10-16')")
    conn.execute("INSERT INTO athlete_status(id, computed_at) VALUES (1, '2026-10-16')")
    conn.commit()
    conn.close()
    return path


def test_local_doctor_reads_without_transport_or_writing(tmp_path, monkeypatch, capsys):
    from garmin_coach import lifecycle
    from garmin_coach.etl import client

    def forbidden(*args, **kwargs):
        raise AssertionError("Local diagnosis must never log in")

    monkeypatch.setattr(client, "login_api", forbidden)
    home = make_home(tmp_path / "athlete")
    database = home / "data" / "garmin.db"
    before = database.read_bytes()
    runtime = tmp_path / "unmanaged-program"

    assert lifecycle.main(["--home", str(home), "doctor", "--runtime-root", str(runtime)]) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["installation"] == str(home)
    assert output["checks"]["database"]["state"] == "ready"
    assert output["checks"]["runtime"]["state"] == "unmanaged"
    assert output["checks"]["login"]["state"] == "missing"
    assert output["client_acceptance"] == "not_run"
    assert "private@example.test" not in json.dumps(output)
    assert "secret-value" not in json.dumps(output)
    assert database.read_bytes() == before
    assert not runtime.exists()


def test_doctor_never_bootstraps_missing_or_old_database(tmp_path, capsys):
    from garmin_coach import lifecycle

    home = make_home(tmp_path / "athlete")
    database = home / "data" / "garmin.db"
    database.unlink()
    runtime = tmp_path / "program"
    args = ["--home", str(home), "doctor", "--runtime-root", str(runtime)]

    assert lifecycle.main(args) == 1
    assert json.loads(capsys.readouterr().out)["checks"]["database"]["state"] == "missing"
    assert not database.exists()
    database.write_bytes(b"not a compatible SQLite database")
    before = database.read_bytes()
    assert lifecycle.main(args) == 1
    assert json.loads(capsys.readouterr().out)["checks"]["database"]["state"] == "incompatible"
    assert database.read_bytes() == before


def test_explicit_live_auth_hides_transport_output_and_does_not_sync(tmp_path, monkeypatch, capsys):
    from garmin_coach import lifecycle

    home = make_home(tmp_path / "athlete")
    (home / ".garminconnect").mkdir()
    (home / ".garminconnect/tokens.json").write_text("fake saved tokens")
    calls = []

    def login_probe(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 1, "secret-value", "private@example.test")

    monkeypatch.setattr(subprocess, "run", login_probe)
    assert (
        lifecycle.main(
            [
                "--home",
                str(home),
                "doctor",
                "--runtime-root",
                str(tmp_path / "runtime"),
                "--live-auth",
            ]
        )
        == 1
    )

    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["checks"]["login"]["state"] == "unverified"
    assert "renew" in result["checks"]["login"]["recovery"].lower()
    assert "secret-value" not in output.out + output.err
    assert "private@example.test" not in output.out + output.err
    assert len(calls) == 1
    assert calls[0][1]["stdin"] == subprocess.DEVNULL
    assert "login_api" in calls[0][0][-2]
    assert "sync(" not in calls[0][0][-2]


@pytest.mark.parametrize("edit_configuration", [False, True])
def test_doctor_probes_captured_configuration_without_importing_a_shadow_package(
    tmp_path, monkeypatch, capsys, edit_configuration
):
    from garmin_coach import cli, distribution
    from garmin_coach.runtime import InstallationContext

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    python = old / "venv/bin/python"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    inventory_path = old / "inventory.json"
    inventory = json.loads(inventory_path.read_text())
    inventory["entries"]["venv/bin/python"]["sha256"] = hashlib.sha256(
        python.read_bytes()
    ).hexdigest()
    inventory_path.write_text(json.dumps(inventory))
    digest = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    ready = json.loads((old / "ready.json").read_text())
    ready["inventory_sha256"] = digest
    (old / "ready.json").write_text(json.dumps(ready))
    state = distribution.read_state(root)
    state["managed_release_inventories"][str(old)] = digest
    distribution.write_state(root, state)
    start = tmp_path / "unrelated"
    package = start / "garmin_coach"
    package.mkdir(parents=True)
    marker = tmp_path / "shadow-executed"
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('shadow imported')\n"
    )
    monkeypatch.chdir(start)
    monkeypatch.setenv("PYTHONPATH", str(start))
    before = (home / "data/garmin.db").read_bytes()

    def select(selected_home):
        context = InstallationContext.select(selected_home)
        if edit_configuration:
            (home / ".env").write_text("DB_PATH=./missing.sqlite\n")
        return context

    assert (
        cli.main(
            ["--home", str(home), "doctor", "--runtime-root", str(root)],
            context_factory=select,
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["runtime"]["state"] == "ready"
    assert result["checks"]["database"]["state"] == "ready"
    assert result["checks"]["protocol"]["state"] == "ready"
    assert (home / "data/garmin.db").read_bytes() == before
    assert not (home / "missing.sqlite").exists()
    assert not marker.exists()


def test_backup_captures_external_wal_database_and_private_settings(tmp_path, capsys):
    from garmin_coach import lifecycle

    home = make_home(tmp_path / "athlete")
    external = tmp_path / "external.db"
    (home / "data" / "garmin.db").rename(external)
    with (home / ".env").open("a") as config:
        config.write(f"DB_PATH={external}\n")
    conn = sqlite3.connect(external)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO niggle(date, body_part, severity) VALUES ('2026-10-16', 'calf', 2)")
    conn.commit()

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "backup", "--runtime-root", str(tmp_path / "runtime")]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    backup = pathlib.Path(result["backup"])
    assert backup.is_relative_to(home)
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / "settings.env").stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup / "database.sqlite") as saved:
        assert saved.execute("SELECT body_part, severity FROM niggle").fetchall() == [("calf", 2)]
    conn.close()
    assert "secret-value" in (backup / "settings.env").read_text()
    assert not (tmp_path / "runtime").exists()


def test_backup_accepts_older_schema_without_migrating_source(tmp_path, capsys):
    from garmin_coach import lifecycle

    home = make_home(tmp_path / "athlete")
    source = home / "data" / "garmin.db"
    source.unlink()
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE legacy_record(value TEXT)")
        conn.execute("INSERT INTO legacy_record VALUES ('preserve old training')")
    before = source.read_bytes()

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "backup", "--runtime-root", str(tmp_path / "runtime")]
        )
        == 0
    )

    snapshot = pathlib.Path(json.loads(capsys.readouterr().out)["backup"]) / "database.sqlite"
    with sqlite3.connect(snapshot) as conn:
        assert (
            conn.execute("SELECT value FROM legacy_record").fetchone()[0] == "preserve old training"
        )
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name='daily_metrics'").fetchone()
            is None
        )
    assert source.read_bytes() == before


def managed_program(tmp_path):
    from garmin_coach import distribution

    root = tmp_path / "program"
    old = root / "releases" / "0.1.0-arm64"
    new = root / "releases" / "0.1.1-arm64"
    for release, version in ((old, "0.1.0"), (new, "0.1.1")):
        (release / "venv" / "bin").mkdir(parents=True)
        python = release / "venv" / "bin" / "python"
        python.write_text("fixture executable")
        python.chmod(0o755)
        metadata = {
            "schema_version": 1,
            "platform": "darwin",
            "architecture": "arm64",
            "version": version,
            "source_commit": "a" * 40,
            "min_macos": "15.0",
        }
        (release / "manifest.json").write_text(json.dumps(metadata))
        manifest_hash = hashlib.sha256((release / "manifest.json").read_bytes()).hexdigest()
        inventory = {
            "schema_version": 1,
            "entries": {
                "manifest.json": {"kind": "file", "sha256": manifest_hash},
                "venv": {"kind": "directory"},
                "venv/bin": {"kind": "directory"},
                "venv/bin/python": {
                    "kind": "file",
                    "sha256": hashlib.sha256(b"fixture executable").hexdigest(),
                },
            },
        }
        (release / "inventory.json").write_text(json.dumps(inventory))
        (release / "ready.json").write_text(
            json.dumps(
                {
                    "version": version,
                    "managed_by": "garmin-coach",
                    "manifest_sha256": manifest_hash,
                    "inventory_sha256": hashlib.sha256(
                        (release / "inventory.json").read_bytes()
                    ).hexdigest(),
                }
            )
        )
    (root / "current").symlink_to(old.relative_to(root))
    distribution.write_state(
        root,
        {
            "managed_by": "garmin-coach",
            "release": "0.1.0",
            "source_commit": "a" * 40,
            "release_dir": str(old),
            "managed_releases": {
                str(old): hashlib.sha256((old / "manifest.json").read_bytes()).hexdigest()
            },
            "managed_release_inventories": {
                str(old): hashlib.sha256((old / "inventory.json").read_bytes()).hexdigest()
            },
        },
    )
    return root, old, new


@pytest.mark.parametrize("failure", [None, "schema", "activation"])
def test_update_preserves_records_and_restores_prior_release_on_failure(
    tmp_path, monkeypatch, capsys, failure
):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    (home / "reports").mkdir()
    (home / "reports" / "push.json").write_text("durable push receipt")
    root, old, new = managed_program(tmp_path)
    monkeypatch.setattr(distribution, "prepare_release", lambda manifest, location: new)

    def external(command, **kwargs):
        probe = "read_database" in " ".join(command)
        current = str(root / "current") in command[0]
        if probe and (failure == "schema" or (failure == "activation" and current)):
            raise subprocess.CalledProcessError(1, command, stderr="secret-value")
        return runtime_success(command)

    monkeypatch.setattr(subprocess, "run", external)
    before = (home / "data" / "garmin.db").read_bytes()
    code = lifecycle.main(
        [
            "--home",
            str(home),
            "program",
            "update",
            "--runtime-root",
            str(root),
            "--manifest",
            str(new / "manifest.json"),
        ]
    )
    output = capsys.readouterr()
    assert code == (2 if failure else 0)
    assert (root / "current").resolve() == (old if failure else new)
    assert (home / "data" / "garmin.db").read_bytes() == before
    assert (home / "reports" / "push.json").read_text() == "durable push receipt"
    assert list((home / "backups" / "program").glob("*/database.sqlite"))
    assert "secret-value" not in output.out + output.err
    state = distribution.read_state(root)
    assert state["release"] == ("0.1.0" if failure else "0.1.1")
    assert state.get("operation") is None


def test_program_removal_previews_then_removes_only_owned_resources(tmp_path, capsys):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)
    unrelated = root / "unrelated.txt"
    unrelated.write_text("keep this")
    state = distribution.read_state(root)
    (root / "bin").mkdir()
    launcher = root / "bin" / "garmin-coach"
    launcher.write_text("owned launcher")
    state["owned_launchers"] = {str(launcher): hashlib.sha256(launcher.read_bytes()).hexdigest()}
    distribution.write_state(root, state)
    args = ["--home", str(home), "program", "remove", "--runtime-root", str(root)]

    assert lifecycle.main(args) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "preview"
    assert old.exists() and launcher.exists()
    assert lifecycle.main(args + ["--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["retained_installation"] == str(home)
    assert not old.exists() and not launcher.exists()
    assert (home / "data" / "garmin.db").exists()
    assert unrelated.read_text() == "keep this"
    assert new.exists()  # Not in the ownership receipt.
    assert lifecycle.main(args + ["--confirm"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "removed"


@pytest.mark.parametrize("setting", ["DB_PATH", "PLANS_DIR", "GARMINTOKENS", "LOG_PATH"])
def test_removal_refuses_external_personal_paths_inside_a_release_before_native_changes(
    tmp_path, monkeypatch, capsys, setting, native_clients
):
    from garmin_coach import clients, distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    calls = []
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or native_clients(command, **kwargs),
    )
    clients.configure(home, root, ["codex", "claude-code"])
    calls.clear()
    durable = old / "personal-records"
    durable.mkdir()
    alias = tmp_path / "external-records"
    alias.symlink_to(durable, target_is_directory=True)
    if setting == "DB_PATH":
        (home / "data" / "garmin.db").rename(durable / "records.sqlite")
        selected = alias / "records.sqlite"
    elif setting == "LOG_PATH":
        (durable / "daily.log").write_text("preserve athlete log")
        selected = alias / "daily.log"
    else:
        (durable / "record.txt").write_text("preserve athlete record")
        selected = alias
    with (home / ".env").open("a") as config:
        config.write(f"{setting}={selected}\n")
    state = distribution.read_state(root)
    before = json.loads((clients.integration_root(home, root) / "registration.json").read_text())

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 2
    )

    capsys.readouterr()
    assert old.exists() and selected.exists()
    assert calls == []
    assert distribution.read_state(root) == state
    assert (
        json.loads((clients.integration_root(home, root) / "registration.json").read_text())
        == before
    )
    if setting == "DB_PATH":
        with sqlite3.connect(selected) as conn:
            assert conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0] == "2026-10-16"


def test_removal_refuses_modified_owned_launcher_before_deleting_release(tmp_path, capsys):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    (root / "bin").mkdir()
    launcher = root / "bin" / "garmin-coach"
    launcher.write_text("user replacement")
    state = distribution.read_state(root)
    state["owned_launchers"] = {str(launcher): "0" * 64}
    distribution.write_state(root, state)

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 2
    )
    capsys.readouterr()
    assert launcher.read_text() == "user replacement"
    assert old.exists()


@pytest.mark.parametrize("change", ["added_note", "edited_program"])
def test_removal_preserves_unknown_or_edited_release_contents_before_native_changes(
    tmp_path, monkeypatch, capsys, change, native_clients
):
    from garmin_coach import clients, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    calls = []
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or native_clients(command, **kwargs),
    )
    clients.configure(home, root, ["codex", "claude-code"])
    calls.clear()
    path = old / "athlete-note.txt" if change == "added_note" else old / "venv" / "bin" / "python"
    path.write_text("keep this user change")
    before = (clients.integration_root(home, root) / "registration.json").read_bytes()

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 2
    )

    capsys.readouterr()
    assert path.read_text() == "keep this user change"
    assert calls == []
    assert (clients.integration_root(home, root) / "registration.json").read_bytes() == before


@pytest.mark.parametrize("folder", ["backups", "reports", "memory"])
def test_removal_resolves_fixed_personal_directories_before_deleting_a_release(
    tmp_path, capsys, folder
):
    from garmin_coach import lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    personal = home / folder
    personal.symlink_to(old / "venv", target_is_directory=True)

    assert (
        lifecycle.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 2
    )

    capsys.readouterr()
    assert personal.is_dir()
    assert old.exists()


def test_one_client_removal_retains_other_host_and_program(
    tmp_path, monkeypatch, capsys, native_clients
):
    from garmin_coach import clients, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    calls = []
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)

    def native(command, **kwargs):
        calls.append(command)
        return native_clients(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", native)
    clients.configure(home, root, ["work", "codex", "claude-code"])
    calls.clear()

    assert (
        lifecycle.main(
            [
                "--home",
                str(home),
                "program",
                "remove",
                "--runtime-root",
                str(root),
                "--client",
                "codex",
                "--confirm",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert clients.status(home, root)["selected_clients"] == ["work", "claude-code"]
    assert calls == []
    assert (
        lifecycle.main(
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
    assert clients.status(home, root)["selected_clients"] == ["work"]
    assert all(command[0].endswith("claude") for command in calls)
    assert old.exists()


def test_daily_job_failure_preserves_program_and_unrelated_jobs(tmp_path, monkeypatch, capsys):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    job = agents / "owned.plist"
    job.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.garmincoach.fixture",
                "ProgramArguments": [
                    str(root / "bin" / "garmin-coach"),
                    "--home",
                    str(home),
                    "daily",
                ],
            }
        )
    )
    unrelated = agents / "other.plist"
    unrelated.write_text("unrelated scheduler")
    state = distribution.read_state(root)
    state["scheduler"] = {
        "label": "com.garmincoach.fixture",
        "path": str(job),
        "sha256": hashlib.sha256(job.read_bytes()).hexdigest(),
    }
    distribution.write_state(root, state)
    calls = []

    def fail(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 5)

    monkeypatch.setattr(subprocess, "run", fail)
    assert (
        lifecycle.main(
            ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
        )
        == 2
    )
    capsys.readouterr()
    assert len(calls) == 1
    assert calls[0][-1].endswith("/com.garmincoach.fixture")
    assert job.exists() and old.exists()
    assert unrelated.read_text() == "unrelated scheduler"


def test_doctor_reports_versions_integrations_and_interrupted_operation(
    tmp_path, monkeypatch, capsys
):
    from garmin_coach import clients, distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, _, _ = managed_program(tmp_path)
    monkeypatch.setattr(clients.shutil, "which", lambda name: None)
    clients.configure(home, root, ["codex"])
    state = distribution.read_state(root)
    state["operation"] = {"kind": "update", "phase": "activating", "backup": str(home / "backups")}
    distribution.write_state(root, state)

    assert lifecycle.main(["--home", str(home), "doctor", "--runtime-root", str(root)]) == 1

    result = json.loads(capsys.readouterr().out)
    assert result["versions"]["release"] == "0.1.0"
    assert result["checks"]["clients"]["state"] == "client_missing"
    assert result["checks"]["skill"]["state"] == "ready"
    assert result["checks"]["scheduling"]["state"] == "not_scheduled"
    assert result["checks"]["operation"]["state"] == "interrupted"


def test_doctor_preserves_each_clients_interrupted_registration_state(
    tmp_path, monkeypatch, capsys, native_clients
):
    from garmin_coach import clients, lifecycle

    home = make_home(tmp_path / "athlete")
    root, _, _ = managed_program(tmp_path)
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(
        subprocess, "run", lambda command, **kwargs: native_clients(command, **kwargs)
    )
    clients.configure(home, root, ["work", "codex", "claude-code"])
    receipt = clients.integration_root(home, root) / "registration.json"
    record = json.loads(receipt.read_text())
    record["registrations"]["openai"]["state"] = "preparing"
    receipt.write_text(json.dumps(record))

    assert lifecycle.main(["--home", str(home), "doctor", "--runtime-root", str(root)]) == 1

    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["clients"]["state"] == "preparing"
    assert result["clients"]["work"]["state"] == "preparing"
    assert result["clients"]["codex"]["state"] == "preparing"
    assert result["clients"]["claude-code"]["state"] == "needs_activation"
    assert "resume" in result["clients"]["codex"]["actions"][0]
    assert "Restart" in result["clients"]["claude-code"]["actions"][0]


@pytest.mark.parametrize("rollback_failure", [False, True])
def test_client_update_failure_restores_prior_runtime_and_reports_recovery_state(
    tmp_path, monkeypatch, capsys, rollback_failure, native_clients
):
    from garmin_coach import clients, distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)
    calls = []
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(distribution, "prepare_release", lambda manifest, location: new)
    configure_attempts = []
    refresh_attempts = []
    phase = "initial"
    import importlib.metadata

    package_version = importlib.metadata.version

    def native(command, **kwargs):
        nonlocal phase
        calls.append(command)
        if "garmin_coach.clients" in command:
            configure_attempts.append(command[0])
            phase = "candidate"
            with monkeypatch.context() as candidate:
                candidate.setattr(
                    importlib.metadata,
                    "version",
                    lambda name: (
                        json.loads((new / "manifest.json").read_text())["version"]
                        if name == "garmin-coach"
                        else package_version(name)
                    ),
                )
                result = clients.configure(home, root, ["codex", "claude-code"])
            phase = "rollback"
            return subprocess.CompletedProcess(command, 0, json.dumps(result))
        if pathlib.Path(command[0]).name == "claude" and command[1:3] == ["plugin", "update"]:
            refresh_attempts.append(phase)
            if phase == "candidate" or rollback_failure:
                return subprocess.CompletedProcess(command, 1, "secret-value", "private output")
        return native_clients(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", native)
    with monkeypatch.context() as previous:
        previous.setattr(
            importlib.metadata,
            "version",
            lambda name: (
                json.loads((old / "manifest.json").read_text())["version"]
                if name == "garmin-coach"
                else package_version(name)
            ),
        )
        clients.configure(home, root, ["codex", "claude-code"])

    assert (
        lifecycle.main(
            [
                "--home",
                str(home),
                "program",
                "update",
                "--runtime-root",
                str(root),
                "--manifest",
                str(new / "manifest.json"),
            ]
        )
        == 2
    )

    output = capsys.readouterr()
    assert "secret-value" not in output.out + output.err
    result = json.loads(output.out)
    assert result["state"] == ("recovery_required" if rollback_failure else "rolled_back")
    assert (root / "current").resolve() == old
    assert len(configure_attempts) == 1
    assert refresh_attempts == ["candidate", "rollback"]
    if rollback_failure:
        assert distribution.read_state(root)["operation"]["phase"] == "recovery_required"
    else:
        receipt = clients.status(home, root)
        assert receipt["selected_clients"] == ["codex", "claude-code"]
        assert all(value["state"] == "needs_activation" for value in receipt["clients"].values())
        assert not distribution.read_state(root).get("operation")


@pytest.mark.parametrize("incomplete", ["client_missing", "preparing"])
def test_missing_client_is_accepted_but_preparing_rolls_back_after_cli_success(
    tmp_path, monkeypatch, capsys, incomplete, native_clients
):
    from garmin_coach import clients, distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, new = managed_program(tmp_path)
    monkeypatch.setattr(clients.shutil, "which", lambda name: "/fixture/bin/" + name)
    monkeypatch.setattr(distribution, "prepare_release", lambda manifest, location: new)
    attempts = []

    def native(command, **kwargs):
        if "garmin_coach.clients" in command:
            attempts.append(command)
            if len(attempts) == 1:
                result = {
                    "clients": {
                        "codex": {"state": incomplete},
                        "claude-code": {"state": "needs_activation"},
                    }
                }
            else:
                result = clients.configure(home, root, ["codex", "claude-code"])
            return subprocess.CompletedProcess(command, 0, json.dumps(result))
        return native_clients(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", native)
    clients.configure(home, root, ["codex", "claude-code"])
    assert lifecycle.main(
        [
            "--home",
            str(home),
            "program",
            "update",
            "--runtime-root",
            str(root),
            "--manifest",
            str(new / "manifest.json"),
        ]
    ) == (0 if incomplete == "client_missing" else 2)

    result = json.loads(capsys.readouterr().out)
    assert result["state"] == ("updated" if incomplete == "client_missing" else "rolled_back")
    assert (root / "current").resolve() == (new if incomplete == "client_missing" else old)
    assert clients.status(home, root)["selected_clients"] == ["codex", "claude-code"]


def test_repeating_active_release_is_verified_without_rewriting_athlete_state(
    tmp_path, monkeypatch, capsys
):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    verified = []
    monkeypatch.setattr(
        distribution, "prepare_release", lambda manifest, location: verified.append(manifest) or old
    )
    state = (root / "install-state.json").read_bytes()

    assert (
        lifecycle.main(
            [
                "--home",
                str(home),
                "program",
                "update",
                "--runtime-root",
                str(root),
                "--manifest",
                str(old / "manifest.json"),
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out)["state"] == "unchanged"
    assert verified == []
    assert (root / "install-state.json").read_bytes() == state
    assert not (home / "backups").exists()


def test_candidate_probe_rejects_a_null_snapshot_before_activation(tmp_path, monkeypatch, capsys):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    with sqlite3.connect(home / "data" / "garmin.db") as conn:
        conn.execute("UPDATE athlete_status SET computed_at=NULL WHERE id=1")
    root, old, new = managed_program(tmp_path)
    python = new / "venv" / "bin" / "python"
    python.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n')
    monkeypatch.setattr(distribution, "prepare_release", lambda manifest, location: new)
    real_run = subprocess.run

    def runtime(command, **kwargs):
        if "read_database" in " ".join(command):
            actual = list(command)
            source = pathlib.Path(__file__).resolve().parents[1] / "src"
            actual[3] = f"import sys; sys.path.insert(0, {str(source)!r}); " + actual[3]
            return real_run(actual, **kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", runtime)
    assert (
        lifecycle.main(
            [
                "--home",
                str(home),
                "program",
                "update",
                "--runtime-root",
                str(root),
                "--manifest",
                str(new / "manifest.json"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["state"] == "rolled_back"
    assert (root / "current").resolve() == old


def test_missing_enabled_scheduler_or_concurrent_lifecycle_operation_refuses_removal(
    tmp_path, capsys
):
    from garmin_coach import distribution, lifecycle

    home = make_home(tmp_path / "athlete")
    root, old, _ = managed_program(tmp_path)
    args = ["--home", str(home), "program", "remove", "--runtime-root", str(root), "--confirm"]
    with (root / ".lifecycle.lock").open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert lifecycle.main(args) == 2
    capsys.readouterr()
    assert old.exists()
    state = distribution.read_state(root)
    state["scheduler"] = {
        "status": "enabled",
        "path": str(tmp_path / "gone.plist"),
        "label": "com.garmincoach.fixture",
        "sha256": "0" * 64,
    }
    distribution.write_state(root, state)
    assert lifecycle.main(args) == 2
    capsys.readouterr()
    assert old.exists()


def test_public_cli_diagnoses_and_backs_up_the_selected_installation(tmp_path, capsys):
    from garmin_coach import cli

    home = make_home(tmp_path / "athlete")
    runtime = tmp_path / "unmanaged-runtime"
    arguments = ["--home", str(home)]
    before = (home / "data" / "garmin.db").read_bytes()

    assert cli.main(arguments + ["doctor", "--runtime-root", str(runtime)]) == 1
    observed = json.loads(capsys.readouterr().out)
    assert observed["installation"] == str(home)
    assert observed["checks"]["database"]["state"] == "ready"
    assert not runtime.exists()

    assert cli.main(arguments + ["program", "backup", "--runtime-root", str(runtime)]) == 0
    capsys.readouterr()
    snapshots = list((home / "backups" / "program").glob("*/database.sqlite"))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as connection:
        assert connection.execute("SELECT date FROM daily_metrics").fetchone()[0] == "2026-10-16"
    assert (home / "data" / "garmin.db").read_bytes() == before
    assert not runtime.exists()
