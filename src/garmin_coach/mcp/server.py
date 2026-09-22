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
from ..core import db, manual_sets
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

    ``invalidated_pushes`` lists days of the confirmed week that already have a
    workout on the account harder than the new plan allows. The week is written
    either way; tell the athlete which days need re-authoring.
    """
    conn = _open()
    try:
        return tools.plan_confirm(
            conn,
            week_start=week_start,
            days=days,
            plans_dir=_PLANS_DIR,
            reports_dir=_REPORTS_DIR,
            data_start_date=get_settings().data_start_date,
        )
    finally:
        conn.close()



@server.tool()
def plan_import(week: str | None = None) -> dict[str, Any]:
    """Re-read the authored plan files into the plan of record, then rebuild the marts.

    Use after the athlete edits ``plans/<monday>_week.md`` by hand: the file is the
    plan of record, this refreshes the cache and everything derived from it.
    Without ``week`` every plan file is read. ``invalidated_pushes`` lists days
    whose already-pushed workout the edited plan no longer allows.
    """
    settings = get_settings()
    conn = _open()
    try:
        return tools.plan_import(
            conn,
            plans_dir=_PLANS_DIR,
            reports_dir=_REPORTS_DIR,
            week=week,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()


@server.tool()
def event_add(
    date: str,
    type: str,  # noqa: A002 - mirrors the goal_event column and the CLI flag
    priority: str,
    status: str,
    date_precision: str,
    target: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Record a goal race and re-date the periodization from it.

    ``type`` is hyrox|run_race, ``priority`` A|B|C (only a confirmed A anchors the
    plan), ``status`` confirmed|tentative, ``date_precision`` exact|approx (approx
    still drives every block). ``target`` is a goal time as H:MM:SS, MM:SS or
    seconds. The response carries the block calendar's row for this week before and
    after the write - report that move to the athlete.
    """
    settings = get_settings()
    conn = _open()
    try:
        return tools.event_add(
            conn,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()


@server.tool()
def event_update(
    event_id: int,
    date: str | None = None,
    type: str | None = None,  # noqa: A002 - mirrors the goal_event column and the CLI flag
    priority: str | None = None,
    status: str | None = None,
    date_precision: str | None = None,
    target: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Change a recorded race (pin an approx date, commit a tentative start).

    ``event_id`` comes from ``get_events``. Fields left unset keep their value; the
    same vocabulary as ``event_add`` applies. The response carries the block
    calendar's row for this week before and after the write.
    """
    settings = get_settings()
    conn = _open()
    try:
        return tools.event_update(
            conn,
            event_id=event_id,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
            data_start_date=settings.data_start_date,
        )
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

    Read ``reconciled.state``, not ``push.applied``: the receipt records what a push
    did, the state what the account holds now. ``live`` (in the library and scheduled
    on the date), ``edited`` (scheduled, but the steps were rewritten in Connect),
    ``unscheduled`` (in the library, unpinned or moved to another day), ``missing``
    (deleted), or ``unverified`` when Garmin could not be reached - say so rather
    than quoting the receipt as fact.

    ``steps_changed`` carries the step verdict beside the state, including on
    ``unscheduled``: True on a rewrite, False when the account's copy still matches
    the push, and null when it could not be judged because the local spec was
    re-authored since - a ``live`` with a null there says nothing about the steps.
    ``renamed_to`` is the athlete's own title for it (allowed, not a fault), and
    ``checked_at`` is when the account was consulted. On ``unverified``,
    ``last_known`` carries the previous finding.

    ``plan_divergence`` is the separate, offline question: non-null when the session
    on the account is *harder* than the plan of record now says for that date, naming
    the pushed type, the current planned intent, and when it was pushed. It means the
    plan was revised after the push - report it and offer to re-author; nothing is
    changed on the watch automatically.
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
    """Log a niggle (a sub-injury complaint) with severity 1-5."""
    conn = _open()
    try:
        return tools.log_niggle(conn, body_part=body_part, severity=severity, date=date, note=note)
    finally:
        conn.close()


@server.tool()
def log_sets(
    activity_id: int, stations: list[str | manual_sets.ManualStationDetails]
) -> dict[str, Any]:
    """Log the stations of a circuit the watch recorded as one nameless set.

    A Hyrox / group-HIIT session reaches the DB as a single UNKNOWN set, invisible
    to the movement-overlap read. Pass the stations in order, as Garmin names
    (``SLED_PULL``, ``SANDBAG_CARRY``, ...) or as ``{subcategory, reps?, sets?,
    duration_s?, max_weight?}`` mappings; a re-log replaces the prior list. The
    day's load split is recomputed at once. Names outside the movement map come
    back as ``unmapped``.
    """
    settings = get_settings()
    conn = _open()
    try:
        return tools.log_sets(
            conn,
            activity_id=activity_id,
            stations=stations,
            data_start_date=settings.data_start_date,
        )
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
def repair_preview(from_date: str, to_date: str) -> dict[str, Any]:
    """Show what the DB holds for each day of a range; nothing is fetched.

    Use when a day looks empty - an unconfirmed day in the freshness envelope, a
    session the athlete says they did. Each day reports its stored activity count,
    which daily streams answered (sleep, HRV, wellness, readiness), and what the plan
    of record expected. Show it to the athlete; ``repair_confirm`` with the returned
    token is what pulls. At most 14 days, and never today (that is refresh_today).
    """
    settings = get_settings()
    conn = _open()
    try:
        return tools.repair_preview(
            conn,
            from_date=from_date,
            to_date=to_date,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()


@server.tool()
def repair_confirm(from_date: str, to_date: str, confirm_token: str) -> dict[str, Any]:
    """Re-pull a previewed range from Garmin and rebuild the marts over it.

    Gated on the token from ``repair_preview``; a stale one is refused without
    contacting Garmin. The range is pulled whole (every stream, every day), so a day
    missing one stream is completed too. Watermarks are never written.
    """
    settings = get_settings()
    conn = _open()
    try:
        transport = client.login(settings)
        return tools.repair_confirm(
            conn,
            transport,
            from_date=from_date,
            to_date=to_date,
            confirm_token=confirm_token,
            data_start_date=settings.data_start_date,
        )
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

    The request may name the session: ``label`` becomes ``GC <date> <label>`` (at
    most 30 characters - name it the way the athlete described the session), or
    ``name`` sets the whole name when the athlete asked for one. A name without the
    session's date is a reusable workout: the same steps pushed on another date are
    scheduled again rather than uploaded twice.

    A session harder than the plan of record for that date is refused, naming both
    intents: the plan is the coaching decision, so change the plan first (a manual
    edit of ``plans/<monday>_week.md`` plus ``plan import``, or ``plan_confirm`` for
    a week with no plan yet). Anything at or below the plan authors normally.
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
    ``confirm_token`` is what ``push_confirm`` requires. It covers the workout, the
    date it is scheduled for, and the plan of record for that date, so retargeting
    the spec or revising the plan invalidates the preview.

    ``action: refuse`` with a message about the plan means the spec is harder than
    the plan of record now allows - the plan was revised after it was authored.
    Re-author the date instead of pushing.
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
    same-name workout, mirroring the CLI's --replace; it does not override the plan
    guard, which refuses a session harder than the plan of record either way.
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
