#!/usr/bin/env python3
"""Back up and check the agent notes: Claude Code's per-project memory for this repo.

The notes live outside the repo, under ``~/.claude/projects/<key>/memory/``: one
Markdown file per fact plus a ``MEMORY.md`` index whose lines are loaded at the
start of every session. The monthly consolidation pass rewrites them unattended
(``docs/agents/consolidation-pass.md``), so this script supplies the two
deterministic parts it must not leave to a model:

- ``backup`` copies the whole notes directory to a new folder under the sibling
  ``memory-backups/``, named to the minute, and never overwrites an earlier one.
- ``check`` reports index drift -- a note with no index line, an index line with no
  note, a note indexed twice -- and exits non-zero on any. It never writes.

Usage::

    python3 scripts/agent_memory.py check
    python3 scripts/agent_memory.py backup
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path.home()
INDEX_NAME = "MEMORY.md"
BACKUPS_NAME = "memory-backups"
_INDEX_LINK = re.compile(r"^\s*-\s*\[[^\]]*\]\(([^)\s]+)\)")


def project_key(path: pathlib.Path) -> str:
    """Encode an absolute path the way Claude Code names its project directories."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def clean_git_env() -> dict[str, str]:
    """Return the environment minus ``GIT_*``, which git hooks export.

    Left in place, ``GIT_DIR`` / ``GIT_INDEX_FILE`` aim a ``git`` call at the repo whose hook
    is running instead of the directory it was run in.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def main_checkout(repo: pathlib.Path) -> pathlib.Path:
    """Return the main checkout of ``repo``, so a worktree resolves like its origin."""
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=clean_git_env(),
    ).stdout.strip()
    return pathlib.Path(common).resolve().parent


def notes_dir(repo: pathlib.Path = REPO_ROOT, home: pathlib.Path = HOME) -> pathlib.Path:
    """Return the agent-notes directory Claude Code uses for this repo's main checkout."""
    return home / ".claude" / "projects" / project_key(main_checkout(repo)) / "memory"


@dataclass(frozen=True)
class Drift:
    """Ways the index and the note files disagree; empty on every field means in sync."""

    orphans: list[str]
    dangling: list[str]
    duplicated: dict[str, int]

    def lines(self) -> list[str]:
        """Render one human-readable line per problem."""
        return (
            [f"  {name}: no index line" for name in self.orphans]
            + [f"  {name}: indexed, but no such file" for name in self.dangling]
            + [f"  {name}: indexed {n} times" for name, n in self.duplicated.items()]
        )


def index_drift(directory: pathlib.Path) -> Drift:
    """Compare the note files in ``directory`` with the link targets in its index."""
    index = directory / INDEX_NAME
    text = index.read_text(encoding="utf-8") if index.exists() else ""
    targets = Counter(
        match.group(1) for line in text.splitlines() if (match := _INDEX_LINK.match(line))
    )
    files = {p.name for p in directory.glob("*.md") if p.name != INDEX_NAME}
    return Drift(
        orphans=sorted(files - targets.keys()),
        dangling=sorted(targets.keys() - files),
        duplicated={name: n for name, n in sorted(targets.items()) if n > 1},
    )


def _print_drift(directory: pathlib.Path, header: str) -> bool:
    """Print ``header`` and one line per drift problem; return whether there were any."""
    problems = index_drift(directory).lines()
    if problems:
        print(f"agent-notes: {header}")
        print("\n".join(problems))
    return bool(problems)


def check(directory: pathlib.Path) -> int:
    """Print the index drift of ``directory``; return 1 on any drift, never write.

    Args:
        directory: The agent-notes directory to inspect.

    Returns:
        0 when every note has exactly one index line and every line has a note, else 1.
    """
    if not directory.is_dir():
        print(f"agent-notes: {directory} not found")
        return 1
    if not _print_drift(directory, f"index drift in {directory}"):
        print(f"agent-notes: index in sync ({directory})")
        return 0
    return 1


def backup(directory: pathlib.Path, backups: pathlib.Path, now: dt.datetime) -> int:
    """Copy ``directory`` into a new ``backups/<date>T<HHMM>`` folder, then report drift.

    Args:
        directory: The agent-notes directory to back up.
        backups: The folder that holds one subfolder per backup.
        now: The local time that names the new subfolder.

    Returns:
        0 once the copy exists (drift is reported but does not fail it), 1 when no copy
        was made: the notes are missing or the target folder already exists.
    """
    if not directory.is_dir():
        print(f"agent-notes: {directory} not found; no backup made")
        return 1
    target = backups / now.strftime("%Y-%m-%dT%H%M")
    if target.exists():
        print(f"agent-notes: {target} already exists; refusing to overwrite it")
        return 1
    shutil.copytree(directory, target)
    print(f"agent-notes: backed up to {target}")
    _print_drift(directory, "index drift (for the pass to fix)")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Dispatch the ``check`` / ``backup`` subcommands; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Report index drift in the agent notes; never writes.")
    sub.add_parser("backup", help="Copy the agent notes to a new folder beside them.")
    args = parser.parse_args(argv)
    directory = notes_dir()
    if args.command == "backup":
        return backup(directory, directory.parent / BACKUPS_NAME, dt.datetime.now())
    return check(directory)


if __name__ == "__main__":
    sys.exit(main())
