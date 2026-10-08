"""Project-local coach launch and transport-free connection diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic_settings import DotEnvSettingsSource

from .core import db
from .core.config import Settings, get_settings


def _installation_settings(root: pathlib.Path) -> Settings:
    try:
        settings = get_settings()
        configured = DotEnvSettingsSource(Settings, env_file=root / ".env")()
    except Exception:
        raise ValueError(
            "Invalid runtime settings; review the selected project's configuration."
        ) from None
    for name in ("db_path", "plans_dir"):
        expected = configured.get(name, Settings.model_fields[name].default)
        configured_path = pathlib.Path(expected).expanduser().resolve()
        runtime_path = pathlib.Path(getattr(settings, name)).expanduser().resolve()
        if runtime_path != configured_path:
            variable = name.upper()
            raise ValueError(
                f"Installation conflict: {variable} differs from the selected project's "
                f"configuration; unset {variable} or intentionally update that project's .env."
            )
    return settings


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
    os.chdir(root)
    settings = _installation_settings(root)
    path = pathlib.Path(settings.db_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(
            "Database missing; select the existing installation or complete its initial import."
        )
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            db.validate_schema(conn)
            horizon = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0]
            snapshot = conn.execute(
                "SELECT computed_at FROM athlete_status WHERE id = 1"
            ).fetchone()
    except sqlite3.Error:
        raise ValueError(
            "Database schema unavailable or incompatible; from the selected project run "
            "poetry run garmin-coach features offline, then repeat check."
        ) from None
    if horizon is None or not snapshot or snapshot[0] is None:
        raise ValueError(
            "Coach data missing; run the documented offline features command after importing history."
        )


async def _probe(root: pathlib.Path) -> dict[str, Any]:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "garmin_coach.project", "serve", "--project", str(root)],
        env=dict(os.environ),
        cwd=str(root.parent),
    )
    async with asyncio.timeout(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                values = {}
                for name in ("get_digest", "get_snapshot"):
                    result = await session.call_tool(name, {})
                    if result.isError or not result.structuredContent:
                        raise ValueError(
                            "Coach read failed; rebuild the marts with the offline features command."
                        )
                    values[name] = result.structuredContent
    return {
        "project": str(root),
        "runtime": sys.version.split()[0],
        "tools": sorted(tool.name for tool in tools.tools),
        "data_through": values["get_digest"]["freshness"]["data_through"],
        "snapshot_computed_at": values["get_snapshot"]["data"]["computed_at"],
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
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(
            "Fixture requires a new or empty directory; existing records are preserved."
        )
    source = pathlib.Path(__file__).resolve().parents[2]
    root.mkdir(parents=True, exist_ok=True)
    for name in ("AGENTS.md", "CLAUDE.md"):
        shutil.copyfile(source / name, root / name)
    for name in ("skills", "docs"):
        shutil.copytree(source / name, root / name)
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
    (root / ".env").write_text("DB_PATH=./data/garmin.db\nDATA_START_DATE=2026-06-08\n")
    (root / "data").mkdir()
    from .marts import snapshot

    conn = db.connect(str(root / "data" / "garmin.db"))
    try:
        db.bootstrap(conn)
        db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-03", "hrv": 61, "load_day": 80})
        snapshot.rollup(conn)
        conn.commit()
    finally:
        conn.close()


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

            server.main(existing_db_only=True)
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
