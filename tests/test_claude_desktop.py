"""Claude Desktop registration seam (scripts/claude_desktop.py).

The risk this file guards: ``register`` writes to the user's real Claude Desktop
config, a file that also holds their other MCP servers and every app preference.
A merge bug there silently destroys state we did not create and cannot restore.
So the tests below never touch the real file - each points ``CONFIG_PATH`` at a
temp copy and asserts on what survives the write.
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
