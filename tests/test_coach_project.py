"""Project setup through the real launcher and stdio MCP, always on temporary data."""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

from garmin_coach.core import db
from garmin_coach.marts import snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "coach_project.py"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "athlete project"
    root.mkdir()
    shutil.copytree(ROOT / "skills", root / "skills")
    for name in ("CLAUDE.md", "AGENTS.md"):
        shutil.copyfile(ROOT / name, root / name)
    (root / "data").mkdir()
    conn = db.connect(str(root / "data" / "garmin.db"))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-03", "hrv": 61, "load_day": 80})
    snapshot.rollup(conn)
    conn.commit()
    conn.close()
    return root


@pytest.fixture
def external_installation(project, monkeypatch):
    database = project.parent / "unrelated.db"
    conn = db.connect(str(database))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-04", "load_day": 20})
    snapshot.rollup(conn)
    conn.commit()
    conn.close()
    before = database.read_bytes()
    monkeypatch.setenv("DB_PATH", str(database))
    monkeypatch.setenv("PLANS_DIR", str(project.parent / "unrelated plans"))
    yield {"DB_PATH": str(database), "PLANS_DIR": str(project.parent / "unrelated plans")}
    assert database.read_bytes() == before


def run_command(project, args, *, env_overrides=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GARMIN")}
    env.pop("DB_PATH", None)
    env.pop("PLANS_DIR", None)
    env["PYTHONPATH"] = str(ROOT / "src")
    env.update(env_overrides or {})
    return subprocess.run(
        args,
        cwd=project.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_launcher(project, command, *args, env_overrides=None):
    return run_command(
        project,
        ["python3", str(LAUNCHER), command, "--project", str(project), *args],
        env_overrides=env_overrides,
    )


def test_check_reads_the_selected_installation_over_stdio_from_another_directory(project):
    before = (project / "data" / "garmin.db").read_bytes()
    result = run_launcher(project, "check")

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["data_through"] == "2026-07-03"
    assert output["snapshot_computed_at"] == "2026-07-03"
    assert {"get_digest", "get_snapshot", "push_confirm", "unschedule_confirm"} <= set(
        output["tools"]
    )
    assert output["client_acceptance"] == "not_run"
    assert "hrv" not in output
    assert (project / "data" / "garmin.db").read_bytes() == before
    assert not (project.parent / "data").exists()


@pytest.mark.parametrize("setting", ["DB_PATH", "PLANS_DIR"])
@pytest.mark.parametrize("command", ["check", "serve"])
def test_guarded_launch_refuses_conflicting_installation_paths(
    project, external_installation, setting, command
):
    before = (project / "data" / "garmin.db").read_bytes()
    result = run_launcher(project, command, env_overrides={setting: external_installation[setting]})
    assert result.returncode == 1
    assert "Installation conflict" in result.stderr
    assert setting in result.stderr
    assert "unset" in result.stderr
    assert result.stdout == ""
    assert (project / "data" / "garmin.db").read_bytes() == before


def test_guarded_launch_accepts_equivalent_environment_paths(project):
    alias = project.parent / "project alias"
    alias.symlink_to(project, target_is_directory=True)
    result = run_launcher(
        project,
        "check",
        env_overrides={
            "DB_PATH": str(alias / "data" / "garmin.db"),
            "PLANS_DIR": str(alias / "plans"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-03"


def test_guarded_launch_uses_custom_paths_explicitly_configured_in_project(
    project, external_installation
):
    (project / ".env").write_text(
        f"DB_PATH={external_installation['DB_PATH']}\nPLANS_DIR={external_installation['PLANS_DIR']}\n"
    )
    result = run_launcher(project, "check", env_overrides=external_installation)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-04"


def test_project_clients_discover_the_canonical_skill_and_all_its_resources():
    canonical = ROOT / "skills" / "coach"
    for discovery in (ROOT / ".agents" / "skills" / "coach", ROOT / ".claude" / "skills" / "coach"):
        assert discovery.resolve() == canonical
        assert (discovery / "SKILL.md").read_bytes() == (canonical / "SKILL.md").read_bytes()
        for resource in (canonical / "references").glob("*.md"):
            assert (discovery / "references" / resource.name).read_bytes() == resource.read_bytes()


def test_manual_config_connects_without_changing_existing_client_settings(
    project, external_installation
):
    config_dir = project / ".codex"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    original = b'[mcp_servers.other]\ncommand = "unrelated"\n'
    config.write_bytes(original)
    result = run_launcher(project, "config", "--client", "codex")
    assert result.returncode == 0, result.stderr
    entry = tomllib.loads(result.stdout)["mcp_servers"]["coach"]
    entry["args"][entry["args"].index("serve")] = "check"
    result = run_command(project, [entry["command"], *entry["args"]])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-03"
    assert config.read_bytes() == original


@pytest.mark.parametrize(
    "name", ["athlete \U0001f6b4", 'athlete "quoted"\\folder', "athlete \x7f\nfolder"]
)
def test_codex_config_preserves_special_path_characters_and_connects(project, name):
    project = project.rename(project.with_name(name))
    before = (project / "data" / "garmin.db").read_bytes()
    result = run_launcher(project, "config", "--client", "codex")
    assert result.returncode == 0, result.stderr
    entry = tomllib.loads(result.stdout)["mcp_servers"]["coach"]
    assert entry["cwd"] == str(project)
    assert entry["args"][-1] == str(project)
    entry["args"][entry["args"].index("serve")] = "check"
    result = run_command(project, [entry["command"], *entry["args"]])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-03"
    assert (project / "data" / "garmin.db").read_bytes() == before


def test_fixture_is_readable_without_touching_an_existing_installation(tmp_path):
    root = tmp_path / "work fixture"
    result = run_launcher(root, "fixture")
    assert result.returncode == 0, result.stderr
    result = run_launcher(root, "check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-03"
    assert "Synthetic" in (root / "memory" / "athlete-profile.md").read_text()
    imports = re.findall(r"^@(.+)$", (root / "AGENTS.md").read_text(), re.MULTILINE)
    assert imports
    for resource in imports:
        assert (root / resource).is_file(), resource
    for rule in (ROOT / ".claude" / "rules").glob("*.md"):
        assert (root / ".claude" / "rules" / rule.name).read_bytes() == rule.read_bytes()
    before = (root / "data" / "garmin.db").read_bytes()
    result = run_launcher(root, "fixture")
    assert result.returncode == 1
    assert "empty" in result.stderr
    assert (root / "data" / "garmin.db").read_bytes() == before


def test_project_guidance_routes_requests_to_the_coach_and_development_contracts():
    guidance = (ROOT / "AGENTS.md").read_text()
    gates = re.findall(r"\*\*MUST\*\*\s+read\s+`([^\`]+)`", guidance)
    assert {"skills/coach/SKILL.md", "docs/DEVELOPMENT.md"} <= set(gates)
    for path in gates:
        assert (ROOT / path).is_file()


@pytest.mark.parametrize("command", ["check", "serve"])
def test_missing_database_is_refused_before_creating_anything(project, command):
    target = project / "data" / "missing.db"
    (project / ".env").write_text("DB_PATH=./data/missing.db\n")
    result = run_launcher(project, command)
    assert result.returncode == 1
    assert "Database missing" in result.stderr
    assert not target.exists()
    assert result.stdout == ""


def test_invalid_settings_do_not_print_secrets(project):
    secret = "fixture-password-never-print"
    (project / ".env").write_text(f"GARMIN_PASSWORD={secret}\nSYNC_RECHECK_DAYS={secret}\n")
    result = run_launcher(project, "check")
    assert result.returncode == 1
    assert "Invalid runtime settings" in result.stderr
    assert secret not in result.stderr + result.stdout


def test_missing_references_are_actionable_before_startup(project):
    (project / "skills" / "coach" / "references" / "planning.md").unlink()
    result = run_launcher(project, "check")
    assert result.returncode == 1
    assert "references missing" in result.stderr


def test_uninitialized_database_is_preserved_instead_of_seeded(project):
    database = project / "data" / "garmin.db"
    database.write_bytes(b"")
    result = run_launcher(project, "check")
    assert result.returncode == 1
    assert "schema unavailable" in result.stderr
    assert database.read_bytes() == b""


@pytest.mark.parametrize("command", ["check", "serve"])
@pytest.mark.parametrize(
    "legacy_sql",
    [
        "ALTER TABLE athlete_status DROP COLUMN plan_source_today",
        "ALTER TABLE daily_metrics DROP COLUMN load_strength",
        "DROP TABLE raw_payloads; CREATE TABLE raw_payloads ("
        "fetched_at TEXT NOT NULL, endpoint TEXT NOT NULL, ref_date TEXT NOT NULL, "
        "payload TEXT NOT NULL, PRIMARY KEY (endpoint, ref_date, fetched_at)); "
        "INSERT INTO raw_payloads VALUES ('2026-07-03T12:00:00', 'fixture', '2026-07-03', '{}')",
    ],
    ids=["snapshot-column", "strength-column", "raw-identity"],
)
def test_guarded_launch_refuses_a_pending_schema_migration_without_writing(
    project, command, legacy_sql
):
    database = project / "data" / "garmin.db"
    conn = db.connect(str(database))
    conn.executescript(legacy_sql)
    conn.commit()
    conn.close()
    before = database.read_bytes()
    result = run_launcher(project, command)
    assert result.returncode == 1
    assert "schema" in result.stderr and "incompatible" in result.stderr
    assert "poetry run garmin-coach features" in result.stderr
    assert result.stdout == ""
    assert database.read_bytes() == before


def test_guarded_launch_accepts_an_explicitly_migrated_database(project):
    conn = db.connect(str(project / "data" / "garmin.db"))
    conn.execute("ALTER TABLE athlete_status DROP COLUMN plan_source_today")
    conn.commit()
    db.bootstrap(conn)
    conn.close()
    result = run_launcher(project, "check")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data_through"] == "2026-07-03"


def test_guarded_launch_preserves_compatible_schema_extensions(project):
    database = project / "data" / "garmin.db"
    conn = db.connect(str(database))
    conn.execute("ALTER TABLE daily_metrics ADD COLUMN local_note TEXT")
    conn.commit()
    conn.close()
    before = database.read_bytes()
    result = run_launcher(project, "check")
    assert result.returncode == 0, result.stderr
    assert database.read_bytes() == before


def test_sparse_installation_reports_missing_coach_data(project):
    conn = db.connect(str(project / "data" / "garmin.db"))
    conn.execute("DELETE FROM athlete_status")
    conn.commit()
    conn.close()
    result = run_launcher(project, "check")
    assert result.returncode == 1
    assert "Coach data missing" in result.stderr


def test_claude_config_runs_the_same_guarded_launcher(project, external_installation):
    result = run_launcher(project, "config", "--client", "claude")
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)["mcpServers"]["coach"]
    entry["args"][entry["args"].index("serve")] = "check"
    result = run_command(project, [entry["command"], *entry["args"]])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["snapshot_computed_at"] == "2026-07-03"


def test_launcher_explains_an_unusable_runtime_without_a_traceback(tmp_path):
    root = tmp_path / "broken checkout"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "coach_project.py"
    shutil.copyfile(LAUNCHER, launcher)
    runtime = root / ".venv" / "bin"
    runtime.mkdir(parents=True)
    (runtime / "python").write_text("not an executable interpreter")
    result = subprocess.run(
        [sys.executable, str(launcher), "check", "--project", str(root)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert "poetry install" in result.stderr
    assert "Traceback" not in result.stderr
