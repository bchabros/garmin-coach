"""In-process smoke test of the MCP wiring (epic #18).

The behaviour lives in ``mcp.tools`` (tested offline in test_tools.py).
These tests cover protocol registration and the server entry points, using real
SQLite and temporary athlete files where argument adaptation affects behavior.
No stdio transport, no network.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import pathlib

import pytest

from garmin_coach.core import db
from garmin_coach.etl import client
from garmin_coach.mcp import server
from garmin_coach.runtime import InstallationContext


FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def existing_context():
    """An already imported temporary installation for protocol operations."""
    from garmin_coach.runtime import current

    current().open_database(create=True).close()


def _past(days: int) -> str:
    import datetime as dt

    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


EXPECTED_TOOLS = {
    # read (local DB)
    "get_snapshot",
    "get_digest",
    "get_recent_activities",
    "get_activity_laps",
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
    "log_treadmill_speed",
    "clear_treadmill_speed",
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
    # workout push (hash handshake), and taking one off a day again
    "author_workout",
    "push_preview",
    "push_confirm",
    "unschedule_preview",
    "unschedule_confirm",
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


def test_refresh_today_reports_an_unanswerable_login_as_readable_error_text(
    athlete_home, existing_context
):
    """The protocol pipe is never a terminal: the typed login error becomes tool error text (#71)."""

    def _login(_settings):
        raise client.LoginUnavailableError("no terminal is attached; run sync from a terminal")

    with InstallationContext.select(athlete_home, import_factory=_login).use():
        with pytest.raises(Exception, match="no terminal is attached; run sync from a terminal"):
            asyncio.run(server.server.call_tool("refresh_today", {}))


def test_log_niggle_names_the_severity_scale_the_writer_accepts():
    """The docstring said 1-3 while the writer took 1-5, so a valid 4 read as refused."""
    tools = asyncio.run(server.server.list_tools())

    niggle = next(t for t in tools if t.name == "log_niggle")
    assert "1-5" in niggle.description


def test_repair_confirm_reports_an_unanswerable_login_as_tool_text(athlete_home, existing_context):
    """Unattended, a stale saved login is a message to read, not a traceback (#71)."""

    def _login(_settings_arg):
        raise client.LoginUnavailableError("saved login expired; run a command in a terminal")

    preview = server.repair_preview(from_date=_past(3), to_date=_past(1))["data"]

    with (
        InstallationContext.select(athlete_home, import_factory=_login).use(),
        pytest.raises(Exception, match="saved login expired"),
    ):
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


def test_repair_confirm_refuses_a_stale_token_without_logging_in(athlete_home, existing_context):
    """A refusal must not cost a Garmin login (ADR 0028)."""
    logins = []
    with InstallationContext.select(athlete_home, import_factory=lambda s: logins.append(s)).use():
        out = server.repair_confirm(
            from_date=_past(3), to_date=_past(1), confirm_token="0000000000000000"
        )

    assert "preview" in out["data"]["error"]
    assert out["data"]["applied"] is False
    assert logins == []


def test_unschedule_confirm_without_a_receipt_never_logs_in(athlete_home, existing_context):
    """A day the coach never pushed is refused before any login (issue #74)."""
    logins = []
    with InstallationContext.select(
        athlete_home, publisher_factory=lambda s: logins.append(s)
    ).use():
        out = server.unschedule_confirm(date="2026-07-17", confirm_token="0000000000000000")

    assert "no push receipt" in out["data"]["error"]
    assert out["data"]["applied"] is False
    assert logins == []


def test_unschedule_preview_without_a_receipt_never_logs_in(athlete_home, existing_context):
    logins = []
    with InstallationContext.select(
        athlete_home, publisher_factory=lambda s: logins.append(s)
    ).use():
        out = server.unschedule_preview(date="2026-07-17")

    assert "no push receipt" in out["data"]["error"]
    assert out["data"]["confirm_token"] is None
    assert logins == []


# The tools that change nothing: the DB, the report artifacts, and the account are
# only read. Claude Desktop asks before every call unless the tool says so, and only a
# tool that says so can be allowed for good (issue #79).
READ_ONLY_TOOLS = {
    "get_snapshot",
    "get_digest",
    "get_recent_activities",
    "get_activity_laps",
    "get_weekly",
    "get_zones",
    "get_plan",
    "get_recommendation",
    "get_events",
    "get_pushed_workouts",
    "get_workout_status",
    "plan_preview",
    "repair_preview",
    "push_preview",
    "unschedule_preview",
    "refresh_today",
}

# The tools that reach the Garmin account, reading or writing.
GARMIN_TOOLS = {
    "get_workout_status",
    "push_preview",
    "push_confirm",
    "unschedule_preview",
    "unschedule_confirm",
    "refresh_today",
    "repair_confirm",
}


def test_every_read_says_it_only_reads_so_desktop_can_stop_asking():
    tools = asyncio.run(server.server.list_tools())

    read_only = {t.name for t in tools if t.annotations and t.annotations.readOnlyHint}
    assert read_only == READ_ONLY_TOOLS


def test_every_write_says_it_writes_so_desktop_keeps_asking():
    tools = asyncio.run(server.server.list_tools())

    for tool in tools:
        if tool.name in READ_ONLY_TOOLS:
            continue
        assert tool.annotations is not None, tool.name
        assert tool.annotations.readOnlyHint is False, tool.name


def test_every_tool_says_whether_it_reaches_the_account():
    tools = asyncio.run(server.server.list_tools())

    open_world = {t.name for t in tools if t.annotations and t.annotations.openWorldHint}
    assert open_world == GARMIN_TOOLS


def test_the_coach_server_refuses_a_missing_database_instead_of_creating_one(athlete_home):
    """A server started in the wrong place must say so, not coach from an empty history."""
    with pytest.raises(Exception, match="No database"):
        asyncio.run(server.server.call_tool("get_snapshot", {}))

    assert not (athlete_home / "data").exists()


def test_a_push_from_chat_records_its_receipt_in_the_installation(athlete_home):
    """The receipt a terminal status check later reads is where the chat push wrote it."""
    from tests.conftest import FakePublisher

    (athlete_home / "data").mkdir()
    conn = db.connect(str(athlete_home / "data" / "garmin.db"))
    db.bootstrap(conn)
    conn.close()
    today = _dt.date.today()
    week = (today + _dt.timedelta(days=7 - today.weekday())).isoformat()
    day = (_dt.date.fromisoformat(week) + _dt.timedelta(days=2)).isoformat()
    request = json.loads((FIXTURES / "hiit_request.json").read_text())
    account = FakePublisher()
    factories = []

    def publisher(settings):
        factories.append(settings.db_path)
        return account

    with InstallationContext.select(athlete_home, publisher_factory=publisher).use():
        server.plan_confirm(week, [{"planned": "WOD", "intent": "crossfit"}] * 7)
        server.author_workout(date=day, request=request)
        token = server.push_preview(date=day)["data"]["confirm_token"]
        server.push_confirm(date=day, confirm_token=token)

    receipt = athlete_home / "reports" / day / "push.json"
    assert json.loads(receipt.read_text())["applied"] is True
    assert factories == [str(athlete_home / "data/garmin.db")] * 2


def _workout_week(athlete_home, *, target_intent="strength"):
    (athlete_home / "data").mkdir()
    conn = db.connect(str(athlete_home / "data" / "garmin.db"))
    db.bootstrap(conn)
    conn.close()
    today = _dt.date.today()
    monday = today + _dt.timedelta(days=7 - today.weekday())
    days = [{"planned": "strength", "intent": "strength"} for _ in range(7)]
    days[2] = {"planned": target_intent, "intent": target_intent}
    outcome = server.plan_confirm(monday.isoformat(), days)["data"]
    assert outcome["written"] is True
    return monday.isoformat(), (monday + _dt.timedelta(days=2)).isoformat()


def test_author_workout_reuses_the_requested_session_from_chat(athlete_home):
    """Repeating a named session preserves its steps instead of taking a recommendation."""
    source, target = _workout_week(athlete_home)
    request = json.loads((FIXTURES / "strength_request.json").read_text())
    request["name"] = "GC FBB A"
    original = server.author_workout(date=source, request=request)["data"]["spec"]

    outcome = server.author_workout(date=target, reuse_from=source)["data"]

    assert outcome["error"] is None
    assert outcome["spec"]["date"] == target
    assert outcome["spec"]["name"] == "GC FBB A"
    assert outcome["spec"]["steps"] == original["steps"]
    persisted = json.loads((athlete_home / "reports" / target / "workout.json").read_text())
    assert persisted == outcome["spec"]


@pytest.mark.parametrize("refusal", ["plan", "past_date"])
def test_reusing_a_session_from_chat_obeys_the_target_days_guards(athlete_home, refusal):
    source, target = _workout_week(athlete_home, target_intent="rest")
    request = json.loads((FIXTURES / "strength_request.json").read_text())
    request["name"] = "GC FBB A"
    original = server.author_workout(date=source, request=request)["data"]
    assert original["error"] is None
    if refusal == "past_date":
        target = _past(1)

    outcome = server.author_workout(date=target, reuse_from=source)["data"]

    assert outcome["spec"] is None
    if refusal == "plan":
        assert "strength" in outcome["error"]
        assert "rest" in outcome["error"]
    else:
        assert "past date" in outcome["error"]
        assert target in outcome["error"]
    assert not (athlete_home / "reports" / target / "workout.json").exists()
    assert (
        json.loads((athlete_home / "reports" / source / "workout.json").read_text())
        == (original["spec"])
    )


def test_reusing_a_missing_session_from_chat_does_not_author_a_recommendation(athlete_home):
    _, target = _workout_week(athlete_home, target_intent="easy")
    missing = _past(1)
    fallback = server.author_workout(date=target)["data"]
    assert fallback["error"] is None
    assert fallback["spec"] is not None
    target_path = athlete_home / "reports" / target / "workout.json"
    before = target_path.read_bytes()

    outcome = server.author_workout(date=target, reuse_from=missing)["data"]

    assert outcome["spec"] is None
    assert missing in outcome["error"]
    assert "no workout.json" in outcome["error"]
    assert target_path.read_bytes() == before
