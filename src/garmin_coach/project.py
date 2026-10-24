"""Project-local coach launch and transport-free connection diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import shutil
import sys
from typing import Any


from .core import installation
from .mcp import probe
from . import coach_resources, synthetic


def _prepare(root: pathlib.Path) -> None:
    if not all((root / name).is_file() for name in ("AGENTS.md", "CLAUDE.md")):
        raise ValueError("Project guidance missing; select the Garmin Coach project folder.")
    skill = root / "skills" / "coach"
    router = skill / "SKILL.md"
    if not router.is_file():
        raise ValueError("Coach skill missing; restore the canonical skill and all references.")
    references = re.findall(r"\*\*MUST\*\*\s+read\s+`(references/[\w-]+\.md)`", router.read_text())
    if not references or any(not (skill / name).is_file() for name in references):
        raise ValueError("Coach references missing; install the complete canonical skill.")
    settings = installation.activate(root)
    evidence = installation.inspect_database(settings)
    if evidence.schema == "missing":
        raise ValueError(
            "Database missing; select the existing installation or complete its initial import."
        )
    if evidence.schema == "incompatible":
        raise ValueError(
            "Database schema unavailable or incompatible; from the selected project run "
            "poetry run garmin-coach features offline, then repeat check."
        )
    if evidence.data_through is None or evidence.snapshot_computed_at is None:
        raise ValueError(
            "Coach data missing; run the documented offline features command after importing history."
        )


async def _probe(root: pathlib.Path) -> dict[str, Any]:
    evidence = await probe.check(
        pathlib.Path(sys.executable),
        root,
        command=[
            sys.executable,
            "-I",
            "-m",
            "garmin_coach.project",
            "serve",
            "--project",
            str(root),
        ],
        env=dict(os.environ),
        cwd=pathlib.Path.cwd(),
    )
    return {
        "project": str(root),
        "runtime": sys.version.split()[0],
        "tools": list(evidence.tools),
        "data_through": evidence.data_through,
        "snapshot_computed_at": evidence.snapshot_computed_at,
        "client_acceptance": "not_run",
    }


def _toml_string(value: str) -> str:
    """Quote Unicode with shared JSON/TOML escapes and TOML's required DEL escape."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _configuration(root: pathlib.Path, client: str) -> str:
    launcher = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "coach_project.py"
    args = [str(launcher), "serve", "--project", str(root)]
    if client == "claude":
        return json.dumps(
            {"mcpServers": {"coach": {"command": sys.executable, "args": args}}}, indent=2
        )
    return (
        "[mcp_servers.coach]\n"
        f"command = {_toml_string(sys.executable)}\n"
        f"args = [{', '.join(_toml_string(arg) for arg in args)}]\n"
        f"cwd = {_toml_string(str(root))}\n"
    )


def _fixture(root: pathlib.Path) -> None:
    synthetic.create_installation(root)
    source = pathlib.Path(__file__).resolve().parents[2]
    for name in ("AGENTS.md", "CLAUDE.md"):
        shutil.copyfile(source / name, root / name)
    shutil.copytree(source / "docs", root / "docs")
    for name, content in coach_resources.read_public(source / "skills/coach").items():
        target = root / "skills/coach" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    shutil.copytree(source / ".claude" / "rules", root / ".claude" / "rules")
    for client in (".agents", ".claude"):
        discovery = root / client / "skills"
        discovery.mkdir(parents=True)
        (discovery / "coach").symlink_to("../../skills/coach")
    memory = root / "memory"
    memory.mkdir()
    shutil.copyfile(source / "memory" / "README.md", memory / "README.md")
    (memory / "athlete-profile.md").write_text(
        "# Synthetic athlete fixture\n\n_Ostatnia aktualizacja: 2026-07-03._\n\n"
        "Synthetic test context. The athlete wants to exercise the coach read.\n"
        "These are not observations about the owner.\n"
    )


def main() -> int:
    """Run a guarded project server or check its actual local MCP connection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "serve", "config", "fixture"))
    parser.add_argument("--project", required=True, type=pathlib.Path)
    parser.add_argument("--client", choices=("codex", "claude"), default="codex")
    args = parser.parse_args()
    root = args.project.expanduser().resolve()
    try:
        if args.command == "fixture":
            _fixture(root)
            print(f"Synthetic fixture created at {root}. Run check before opening a client chat.")
            return 0
        if args.command == "config":
            print(_configuration(root, args.client))
            return 0
        _prepare(root)
        if args.command == "serve":
            from .mcp import server

            server.main(existing_db_only=True, argv=[])
        else:
            print(json.dumps(asyncio.run(_probe(root)), indent=2))
    except ValueError as exc:
        print(f"coach-project: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "coach-project: MCP connection failed; check the Poetry runtime and project launch configuration.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
