"""Claude Desktop registration seam (scripts/claude_desktop.py).

The risk this file guards: ``register`` writes to the user's real Claude Desktop
config, a file that also holds their other MCP servers and every app preference.
A merge bug there silently destroys state we did not create and cannot restore.
So the tests below never touch the real file - each points ``CONFIG_PATH`` at a
temp copy and asserts on what survives the write.

The second risk: ``check_skill`` is the only thing standing between a skill edit
and Cowork silently running last month's copy. It compares the whole skill
directory, so the tests point it at temp trees and assert on which files it names
stale, and why.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from typing import Any

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "claude_desktop.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("claude_desktop", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["claude_desktop"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load_module()


@pytest.fixture
def config_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A config carrying foreign servers and preferences we must not disturb."""
    payload: dict[str, Any] = {
        "mcpServers": {
            "desktop-commander": {"command": "npx", "args": ["@wonderwhy-er/desktop-commander"]},
            "garmin": {"command": "npx", "args": ["-y", "@nicolasvegam/garmin-connect-mcp"]},
        },
        "coworkUserFilesPath": "/Users/someone/Claude",
        "preferences": {"menuBarEnabled": True, "quickEntryShortcut": {"accelerator": "Alt+Space"}},
    }
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _register(mod, config_file: pathlib.Path, monkeypatch) -> int:
    monkeypatch.setattr(mod, "CONFIG_PATH", config_file)
    monkeypatch.setattr(mod, "desktop_is_running", lambda: False)
    return mod.register()


def test_register_adds_the_coach_entry(mod, config_file, monkeypatch):
    """A fresh machine gains a coach entry pointing at this repo."""
    assert _register(mod, config_file, monkeypatch) == 0

    written = json.loads(config_file.read_text())
    assert written["mcpServers"]["coach"] == mod.desired_entry()


def test_register_preserves_foreign_servers_and_preferences(mod, config_file, monkeypatch):
    """Everything we did not author survives the write byte-for-byte."""
    before = json.loads(config_file.read_text())

    _register(mod, config_file, monkeypatch)

    after = json.loads(config_file.read_text())
    assert after["preferences"] == before["preferences"]
    assert after["coworkUserFilesPath"] == before["coworkUserFilesPath"]
    for name in ("desktop-commander", "garmin"):
        assert after["mcpServers"][name] == before["mcpServers"][name]
    assert set(after) == set(before)


def test_register_is_idempotent(mod, config_file, monkeypatch):
    """A second run is a no-op: same bytes, and no backup churn."""
    _register(mod, config_file, monkeypatch)
    first = config_file.read_text()
    backups_after_first = list(config_file.parent.glob("*.bak-*"))

    assert _register(mod, config_file, monkeypatch) == 0

    assert config_file.read_text() == first
    assert list(config_file.parent.glob("*.bak-*")) == backups_after_first


def test_register_backs_up_before_writing(mod, config_file, monkeypatch):
    """The pre-write state is recoverable from disk."""
    before = config_file.read_text()

    _register(mod, config_file, monkeypatch)

    backups = list(config_file.parent.glob("*.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == before


def test_register_refuses_while_desktop_is_running(mod, config_file, monkeypatch):
    """Desktop rewrites this file as it runs, so a write now can be clobbered."""
    monkeypatch.setattr(mod, "CONFIG_PATH", config_file)
    monkeypatch.setattr(mod, "desktop_is_running", lambda: True)
    before = config_file.read_text()

    assert mod.register() == 2
    assert config_file.read_text() == before


def test_register_forced_writes_while_desktop_is_running(mod, config_file, monkeypatch):
    """--force is the documented override for the running-Desktop guard."""
    monkeypatch.setattr(mod, "CONFIG_PATH", config_file)
    monkeypatch.setattr(mod, "desktop_is_running", lambda: True)

    assert mod.register(force=True) == 0
    assert "coach" in json.loads(config_file.read_text())["mcpServers"]


def test_register_refuses_to_overwrite_unparseable_config(mod, tmp_path, monkeypatch):
    """Never guess at a config we cannot read - the user's state is in there."""
    broken = tmp_path / "claude_desktop_config.json"
    broken.write_text("{ this is not json")
    monkeypatch.setattr(mod, "CONFIG_PATH", broken)
    monkeypatch.setattr(mod, "desktop_is_running", lambda: False)

    with pytest.raises(mod.RegistrationError):
        mod.register()
    assert broken.read_text() == "{ this is not json"


def test_register_updates_a_stale_entry(mod, config_file, monkeypatch):
    """A coach entry pointing at an old path is corrected, not duplicated."""
    stale = json.loads(config_file.read_text())
    stale["mcpServers"]["coach"] = {
        "command": "/bin/zsh",
        "args": ["-c", "cd /old/path && poetry run garmin-coach-mcp"],
    }
    config_file.write_text(json.dumps(stale, indent=2))

    _register(mod, config_file, monkeypatch)

    servers = json.loads(config_file.read_text())["mcpServers"]
    assert servers["coach"] == mod.desired_entry()
    assert len([k for k in servers if k == "coach"]) == 1


SKILL_FILES = {"SKILL.md": "# Coach\nrouter\n", "references/report.md": "# Report\nsections\n"}


def _write_tree(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    """Materialize a {relative path: text} mapping under root."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def skill_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """The repo-side skill directory: a router plus one reference file."""
    return _write_tree(tmp_path / "repo" / "skills" / "coach", SKILL_FILES)


def _sync(mod, home: pathlib.Path, files: dict[str, str], slot: str = "a/b") -> pathlib.Path:
    """Materialize a copy of the skill where Claude Desktop syncs it down."""
    cached = home / pathlib.Path(mod.SKILL_CACHE_GLOB.replace("*/*", slot))
    return _write_tree(cached, files)


def _check_skill(mod, skill_repo: pathlib.Path, home: pathlib.Path, monkeypatch) -> bool:
    monkeypatch.setattr(mod, "REPO_SKILL_DIR", skill_repo)
    monkeypatch.setattr(mod, "HOME", home)
    return mod.check_skill()


def test_check_skill_passes_when_every_file_matches(mod, skill_repo, tmp_path, monkeypatch):
    """A synced copy holding the same files, byte for byte, is current."""
    home = tmp_path / "home"
    _sync(mod, home, SKILL_FILES)

    assert _check_skill(mod, skill_repo, home, monkeypatch) is True


def test_check_skill_names_a_file_whose_content_differs(
    mod, skill_repo, tmp_path, monkeypatch, capsys
):
    """An edited reference file is stale even though the router still matches."""
    home = tmp_path / "home"
    _sync(mod, home, {**SKILL_FILES, "references/report.md": "# Report\nedited elsewhere\n"})

    assert _check_skill(mod, skill_repo, home, monkeypatch) is False
    out = capsys.readouterr().out
    assert "references/report.md" in out
    assert "SKILL.md" not in out.replace("skills/coach/", "")


def test_check_skill_names_a_file_missing_from_the_account(
    mod, skill_repo, tmp_path, monkeypatch, capsys
):
    """A reference file added locally but never re-uploaded must not pass silently."""
    home = tmp_path / "home"
    _sync(mod, home, {"SKILL.md": SKILL_FILES["SKILL.md"]})

    assert _check_skill(mod, skill_repo, home, monkeypatch) is False
    assert "references/report.md" in capsys.readouterr().out


def test_check_skill_names_a_file_left_over_on_the_account(
    mod, skill_repo, tmp_path, monkeypatch, capsys
):
    """A file deleted from the repo still runs in Cowork until it is re-uploaded."""
    home = tmp_path / "home"
    _sync(mod, home, {**SKILL_FILES, "references/dropped.md": "# Dropped\n"})

    assert _check_skill(mod, skill_repo, home, monkeypatch) is False
    assert "references/dropped.md" in capsys.readouterr().out


def test_check_skill_fails_when_any_cached_copy_is_stale(
    mod, skill_repo, tmp_path, monkeypatch, capsys
):
    """Every synced copy is checked, not just the first one found."""
    home = tmp_path / "home"
    _sync(mod, home, SKILL_FILES, slot="a/current")
    _sync(mod, home, {**SKILL_FILES, "SKILL.md": "# Coach\nold router\n"}, slot="a/old")

    assert _check_skill(mod, skill_repo, home, monkeypatch) is False
    assert "old" in capsys.readouterr().out


def test_check_skill_reports_when_the_skill_never_synced(
    mod, skill_repo, tmp_path, monkeypatch, capsys
):
    """No cached copy at all means Claude has never seen this skill."""
    assert _check_skill(mod, skill_repo, tmp_path / "home", monkeypatch) is False
    assert "never synced" in capsys.readouterr().out


def test_check_skill_reports_when_the_repo_has_no_skill(mod, tmp_path, monkeypatch, capsys):
    """Nothing to compare against is a failure, not a pass."""
    home = tmp_path / "home"
    _sync(mod, home, SKILL_FILES)

    assert _check_skill(mod, tmp_path / "absent", home, monkeypatch) is False
    assert "no skill in this repo" in capsys.readouterr().out
