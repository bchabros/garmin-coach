"""Shared read-only stdio evidence, without assistant activation claims."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sqlite3
import sys
from dataclasses import asdict, dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ..core import installation
from ..core.config import Settings
from ..synthetic import SYNTHETIC_DATE, create_installation

PROBE_API_VERSION = 1


@dataclass(frozen=True)
class ProbeEvidence:
    """Tool inventory and observation dates, without athlete metric values."""

    tools: tuple[str, ...]
    data_through: str | None
    snapshot_data_through: str | None
    snapshot_computed_at: str | None


async def check(
    python: pathlib.Path,
    home: pathlib.Path,
    *,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: pathlib.Path | None = None,
    timeout: float = 15,
    settings: Settings | None = None,
) -> ProbeEvidence:
    """Require inventory plus successful digest/snapshot reads over bounded real stdio.

    Args:
        python: Selected or verified managed runtime interpreter.
        home: Explicit athlete installation for the default guarded server.
        command: Optional complete source/launcher server command.
        env: Explicit child environment when the caller owns source launch policy.
        cwd: Explicit child working directory when required.
        timeout: Maximum duration of the complete protocol conversation.
        settings: Validated configuration captured by diagnosis; cannot accompany a custom command.

    Returns:
        Inventory and independent evidence dates, never a client-acceptance verdict.
    """
    selected = command or [
        str(python),
        "-I",
        "-c",
        "from garmin_coach.mcp.server import main; main(existing_db_only=True)",
        "--home",
        str(home),
    ]
    if settings is not None:
        if command is not None:
            raise ValueError("Captured settings cannot accompany a custom server command.")
        selected = _captured_command(python, home, settings)
    parameters = StdioServerParameters(
        command=selected[0],
        args=selected[1:],
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    async with asyncio.timeout(timeout):
        with open(os.devnull, "w") as errors:
            async with stdio_client(parameters, errlog=errors) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as session:
                    await session.initialize()
                    tools = tuple(sorted(tool.name for tool in (await session.list_tools()).tools))
                    if not {"get_digest", "get_snapshot"} <= set(tools):
                        raise ValueError("Coach digest/snapshot tools missing.")
                    values = await _read_tools(session)
    return _evidence(tools, values)


def _captured_command(python: pathlib.Path, home: pathlib.Path, settings: Settings) -> list[str]:
    # Run the selected runtime's server with validated choices, without reloading .env.
    # Credentials are unnecessary for the two local reads and never cross this boundary.
    values = settings.model_dump(exclude={"garmin_email", "garmin_password"})
    script = """
import json, os, pathlib, sys
from importlib.util import find_spec
from garmin_coach.core import installation
from garmin_coach.core.config import Settings
from garmin_coach.mcp import server
home, values = json.loads(sys.argv[1])
os.environ[installation.HOME_VARIABLE] = home
settings = Settings(_env_file=None, garmin_email='', garmin_password=None, **values)
if find_spec('garmin_coach.runtime') is None:
    # Pre-context releases retain settings-reader bindings in these two adapters.
    server.get_settings = lambda: settings
    server.tools.get_settings = lambda: settings
    installation.activate = lambda _home=None: settings
    server.main(existing_db_only=True, argv=['--home', home])
else:
    from garmin_coach.runtime import InstallationContext
    context = InstallationContext(pathlib.Path(home), settings, existing_db_only=True)
    with context.use():
        server.server.run()
"""
    return [str(python), "-I", "-c", script, json.dumps([str(home), values])]


async def _read_tools(session: ClientSession) -> dict[str, Any]:
    values = {}
    for name in ("get_digest", "get_snapshot"):
        result = await session.call_tool(name, {})
        if result.isError or not isinstance(result.structuredContent, dict):
            raise ValueError("Coach read failed; rebuild marts with the offline features command.")
        values[name] = result.structuredContent
    return values


def _evidence(tools: tuple[str, ...], values: dict[str, Any]) -> ProbeEvidence:
    try:
        return ProbeEvidence(
            tools,
            values["get_digest"]["freshness"]["data_through"],
            values["get_snapshot"]["freshness"]["data_through"],
            values["get_snapshot"]["data"]["computed_at"],
        )
    except (KeyError, TypeError):
        raise ValueError("Coach read returned incomplete evidence.") from None


def _database_report(args: argparse.Namespace) -> int:
    evidence = installation.inspect_database(installation.activate(args.home))
    print(json.dumps(asdict(evidence)))
    if evidence.schema != "compatible":
        return 0 if args.allow_missing and evidence.schema == "missing" else 2
    if args.require_data and not (evidence.data_through and evidence.snapshot_computed_at):
        return 2
    return 0


def _server_command(value: str | None, home: pathlib.Path) -> list[str] | None:
    if value is None:
        return None
    command = json.loads(value)
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(arg, str) for arg in command)
    ):
        raise ValueError("Server command must be a nonempty string array.")
    return [*command, "--home", str(home)]


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "synthetic", "evidence"))
    parser.add_argument("--home", required=True, type=pathlib.Path)
    parser.add_argument("--server-command")
    parser.add_argument("--require-data", action="store_true")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args(argv)
    args.home = args.home.expanduser().absolute()
    return args


def _protocol_report(args: argparse.Namespace) -> None:
    command = _server_command(args.server_command, args.home)
    if args.command == "synthetic":
        create_installation(args.home)
    evidence = asyncio.run(
        check(pathlib.Path(sys.executable), args.home, command=command, timeout=45)
    )
    if args.command == "synthetic":
        if (
            evidence.data_through != SYNTHETIC_DATE
            or evidence.snapshot_data_through != SYNTHETIC_DATE
        ):
            raise ValueError("Synthetic coach evidence dates do not match.")
        print("packaged stdio digest and snapshot: passed; client_acceptance: not_run")
    else:
        print(
            json.dumps(
                {
                    **asdict(evidence),
                    "runtime": sys.version.split()[0],
                    "client_acceptance": "not_run",
                }
            )
        )


def main(argv: list[str] | None = None) -> int:
    """Run packaged evidence or synthetic protocol checks under this runtime's code."""
    args = _arguments(argv)
    try:
        if args.command == "evidence":
            return _database_report(args)
        _protocol_report(args)
    except (ValueError, OSError, sqlite3.Error):
        print(
            "coach-probe: selected installation or runtime evidence unavailable.", file=sys.stderr
        )
        return 2
    except Exception:
        print(
            "coach-probe: stdio check failed; inspect the selected runtime and installation.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
