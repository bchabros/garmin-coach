"""Assistant setup on temporary installations and client executables only."""

from __future__ import annotations

import json
import os
import pathlib
import asyncio
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(sys.executable)


@pytest.fixture(autouse=True)
def isolated_client_user(tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))


def invoke(home, runtime, *arguments, path=""):
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PATH=path)
    environment.pop("GARMIN_COACH_HOME", None)
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


def _section(text, heading):
    start = text.index(heading + "\n")
    return text[start : text.index("\n## ", start)]


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
    assert (package / "skills/coach/references/saved-context.md").is_file()
    binding = json.loads((package / ".mcp.json").read_text())["mcpServers"]["coach"]
    assert binding["command"] == str(runtime / "bin/garmin-coach-mcp")
    assert binding["args"] == ["--home", str(athlete_home)]
    router = (package / "skills/coach/SKILL.md").read_text()
    assert str(athlete_home) in router
    assert str(runtime / "bin/garmin-coach") in router
    assert router.index("## Selected athlete installation") < router.index("## Running the CLI")
    canonical = (ROOT / "skills/coach/SKILL.md").read_text()
    assert _section(router, "## Running the CLI") == _section(canonical, "## Running the CLI")
    assert "--break-system-packages" not in router
    assert "poetry run" not in (package / "skills/coach/references/report.md").read_text()
    marketplace = json.loads((package.parent / ".agents/plugins/marketplace.json").read_text())
    entry = marketplace["plugins"][0]
    # Required by the external OpenAI marketplace contract, not inferred from the generator.
    assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    assert entry["category"] == "Productivity"
    assert sorted(p.name for p in athlete_home.iterdir()) == [".env"]


def fake_client(directory, executable, *, failure=""):
    directory.mkdir(exist_ok=True)
    target = directory / executable
    target.write_text(
        f"#!{PYTHON}\n"
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(directory / 'commands.jsonl')!r})\n"
        "state_path = log.with_name(pathlib.Path(sys.argv[0]).name + '-state.json')\n"
        "state = json.loads(state_path.read_text()) if state_pat"
        "h.exists() else {'plugins': [], 'marketplaces': []}\n"
        "args = sys.argv[1:]\n"
        "if args[:3] == ['plugin', 'marketplace', 'list']:\n"
        "    print(json.dumps(state['marketplaces'] if pathlib.P"
        "ath(sys.argv[0]).name == 'claude' else {'marketplaces':"
        " state['marketplaces']})); sys.exit()\n"
        "if args[:2] == ['plugin', 'list']:\n"
        "    print(json.dumps(state['plugins'] if pathlib.Path(s"
        "ys.argv[0]).name == 'claude' else {'installed': state['"
        "plugins'], 'available': []})); sys.exit()\n"
        "with log.open('a') as stream: stream.write(json.dumps(args) + '\\n')\n"
        f"if {failure!r} and {failure!r} in args: sys.exit(17)\n"
        "if args[:3] == ['plugin', 'marketplace', 'add']:\n"
        "    root = pathlib.Path(args[3]); name = root.name\n"
        "    name = 'garmin-coach-' + name\n"
        "    state['marketplaces'] = [{'name': name, 'source': '"
        "directory', 'path': str(root), 'root': str(root)}]\n"
        "elif args[:2] in (['plugin', 'install'], ['plugin', 'add']):\n"
        "    state['plugins'] = [{'id': args[2], 'pluginId': arg"
        "s[2], 'scope': 'user', 'enabled': True}]\n"
        "elif args[:2] in (['plugin', 'uninstall'], ['plugin', 'remove']):\n"
        "    state['plugins'] = []\n"
        "elif args[:3] == ['plugin', 'marketplace', 'remove']:\n"
        "    state['marketplaces'] = []\n"
        "state_path.write_text(json.dumps(state))\n"
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
        environment = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        environment.pop("GARMIN_COACH_HOME", None)
        parameters = StdioServerParameters(
            command=config["command"],
            args=config["args"],
            cwd=str(cache),
            env=environment,
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


def test_interrupted_registration_status_retains_each_client_and_resumption_actions(
    athlete_home, tmp_path
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    tools.mkdir()
    executable = tools / "claude"
    executable.write_text(
        f"#!{PYTHON}\n"
        "import os, signal, sys\nif 'list' in sys.argv: print('[]')\n"
        "else: os.kill(os.getppid(), signal.SIGKILL)\n"
    )
    executable.chmod(0o755)
    interrupted = invoke(
        athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
    )
    assert interrupted.returncode < 0
    inspected = invoke(athlete_home, runtime, "status", path=str(tools))
    assert inspected.returncode == 0, inspected.stderr
    state = json.loads(inspected.stdout)
    assert state["clients"]["claude-code"]["state"] == "preparing"
    assert any("rerun" in action.lower() for action in state["clients"]["claude-code"]["actions"])
    fake_client(tools, "claude")
    resumed = invoke(
        athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
    )
    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["clients"]["claude-code"]["state"] == "needs_activation"


def test_adding_codex_does_not_demote_registered_claude_when_cli_is_absent(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    assert (
        invoke(
            athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
        ).returncode
        == 0
    )
    fake_client(tools, "codex")
    (tools / "claude").unlink()
    added = invoke(athlete_home, runtime, "configure", "--clients", "codex", path=str(tools))
    assert added.returncode == 0, added.stderr
    assert json.loads(added.stdout)["clients"]["claude-code"]["state"] == "needs_activation"


def test_version_rewrite_interruption_can_be_inspected_retried_and_removed(
    athlete_home, tmp_path, monkeypatch
):
    import importlib.metadata
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    clients.configure(athlete_home, runtime, ["claude-code"])
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    replace = pathlib.Path.replace
    interrupted = False

    def power_loss(source, target):
        nonlocal interrupted
        result = replace(source, target)
        if str(target).endswith("/.codex-plugin/plugin.json") and not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(pathlib.Path, "replace", power_loss)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, ["claude-code"])
    assert clients.status(athlete_home, runtime)["integrity"] == "preparing"
    assert (
        clients.configure(athlete_home, runtime, ["claude-code"])["clients"]["claude-code"]["state"]
        == "needs_activation"
    )
    assert clients.remove(athlete_home, runtime, confirm=True)["selected_clients"] == []


def test_runtime_bound_to_another_athlete_refuses_before_native_commands(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    runtime.mkdir()
    (runtime / "install-state.json").write_text(
        json.dumps({"schema_version": 1, "selected_home": str(tmp_path / "other-athlete")})
    )
    fake_client(tools, "claude")
    result = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert result.returncode == 2
    assert "another athlete installation" in result.stderr
    assert not (tools / "commands.jsonl").exists()


def test_user_scope_binding_refuses_a_second_athlete_or_runtime(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert first.returncode == 0, first.stderr
    before = (tools / "commands.jsonl").read_text()
    other = tmp_path / "another-athlete"
    other.mkdir()
    (other / ".env").write_text("")
    for home in (athlete_home, other):
        result = invoke(
            home,
            tmp_path / "second-runtime",
            "configure",
            "--clients",
            "claude-code",
            path=str(tools),
        )
        assert result.returncode == 2
        assert "user-scope" in result.stderr
    assert (tools / "commands.jsonl").read_text() == before
    assert invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools)).returncode == 0
    assert (
        invoke(
            other,
            tmp_path / "second-runtime",
            "configure",
            "--clients",
            "claude-code",
            path=str(tools),
        ).returncode
        == 0
    )


def test_unknown_native_garmin_binding_is_refused_before_mutation(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    (tools / "claude-state.json").write_text(
        json.dumps(
            {
                "plugins": [{"id": "garmin-coach-other@garmin-coach-other", "scope": "user"}],
                "marketplaces": [],
            }
        )
    )
    result = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert result.returncode == 2
    assert "user-scope" in result.stderr
    assert not (tools / "commands.jsonl").exists()


def test_removal_skips_plugin_never_installed_after_marketplace_failure(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude", failure="add")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert json.loads(first.stdout)["clients"]["claude-code"]["state"] == "failed"
    fake_client(tools, "claude", failure="uninstall")
    removed = invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools))
    assert removed.returncode == 0, removed.stderr
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert not any(command[:2] == ["plugin", "uninstall"] for command in commands)


def test_interrupted_retry_replaces_old_client_missing_state(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    assert invoke(athlete_home, runtime, "configure", "--clients", "claude-code").returncode == 0
    tools.mkdir()
    executable = tools / "claude"
    executable.write_text(
        f"#!{PYTHON}\n"
        'import os, signal, sys\nif "list" in sys.argv: print("[]")\n'
        "else: os.kill(os.getppid(), signal.SIGKILL)\n"
    )
    executable.chmod(0o755)
    assert (
        invoke(
            athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
        ).returncode
        < 0
    )
    inspected = json.loads(invoke(athlete_home, runtime, "status").stdout)
    assert inspected["clients"]["claude-code"]["state"] == "preparing"


def test_version_change_refreshes_registered_claude_without_uninstall(
    athlete_home, tmp_path, monkeypatch
):
    import importlib.metadata
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    clients.configure(athlete_home, runtime, ["claude-code"])
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    result = clients.configure(athlete_home, runtime, ["claude-code"])
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert any(command[:2] == ["plugin", "update"] for command in commands)
    assert not any(command[:2] == ["plugin", "uninstall"] for command in commands)
    assert result["clients"]["claude-code"]["state"] == "needs_activation"


def test_rollback_restores_owned_prior_generation_without_deleting_user_files(
    athlete_home, tmp_path, monkeypatch
):
    import importlib.metadata
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    first = clients.configure(athlete_home, runtime, ["claude-code"])
    root = pathlib.Path(first["integration_root"])
    backup = tmp_path / "recovery" / root.name
    shutil.copytree(root, backup)
    note = root / "operator-note.txt"
    note.write_text("Keep this unrelated note")
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    clients.configure(athlete_home, runtime, ["claude-code"])
    restored = clients.restore(athlete_home, runtime, backup)
    assert restored["package_version"] == first["package_version"]
    assert restored["artifacts"] == first["artifacts"]
    assert restored["clients"]["claude-code"]["state"] == "needs_activation"
    assert note.read_text() == "Keep this unrelated note"
    router = root / "plugin/skills/coach/SKILL.md"
    router.write_text("Operator replacement")
    with pytest.raises(ValueError, match="changed"):
        clients.restore(athlete_home, runtime, backup)
    assert router.read_text() == "Operator replacement"


def test_legacy_binding_is_adopted_only_after_native_inventory_verifies_its_source(
    athlete_home, tmp_path
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert first.returncode == 0
    registry = pathlib.Path.home() / "Library/Application Support/Garmin Coach/client-bindings.json"
    registry.unlink()
    adopted = invoke(
        athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
    )
    assert adopted.returncode == 0, adopted.stderr
    registry.unlink()
    state_path = tools / "claude-state.json"
    native = json.loads(state_path.read_text())
    native["marketplaces"][0]["path"] = str(tmp_path / "new-runtime")
    state_path.write_text(json.dumps(native))
    refused = invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools))
    assert refused.returncode == 2
    assert "ownership is unverified" in refused.stderr


def test_removal_treats_native_proof_of_absent_plugin_as_already_removed(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    assert (
        invoke(
            athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
        ).returncode
        == 0
    )
    state_path = tools / "claude-state.json"
    state = json.loads(state_path.read_text())
    state["plugins"] = []
    state_path.write_text(json.dumps(state))
    fake_client(tools, "claude", failure="uninstall")
    removed = invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools))
    assert removed.returncode == 0, removed.stderr
    assert json.loads(removed.stdout)["selected_clients"] == []


def test_case_alias_does_not_create_second_plugin_identity(athlete_home, tmp_path):
    alias = athlete_home.with_name(athlete_home.name.upper())
    if not alias.exists():
        pytest.skip("Filesystem distinguishes letter case")
    runtime = tmp_path / "runtime"
    first = json.loads(invoke(athlete_home, runtime, "configure", "--clients", "codex").stdout)
    second = invoke(alias, runtime, "configure", "--clients", "codex")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["integration_root"] == first["integration_root"]


def test_stale_runtime_cannot_remove_newer_runtime_registration(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    first = json.loads(
        invoke(
            athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
        ).stdout
    )
    receipt_path = pathlib.Path(first["integration_root"]) / "registration.json"
    stale_receipt = receipt_path.read_bytes()
    assert invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools)).returncode == 0
    newer = tmp_path / "newer-runtime"
    assert (
        invoke(
            athlete_home, newer, "configure", "--clients", "claude-code", path=str(tools)
        ).returncode
        == 0
    )
    receipt_path.write_bytes(stale_receipt)
    before = (tools / "commands.jsonl").read_text()
    refused = invoke(athlete_home, runtime, "remove", "--confirm", path=str(tools))
    assert refused.returncode == 2
    assert "user-scope" in refused.stderr
    assert (tools / "commands.jsonl").read_text() == before


def test_rollback_reattaches_original_clients_after_legacy_eager_detach(
    athlete_home, tmp_path, monkeypatch
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    first = clients.configure(athlete_home, runtime, ["claude-code"])
    backup = tmp_path / "recovery-integration"
    shutil.copytree(first["integration_root"], backup)
    clients.remove(athlete_home, runtime, confirm=True)
    restored = clients.restore(athlete_home, runtime, backup)
    assert restored["selected_clients"] == ["claude-code"]
    assert restored["clients"]["claude-code"]["state"] == "needs_activation"


def test_legacy_status_marks_ownership_unverified_with_supported_recovery(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "claude")
    assert (
        invoke(
            athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
        ).returncode
        == 0
    )
    registry = pathlib.Path.home() / "Library/Application Support/Garmin Coach/client-bindings.json"
    registry.unlink()
    inspected = json.loads(invoke(athlete_home, runtime, "status").stdout)
    assert inspected["ownership"]["state"] == "unverified"
    assert "configure" in inspected["ownership"]["actions"][0]
    assert not registry.exists()


def test_interrupted_openai_refresh_resumes_after_owned_cache_removal(
    athlete_home, tmp_path, monkeypatch
):
    import importlib.metadata
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    fake_client(tools, "codex")
    monkeypatch.setenv("PATH", str(tools))
    clients.configure(athlete_home, runtime, ["codex"])
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    execute = subprocess.run

    def interrupted(arguments, **kwargs):
        if arguments[1:3] == ["plugin", "add"]:
            raise KeyboardInterrupt
        return execute(arguments, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "run", interrupted)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, ["codex"])
    assert clients.status(athlete_home, runtime)["clients"]["codex"]["state"] == "preparing"
    restored = clients.configure(athlete_home, runtime, ["codex"])
    assert restored["clients"]["codex"]["state"] == "needs_activation"
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert sum(command[:2] == ["plugin", "remove"] for command in commands) == 1


def test_unmanaged_runtime_does_not_bind_different_hosts_to_different_athletes(
    athlete_home, tmp_path
):
    runtime = tmp_path / "runtime"
    assert invoke(athlete_home, runtime, "configure", "--clients", "codex").returncode == 0
    other = tmp_path / "other-athlete"
    other.mkdir()
    (other / ".env").write_text("")
    result = invoke(other, runtime, "configure", "--clients", "claude-code")
    assert result.returncode == 2
    assert "another athlete installation" in result.stderr


def test_late_client_cli_cannot_bypass_inventory_after_missing_client_preparation(
    athlete_home, tmp_path
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    prepared = invoke(athlete_home, runtime, "configure", "--clients", "claude-code")
    assert prepared.returncode == 0, prepared.stderr
    assert json.loads(prepared.stdout)["clients"]["claude-code"]["state"] == "client_missing"
    fake_client(tools, "claude")
    (tools / "claude-state.json").write_text(
        json.dumps(
            {
                "plugins": [{"id": "garmin-coach-other@garmin-coach-other", "scope": "user"}],
                "marketplaces": [],
            }
        )
    )
    result = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert result.returncode == 2
    assert "user-scope" in result.stderr
    assert not (tools / "commands.jsonl").exists()


@pytest.mark.parametrize("recovery", ["restore", "configure"])
def test_interrupted_rollback_of_interrupted_generation_remains_resumable(
    athlete_home, tmp_path, monkeypatch, recovery
):
    import importlib.metadata
    from garmin_coach import clients

    monkeypatch.setenv("PATH", "")
    runtime = tmp_path / "runtime"
    first = clients.configure(athlete_home, runtime, ["codex"])
    root = pathlib.Path(first["integration_root"])
    backup = tmp_path / "snapshot-a"
    shutil.copytree(root, backup)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    replace = pathlib.Path.replace

    def interrupted_b(source, target):
        result = replace(source, target)
        if str(target).endswith("/.codex-plugin/plugin.json"):
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(pathlib.Path, "replace", interrupted_b)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, ["codex"])

    if recovery == "configure":
        monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.3.0")

    def recover():
        if recovery == "restore":
            return clients.restore(athlete_home, runtime, backup)
        return clients.configure(athlete_home, runtime, ["codex"])

    def interrupted_rollback(source, target):
        result = replace(source, target)
        if pathlib.Path(target) == root / "registration.json":
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(pathlib.Path, "replace", interrupted_rollback)
        with pytest.raises(KeyboardInterrupt):
            recover()
    assert clients.status(athlete_home, runtime)["integrity"] == "preparing"
    restored = recover()
    if recovery == "restore":
        assert restored["artifacts"] == first["artifacts"]
        assert restored["package_version"] == first["package_version"]
    else:
        assert restored["package_version"] == "0.3.0"
    manifest = root / "plugin/.codex-plugin/plugin.json"
    manifest.write_text("Operator replacement")
    with pytest.raises(ValueError, match="artifact changed"):
        recover()
    assert manifest.read_text() == "Operator replacement"


def strict_client(directory, host):
    fake_client(directory, host)
    executable = directory / host
    script = executable.read_text()
    script = script.replace(
        "if args[:3] == ['plugin', 'marketplace', 'add']:\n",
        "if args[:3] == ['plugin', 'marketplace', 'add']:\n"
        "    if state['marketplaces']: sys.exit(19)\n",
    ).replace(
        "elif args[:2] in (['plugin', 'install'], ['plugin', 'add']):\n",
        "elif args[:2] in (['plugin', 'install'], ['plugin', 'add'], ['plugin', 'update']):\n"
        "    if args[1] != 'update' and state['plugins']: sys.exit(19)\n",
    )
    # Inventory version is the external evidence that cached plugin generation matches.
    script = script.replace(
        "'enabled': True}]",
        "'enabled': True, 'version': json.loads("
        "(pathlib.Path(state['marketplaces'][0]['path']) / "
        "'plugin/.codex-plugin/plugin.json').read_text())['version']}]",
    )
    executable.write_text(script)


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("stage", ["marketplace", "plugin"])
def test_committed_native_success_before_receipt_is_reconciled_without_duplicate_add(
    athlete_home, tmp_path, monkeypatch, host, stage
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, host)
    monkeypatch.setenv("PATH", str(tools))
    execute = subprocess.run
    interrupted = False

    def power_loss(arguments, **kwargs):
        nonlocal interrupted
        result = execute(arguments, **kwargs)
        expected = (
            ["plugin", "marketplace", "add"]
            if stage == "marketplace"
            else ["plugin", "add" if host == "codex" else "install"]
        )
        if not interrupted and arguments[1 : 1 + len(expected)] == expected:
            interrupted = True
            raise KeyboardInterrupt
        return result

    selected = ["codex" if host == "codex" else "claude-code"]
    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "run", power_loss)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, selected)
    retried = clients.configure(athlete_home, runtime, selected)
    assert retried["clients"][selected[0]]["state"] == "needs_activation"
    assert clients.status(athlete_home, runtime)["integrity"] == "verified"
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    assert len(commands) == 2


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("previously_registered", [False, True])
def test_new_artifact_generation_after_uncertain_native_success_requires_refresh(
    athlete_home, tmp_path, monkeypatch, host, previously_registered
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, host)
    monkeypatch.setenv("PATH", str(tools))
    selected = ["codex" if host == "codex" else "claude-code"]
    if previously_registered:
        clients.configure(athlete_home, runtime, selected)
        import importlib.metadata

        monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")
    execute = subprocess.run

    def power_loss(arguments, **kwargs):
        result = execute(arguments, **kwargs)
        operation = "add" if host == "codex" else ("update" if previously_registered else "install")
        if arguments[1:3] == ["plugin", operation]:
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "run", power_loss)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, selected)
    reference = ROOT / "skills/coach/references/report.md"
    read_bytes = pathlib.Path.read_bytes

    def changed_package(path):
        original = read_bytes(path)
        return (
            original + b"\nNew packaged reference generation\n" if path == reference else original
        )

    monkeypatch.setattr(pathlib.Path, "read_bytes", changed_package)
    retried = clients.configure(athlete_home, runtime, selected)
    assert retried["clients"][selected[0]]["state"] == "needs_activation"
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    if host == "claude":
        assert sum(command[:2] == ["plugin", "update"] for command in commands) == (
            2 if previously_registered else 1
        )
    else:
        assert any(command[:2] == ["plugin", "remove"] for command in commands)
        assert sum(command[:2] == ["plugin", "add"] for command in commands) == (
            3 if previously_registered else 2
        )


def test_native_failure_with_installed_entry_is_not_promoted_to_success(
    athlete_home, tmp_path, monkeypatch
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    execute = subprocess.run

    def native_error(arguments, **kwargs):
        result = execute(arguments, **kwargs)
        if arguments[1:3] == ["plugin", "install"]:
            return subprocess.CompletedProcess(
                arguments, 1, "private client output", "private error"
            )
        return result

    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "run", native_error)
        first = clients.configure(athlete_home, runtime, ["claude-code"])
    assert first["clients"]["claude-code"]["state"] == "failed"
    retried = clients.configure(athlete_home, runtime, ["claude-code"])
    assert retried["clients"]["claude-code"]["state"] == "failed"
    assert "private client output" not in json.dumps(retried)


def test_uncertain_native_success_with_changed_source_is_refused(
    athlete_home, tmp_path, monkeypatch
):
    from garmin_coach import clients

    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, "claude")
    monkeypatch.setenv("PATH", str(tools))
    execute = subprocess.run

    def power_loss(arguments, **kwargs):
        result = execute(arguments, **kwargs)
        if arguments[1:3] == ["plugin", "install"]:
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "run", power_loss)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, ["claude-code"])
    state_path = tools / "claude-state.json"
    native = json.loads(state_path.read_text())
    native["marketplaces"][0]["path"] = str(tmp_path / "another-runtime")
    state_path.write_text(json.dumps(native))
    before = (tools / "commands.jsonl").read_bytes()
    with pytest.raises(ValueError, match="ownership is unverified"):
        clients.configure(athlete_home, runtime, ["claude-code"])
    assert (tools / "commands.jsonl").read_bytes() == before


@pytest.mark.parametrize("layout", ["source", "package"])
@pytest.mark.parametrize(
    "missing",
    [
        None,
        "SKILL.md",
        "references/report.md",
        "references/saved-context.md",
        "symlink:references/report.md",
        "symlink:references",
    ],
)
def test_client_setup_copies_only_public_coach_resources_from_source_or_package(
    athlete_home, tmp_path, layout, missing
):
    source = tmp_path / "temporary-source"
    shutil.copytree(ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    public = [
        "SKILL.md",
        "references/report.md",
        "references/planning.md",
        "references/authoring.md",
        "references/saved-context.md",
    ]
    resources = source / ("skills/coach" if layout == "source" else "src/skills/coach")
    for relative in public:
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "skills/coach" / relative).read_bytes())
    (resources / "references/context.md").write_text(
        "Synthetic private athlete context: do not distribute"
    )
    if layout == "package":
        metadata = source / "src/garmin_coach-0.1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: garmin-coach\nVersion: 0.1.0\n"
        )
        (metadata / "RECORD").write_text(
            "".join(f"skills/coach/{name},,\n" for name in [*public, "references/context.md"])
        )
    if missing is not None:
        if missing.startswith("symlink:"):
            target = resources / missing.removeprefix("symlink:")
            outside = tmp_path / "outside"
            if target.is_dir():
                target.rename(outside)
            else:
                target.unlink()
                outside.write_text("Synthetic private athlete facts")
            target.symlink_to(outside)
        else:
            (resources / missing).unlink()
    runtime = tmp_path / "runtime"
    environment = dict(os.environ, PYTHONPATH=str(source / "src"), PATH="")
    environment.pop("GARMIN_COACH_HOME", None)
    result = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "garmin_coach.clients",
            "--home",
            str(athlete_home),
            "--runtime-root",
            str(runtime),
            "configure",
            "--clients",
            "codex",
        ],
        cwd=source,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if missing is not None:
        assert result.returncode == 2
        assert "Coach resources missing" in result.stderr
        assert "Traceback" not in result.stderr
        return
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    packaged = pathlib.Path(receipt["integration_root"]) / "plugin/skills/coach"
    assert sorted(str(path.relative_to(packaged)) for path in packaged.rglob("*.md")) == sorted(
        public
    )
    assert all(
        b"Synthetic private athlete context" not in path.read_bytes()
        for path in packaged.rglob("*.md")
    )


def legacy_private_resource(receipt, *, present=True, edited=False, owned=True):
    import hashlib

    root = pathlib.Path(receipt["integration_root"])
    relative = "plugin/skills/coach/references/context.md"
    path = root / relative
    original = b"Synthetic legacy private context"
    if present:
        path.write_bytes(b"Operator private replacement" if edited else original)
    if owned:
        record_path = root / "registration.json"
        record = json.loads(record_path.read_text())
        record["artifacts"][relative] = hashlib.sha256(original).hexdigest()
        record_path.write_text(json.dumps(record))
    return path


@pytest.mark.parametrize("host", ["codex", "claude"])
@pytest.mark.parametrize("present", [False, True])
def test_owned_legacy_private_resource_is_removed_and_native_cache_reinstalled(
    athlete_home, tmp_path, host, present
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, host)
    selected = "codex" if host == "codex" else "claude-code"
    first = invoke(athlete_home, runtime, "configure", "--clients", selected, path=str(tools))
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    private = legacy_private_resource(receipt, present=present)
    retried = invoke(athlete_home, runtime, "configure", "--clients", selected, path=str(tools))
    assert retried.returncode == 0, retried.stderr
    assert not private.exists()
    assert "Synthetic legacy private context" not in retried.stdout + retried.stderr
    updated = json.loads(retried.stdout)
    assert not any(path.endswith("/references/context.md") for path in updated["artifacts"])
    commands = [json.loads(line) for line in (tools / "commands.jsonl").read_text().splitlines()]
    removal = "remove" if host == "codex" else "uninstall"
    assert any(command[:2] == ["plugin", removal] for command in commands)
    assert (
        sum(
            command[:2] == ["plugin", "add" if host == "codex" else "install"]
            for command in commands
        )
        == 2
    )


def test_interrupted_owned_private_resource_removal_resumes_without_losing_ownership(
    athlete_home, tmp_path, monkeypatch
):
    from garmin_coach import clients

    monkeypatch.setenv("PATH", "")
    runtime = tmp_path / "runtime"
    receipt = clients.configure(athlete_home, runtime, ["codex"])
    private = legacy_private_resource(receipt)
    unlink = pathlib.Path.unlink

    def power_loss(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path == private:
            raise KeyboardInterrupt
        return result

    with monkeypatch.context() as fault:
        fault.setattr(pathlib.Path, "unlink", power_loss)
        with pytest.raises(KeyboardInterrupt):
            clients.configure(athlete_home, runtime, ["codex"])
    assert not private.exists()
    assert clients.status(athlete_home, runtime)["integrity"] == "preparing"
    restored = clients.configure(athlete_home, runtime, ["codex"])
    assert not any(path.endswith("/references/context.md") for path in restored["artifacts"])
    assert clients.status(athlete_home, runtime)["integrity"] == "verified"


@pytest.mark.parametrize("owned", [False, True])
def test_unowned_or_edited_private_resource_is_preserved_before_native_changes(
    athlete_home, tmp_path, owned
):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, "claude")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert first.returncode == 0, first.stderr
    receipt = json.loads(first.stdout)
    private = legacy_private_resource(receipt, owned=owned, edited=owned)
    before = (tools / "commands.jsonl").read_bytes()
    contents = private.read_bytes()
    retried = invoke(
        athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools)
    )
    assert retried.returncode == 2
    assert "inspect" in retried.stderr.lower()
    assert private.read_bytes() == contents
    assert (tools / "commands.jsonl").read_bytes() == before
    assert contents.decode() not in retried.stdout + retried.stderr


@pytest.mark.parametrize("state", ["owned", "edited", "unowned"])
def test_restore_never_reintroduces_private_resources_from_prior_snapshot(
    athlete_home, tmp_path, monkeypatch, state
):
    from garmin_coach import clients

    monkeypatch.setenv("PATH", "")
    runtime = tmp_path / "runtime"
    receipt = clients.configure(athlete_home, runtime, ["codex"])
    private = legacy_private_resource(receipt, owned=state != "unowned")
    backup = tmp_path / "old-snapshot"
    shutil.copytree(receipt["integration_root"], backup)
    snapshot_private = backup / "plugin/skills/coach/references/context.md"
    if state == "edited":
        snapshot_private.write_text("Operator private replacement")
    if state != "owned":
        with pytest.raises(ValueError, match="snapshot"):
            clients.restore(athlete_home, runtime, backup)
        assert private.exists() and snapshot_private.exists()
        return
    restored = clients.restore(athlete_home, runtime, backup)
    assert not private.exists()
    assert not any(path.endswith("/references/context.md") for path in restored["artifacts"])
    assert snapshot_private.read_bytes() == b"Synthetic legacy private context"


def test_missing_client_does_not_claim_legacy_native_cache_was_cleaned(athlete_home, tmp_path):
    runtime, tools = tmp_path / "runtime", tmp_path / "tools"
    strict_client(tools, "claude")
    first = invoke(athlete_home, runtime, "configure", "--clients", "claude-code", path=str(tools))
    assert first.returncode == 0, first.stderr
    private = legacy_private_resource(json.loads(first.stdout))
    (tools / "claude").unlink()
    retried = invoke(athlete_home, runtime, "configure", "--clients", "claude-code")
    assert retried.returncode == 0, retried.stderr
    assert not private.exists()
    result = json.loads(retried.stdout)
    assert result["clients"]["claude-code"]["state"] == "client_missing"
    assert any(
        "native plugin cache has not been verified clean" in action
        for action in result["clients"]["claude-code"]["actions"]
    )
    assert result["registrations"]["claude-code"]["needs_clean_install"] is True


def test_restore_accepts_complete_older_generation_without_saved_context(
    athlete_home, tmp_path, monkeypatch
):
    from garmin_coach import clients

    monkeypatch.setenv("PATH", "")
    runtime = tmp_path / "runtime"
    current = clients.configure(athlete_home, runtime, ["codex"])
    backup = tmp_path / "older-snapshot"
    shutil.copytree(current["integration_root"], backup)
    record_path = backup / "registration.json"
    record = json.loads(record_path.read_text())
    new_reference = "plugin/skills/coach/references/saved-context.md"
    del record["artifacts"][new_reference]
    record_path.write_text(json.dumps(record))
    (backup / new_reference).unlink()

    restored = clients.restore(athlete_home, runtime, backup)

    assert new_reference not in restored["artifacts"]
    assert clients.status(athlete_home, runtime)["integrity"] == "verified"
    assert (backup / "plugin/skills/coach/references/report.md").is_file()
