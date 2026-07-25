"""The coach MCP server: a thin protocol layer over ``mcp.tools`` (epic #18).

Registered as ``coach`` in the repo's ``.mcp.json`` (stdio). Every tool opens
its own connection to the finished DB, delegates to a pure function in
``mcp.tools``, and returns its freshness-enveloped dict. Four tools touch
Garmin: ``refresh_today`` (a read through the transport seam),
``push_preview``/``push_confirm`` (the outbound push path behind the
preview-hash handshake), and ``get_workout_status``, which checks a push
receipt against the account rather than reporting it as fact (issue #41) - see
ADR 0014 and its annex. No computation happens here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import tools
from ..core import db
from ..core.config import get_settings
from ..etl import client
from ..workouts import publish

server = FastMCP("coach")

_REPORTS_DIR = "./reports"
_PLANS_DIR = "./plans"


def _open() -> sqlite3.Connection:
    """Open the configured DB with the schema bootstrapped."""
    settings = get_settings()
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)
    return conn


@server.tool()
def get_snapshot() -> dict[str, Any]:
    """Current athlete snapshot (athlete_status): where the athlete stands right now."""
    conn = _open()
    try:
        return tools.get_snapshot(conn)
    finally:
        conn.close()


@server.tool()
def get_digest(to_date: str | None = None) -> dict[str, Any]:
    """The cited coach digest (signals, weekly, zones, recommendation) for a horizon."""
    conn = _open()
    try:
        return tools.get_digest(conn, to_date=to_date)
    finally:
        conn.close()


@server.tool()
def get_recent_activities(n: int = 10) -> dict[str, Any]:
    """The n most recent activities, newest first, as a compact projection."""
    conn = _open()
    try:
        return tools.get_recent_activities(conn, n=n)
    finally:
        conn.close()


@server.tool()
def get_weekly(week_start: str | None = None) -> dict[str, Any]:
    """Weekly mart rows plus the plan-vs-actual grid (one week, or all weeks)."""
    conn = _open()
    try:
        return tools.get_weekly(conn, week_start=week_start)
    finally:
        conn.close()


@server.tool()
def get_zones() -> dict[str, Any]:
    """Current HR/pace zones: the LTHR anchor, bounds, threshold pace, staleness."""
    conn = _open()
    try:
        return tools.get_zones(conn)
    finally:
        conn.close()


@server.tool()
def get_plan(week_start: str | None = None) -> dict[str, Any]:
    """The plan of record for a week (default: this week), per-day source included.

    ``source: plan_week`` means the athlete authored the day; ``plan_template``
    means the repeating template answered for it. ``has_plan: False`` marks an
    unplanned week - propose one with ``plan_preview``.
    """
    conn = _open()
    try:
        return tools.get_plan(conn, week_start=week_start)
    finally:
        conn.close()


@server.tool()
def plan_preview(week_start: str, days: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate a proposed week of training and show it back; nothing is written.

    ``days`` is seven ``{planned, intent}`` dicts, Monday-Sunday. ``planned`` is
    the free-text session (paces, distances, HR caps); ``intent`` must be one of
    rest | easy | tempo | strength | hyrox | crossfit | quality. Compose it from
    the athlete's history and standing, show the result, and only then confirm.
    """
    conn = _open()
    try:
        return tools.plan_preview(conn, week_start=week_start, days=days, plans_dir=_PLANS_DIR)
    finally:
        conn.close()


@server.tool()
def plan_confirm(week_start: str, days: list[dict[str, Any]]) -> dict[str, Any]:
    """Write the previewed week to plans/<monday>_week.md and cache it in the DB.

    Refused when the week already has a plan file - revise that one by hand and
    re-import, so its prose and revision log are never clobbered.
    """
    conn = _open()
    try:
        return tools.plan_confirm(conn, week_start=week_start, days=days, plans_dir=_PLANS_DIR)
    finally:
        conn.close()


@server.tool()
def get_recommendation(date: str | None = None) -> dict[str, Any]:
    """The deterministic session recommendation targeting a date (default tomorrow)."""
    conn = _open()
    try:
        return tools.get_recommendation(conn, date=date)
    finally:
        conn.close()


@server.tool()
def get_events(today: str | None = None) -> dict[str, Any]:
    """Goal races with countdowns and the anchor flag."""
    conn = _open()
    try:
        return tools.get_events(conn, today=today)
    finally:
        conn.close()


@server.tool()
def get_workout_status(date: str) -> dict[str, Any]:
    """The authored spec, the push receipt, and that receipt checked against Garmin.

    ``reconciled.state`` is what the account actually holds: ``live`` (scheduled on
    the date), ``unscheduled``, ``missing``, or ``unverified`` when Garmin could not
    be reached. The receipt is returned untouched beside it - it records the push,
    not what became of it.
    """
    conn = _open()
    try:
        return tools.get_workout_status(
            conn,
            date=date,
            connect=lambda: publish.connect_publisher(get_settings()),
            reports_dir=_REPORTS_DIR,
        )
    finally:
        conn.close()


@server.tool()
def log_rpe(
    activity_id: int,
    rpe: int,
    soreness: int | None = None,
    mood: int | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Log a session RPE (1-10) for an activity; recomputes the day's blended load."""
    settings = get_settings()
    conn = _open()
    try:
        return tools.log_rpe(
            conn,
            activity_id=activity_id,
            rpe=rpe,
            soreness=soreness,
            mood=mood,
            note=note,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()


@server.tool()
def log_niggle(
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Log a niggle (a sub-injury complaint) with severity 1-3."""
    conn = _open()
    try:
        return tools.log_niggle(conn, body_part=body_part, severity=severity, date=date, note=note)
    finally:
        conn.close()


@server.tool()
def refresh_today() -> dict[str, Any]:
    """Pull today's (partial) Garmin data and rebuild the mart through today.

    The one read that talks to Garmin (issue #8). Watermarks are never
    advanced - the nightly run re-pulls the day complete. Intraday fields stay
    partial until then; check the freshness envelope.
    """
    settings = get_settings()
    conn = _open()
    try:
        transport = client.login(settings)
        return tools.refresh_today(conn, transport, data_start_date=settings.data_start_date)
    finally:
        conn.close()


@server.tool()
def author_workout(
    date: str,
    request: dict[str, Any] | None = None,
    sport: str | None = None,
) -> dict[str, Any]:
    """Author a structured workout spec for a date and write workout.json.

    Without ``request`` the spec comes from the recommendation targeting the
    date, its intent picking the sport unless an explicit ``sport`` overrides
    it; with one, the athlete/hybrid request (including a custom ``structure``
    block) is authored as-is. Pure - nothing touches Garmin.
    """
    conn = _open()
    try:
        return tools.author_workout(
            conn, date=date, request=request, sport=sport, reports_dir=_REPORTS_DIR
        )
    finally:
        conn.close()


@server.tool()
def push_preview(date: str) -> dict[str, Any]:
    """Dry-run the push for a date: resolved action, Garmin payload, and confirm token.

    Nothing is written. Show the result to the athlete; the returned
    ``confirm_token`` is what ``push_confirm`` requires. It covers the workout and
    the date it is scheduled for, so retargeting the spec invalidates the preview.
    """
    settings = get_settings()
    conn = _open()
    try:
        publisher = publish.connect_publisher(settings)
        return tools.push_preview(conn, date=date, publisher=publisher, reports_dir=_REPORTS_DIR)
    finally:
        conn.close()


@server.tool()
def push_confirm(date: str, confirm_token: str, replace: bool = False) -> dict[str, Any]:
    """Write the previewed workout to the Garmin account (upload + schedule).

    Requires the ``confirm_token`` returned by ``push_preview`` - any other value
    is refused without touching the account. ``replace`` overwrites a changed
    same-name workout, mirroring the CLI's --replace.
    """
    settings = get_settings()
    conn = _open()
    try:
        publisher = publish.connect_publisher(settings)
        return tools.push_confirm(
            conn,
            date=date,
            confirm_token=confirm_token,
            publisher=publisher,
            replace=replace,
            reports_dir=_REPORTS_DIR,
        )
    finally:
        conn.close()


def main() -> None:
    """Run the coach MCP server over stdio."""
    server.run()


if __name__ == "__main__":
    main()
