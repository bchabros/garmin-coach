"""In-process smoke test of the MCP wiring (epic #18).

The behaviour lives in ``mcp.tools`` (tested offline in test_tools.py);
here we only assert the protocol layer registers every tool under its expected
name with a usable description. No stdio transport, no network.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from garmin_coach.etl import client
from garmin_coach.mcp import server


def _past(days: int) -> str:
    import datetime as dt

    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


EXPECTED_TOOLS = {
    # read (local DB)
    "get_snapshot",
    "get_digest",
    "get_recent_activities",
    "get_weekly",
    "get_zones",
    "get_plan",
    "get_recommendation",
    "get_events",
    "get_workout_status",
    "get_pushed_workouts",
    # local writes
    "log_rpe",
    "log_niggle",
    "log_sets",
    # plan of record (preview -> confirm writes plans/<monday>_week.md)
    "plan_preview",
    "plan_confirm",
    "plan_import",
    # goal events (the race calendar the periodization is dated from)
    "event_add",
    "event_update",
    # transport (Garmin read)
    "refresh_today",
    "repair_preview",
    "repair_confirm",
    # workout push (hash handshake)
    "author_workout",
    "push_preview",
    "push_confirm",
}


def test_every_tool_is_registered_under_its_expected_name():
    tools = asyncio.run(server.server.list_tools())

    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_every_tool_carries_a_description():
    tools = asyncio.run(server.server.list_tools())

    assert all(t.description for t in tools)


def test_author_workout_wrapper_does_not_force_a_sport():
    # the recommendation's intent picks the sport unless the caller overrides it;
    # a "run" default here would silently disable the auto-mapping (issue #16, T4)
    tools = asyncio.run(server.server.list_tools())
    author = next(t for t in tools if t.name == "author_workout")

    assert author.inputSchema["properties"]["sport"]["default"] is None


def test_log_sets_accepts_bare_names_and_detailed_stations():
    """The station list takes plain names or ``{subcategory, ...}`` mappings (issue #60)."""
    tools = asyncio.run(server.server.list_tools())
    log_sets = next(t for t in tools if t.name == "log_sets")

    items = log_sets.inputSchema["properties"]["stations"]["items"]
    assert any(option.get("type") == "string" for option in items["anyOf"])
    details_ref = next(option["$ref"] for option in items["anyOf"] if "$ref" in option)
    details = log_sets.inputSchema["$defs"][details_ref.rsplit("/", 1)[-1]]
    assert details["type"] == "object"
    assert details["required"] == ["subcategory"]
    assert details["additionalProperties"] is False


def test_refresh_today_reports_an_unanswerable_login_as_readable_error_text(tmp_path, monkeypatch):
    """The protocol pipe is never a terminal: the typed login error becomes tool error text (#71)."""
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: types.SimpleNamespace(db_path=str(tmp_path / "t.db"), data_start_date="2026-06-08"),
    )

    def _login(_settings):
        raise client.LoginUnavailableError("no terminal is attached; run sync from a terminal")

    monkeypatch.setattr(server.client, "login", _login)

    with pytest.raises(Exception, match="no terminal is attached; run sync from a terminal"):
        asyncio.run(server.server.call_tool("refresh_today", {}))


def test_log_niggle_names_the_severity_scale_the_writer_accepts():
    """The docstring said 1-3 while the writer took 1-5, so a valid 4 read as refused."""
    tools = asyncio.run(server.server.list_tools())

    niggle = next(t for t in tools if t.name == "log_niggle")
    assert "1-5" in niggle.description


def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server,
        "get_settings",
        lambda: types.SimpleNamespace(
            db_path=str(tmp_path / "t.db"), data_start_date="2026-06-08", sync_recheck_days=3
        ),
    )


def test_repair_confirm_reports_an_unanswerable_login_as_tool_text(tmp_path, monkeypatch):
    """Unattended, a stale saved login is a message to read, not a traceback (#71)."""
    _settings(tmp_path, monkeypatch)

    def _login(_settings_arg):
        raise client.LoginUnavailableError("saved login expired; run a command in a terminal")

    monkeypatch.setattr(server.client, "login", _login)
    preview = server.repair_preview(from_date=_past(3), to_date=_past(1))["data"]

    with pytest.raises(Exception, match="saved login expired"):
        asyncio.run(
            server.server.call_tool(
                "repair_confirm",
                {
                    "from_date": _past(3),
                    "to_date": _past(1),
                    "confirm_token": preview["confirm_token"],
                },
            )
        )


def test_repair_confirm_refuses_a_stale_token_without_logging_in(tmp_path, monkeypatch):
    """A refusal must not cost a Garmin login (ADR 0028)."""
    _settings(tmp_path, monkeypatch)
    logins = []
    monkeypatch.setattr(server.client, "login", lambda s: logins.append(s))

    out = server.repair_confirm(
        from_date=_past(3), to_date=_past(1), confirm_token="0000000000000000"
    )

    assert "preview" in out["data"]["error"]
    assert out["data"]["applied"] is False
    assert logins == []
