"""Agent-notes seam (scripts/agent_memory.py).

The risk this file guards: the agent notes live outside the repo, under the real
``~/.claude``, and the consolidation pass rewrites them unattended. The helper must
find the right directory (the main checkout's, even from a worktree), never write
when asked to check, and never overwrite an earlier backup. So every test here runs
against temp directories and asserts on files on disk and on exit codes.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "agent_memory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_memory", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_memory"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mod():
    return _load_module()


def _git(cwd: pathlib.Path, *args: str) -> None:
    # A pre-commit hook exports GIT_DIR / GIT_INDEX_FILE; left in place they would aim these
    # commands at the real repo instead of the temp one.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def test_project_key_matches_claude_code_encoding(mod):
    """Every non-alphanumeric character becomes a dash, as in ~/.claude/projects."""
    assert mod.project_key(pathlib.Path("/Users/Chabi/garmin-coach")) == (
        "-Users-Chabi-garmin-coach"
    )
    assert mod.project_key(pathlib.Path("/a/b.c_d e")) == "-a-b-c-d-e"


def test_worktree_resolves_to_the_main_checkout_notes(mod, tmp_path):
    """A session in a worktree must consolidate the notes of the main checkout."""
    main = tmp_path / "repo"
    main.mkdir()
    _git(main, "init", "-q")
    _git(
        main,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
    )
    _git(main, "worktree", "add", "-q", str(tmp_path / "wt"))
    home = tmp_path / "home"

    from_main = mod.notes_dir(main, home)
    from_worktree = mod.notes_dir(tmp_path / "wt", home)

    assert from_main == from_worktree
    assert from_main == home / ".claude" / "projects" / mod.project_key(main.resolve()) / "memory"


def _notes(directory: pathlib.Path, files: list[str], index_targets: list[str]) -> pathlib.Path:
    directory.mkdir(parents=True)
    for name in files:
        (directory / name).write_text(f"---\nname: {name}\n---\nbody of {name}\n")
    lines = ["# Memory index", ""] + [f"- [{t}]({t}) — hook for {t}" for t in index_targets]
    (directory / "MEMORY.md").write_text("\n".join(lines) + "\n")
    return directory


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_check_passes_on_a_one_to_one_index(mod, tmp_path, capsys):
    notes = _notes(tmp_path / "memory", ["a.md", "b.md"], ["a.md", "b.md"])

    assert mod.check(notes) == 0
    assert "in sync" in capsys.readouterr().out


def test_check_names_every_kind_of_drift_and_fails(mod, tmp_path, capsys):
    """The 2026-07-28 case (a note with no index line) plus its two mirror images."""
    notes = _notes(
        tmp_path / "memory",
        ["indexed.md", "orphan.md", "twice.md"],
        ["indexed.md", "gone.md", "twice.md", "twice.md"],
    )

    assert mod.check(notes) == 1
    out = capsys.readouterr().out
    assert "orphan.md" in out and "no index line" in out
    assert "gone.md" in out and "no such file" in out
    assert "twice.md" in out and "indexed 2 times" in out
    assert "indexed.md" not in out


def test_check_never_writes(mod, tmp_path):
    notes = _notes(tmp_path / "memory", ["orphan.md"], ["gone.md"])
    before = _snapshot(tmp_path)

    mod.check(notes)

    assert _snapshot(tmp_path) == before


def test_check_fails_when_the_notes_directory_is_missing(mod, tmp_path, capsys):
    """A wrong path must not read as 'nothing drifted'."""
    assert mod.check(tmp_path / "nowhere") == 1
    assert "not found" in capsys.readouterr().out


def test_main_check_runs_against_the_resolved_notes(mod, tmp_path, monkeypatch):
    notes = _notes(tmp_path / "memory", ["orphan.md"], [])
    monkeypatch.setattr(mod, "notes_dir", lambda: notes)

    assert mod.main(["check"]) == 1
