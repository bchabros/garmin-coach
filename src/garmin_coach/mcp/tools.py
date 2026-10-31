"""Tool functions behind the coach MCP server (epic #18).

Pure functions over the finished DB and the report artifacts; the protocol
layer in ``mcp.server`` only wires them up. Every response is wrapped in a
freshness envelope so a chat session can never mistake partial same-day data
for final numbers. No new computation happens here: each tool wraps a reader
or a seam the CLI already uses (the golden rule holds - this module never
constructs a Garmin transport; the tools that need one take it injected).

``get_workout_status`` is the one *read* that needs an injected transport: a
push receipt describes what a push did, and only the account can say what
became of it (issue #41, annexed to ADR 0014). It takes a factory rather than a
publisher, so a date that was never pushed costs no login at all. The unschedule
pair (issue #74) takes the same factory for the same reason.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import athlete_changes, daily, plan_changes
from ..coach import activity, digest, freshness, observations, thresholds as _thresholds
from ..core import db, manual_sets, plan, treadmill, observations as recorded_observations
from ..runtime import current
from ..etl import sync
from ..etl.sync import GarminClient
from ..marts import features, periodize, snapshot
from ..workouts import publish, push

# Compact activity projection: enough for "what did I do?", no per-minute series.
_ACTIVITY_COLUMNS = (
    "activity_id",
    "date",
    "gtype",
    "discipline",
    "name",
    "dur_s",
    "distance_m",
    "avg_hr",
    "avg_speed_mps",
    "aero_te",
    "anaero_te",
    "training_load",
)


def _wrap(conn: sqlite3.Connection, data: Any) -> dict[str, Any]:
    return freshness.wrap(conn, data, recheck_days=_recheck_days())


def wrap(conn: sqlite3.Connection, data: Any) -> dict[str, Any]:
    """Put a tool payload in the freshness envelope, for a server-side early return."""
    return _wrap(conn, data)


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _digest_for(conn: sqlite3.Connection, to_date: str | None = None) -> dict[str, Any]:
    """Build the cited digest for a horizon with the stored thresholds."""
    thresholds = _thresholds.read(conn)
    return digest.build_digest(
        conn, to_date=to_date, thresholds=thresholds, recheck_days=_recheck_days()
    )


def _recheck_days() -> int:
    """The re-check window the nightly sync uses - one source for every response."""
    return current().settings.sync_recheck_days


def _day_before(date: str) -> str:
    """The digest horizon for a target date: the day before it."""
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()


def get_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the athlete_status snapshot row (None until features has run)."""
    return _wrap(conn, snapshot.read(conn))


def get_observation_history(
    conn: sqlite3.Connection, from_date: str | None = None, to_date: str | None = None
) -> dict[str, Any]:
    """Read imported weighings, Garmin forecasts and their source-check coverage offline."""
    start = from_date or current().settings.data_start_date
    end = to_date or dt.date.today().isoformat()
    try:
        data = observations.read(
            conn, from_date=start, to_date=end, data_start_date=current().settings.data_start_date
        )
    except ValueError as exc:
        data = {"error": str(exc)}
    return _wrap(conn, data)


def get_digest(conn: sqlite3.Connection, to_date: str | None = None) -> dict[str, Any]:
    """Build and return the cited digest for a horizon (default: latest mart day)."""
    return _wrap(conn, _digest_for(conn, to_date))


def get_recent_activities(conn: sqlite3.Connection, n: int = 10) -> dict[str, Any]:
    """Return the n most recent activities, newest first, as a compact projection.

    An activity dated today carries ``partial_today: True`` - its training-effect
    numbers may still settle as Garmin finishes processing, so they are not final.
    """
    cur = conn.execute(
        f"SELECT {', '.join(_ACTIVITY_COLUMNS)} FROM activities ORDER BY start_local DESC LIMIT ?",
        (n,),
    )
    today = dt.date.today().isoformat()
    rows = _rows(cur)
    for row in rows:
        row.update(activity.markers(conn, row["activity_id"]))
        if row.get("date") == today:
            row["partial_today"] = True
    return _wrap(conn, rows)


def get_activity_laps(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Read saved laps and descriptive interval facts, without contacting Garmin."""
    return _wrap(conn, activity.read(conn, activity_id))


def get_weekly(conn: sqlite3.Connection, week_start: str | None = None) -> dict[str, Any]:
    """Return weekly mart rows plus the plan-vs-actual grid for those weeks."""
    if week_start is None:
        weeks = _rows(conn.execute("SELECT * FROM weekly_metrics ORDER BY week_start"))
        plan_actual = _rows(
            conn.execute("SELECT * FROM weekly_plan_actual ORDER BY week_start, date")
        )
    else:
        weeks = _rows(
            conn.execute("SELECT * FROM weekly_metrics WHERE week_start = ?", (week_start,))
        )
        plan_actual = _rows(
            conn.execute(
                "SELECT * FROM weekly_plan_actual WHERE week_start = ? ORDER BY date",
                (week_start,),
            )
        )
    for day in plan_actual:
        if day["planned"] is None:
            day["matched"] = None
    return _wrap(conn, {"weeks": weeks, "plan_actual": plan_actual})


def get_zones(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the current athlete_zones row (anchor, bounds, paces, staleness)."""
    rows = _rows(conn.execute("SELECT * FROM athlete_zones WHERE id = 1"))
    return _wrap(conn, rows[0] if rows else None)


def get_plan(conn: sqlite3.Connection, week_start: str | None = None) -> dict[str, Any]:
    """Return the resolved plan of record for a week (default: the current week).

    Each day carries its ``source``: ``plan_week`` when the athlete authored the
    week, ``plan_template`` when the repeating template answered for it. A week
    with ``has_plan: False`` is unplanned - the template is a shape, not a plan
    the athlete agreed to (issue #21).
    """
    week_start = week_start or _current_monday()
    error = plan.week_start_error(week_start)
    if error is not None:
        return _wrap(conn, {"error": error})
    data = {
        "week_start": week_start,
        "has_plan": plan.has_override(conn, week_start),
        "days": plan.resolve_week(conn, week_start),
        "error": None,
    }
    return _wrap(conn, data)


def _current_monday() -> str:
    today = dt.date.today()
    return (today - dt.timedelta(days=today.weekday())).isoformat()


def get_recommendation(conn: sqlite3.Connection, date: str | None = None) -> dict[str, Any]:
    """Return the session recommendation block targeting ``date`` (default: tomorrow).

    Mirrors the author path: the digest horizon is the day before the target,
    and the digest's embedded recommendation block is returned as-is.
    """
    to_date = _day_before(date) if date is not None else None
    dg = _digest_for(conn, to_date)
    return _wrap(conn, dg.get("recommendation"))


def get_events(conn: sqlite3.Connection, today: str | None = None) -> dict[str, Any]:
    """Return the goal races annotated with countdowns and the anchor flag."""
    day = today or dt.date.today().isoformat()
    return _wrap(conn, periodize.annotate(db.list_goal_events(conn), day))


def get_workout_status(
    conn: sqlite3.Connection,
    date: str,
    *,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Wrap the shared dated-workout get workout status outcome."""
    return _wrap(
        conn, push.get_workout_status(conn, date, connect=connect, reports_dir=reports_dir)
    )


# --- action tools (local writes, transport, workout push) -------------------


def log_rpe(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    rpe: int,
    soreness: int | None = None,
    mood: int | None = None,
    note: str | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Report saved session effort and its refresh outcome with freshness."""
    return _wrap(
        conn,
        athlete_changes.log_session_rpe(
            conn,
            activity_id=activity_id,
            rpe=rpe,
            soreness=soreness,
            mood=mood,
            note=note,
            data_start_date=data_start_date,
        ).as_data(),
    )


def log_niggle(
    conn: sqlite3.Connection,
    *,
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Report a saved complaint with freshness; no mart refresh is required."""
    return _wrap(
        conn,
        athlete_changes.log_niggle(
            conn, body_part=body_part, severity=severity, date=date, note=note
        ).as_data(),
    )


def log_sets(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    stations: list[str | manual_sets.ManualStationDetails],
    data_start_date: str,
) -> dict[str, Any]:
    """Report the shared manual-overlay outcome with freshness."""
    return _wrap(
        conn,
        athlete_changes.log_activity_sets(
            conn,
            activity_id=activity_id,
            stations=stations,
            data_start_date=data_start_date,
        ).as_data(),
    )


def log_treadmill_speed(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    binding: str,
    speed_kmh: float | None = None,
    repetitions: list[treadmill.SpeedReport] | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Save a complete reported-speed set for reviewed work, with its refresh outcome."""
    return _wrap(
        conn,
        athlete_changes.log_treadmill_speed(
            conn,
            activity_id=activity_id,
            binding=binding,
            speed_kmh=speed_kmh,
            repetitions=repetitions,
            data_start_date=data_start_date,
        ).as_data(),
    )


def clear_treadmill_speed(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    data_start_date: str,
) -> dict[str, Any]:
    """Remove the manual console report without requiring a still-valid work binding."""
    return _wrap(
        conn,
        athlete_changes.clear_treadmill_speed(
            conn,
            activity_id=activity_id,
            data_start_date=data_start_date,
        ).as_data(),
    )


def refresh_today(
    conn: sqlite3.Connection,
    client: GarminClient,
    *,
    data_start_date: str,
    today: str | None = None,
) -> dict[str, Any]:
    """Run the same-day refresh (issue #8) and report its status.

    Thin wrapper over ``daily.run_refresh_today`` - the one MCP tool that
    triggers Garmin transport (a read). Watermarks are never advanced.
    """
    result = daily.run_refresh_today(client, conn, data_start_date=data_start_date, today=today)
    data = {
        "status": result.status,
        "features_ok": result.features_ok,
        "warnings": list(result.sync.warnings) if result.sync else [],
        "errors": list(result.errors),
    }
    return _wrap(conn, data)


# --- gap repair: read the gap from the DB, then re-pull a bounded range (#72)

# How wide a range one repair may pull. Wide enough for a holiday's worth of days a
# watch uploaded late, narrow enough that it can never become a second backfill.
REPAIR_MAX_DAYS = 14

# The daily streams a preview reports on; their tables are core's to name.
_REPAIR_STREAMS = ("sleep", "hrv", "wellness", "readiness")


def repair_preview(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    data_start_date: str,
) -> dict[str, Any]:
    """Show what the DB holds for each day of a range, and hand over a confirm token.

    Transport-free: this is the read that decides whether a repair is worth it, so it
    must never be the call that contacts Garmin. ``repair_confirm`` is what pulls.

    The token covers the range *and* the per-day state, so a nightly run that fills
    the gap between preview and confirm makes the token stale rather than letting a
    pull happen against a picture that has moved.
    """
    error = _repair_range_error(from_date, to_date, data_start_date)
    if error is not None:
        return _wrap(conn, {"days": None, "confirm_token": None, "error": error})

    days = [_repair_day(conn, date) for date in _repair_dates(from_date, to_date)]
    data = {
        "from_date": from_date,
        "to_date": to_date,
        "days": days,
        "confirm_token": repair_token(from_date, to_date, days),
        "error": None,
    }
    return _wrap(conn, data)


def _repair_dates(from_date: str, to_date: str) -> list[str]:
    start, end = dt.date.fromisoformat(from_date), dt.date.fromisoformat(to_date)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def _repair_day(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """What one day holds: stored activities, which daily streams answered, what was planned."""
    planned = plan.resolve_day(conn, date)
    day: dict[str, Any] = {
        "date": date,
        "activities": db.count_activities(conn, date),
        "planned": planned["intent"] if planned else None,
        "plan_source": planned["source"] if planned else None,
    }
    for stream in _REPAIR_STREAMS:
        day[stream] = db.has_daily_row(conn, db.DAILY_STREAM_TABLES[stream], date)
    day["observations"] = recorded_observations.day_summary(conn, date)
    return day


def _repair_range_error(from_date: str, to_date: str, data_start_date: str) -> str | None:
    """Why this range may not be repaired, in the athlete's terms, or None."""
    try:
        start = dt.date.fromisoformat(from_date)
        end = dt.date.fromisoformat(to_date)
    except ValueError:
        return f"dates must be YYYY-MM-DD (got {from_date!r} .. {to_date!r})"
    if end < start:
        return f"from_date must be on or before to_date (got {from_date} .. {to_date})"
    if end >= dt.date.today():
        return "the repair is for finished days; today is what refresh_today is for"
    if (end - start).days + 1 > REPAIR_MAX_DAYS:
        return (
            f"a repair covers at most {REPAIR_MAX_DAYS} days "
            f"(got {(end - start).days + 1}); repeat it for an older range"
        )
    if from_date < data_start_date:
        return f"there is no real data before {data_start_date} (got {from_date})"
    return None


def repair_token(from_date: str, to_date: str, days: list[dict[str, Any]]) -> str:
    """The token gating preview -> confirm: the range plus what each day held."""
    payload = json.dumps(
        {"from": from_date, "to": to_date, "days": days}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def repair_refusal(
    conn: sqlite3.Connection,
    *,
    from_date: str,
    to_date: str,
    confirm_token: str,
    data_start_date: str,
) -> str | None:
    """Why this repair may not run, or None when it may.

    Transport-free and checked before the caller logs in, so a bad range or a token
    from a picture that has moved costs no Garmin call at all (ADR 0028).
    """
    error = _repair_range_error(from_date, to_date, data_start_date)
    if error is not None:
        return error
    days = [_repair_day(conn, date) for date in _repair_dates(from_date, to_date)]
    if confirm_token != repair_token(from_date, to_date, days):
        return (
            "stale confirm_token: the range, or what the DB holds for it, changed "
            "since the preview; run repair_preview again"
        )
    return None


def repair_confirm(
    conn: sqlite3.Connection,
    client: GarminClient,
    *,
    from_date: str,
    to_date: str,
    confirm_token: str,
    data_start_date: str,
) -> dict[str, Any]:
    """Re-pull a previewed range from Garmin and rebuild the marts over it.

    The second tool that reaches Garmin for a date the caller chose (ADR 0028). It
    pulls the range whole, exactly as ``backfill`` does, so a day that was missing
    only one stream is completed too; watermarks are never written, so the nightly
    run's own re-check window is unaffected.

    A mismatched token is refused without touching the account: the range, or what
    the DB held for it, changed since the preview (or no preview happened).
    """
    error = repair_refusal(
        conn,
        from_date=from_date,
        to_date=to_date,
        confirm_token=confirm_token,
        data_start_date=data_start_date,
    )
    if error is not None:
        return _wrap(conn, {"applied": False, "error": error})

    days = _repair_dates(from_date, to_date)
    before = db.count_activities(conn, from_date, to_date)
    try:
        imported = sync.backfill(client, conn, from_date, to_date)
    except Exception as exc:  # noqa: BLE001 - a tool reports; it never raises out of MCP
        return _wrap(conn, {"applied": False, "error": f"repair failed: {exc}"})

    data = {
        "applied": True,
        "from_date": from_date,
        "to_date": to_date,
        "days": len(days),
        "activities_stored": db.count_activities(conn, from_date, to_date) - before,
        "features_ok": False,
        "observation_warnings": imported.warnings,
        "complete": not imported.warnings,
        "error": "some observation reads remain incomplete" if imported.warnings else None,
    }
    try:
        features.rebuild_marts(conn, data_start_date=data_start_date)
        data["features_ok"] = True
    except Exception as exc:  # noqa: BLE001 - the pull landed; say the rebuild did not
        data["error"] = f"data pulled, but the mart rebuild failed: {exc}"
        data["recovery_command"] = "garmin-coach features"
    return _wrap(conn, data)


def author_workout(
    conn: sqlite3.Connection,
    date: str,
    request: dict[str, Any] | None = None,
    sport: str | None = None,
    reuse_from: str | None = None,
    *,
    reports_dir: str,
) -> dict[str, Any]:
    """Prepare or repeat a workout through the shared dated-workout flow."""
    return _wrap(
        conn,
        push.author_workout(
            conn,
            date,
            request=request,
            sport=sport,
            reuse_from=reuse_from,
            reports_dir=reports_dir,
            recheck_days=_recheck_days(),
        ),
    )


# --- goal events and plan import: the writers that keep the plan correct (#72)


def event_add(
    conn: sqlite3.Connection,
    *,
    date: str,
    type: str,  # noqa: A002 - mirrors the goal_event column and the CLI flag
    priority: str,
    status: str,
    date_precision: str,
    target: str | None = None,
    note: str | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Report the shared goal-event change with freshness."""
    return _wrap(
        conn,
        athlete_changes.event_add(
            conn,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
            data_start_date=data_start_date,
        ).as_data(),
    )


def event_update(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    date: str | None = None,
    type: str | None = None,  # noqa: A002 - mirrors the goal_event column and the CLI flag
    priority: str | None = None,
    status: str | None = None,
    date_precision: str | None = None,
    target: str | None = None,
    note: str | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Report the shared goal-event change with freshness."""
    return _wrap(
        conn,
        athlete_changes.event_update(
            conn,
            event_id=event_id,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
            data_start_date=data_start_date,
        ).as_data(),
    )


def plan_import(
    conn: sqlite3.Connection,
    *,
    plans_dir: str,
    reports_dir: str,
    week: str | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Re-read the authored plan files from chat, then rebuild the marts.

    The counterpart of a hand edit to ``plans/<monday>_week.md``: the file stays the
    plan of record (ADR 0015) and this refreshes the cache and everything derived
    from it. ``invalidated_pushes`` names the days whose already-pushed workout the
    edited plan no longer allows (issue #22).
    """
    result = plan_changes.import_plans(
        conn,
        plans_dir=plans_dir,
        reports_dir=reports_dir,
        week=week,
        data_start_date=data_start_date,
    )
    if not result.weeks and result.error is None:
        return _wrap(conn, _import_error(f"no plan file for {week or 'any week'} in {plans_dir}"))
    return _wrap(conn, result.as_data())


def _import_error(message: str) -> dict[str, Any]:
    return {"weeks": [], "invalidated_pushes": [], "error": message}


def plan_preview(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    days: list[dict[str, Any]],
    plans_dir: str,
) -> dict[str, Any]:
    """Validate a proposed week and show it back; nothing is written.

    The coach composes ``days`` (seven ``{planned, intent}`` dicts, Monday-Sunday)
    from the reads it already has; this side only checks the deterministic
    contract - the intent vocabulary, the Monday, seven days - and dates the rows.
    Show the result to the athlete: ``plan_confirm`` is what writes it.
    """
    resolved, error = _validate_proposal(week_start, days, plans_dir)
    if error is not None:
        return _wrap(conn, {"week_start": week_start, "days": None, "error": error})
    return _wrap(conn, {"week_start": week_start, "days": resolved, "error": None})


def plan_confirm(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    days: list[dict[str, Any]],
    plans_dir: str,
    reports_dir: str,
    data_start_date: str,
) -> dict[str, Any]:
    """Write a previewed week to ``plans/<monday>_week.md``, cache it, rebuild the marts.

    Refuses when a plan of record for the week already exists - the file carries
    prose the intent vocabulary cannot hold (paces, HR caps, the revision log), so
    revising an authored week stays a manual edit + re-import (issue #21). The
    written file goes back through the same parser as a hand-written plan, so
    there is exactly one ingestion path.

    ``invalidated_pushes`` names the days of the confirmed week whose already-pushed
    workout the new plan no longer allows (issue #22) - the write succeeded, and
    those days need re-authoring.
    """
    resolved, error = _validate_proposal(week_start, days, plans_dir)
    if error is not None:
        return _wrap(conn, {"week_start": week_start, "written": False, "error": error})

    try:
        path = plan.write_week_file(plans_dir, week_start, days)
    except FileExistsError as exc:
        # A tool reports; it never raises out of the MCP call. Validation already
        # ran, so reaching here means the plans/ directory changed under us.
        return _wrap(conn, {"week_start": week_start, "written": False, "error": str(exc)})
    result = plan_changes.import_plans(
        conn,
        plans_dir=plans_dir,
        reports_dir=reports_dir,
        week=week_start,
        data_start_date=data_start_date,
    )
    data = {
        "week_start": week_start,
        "written": True,
        "path": str(path),
        "days": resolved,
        **result.as_data(),
    }
    return _wrap(conn, data)


def _validate_proposal(
    week_start: str, days: list[dict[str, Any]], plans_dir: str
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate a proposed week and date its rows for display; ``core.plan`` owns the rules.

    Including the already-authored check, so a preview never shows a plan that
    confirm would refuse.
    """
    error = plan.validate_days(week_start, days, plans_dir)
    if error is not None:
        return None, error

    monday = dt.date.fromisoformat(week_start)
    resolved = [
        {
            "date": (monday + dt.timedelta(days=i)).isoformat(),
            "dow": i,
            "planned": d["planned"],
            "intent": d["intent"],
            "source": "plan_week",
        }
        for i, d in enumerate(days)
    ]
    return resolved, None


def get_pushed_workouts(
    conn: sqlite3.Connection, *, reports_dir: str, since: str | None = None
) -> dict[str, Any]:
    """Wrap the shared dated-workout get pushed workouts outcome."""
    return _wrap(conn, push.get_pushed_workouts(conn, reports_dir=reports_dir, since=since))


def push_preview(
    conn: sqlite3.Connection,
    *,
    date: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Wrap the shared dated-workout push preview outcome."""
    return _wrap(conn, push.push_preview(conn, date=date, connect=connect, reports_dir=reports_dir))


def push_confirm(
    conn: sqlite3.Connection,
    *,
    date: str,
    confirm_token: str,
    connect: Callable[[], publish.WorkoutPublisher],
    replace: bool = False,
    reports_dir: str,
) -> dict[str, Any]:
    """Wrap the shared dated-workout push confirm outcome."""
    return _wrap(
        conn,
        push.push_confirm(
            conn,
            date=date,
            confirm_token=confirm_token,
            connect=connect,
            replace=replace,
            reports_dir=reports_dir,
        ),
    )


# --- taking a pushed workout off a day (issue #74) --------------------------


def unschedule_preview(
    conn: sqlite3.Connection,
    *,
    date: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Wrap the shared dated-workout unschedule preview outcome."""
    return _wrap(
        conn, push.unschedule_preview(conn, date=date, connect=connect, reports_dir=reports_dir)
    )


def unschedule_confirm(
    conn: sqlite3.Connection,
    *,
    date: str,
    confirm_token: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Wrap the shared dated-workout unschedule confirm outcome."""
    return _wrap(
        conn,
        push.unschedule_confirm(
            conn, date=date, confirm_token=confirm_token, connect=connect, reports_dir=reports_dir
        ),
    )
