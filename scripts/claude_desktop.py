#!/usr/bin/env python3
"""Wire this repo into Claude Desktop: register the coach MCP server, check skill drift.

Two separate problems, only one of which can be automated:

- **The MCP server** is registered by adding one entry to Claude Desktop's config
  JSON. This script does that idempotently, preserving every other key in the file.
  The entry invokes the ``garmin-coach-mcp`` console script by name, so it is
  written once per machine and never needs updating when the code changes.

- **The coach skill** lives on the user's Claude account (it is synced *down* to
  Claude Desktop, not read from this repo), which is what makes it visible to
  Cowork and to claude.ai chat. There is no supported local or API path to push it
  up, so this script cannot upload it -- it only reports whether the copy Claude
  last synced still mirrors ``skills/coach/`` file for file, turning a silent
  staleness problem into a loud one. Re-uploading stays manual, but the archive the
  upload form wants is built here, so the folder is never zipped by hand.

Usage::

    python3 scripts/claude_desktop.py check      # report only, never writes
    python3 scripts/claude_desktop.py register   # add/update the MCP entry
    python3 scripts/claude_desktop.py package    # build the uploadable archive
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
import zipfile
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER_NAME = "coach"
SKILL_NAME = "coach"
REPO_SKILL_DIR = REPO_ROOT / "skills" / SKILL_NAME
DIST_DIR = REPO_ROOT / "dist"

_CLAUDE_SUPPORT = "Library/Application Support/Claude"

# Two independent test seams: HOME for the synced-skill cache, CONFIG_PATH for the Desktop
# config. CONFIG_PATH deliberately does not ride on HOME -- patching HOME alone would
# otherwise leave register() writing the real config file.
HOME = pathlib.Path.home()
CONFIG_PATH = pathlib.Path.home() / _CLAUDE_SUPPORT / "claude_desktop_config.json"
SKILL_CACHE_GLOB = (
    f"{_CLAUDE_SUPPORT}/local-agent-mode-sessions/skills-plugin/*/*/skills/{SKILL_NAME}"
)

# What the check observed about a file, not why -- the script cannot know the cause, and
# the likeliest one for a mismatch is an un-uploaded local edit, not an edit elsewhere.
CONTENT_DIFFERS = "differs"
MISSING_FROM_ACCOUNT = "missing from account"
LEFT_OVER_ON_ACCOUNT = "left over on account"


class RegistrationError(RuntimeError):
    """Raised when the Desktop config cannot be read or safely updated."""


def _poetry_path() -> str:
    """Return an absolute path to the poetry executable.

    Raises:
        RegistrationError: If poetry is not on PATH.
    """
    found = shutil.which("poetry")
    if found is None:
        raise RegistrationError("poetry not found on PATH; install it or run `task install` first")
    return found


def desired_entry() -> dict[str, Any]:
    """Build the MCP server entry for this repo.

    The command cds into the repo first: the server resolves ``.env``, the SQLite
    file, and ``reports/`` relative to its working directory, and Claude Desktop
    spawns the command from its own cwd.
    """
    return {
        "command": "/bin/zsh",
        "args": ["-c", f"cd {REPO_ROOT} && {_poetry_path()} run garmin-coach-mcp"],
    }


def load_config() -> dict[str, Any]:
    """Read and parse the Claude Desktop config.

    Raises:
        RegistrationError: If the file is missing or is not valid JSON. Both are
            refusals to guess: overwriting an unparseable config would discard
            whatever the user has in it.
    """
    if not CONFIG_PATH.exists():
        raise RegistrationError(
            f"no Claude Desktop config at {CONFIG_PATH}; open Claude Desktop once to create it"
        )
    try:
        parsed = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise RegistrationError(f"{CONFIG_PATH} is not valid JSON ({exc}); refusing to overwrite")
    if not isinstance(parsed, dict):
        raise RegistrationError(f"{CONFIG_PATH} is not a JSON object; refusing to overwrite")
    return parsed


def desktop_is_running() -> bool:
    """Return True if Claude Desktop appears to be running.

    Claude Desktop holds the config in memory and rewrites it (preferences and
    window state) as it runs, so a write landing now can be clobbered when it
    next saves.
    """
    result = subprocess.run(["pgrep", "-x", "Claude"], capture_output=True, check=False)
    return result.returncode == 0


def _backup(path: pathlib.Path) -> pathlib.Path:
    """Copy path alongside itself with a timestamped suffix and return the copy."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_suffix(f".json.bak-{stamp}")
    shutil.copy2(path, target)
    return target


def register(force: bool = False) -> int:
    """Add or update this repo's MCP entry in the Desktop config.

    Every other key in the file -- other servers, preferences, window state -- is
    read and written back untouched.

    Args:
        force: Write even when Claude Desktop is running (it may clobber the write).

    Returns:
        A process exit code.
    """
    config = load_config()
    entry = desired_entry()
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise RegistrationError(
            "`mcpServers` in the config is not an object; refusing to overwrite"
        )

    if servers.get(SERVER_NAME) == entry:
        print(f"[claude-desktop] '{SERVER_NAME}' is already registered and current; nothing to do")
        return 0

    if desktop_is_running() and not force:
        print(
            "[claude-desktop] Claude Desktop is running. It rewrites this config as it runs, so a\n"
            "                 write now may be silently clobbered. Quit Claude Desktop and re-run,\n"
            "                 or pass --force to write anyway.",
            file=sys.stderr,
        )
        return 2

    was = "updating" if SERVER_NAME in servers else "adding"
    servers[SERVER_NAME] = entry
    backup = _backup(CONFIG_PATH)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    print(f"[claude-desktop] {was} '{SERVER_NAME}' in {CONFIG_PATH}")
    print(f"[claude-desktop] backup written to {backup}")
    print("[claude-desktop] restart Claude Desktop for the change to take effect")
    return 0


def check_mcp() -> bool:
    """Report whether the Desktop config holds a current entry for this repo.

    Returns:
        True if the registration is present and current.
    """
    try:
        servers = load_config().get("mcpServers", {})
    except RegistrationError as exc:
        print(f"[mcp]   cannot check: {exc}")
        return False

    current = servers.get(SERVER_NAME) if isinstance(servers, dict) else None
    if current is None:
        print(f"[mcp]   '{SERVER_NAME}' is NOT registered in Claude Desktop")
        print("[mcp]   fix: task claude:register")
        return False
    if current != desired_entry():
        print(f"[mcp]   '{SERVER_NAME}' is registered but points somewhere else:")
        print(f"[mcp]     found:  {json.dumps(current)}")
        print(f"[mcp]     expect: {json.dumps(desired_entry())}")
        print("[mcp]   fix: task claude:register")
        return False
    print(f"[mcp]   '{SERVER_NAME}' is registered and current")
    return True


def _markdown_tree(root: pathlib.Path) -> dict[str, str]:
    """Map every Markdown file under root to its text, keyed by path relative to root."""
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.md"))}


def _stale_files(repo: dict[str, str], cached: dict[str, str]) -> list[tuple[str, str]]:
    """List every file in which a synced copy fails to mirror the repo.

    The skill is a directory now -- a router plus its reference files -- and the
    upload ships all of it, so a copy is current only when it holds the same files
    with the same bytes. Three ways that breaks, each its own fix.

    Args:
        repo: Markdown tree of the skill directory in this repo.
        cached: Markdown tree of the copy Claude last synced down.

    Returns:
        One (relative path, reason) pair per divergent file, sorted by path.
    """
    edited = {name for name in repo.keys() & cached.keys() if repo[name] != cached[name]}
    reasons = {name: CONTENT_DIFFERS for name in edited}
    reasons.update({name: MISSING_FROM_ACCOUNT for name in repo.keys() - cached.keys()})
    reasons.update({name: LEFT_OVER_ON_ACCOUNT for name in cached.keys() - repo.keys()})
    return sorted(reasons.items())


def _report_stale(stale: dict[pathlib.Path, list[tuple[str, str]]]) -> None:
    """Print each synced copy that has drifted, naming every file and why."""
    print(f"[skill] '{SKILL_NAME}' on your Claude account is STALE -- it differs from this repo")
    for cached_dir, files in stale.items():
        print(f"[skill]   synced copy: {cached_dir}")
        for name, reason in files:
            print(f"[skill]     {name} ({reason})")
        print(f"[skill]   diff: diff -r '{cached_dir}' '{REPO_SKILL_DIR}'")
    print("[skill] Cowork and claude.ai chat are running the old version. Re-upload is manual:")
    print("[skill] fix: task claude:package, then upload dist/coach.zip at")
    print("[skill]      claude.ai -> Settings -> Capabilities -> Skills")


def check_skill() -> bool:
    """Report whether the coach skill Claude last synced matches this repo.

    The cached copy under Application Support is synced down from the user's Claude
    account, so it stands in for "what Cowork and claude.ai chat are actually
    running" -- as of the last sync. Every Markdown file under the skill directory
    is compared, because the upload ships the whole folder.

    Returns:
        True if every synced copy mirrors the repo directory file for file.
    """
    if not REPO_SKILL_DIR.is_dir():
        print(f"[skill] no skill in this repo at {REPO_SKILL_DIR}")
        return False

    cached_dirs = sorted(HOME.glob(SKILL_CACHE_GLOB))
    if not cached_dirs:
        print(f"[skill] '{SKILL_NAME}' has never synced to this machine")
        print("[skill] Claude has no copy of it, or Claude Desktop has not synced yet.")
        print("[skill] fix: task claude:package, then upload dist/coach.zip at")
        print("[skill]      claude.ai -> Settings -> Capabilities -> Skills")
        return False

    repo_tree = _markdown_tree(REPO_SKILL_DIR)
    stale = {
        cached_dir: files
        for cached_dir in cached_dirs
        if (files := _stale_files(repo_tree, _markdown_tree(cached_dir)))
    }
    if stale:
        _report_stale(stale)
        return False

    print(f"[skill] '{SKILL_NAME}' on your Claude account matches this repo (as of last sync)")
    return True


def package_skill() -> int:
    """Build the archive the claude.ai Skills form takes, so the folder is never zipped by hand.

    The form uploads one archive, not a directory, and it wants the skill's own
    folder at the top level -- ``coach/SKILL.md``, never a bare ``SKILL.md`` -- which
    is also the layout the official ``.skill`` packager writes. Same bytes either way:
    a ``.skill`` file is this zip under a different extension.

    Returns:
        0 once the archive is written, 1 if there is no skill directory to package.
    """
    if not REPO_SKILL_DIR.is_dir():
        print(f"[skill] no skill in this repo at {REPO_SKILL_DIR}")
        return 1

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    archive = DIST_DIR / f"{SKILL_NAME}.zip"
    files = sorted(REPO_SKILL_DIR.rglob("*.md"))
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            arcname = path.relative_to(REPO_SKILL_DIR.parent)
            bundle.write(path, arcname)
            print(f"[skill]   + {arcname}")
    print(f"[skill] wrote {archive} ({len(files)} files)")
    print("[skill] upload it at claude.ai -> Settings -> Capabilities -> Skills")
    print("[skill] then: task claude:check")
    return 0


def check() -> int:
    """Report MCP registration and skill freshness without writing anything.

    Returns:
        0 if both are current, 1 otherwise.
    """
    print(f"[claude-desktop] repo: {REPO_ROOT}")
    mcp_ok = check_mcp()
    skill_ok = check_skill()
    return 0 if (mcp_ok and skill_ok) else 1


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to check or register.

    Returns:
        A process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Report MCP registration and skill freshness; never writes.")
    sub.add_parser("package", help="Build the uploadable coach skill archive under dist/.")
    register_parser = sub.add_parser(
        "register", help="Add or update this repo's coach MCP entry in Claude Desktop."
    )
    register_parser.add_argument(
        "--force",
        action="store_true",
        help="Write even while Claude Desktop is running (it may clobber the write).",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "register":
            return register(force=args.force)
        if args.command == "package":
            return package_skill()
        return check()
    except RegistrationError as exc:
        print(f"[claude-desktop] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
