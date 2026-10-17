"""Read-only stdio handshake and digest probe, without assistant activation claims."""

from __future__ import annotations

import asyncio
import pathlib
import os

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def check(python: pathlib.Path, home: pathlib.Path) -> None:
    """Require an initialized coach server and successful local digest read.

    Args:
        python: Verified managed runtime interpreter.
        home: Explicit athlete installation; no directory fallback is used.

    Raises:
        Exception: The bounded handshake, tool inventory or read fails.
    """
    parameters = StdioServerParameters(
        command=str(python),
        args=[
            "-I",
            "-c",
            "from garmin_coach.mcp.server import main; main(existing_db_only=True)",
            "--home",
            str(home),
        ],
    )
    async with asyncio.timeout(15):
        with open(os.devnull, "w") as errors:
            async with stdio_client(parameters, errlog=errors) as (incoming, outgoing):
                async with ClientSession(incoming, outgoing) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if "get_digest" not in {tool.name for tool in tools.tools}:
                        raise ValueError("Coach digest tool missing.")
                    result = await session.call_tool("get_digest", {})
                    if result.isError:
                        raise ValueError("Coach digest read failed.")
