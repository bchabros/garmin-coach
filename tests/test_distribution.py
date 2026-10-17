"""Distribution and scheduler behavior on disposable roots (issue #86)."""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from garmin_coach import distribution


@pytest.fixture(autouse=True)
def supported_host(monkeypatch):
    monkeypatch.setattr(distribution.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(distribution.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(distribution.platform, "mac_ver", lambda: ("15.0", (), ""))


def test_bad_release_download_never_activates_or_changes_the_existing_runtime(tmp_path):
    root = tmp_path / "runtime"
    (root / "releases" / "old").mkdir(parents=True)
    (root / "current").symlink_to("releases/old")
    payload = tmp_path / "python.tar.gz"
    payload.write_bytes(b"corrupted download")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.2.0",
                "source_commit": "a" * 40,
                "platform": "darwin",
                "architecture": "arm64",
                "min_macos": "15.0",
                "python": {"url": payload.as_uri(), "sha256": "0" * 64},
                "bundle": {
                    "url": payload.as_uri(),
                    "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                },
            }
        )
    )
    with pytest.raises(ValueError, match="checksum"):
        distribution.prepare_release(manifest, root)
    assert (root / "current").resolve() == root / "releases" / "old"
    assert not (root / "install-state.json").exists()


def _release_fixture(tmp_path):
    """A tiny executable package keeps installer tests offline without borrowed dependencies."""
    import io
    import sys
    import tarfile
    import zipfile

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "garmin_coach-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in ("", "core/", "mcp/"):
            archive.writestr(f"garmin_coach/{name}__init__.py", "")
        archive.writestr("garmin_coach/cli.py", "print('fixture CLI 0.2.0')\n")
        archive.writestr("garmin_coach/mcp/server.py", "print('fixture MCP 0.2.0')\n")
        archive.writestr("garmin_coach/core/schema.sql", "CREATE TABLE sample (id INTEGER);\n")
        prefix = "garmin_coach-0.2.0.dist-info/"
        archive.writestr(
            prefix + "METADATA", "Metadata-Version: 2.1\nName: garmin-coach\nVersion: 0.2.0\n"
        )
        archive.writestr(
            prefix + "WHEEL",
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(prefix + "RECORD", "")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        f"garmin-coach==0.2.0 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n"
    )
    bundle = tmp_path / "bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(wheels, arcname="wheels")
        archive.add(requirements, arcname="requirements.txt")
    python = tmp_path / "python.tar.gz"
    with tarfile.open(python, "w:gz") as archive:
        executable = f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n".encode()
        entry = tarfile.TarInfo("python/bin/python3")
        entry.mode, entry.size = 0o755, len(executable)
        archive.addfile(entry, io.BytesIO(executable))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "0.2.0",
                "source_commit": "a" * 40,
                "platform": "darwin",
                "architecture": "arm64",
                "min_macos": "15.0",
                "python": {
                    "url": python.as_uri(),
                    "sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
                },
                "bundle": {
                    "url": bundle.as_uri(),
                    "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                },
            }
        )
    )
    return manifest


def test_private_runtime_is_prepared_activated_and_reused_from_an_unrelated_folder(tmp_path):
    import subprocess

    root = tmp_path / "runtime with spaces"
    manifest = _release_fixture(tmp_path)
    release = distribution.prepare_release(manifest, root)
    assert not (root / "current").exists()
    distribution.activate_release(release, root)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [str(root / "bin" / "garmin-coach")], cwd=elsewhere, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fixture CLI 0.2.0"
    assert distribution.read_state(root)["release"] == "0.2.0"
    assert distribution.prepare_release(manifest, root) == release
    assert list(elsewhere.iterdir()) == []


def test_opt_in_schedule_uses_explicit_installation_and_reuses_only_its_owned_job(tmp_path):
    import plistlib

    home, root, agents = tmp_path / "athlete", tmp_path / "runtime", tmp_path / "agents"
    home.mkdir()
    calls = []
    preview = distribution.configure_schedule(
        home, root, "06:30", enabled=False, agents_dir=agents, execute=calls.append
    )
    assert preview["status"] == "disabled"
    assert not agents.exists()
    result = distribution.configure_schedule(
        home, root, "06:30", enabled=True, agents_dir=agents, execute=calls.append
    )
    job = pathlib.Path(result["path"])
    plist = plistlib.loads(job.read_bytes())
    assert plist["ProgramArguments"] == [
        str(root / "bin" / "garmin-coach"),
        "--home",
        str(home),
        "daily",
    ]
    assert plist["StartCalendarInterval"] == {"Hour": 6, "Minute": 30}
    assert "RunAtLoad" not in plist
    assert "GARMIN_PASSWORD" not in job.read_text()
    assert result["status"] == "enabled"
    distribution.configure_schedule(
        home, root, "06:30", enabled=True, agents_dir=agents, execute=calls.append
    )
    assert len(calls) == 1
    job.write_text("unrelated or edited job")
    with pytest.raises(ValueError, match="changed|owned"):
        distribution.configure_schedule(
            home, root, "06:30", enabled=True, agents_dir=agents, execute=calls.append
        )
    assert job.read_text() == "unrelated or edited job"


def test_setup_keeps_incomplete_onboarding_resumable_and_does_not_claim_client_acceptance(tmp_path):
    from garmin_coach.core import db

    home, root = tmp_path / "athlete", tmp_path / "runtime"
    home.mkdir()
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    (home / "data").mkdir()
    conn = db.connect(str(home / "data" / "garmin.db"))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-03"})
    conn.commit()
    conn.close()
    calls = []

    def command(arguments):
        calls.append(arguments)
        outcome = home / "reports" / "2026-07-04" / "setup-fixture"
        outcome.mkdir(parents=True)
        (outcome / "status.json").write_text(
            json.dumps(
                {
                    "onboarding": {
                        "import_complete": False,
                        "read_ready": False,
                        "failure_kind": "rate_limit",
                        "completed_through": "2026-07-03",
                    }
                }
            )
        )
        return 2

    result = distribution.finish_setup(home, root, clients="codex", execute=command)
    assert result["onboarding"]["status"] == "partial"
    assert result["coach_read"] == "ready"
    assert result["clients"]["status"] == "not_run"
    assert result["client_acceptance"] == "not_run"
    assert distribution.read_state(root)["selected_home"] == str(home)
    assert len(calls) == 1


def test_interrupted_scheduler_registration_can_resume_without_rewriting_the_job(tmp_path):
    import subprocess

    home, root, agents = tmp_path / "athlete", tmp_path / "runtime", tmp_path / "agents"
    home.mkdir()

    def failed(arguments):
        raise subprocess.CalledProcessError(1, arguments)

    with pytest.raises(subprocess.CalledProcessError):
        distribution.configure_schedule(
            home, root, "06:00", enabled=True, agents_dir=agents, execute=failed
        )
    result = distribution.configure_schedule(
        home, root, "06:00", enabled=True, agents_dir=agents, execute=lambda args: None
    )
    assert result["status"] == "enabled"
    assert len(list(agents.glob("*.plist"))) == 1


def test_repeating_versioned_install_repairs_missing_private_runtime(tmp_path):
    import shutil

    manifest = _release_fixture(tmp_path)
    root = tmp_path / "runtime"
    release = distribution.prepare_release(manifest, root)
    distribution.activate_release(release, root)
    shutil.rmtree(release / "venv")
    repaired = distribution.prepare_release(manifest, root)
    distribution.activate_release(repaired, root)
    assert (root / "current" / "venv" / "bin" / "python").exists()
    assert distribution.read_state(root)["release"] == "0.2.0"


def test_program_and_athlete_directories_must_remain_separate_before_setup(tmp_path):
    home = tmp_path / "athlete"
    home.mkdir()
    with pytest.raises(ValueError, match="separate|overlap"):
        distribution.finish_setup(home, home / "program", clients="codex", execute=lambda args: 0)
    assert list(home.iterdir()) == []


def test_incompatible_release_is_refused_before_downloading_or_creating_program_files(tmp_path):
    manifest = _release_fixture(tmp_path)
    value = json.loads(manifest.read_text())
    value["architecture"] = "x86_64"
    manifest.write_text(json.dumps(value))
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="architecture"):
        distribution.prepare_release(manifest, root)
    assert not root.exists()


def test_declined_import_is_configured_not_completed_and_does_not_prepare_clients(tmp_path):
    home, root = tmp_path / "athlete", tmp_path / "runtime"
    home.mkdir()
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    calls = []
    result = distribution.finish_setup(
        home, root, clients="codex", execute=lambda command: calls.append(command) or 0
    )
    assert result["onboarding"]["status"] == "configured"
    assert result["coach_read"] == "not_ready"
    assert result["clients"]["status"] == "not_run"
    assert len(calls) == 1


def test_private_runtime_ignores_callers_pip_target_and_pythonpath(tmp_path, monkeypatch):
    manifest = _release_fixture(tmp_path)
    foreign = tmp_path / "foreign-pip-target"
    monkeypatch.setenv("PIP_TARGET", str(foreign))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "unrelated-modules"))
    root = tmp_path / "runtime"
    release = distribution.prepare_release(manifest, root)
    distribution.activate_release(release, root)
    assert not foreign.exists()
    assert (release / "venv" / "bin" / "python").exists()


def test_missing_client_is_reported_even_when_configuration_command_exits_zero(tmp_path):
    import sys
    from garmin_coach.core import db

    home, root = tmp_path / "athlete", tmp_path / "runtime"
    home.mkdir()
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    (home / "data").mkdir()
    conn = db.connect(str(home / "data" / "garmin.db"))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-03"})
    conn.commit()
    conn.close()
    (root / "bin").mkdir(parents=True)
    payload = {
        "integrity": "verified",
        "runtime_available": True,
        "clients": {
            "codex": {"state": "client_missing", "actions": ["Install Codex and repeat setup."]}
        },
    }
    launcher = root / "bin" / "garmin-coach"
    launcher.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({payload!r}))\n")
    launcher.chmod(0o755)
    result = distribution.finish_setup(home, root, clients="codex", execute=lambda command: 0)
    assert result["clients"]["status"] == "incomplete"
    assert result["clients"]["by_client"]["codex"]["state"] == "client_missing"
    assert result["clients"]["by_client"]["codex"]["actions"] == ["Install Codex and repeat setup."]


def test_changed_active_pointer_is_preserved_instead_of_replaced(tmp_path):
    manifest = _release_fixture(tmp_path)
    root = tmp_path / "runtime"
    release = distribution.prepare_release(manifest, root)
    distribution.activate_release(release, root)
    foreign = tmp_path / "unrelated-runtime"
    foreign.mkdir()
    (root / "current").unlink()
    (root / "current").symlink_to(foreign)
    with pytest.raises(ValueError, match="pointer|changed"):
        distribution.activate_release(release, root)
    assert (root / "current").resolve() == foreign


def test_unknown_personal_file_prevents_destructive_private_runtime_repair(tmp_path):
    import shutil

    manifest = _release_fixture(tmp_path)
    root = tmp_path / "runtime"
    release = distribution.prepare_release(manifest, root)
    note = release / "my-training-notes.txt"
    note.write_text("Keep this personal note.")
    shutil.rmtree(release / "venv")
    with pytest.raises(ValueError, match="inventory|unowned|Unknown"):
        distribution.prepare_release(manifest, root)
    assert note.read_text() == "Keep this personal note."


def test_ownership_inventory_allows_only_known_python_cache_changes(tmp_path):
    manifest = _release_fixture(tmp_path)
    release = distribution.prepare_release(manifest, tmp_path / "runtime")
    (source,) = release.glob("venv/lib/python*/site-packages/garmin_coach/cli.py")
    cache = source.parent / "__pycache__" / "cli.cpython-313.pyc"
    cache.write_bytes(b"mutable interpreter cache")
    distribution.verify_release_inventory(release)
    personal = cache.parent / "athlete.sqlite"
    personal.write_bytes(b"personal database")
    with pytest.raises(ValueError, match="inventory|Unknown"):
        distribution.verify_release_inventory(release)
    assert personal.read_bytes() == b"personal database"
