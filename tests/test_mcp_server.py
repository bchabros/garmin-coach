"""In-process smoke test of the MCP wiring (epic #18).

The behaviour lives in ``mcp_tools`` (tested offline in test_mcp_tools.py);
here we only assert the protocol layer registers every tool under its expected
name with a usable description. No stdio transport, no network.
"""

from __future__ import annotations

import asyncio

from garmin_coach import mcp_server

EXPECTED_TOOLS = {
    # read (local DB)
    "get_snapshot",
    "get_digest",
    "get_recent_activities",
    "get_weekly",
    "get_zones",
    "get_recommendation",
    "get_events",
    "get_workout_status",
    # local writes
    "log_rpe",
    "log_niggle",
    # transport (Garmin read)
    "refresh_today",
    # workout push (hash handshake)
    "author_workout",
    "push_preview",
    "push_confirm",
}


def test_every_tool_is_registered_under_its_expected_name():
    tools = asyncio.run(mcp_server.server.list_tools())

    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_carries_a_description():
    tools = asyncio.run(mcp_server.server.list_tools())

    assert all(t.description for t in tools)
