"""Distribution and scheduler behavior on disposable roots (issue #86)."""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

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


def _release_fixture(tmp_path, *, broken_mcp=False, fake_cli=False, architecture="arm64"):
    """Build real app code offline; only test dependencies are borrowed, like test_package."""
    import io
    import sys
    import tarfile
    import zipfile

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = wheels / "garmin_coach-0.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        source = pathlib.Path(__file__).resolve().parents[1] / "src" / "garmin_coach"
        for path in source.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                relative = pathlib.Path("garmin_coach") / path.relative_to(source)
                if fake_cli and relative.as_posix() == "garmin_coach/cli.py":
                    archive.writestr(str(relative), _FAKE_SETUP_CLI)
                elif broken_mcp and relative.as_posix() == "garmin_coach/mcp/server.py":
                    archive.writestr(str(relative), "print('not an MCP server')\n")
                else:
                    archive.write(path, str(relative))
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
        archive.add(
            pathlib.Path(__file__).resolve().parents[1] / "src/garmin_coach/distribution.py",
            arcname="bootstrap.py",
        )
    python = tmp_path / "python.tar.gz"
    with tarfile.open(python, "w:gz") as archive:
        import sysconfig

        borrowed = sysconfig.get_paths()["purelib"]
        wrapper = (
            f"#!{sys.executable}\n"
            "import pathlib, subprocess, sys\n"
            "result = subprocess.run([sys.executable, *sys.argv[1:]], check=False)\n"
            "if result.returncode == 0 and sys.argv[1:4] == ['-I', '-m', 'venv']:\n"
            "    python = pathlib.Path(sys.argv[4]) / 'bin' / 'python'\n"
            "    library = subprocess.check_output([str(python), '-I', '-c', \"import sysconfig; print(sysconfig.get_paths()['purelib'])\"], text=True).strip()\n"
            f"    pathlib.Path(library, 'borrowed.pth').write_text({borrowed!r} + '\\n')\n"
            "raise SystemExit(result.returncode)\n"
        )
        executable = wrapper.encode()
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
                "architecture": architecture,
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
        [str(root / "bin" / "garmin-coach"), "--help"],
        cwd=elsewhere,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "usage: " in result.stdout
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
    record = distribution.read_state(root)["scheduler"]
    path = pathlib.Path(record["path"])
    content = path.read_bytes()
    path.unlink()
    missing = distribution.configure_schedule(home, root, "06:00", enabled=False, agents_dir=agents)
    assert missing["status"] == "missing"
    path.write_bytes(content)
    restored = distribution.configure_schedule(
        home, root, "06:00", enabled=False, agents_dir=agents
    )
    assert restored["status"] == "failed"
    assert restored["actions"]
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

    def completed_stage(command):
        if command[-1] == "setup":
            output = home / "reports" / "2026-07-03" / "setup-complete"
            output.mkdir(parents=True)
            (output / "digest.json").write_text(
                json.dumps(
                    {
                        "onboarding": {
                            "import_complete": True,
                            "read_ready": True,
                            "completed_through": "2026-07-03",
                        }
                    }
                )
            )
        return 0

    result = distribution.finish_setup(home, root, clients="codex", execute=completed_stage)
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


@pytest.mark.parametrize("change,expected", [("remove", "missing"), ("edit", "changed")])
def test_skipped_scheduler_revalidates_saved_job_and_preserves_its_choice(
    tmp_path, change, expected
):
    home, root, agents = tmp_path / "athlete", tmp_path / "runtime", tmp_path / "agents"
    home.mkdir()
    calls = []
    saved = distribution.configure_schedule(
        home, root, "06:30", enabled=True, agents_dir=agents, execute=calls.append
    )
    path = pathlib.Path(saved["path"])
    if change == "remove":
        path.unlink()
    else:
        path.write_text("edited job, preserve it")
    result = distribution.configure_schedule(
        home, root, "06:30", enabled=False, agents_dir=agents, execute=calls.append
    )
    assert result["status"] == expected
    assert result["choice"] == "enabled"
    assert result["actions"]
    assert distribution.read_state(root)["scheduler"] == result
    repeated = distribution.configure_schedule(
        home, root, "06:30", enabled=False, agents_dir=agents, execute=calls.append
    )
    assert repeated == result
    assert len(calls) == 1
    assert path.read_text() == "edited job, preserve it" if change == "edit" else not path.exists()


def test_schedule_preview_names_the_exact_log_destinations_before_consent(tmp_path):
    home, root = tmp_path / "athlete", tmp_path / "runtime"
    preview = distribution.configure_schedule(
        home, root, "06:00", enabled=False, agents_dir=tmp_path / "agents"
    )
    assert preview["stdout_path"] == str(home / "logs" / "launchd.out.log")
    assert preview["stderr_path"] == str(home / "logs" / "launchd.err.log")
    assert not home.exists()


def test_broken_mcp_protocol_is_refused_before_readiness_or_activation(tmp_path):
    import subprocess

    manifest = _release_fixture(tmp_path, broken_mcp=True)
    root = tmp_path / "runtime"
    with pytest.raises(subprocess.SubprocessError):
        distribution.prepare_release(manifest, root)
    assert not (root / "releases" / "0.2.0-arm64" / "ready.json").exists()
    assert not (root / "current").exists()


def test_adopted_incompatible_database_is_refused_before_activation(tmp_path):
    import sqlite3

    manifest = _release_fixture(tmp_path)
    root, home = tmp_path / "runtime", tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    (home / "data").mkdir()
    with sqlite3.connect(home / "data" / "garmin.db") as conn:
        conn.execute("CREATE TABLE incompatible (value TEXT)")
    before = (home / "data" / "garmin.db").read_bytes()
    assert (
        distribution.main(
            [
                "install",
                "--manifest",
                str(manifest),
                "--runtime-root",
                str(root),
                "--home",
                str(home),
                "--prepare-only",
            ]
        )
        == 2
    )
    assert not (root / "current").exists()
    assert (home / "data" / "garmin.db").read_bytes() == before


def test_declining_import_with_an_empty_compatible_database_is_not_completion(tmp_path):
    from garmin_coach.core import db

    home, root = tmp_path / "athlete", tmp_path / "runtime"
    home.mkdir()
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    (home / "data").mkdir()
    conn = db.connect(str(home / "data" / "garmin.db"))
    db.bootstrap(conn)
    conn.close()
    calls = []
    result = distribution.finish_setup(
        home, root, clients="codex", execute=lambda command: calls.append(command) or 0
    )
    assert result["onboarding"]["status"] == "configured"
    assert result["onboarding"]["import_complete"] is None
    assert result["clients"]["status"] == "not_run"
    assert len(calls) == 1


def test_interrupted_first_runtime_build_can_retry_without_removing_added_files(
    tmp_path, monkeypatch
):
    import subprocess

    manifest = _release_fixture(tmp_path)
    root = tmp_path / "runtime"
    real_run = subprocess.run
    interrupted = False

    def fail_first_pip(command, *args, **kwargs):
        nonlocal interrupted
        if "pip" in command and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_first_pip)
    with pytest.raises(KeyboardInterrupt):
        distribution.prepare_release(manifest, root)
    incomplete = next((root / "releases").iterdir())
    note = incomplete / "my-note.txt"
    note.write_text("Keep this note after interruption.")
    repaired = distribution.prepare_release(manifest, root)
    distribution.activate_release(repaired, root)
    assert note.read_text() == "Keep this note after interruption."
    assert (root / "current").resolve() == repaired
    assert repaired != incomplete


@pytest.mark.parametrize("interruption", ["current", "garmin-coach"])
def test_interrupted_first_activation_resumes_owned_launchers_and_pointer(
    tmp_path, monkeypatch, interruption
):
    import os

    root = tmp_path / "runtime"
    release = distribution.prepare_release(_release_fixture(tmp_path), root)
    saved = distribution.read_state(root)
    saved["operation"] = {"kind": "update", "candidate": str(release)}
    distribution.write_state(root, saved)
    real_replace = os.replace
    interrupted = False

    def interrupt_pointer(source, destination):
        nonlocal interrupted
        real_replace(source, destination)
        if pathlib.Path(destination).name == interruption and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt_pointer)
    with pytest.raises(KeyboardInterrupt):
        distribution.activate_release(release, root)
    distribution.activate_release(release, root)
    assert distribution.read_state(root)["release_dir"] == str(release)
    assert (root / "current").resolve() == release
    assert not distribution.read_state(root).get("pending_activation")
    assert distribution.read_state(root)["operation"] == saved["operation"]


def test_repeated_completed_setup_can_add_a_client_without_importing_again(tmp_path):
    import sys
    from garmin_coach.core import db

    home, root = tmp_path / "athlete", tmp_path / "runtime"
    (home / "data").mkdir(parents=True)
    (home / ".env").write_text("DB_PATH=./data/garmin.db\n")
    conn = db.connect(str(home / "data" / "garmin.db"))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-03"})
    conn.commit()
    conn.close()
    distribution.write_state(
        root,
        {
            "selected_home": str(home),
            "selected_clients": ["claude-code"],
            "stages": {
                "onboarding": {"status": "complete", "import_complete": True, "read_ready": True}
            },
        },
    )
    (root / "bin").mkdir()
    launcher = root / "bin" / "garmin-coach"
    payload = {
        "integrity": "verified",
        "runtime_available": True,
        "clients": {
            "codex": {"state": "needs_activation"},
            "claude-code": {"state": "needs_activation"},
        },
    }
    launcher.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({payload!r}))\n")
    launcher.chmod(0o755)
    commands = []
    result = distribution.finish_setup(
        home, root, clients="codex", execute=lambda cmd: commands.append(cmd) or 0
    )
    assert result["onboarding"]["status"] == "complete"
    assert result["clients"]["status"] == "needs_activation"
    assert all("setup" not in cmd for cmd in commands)
    assert distribution.read_state(root)["selected_clients"] == ["claude-code", "codex"]


def test_scheduler_ownership_is_durable_before_plist_becomes_visible(tmp_path, monkeypatch):
    import os

    home, root, agents = tmp_path / "athlete", tmp_path / "runtime", tmp_path / "agents"
    home.mkdir()
    real_replace = os.replace
    interrupted = False

    def interrupt_plist(source, destination):
        nonlocal interrupted
        real_replace(source, destination)
        if pathlib.Path(destination).suffix == ".plist" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt_plist)
    # A crash immediately after creating the job must retain enough ownership to retry.
    real_write = pathlib.Path.write_bytes

    def interrupt_old_writer(path, data):
        result = real_write(path, data)
        if path.suffix == ".plist" and not interrupted:
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(pathlib.Path, "write_bytes", interrupt_old_writer)
    with pytest.raises(KeyboardInterrupt):
        distribution.configure_schedule(
            home, root, "07:00", enabled=True, agents_dir=agents, execute=lambda command: None
        )
    result = distribution.configure_schedule(
        home, root, "07:00", enabled=True, agents_dir=agents, execute=lambda command: None
    )
    assert result["status"] == "enabled"
    assert result["time"] == "07:00"
    assert len(list(agents.glob("*.plist"))) == 1


def test_rosetta_installer_refuses_intel_runtime_on_apple_silicon(tmp_path, monkeypatch):
    import subprocess

    manifest = _release_fixture(tmp_path)
    metadata = json.loads(manifest.read_text())
    metadata["architecture"] = "x86_64"
    manifest.write_text(json.dumps(metadata))
    monkeypatch.setattr(distribution.platform, "machine", lambda: "x86_64")
    real_run = subprocess.run

    def translated(command, *args, **kwargs):
        if command[0] == "/usr/sbin/sysctl":
            return subprocess.CompletedProcess(command, 0, stdout="1\n", stderr="")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", translated)
    with pytest.raises(ValueError, match="Rosetta|native"):
        distribution.prepare_release(manifest, tmp_path / "runtime")
    assert not (tmp_path / "runtime").exists()


_FAKE_SETUP_CLI = """
import json, pathlib, sys
from garmin_coach.core import db

def main():
    home = pathlib.Path(sys.argv[sys.argv.index('--home') + 1])
    if 'setup' in sys.argv:
        (home / 'data').mkdir(parents=True)
        (home / '.env').write_text('DB_PATH=./data/garmin.db\\n')
        connection = db.connect(str(home / 'data/garmin.db'))
        db.bootstrap(connection)
        db.upsert_daily(connection, 'daily_metrics', {'date': '2026-07-03'})
        connection.commit()
        connection.close()
        output = home / 'reports/2026-07-03/setup-fixture'
        output.mkdir(parents=True)
        (output / 'digest.json').write_text(json.dumps({'onboarding': {
            'import_complete': True, 'read_ready': True, 'completed_through': '2026-07-03'}}))
    elif 'status' in sys.argv:
        print(json.dumps({'integrity': 'verified', 'runtime_available': True, 'clients': {
            name: {'state': 'needs_activation'} for name in ('codex', 'claude-code')}}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
"""


def test_complete_installer_execs_private_runtime_and_prints_summary_with_fake_account_cli(
    tmp_path,
):
    import os
    import subprocess
    import sys

    manifest = _release_fixture(tmp_path, fake_cli=True, architecture=os.uname().machine)
    root, athlete, home = tmp_path / "runtime", tmp_path / "athlete", tmp_path / "system-home"
    home.mkdir()
    source = pathlib.Path(__file__).resolve().parents[1] / "src"
    environment = {**os.environ, "HOME": str(home), "PYTHONPATH": str(source)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "garmin_coach.distribution",
            "install",
            "--manifest",
            str(manifest),
            "--runtime-root",
            str(root),
            "--home",
            str(athlete),
            "--clients",
            "codex",
            "--no-schedule",
        ],
        env=environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert '"status": "complete"' in result.stdout
    assert '"status": "needs_activation"' in result.stdout
    assert "First-read prompt:" in result.stdout
    assert distribution.read_state(root)["stages"]["onboarding"]["status"] == "complete"
    assert not (home / "Library" / "LaunchAgents").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Private-runtime setup requires native macOS.")
def test_scheduler_failure_keeps_summary_and_repeated_setup_uses_saved_hour(tmp_path, monkeypatch):
    import os
    import subprocess

    root, athlete, home = tmp_path / "runtime", tmp_path / "athlete", tmp_path / "system-home"
    home.mkdir()
    monkeypatch.setattr(distribution.platform, "machine", lambda: os.uname().machine)
    manifest = _release_fixture(tmp_path, fake_cli=True, architecture=os.uname().machine)
    release = distribution.prepare_release(manifest, root)
    distribution.activate_release(release, root)
    arguments = [
        "install",
        "--manifest",
        str(manifest),
        "--runtime-root",
        str(root),
        "--home",
        str(athlete),
        "--clients",
        "codex",
    ]
    driver = """
import builtins, subprocess, sys, types
from garmin_coach import distribution
real_run = subprocess.run
failed = sys.argv[1] == 'fail'
def run(command, *args, **kwargs):
    if command[0] == '/bin/launchctl':
        if command[1] == 'print':
            return subprocess.CompletedProcess(command, 1)
        if failed:
            raise subprocess.CalledProcessError(5, command)
        return subprocess.CompletedProcess(command, 0)
    return real_run(command, *args, **kwargs)
subprocess.run = run
sys.stdin = types.SimpleNamespace(isatty=lambda: True)
builtins.input = lambda prompt: 'y'
raise SystemExit(distribution.main(sys.argv[2:]))
"""
    environment = {**os.environ, "HOME": str(home)}
    python = str(release / "venv/bin/python")
    failed = subprocess.run(
        [python, "-I", "-c", driver, "fail", *arguments, "--daily", "07:00"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert failed.returncode == 1, failed.stderr
    assert "First-read prompt:" in failed.stdout
    assert '"scheduler"' in failed.stdout and '"incomplete"' in failed.stdout
    assert "activation failed" in failed.stdout
    assert distribution.read_state(root)["scheduler"]["time"] == "07:00"
    repeated = subprocess.run(
        [python, "-I", "-c", driver, "success", *arguments],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert distribution.read_state(root)["scheduler"]["time"] == "07:00"
    assert distribution.read_state(root)["scheduler"]["status"] == "enabled"
    assert distribution.read_state(root)["stages"]["onboarding"]["reused"] is True


def test_existing_legacy_daily_job_is_preserved_and_blocks_duplicate_schedule(tmp_path):
    home, root, agents = tmp_path / "athlete", tmp_path / "runtime", tmp_path / "agents"
    agents.mkdir()
    legacy = agents / "com.garmincoach.daily.plist"
    legacy.write_text("legacy operator job")
    calls = []
    with pytest.raises(ValueError, match="legacy|existing daily"):
        distribution.configure_schedule(
            home, root, "06:00", enabled=True, agents_dir=agents, execute=calls.append
        )
    assert legacy.read_text() == "legacy operator job"
    assert list(agents.iterdir()) == [legacy]
    assert not root.exists()
    assert not calls


def test_interruption_before_first_manifest_write_can_retry_from_intent_receipt(
    tmp_path, monkeypatch
):
    manifest = _release_fixture(tmp_path)
    root = tmp_path / "runtime"
    canonical = root / "releases/0.2.0-arm64"
    real_mkdir = pathlib.Path.mkdir
    interrupted = False

    def mkdir(path, *args, **kwargs):
        nonlocal interrupted
        result = real_mkdir(path, *args, **kwargs)
        if path == canonical and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    monkeypatch.setattr(pathlib.Path, "mkdir", mkdir)
    with pytest.raises(KeyboardInterrupt):
        distribution.prepare_release(manifest, root)
    assert not (canonical / "manifest.json").exists()
    repaired = distribution.prepare_release(manifest, root)
    assert repaired != canonical
    assert (repaired / "ready.json").is_file()
    assert canonical.is_dir()


def test_loaded_legacy_job_without_plist_blocks_duplicate_schedule(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setenv("HOME", str(tmp_path))
    observed = []

    def launchctl(command, **kwargs):
        observed.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", launchctl)
    root = tmp_path / "runtime"
    with pytest.raises(ValueError, match="legacy"):
        distribution.configure_schedule(
            tmp_path / "athlete",
            root,
            "06:00",
            enabled=True,
            execute=lambda command: pytest.fail("unexpected bootstrap"),
        )
    assert observed[0][1] == "print"
    assert not root.exists()
    assert not (tmp_path / "Library").exists()


def test_pending_activation_can_roll_back_to_recorded_release_after_pointer_switch(
    tmp_path, monkeypatch
):
    import os
    import shutil

    root = tmp_path / "runtime"
    previous = distribution.prepare_release(_release_fixture(tmp_path), root)
    distribution.activate_release(previous, root)
    candidate = previous.with_name(previous.name + "-staged")
    shutil.copytree(previous, candidate, symlinks=True)
    real_replace = os.replace
    interrupted = False
    rollback_interrupted = False

    def interrupt(source, destination):
        nonlocal interrupted, rollback_interrupted
        real_replace(source, destination)
        if pathlib.Path(destination) == root / "current" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        if pathlib.Path(destination) == root / "install-state.json" and interrupted:
            pending = distribution.read_state(root).get("pending_activation", {})
            if pending.get("release_dir") == str(previous) and not rollback_interrupted:
                rollback_interrupted = True
                raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        distribution.activate_release(candidate, root)
    assert (root / "current").resolve() == candidate
    with pytest.raises(KeyboardInterrupt):
        distribution.activate_release(previous, root)
    assert (root / "current").resolve() == candidate
    distribution.activate_release(previous, root)
    assert (root / "current").resolve() == previous
    assert distribution.read_state(root)["release_dir"] == str(previous)
    assert not distribution.read_state(root).get("pending_activation")
