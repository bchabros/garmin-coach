"""Assistant setup on temporary installations and client executables only."""

from __future__ import annotations

import json
import os
import pathlib
import asyncio
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(sys.executable)


def invoke(home, runtime, *arguments, path=""):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PATH=path)
    return subprocess.run(
        [
            str(PYTHON),
            "-m",
            "garmin_coach.clients",
            "--home",
            str(home),
            "--runtime-root",
            str(runtime),
            *arguments,
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_missing_clients_prepare_complete_packages_without_modifying_athlete_data(
    athlete_home,
    tmp_path,
):
    runtime = tmp_path / "program"
    result = invoke(athlete_home, runtime, "configure", "--clients", "work,claude-code")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["selected_clients"] == ["work", "claude-code"]
    assert output["clients"]["work"]["state"] == "client_missing"
    assert output["client_acceptance"] == "not_run"
    package = pathlib.Path(output["integration_root"]) / "plugin"
    assert (package / "skills/coach/references/report.md").is_file()
    assert (package / "skills/coach/references/planning.md").is_file()
    assert (package / "skills/coach/references/authoring.md").is_file()
    binding = json.loads((package / ".mcp.json").read_text())["mcpServers"]["coach"]
    assert binding["command"] == str(runtime / "bin/garmin-coach-mcp")
    assert binding["args"] == ["--home", str(athlete_home)]
    router = (package / "skills/coach/SKILL.md").read_text()
    assert str(athlete_home) in router
    assert str(runtime / "bin/garmin-coach") in router
    assert router.index("## Selected athlete installation") < router.index("## Running the CLI")
    assert sorted(p.name for p in athlete_home.iterdir()) == [".env"]


def fake_client(directory, executable, *, failure=""):
    directory.mkdir(exist_ok=True)
    target = directory / executable
    target.write_text(
        f"#!{PYTHON}\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(directory / 'commands.jsonl')!r})\n"
        "with log.open('a') as stream: stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"if {failure!r} and {failure!r} in sys.argv: sys.exit(17)\n"
        "print('{}')\n"
    )
    target.chmod(0o755)


def test_registration_is_additive_and_a_rerun_does_not_repeat_completed_commands(
    athlete_home,
    tmp_path,
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "codex")
    fake_client(tools, "claude")
    first = invoke(athlete_home, runtime, "configure", "--clients", "codex", path=str(tools))
    assert first.returncode == 0, first.stderr
    one = json.loads(first.stdout)
    assert one["clients"]["codex"]["state"] == "needs_activation"
    second = invoke(
        athlete_home, runtime, "configure", "--clients", "work,claude-code", path=str(tools)
    )
    assert second.returncode == 0, second.stderr
    both = json.loads(second.stdout)
    assert both["selected_clients"] == ["codex", "work", "claude-code"]
    assert set(both["registrations"]) == {"openai", "claude-code"}
    assert all(item["state"] == "needs_activation" for item in both["clients"].values())
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert len(commands) == 4
    assert commands[0][:3] == ["plugin", "marketplace", "add"]
    assert commands[1][:2] == ["plugin", "add"]
    assert commands[2][:3] == ["plugin", "marketplace", "add"]
    assert commands[3][:2] == ["plugin", "install"]


def test_partial_failure_is_redacted_and_resumes_after_the_completed_marketplace_step(
    athlete_home,
    tmp_path,
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude", failure="install")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["clients"]["claude-code"]["state"] == "failed"
    fake_client(tools, "claude")
    retry = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["clients"]["claude-code"]["state"] == "needs_activation"
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert len(commands) == 3
    assert commands[-1][:2] == ["plugin", "install"]


def test_edited_owned_file_is_refused_before_registration_or_overwrite(athlete_home, tmp_path):
    runtime = tmp_path / "runtime"
    first = json.loads(invoke(athlete_home, runtime, "configure", "--clients", "codex").stdout)
    router = pathlib.Path(first["integration_root"]) / "plugin/skills/coach/SKILL.md"
    router.write_text("User replacement")
    retry = invoke(athlete_home, runtime, "configure", "--clients", "codex")
    assert retry.returncode == 2
    assert "changed" in retry.stderr
    assert router.read_text() == "User replacement"


def test_status_requires_no_client_selection_and_detects_missing_package_artifacts(
    athlete_home,
    tmp_path,
):
    runtime = tmp_path / "runtime"
    first = json.loads(invoke(athlete_home, runtime, "configure", "--clients", "codex").stdout)
    package = pathlib.Path(first["integration_root"]) / "plugin"
    (package / "skills/coach/references/report.md").unlink()
    result = invoke(athlete_home, runtime, "status")
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["integrity"] == "changed"
    assert status["client_acceptance"] == "not_run"


def test_cache_installed_plugin_reads_the_selected_database_from_an_unrelated_folder(
    athlete_home,
    tmp_path,
):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from garmin_coach.core import db
    from garmin_coach.marts import snapshot

    runtime = tmp_path / "runtime with spaces"
    (runtime / "bin").mkdir(parents=True)
    launcher = runtime / "bin/garmin-coach-mcp"
    launcher.write_text(f"#!{PYTHON}\nfrom garmin_coach.mcp.server import main\nmain()\n")
    launcher.chmod(0o755)
    (athlete_home / "data").mkdir()
    connection = db.connect(str(athlete_home / "data/garmin.db"))
    db.bootstrap(connection)
    db.upsert_daily(connection, "daily_metrics", {"date": "2026-07-03", "hrv": None})
    snapshot.rollup(connection)
    connection.commit()
    connection.close()
    output = json.loads(invoke(athlete_home, runtime, "configure", "--clients", "codex").stdout)
    cache = tmp_path / "client cache"
    shutil.copytree(pathlib.Path(output["integration_root"]) / "plugin", cache)
    config = json.loads((cache / ".mcp.json").read_text())["mcpServers"]["coach"]

    async def read():
        parameters = StdioServerParameters(
            command=config["command"],
            args=config["args"],
            cwd=str(cache),
            env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        )
        async with stdio_client(parameters) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as session:
                await session.initialize()
                digest = await session.call_tool("get_digest", {})
                standing = await session.call_tool("get_snapshot", {})
                assert not digest.isError and not standing.isError
                return digest.structuredContent

    digest = asyncio.run(read())
    assert digest["freshness"]["data_through"] == "2026-07-03"
    assert not (cache / "data").exists()


def test_removal_previews_and_preserves_the_shared_openai_registration(
    athlete_home,
    tmp_path,
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "codex")
    first = invoke(athlete_home, runtime, "configure", "--clients", "work,codex", path=str(tools))
    assert first.returncode == 0, first.stderr
    preview = clients.remove(athlete_home, runtime, ["work"])
    assert preview["confirmed"] is False
    assert clients.status(athlete_home, runtime)["selected_clients"] == ["work", "codex"]
    removed = clients.remove(athlete_home, runtime, ["work"], confirm=True)
    assert removed["selected_clients"] == ["codex"]
    commands = (tools / "commands.jsonl").read_text().splitlines()
    assert len(commands) == 2
    removed = clients.remove(athlete_home, runtime, ["codex"], confirm=True)
    assert removed["selected_clients"] == []
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert commands[-2][0:2] == ["plugin", "remove"]
    assert commands[-1][0:3] == ["plugin", "marketplace", "remove"]
    assert sorted(p.name for p in athlete_home.iterdir()) == [".env"]


def test_main_cli_parser_adapter_selects_the_requested_installation(athlete_home, tmp_path, capsys):
    import argparse
    from garmin_coach import clients
    from garmin_coach.core import installation

    parser = argparse.ArgumentParser()
    installation.add_home_argument(parser)
    clients.add_parser(parser.add_subparsers(required=True))
    args = parser.parse_args(
        [
            "--home",
            str(athlete_home),
            "clients",
            "status",
            "--runtime-root",
            str(tmp_path / "program"),
        ]
    )
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["integrity"] == "not_configured"


def test_symlinked_package_resource_cannot_be_overwritten_or_removed(athlete_home, tmp_path):
    runtime = tmp_path / "runtime"
    result = json.loads(invoke(athlete_home, runtime, "configure", "--clients", "codex").stdout)
    reference = (
        pathlib.Path(result["integration_root"]) / "plugin/skills/coach/references/report.md"
    )
    outside = tmp_path / "unrelated.md"
    outside.write_text("Unrelated document")
    reference.unlink()
    reference.symlink_to(outside)
    retry = invoke(athlete_home, runtime, "configure", "--clients", "codex")
    assert retry.returncode == 2
    assert "symlink" in retry.stderr
    assert outside.read_text() == "Unrelated document"


def test_main_cli_routes_client_setup_to_the_selected_installation(athlete_home, tmp_path):
    runtime = tmp_path / "cli runtime"
    result = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "garmin_coach.cli",
            "--home",
            str(athlete_home),
            "clients",
            "configure",
            "--clients",
            "codex",
            "--runtime-root",
            str(runtime),
        ],
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), PATH=""),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["home"] == str(athlete_home)
    assert output["selected_clients"] == ["codex"]
    assert output["clients"]["codex"]["state"] == "client_missing"
    assert sorted(path.name for path in athlete_home.iterdir()) == [".env"]
