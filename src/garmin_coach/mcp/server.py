"""The coach MCP server: a thin protocol layer over ``mcp.tools`` (epic #18).

Registered as ``coach`` in the repo's ``.mcp.json`` (stdio). Every tool opens
its own connection to the finished DB, delegates to a pure function in
``mcp.tools``, and returns its freshness-enveloped dict. The only tools that
touch Garmin are ``refresh_today`` (a read through the transport seam) and
``push_preview``/``push_confirm`` (the outbound push path behind the
preview-hash handshake) - see ADR 0014. No computation happens here.
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
    """The authored workout spec and push receipt for a date (None when absent)."""
    conn = _open()
    try:
        return tools.get_workout_status(conn, date=date, reports_dir=_REPORTS_DIR)
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
    sport: str = "run",
) -> dict[str, Any]:
    """Author a structured workout spec for a date and write workout.json.

    Without ``request`` the spec comes from the recommendation targeting the
    date; with one, the athlete/hybrid request (including a custom
    ``structure`` block) is authored as-is. Pure - nothing touches Garmin.
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
    """Dry-run the push for a date: resolved action, Garmin payload, and spec_hash.

    Nothing is written. Show the result to the athlete; the returned
    ``spec_hash`` is the token ``push_confirm`` requires.
    """
    settings = get_settings()
    conn = _open()
    try:
        publisher = publish.connect_publisher(settings)
        return tools.push_preview(conn, date=date, publisher=publisher, reports_dir=_REPORTS_DIR)
    finally:
        conn.close()


@server.tool()
def push_confirm(date: str, spec_hash: str, replace: bool = False) -> dict[str, Any]:
    """Write the previewed workout to the Garmin account (upload + schedule).

    Requires the ``spec_hash`` returned by ``push_preview`` - any other value
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
            spec_hash=spec_hash,
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
