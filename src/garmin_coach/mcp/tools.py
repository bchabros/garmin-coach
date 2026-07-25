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
publisher, so a date that was never pushed costs no login at all.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import cli, daily
from ..coach import digest, report
from ..core import db, plan
from ..etl.sync import GarminClient
from ..marts import periodize, snapshot
from ..workouts import author, publish

# Mart fields that accumulate during the day; they are only final after the
# nightly run. Morning-complete streams (sleep, HRV, readiness) never appear.
PARTIAL_INTRADAY_FIELDS = (
    "load_day",
    "acute7",
    "chronic28",
    "acwr",
    "load_low",
    "load_high",
    "load_anaerobic",
    "load_strength",
    "z1_min",
    "z2_min",
    "z3_min",
    "z4_min",
    "z5_min",
    "rhr",
    "rhr_delta",
    "stress_avg",
    "bb_min",
    "bb_max",
    "bb_recharge",
)

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


def _freshness(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build the freshness envelope from the mart horizon vs the actual today."""
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    data_through = row[0] if row else None
    today_included = data_through == dt.date.today().isoformat()
    return {
        "data_through": data_through,
        "today_included": today_included,
        "partial_fields": list(PARTIAL_INTRADAY_FIELDS) if today_included else [],
    }


def _wrap(conn: sqlite3.Connection, data: Any) -> dict[str, Any]:
    return {"data": data, "freshness": _freshness(conn)}


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _digest_for(conn: sqlite3.Connection, to_date: str | None = None) -> dict[str, Any]:
    """Build the cited digest for a horizon with the stored thresholds."""
    thresholds = report.read_thresholds(conn)
    return digest.build_digest(conn, to_date=to_date, thresholds=thresholds)


def _day_before(date: str) -> str:
    """The digest horizon for a target date: the day before it."""
    return (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()


def get_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the athlete_status snapshot row (None until features has run)."""
    return _wrap(conn, snapshot.read(conn))


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
        if row.get("date") == today:
            row["partial_today"] = True
    return _wrap(conn, rows)


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
    reports_dir: str = "reports",
) -> dict[str, Any]:
    """Return the authored spec, the push receipt, and the receipt reconciled with Garmin.

    The receipt records what a push did; it is never presented as what the account
    holds now (issue #41). ``reconciled`` is the fresh account-side finding and sits
    beside the untouched receipt, because "we pushed it and it worked" stays true
    even after the athlete deletes the workout.

    Args:
        conn: The finished DB, for the freshness envelope.
        date: The day whose workout is being asked about.
        connect: Builds the Garmin read surface, called only when a receipt names a
            workout to check - so a date with no push never logs in.
        reports_dir: Root of the per-day report artifacts.

    Returns:
        The wrapped ``date``/``workout``/``push``/``reconciled`` block; ``reconciled``
        is None when there is no receipt to check.
    """
    day_dir = pathlib.Path(reports_dir) / date
    push = _read_json(day_dir / "push.json")
    workout = _read_json(day_dir / "workout.json")
    data = {
        "date": date,
        "workout": workout,
        "push": push,
        "reconciled": publish.reconcile(connect, push, workout, date),
    }
    return _wrap(conn, data)


def _read_json(path: pathlib.Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


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
    """Log a session RPE from chat; recomputes the affected day's blended load."""
    try:
        date = cli.log_session_rpe(
            conn,
            activity_id=activity_id,
            rpe=rpe,
            soreness=soreness,
            mood=mood,
            note=note,
            data_start_date=data_start_date,
        )
    except ValueError as exc:
        return _wrap(conn, {"error": str(exc)})
    return _wrap(conn, {"activity_id": activity_id, "rpe": rpe, "date": date, "error": None})


def log_niggle(
    conn: sqlite3.Connection,
    *,
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Log a niggle (a sub-injury complaint) from chat."""
    try:
        day = cli.log_niggle(conn, body_part=body_part, severity=severity, date=date, note=note)
    except ValueError as exc:
        return _wrap(conn, {"error": str(exc)})
    return _wrap(conn, {"body_part": body_part, "severity": severity, "date": day, "error": None})


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


def author_workout(
    conn: sqlite3.Connection,
    date: str,
    request: dict[str, Any] | None = None,
    sport: str | None = None,
    reports_dir: str = "reports",
) -> dict[str, Any]:
    """Author a workout spec for a date and write ``workout.json``.

    Mirrors the CLI author path: without ``request`` the spec comes from the
    recommendation targeting ``date`` (its intent picking the sport unless an
    explicit ``sport`` overrides it); with one, the request dict (athlete or
    hybrid, including a custom ``structure``) is authored as-is.
    """
    dg = _digest_for(conn, _day_before(date))
    recommendation = dg.get("recommendation")

    if request is not None:
        request = dict(request)
        request["date"] = date
    else:
        if recommendation is None:
            return _wrap(
                conn,
                {"spec": None, "error": f"no recommendation for {date}; run features first"},
            )
        request = author.request_from_recommendation(recommendation, sport=sport)

    context = {
        "zones": dg.get("zones"),
        "today": dt.date.today().isoformat(),
        "recommendation": recommendation,
    }
    try:
        spec = author.author(request, context)
    except (author.HyroxSplitRequired, ValueError) as exc:
        return _wrap(conn, {"spec": None, "error": str(exc)})

    if spec is None:
        return _wrap(conn, {"spec": None, "error": None, "note": "rest - nothing to author"})

    out_dir = pathlib.Path(reports_dir) / date
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "workout.json"
    path.write_text(json.dumps(spec, indent=2))
    return _wrap(conn, {"spec": spec, "error": None, "path": str(path)})


def plan_preview(
    conn: sqlite3.Connection,
    *,
    week_start: str,
    days: list[dict[str, Any]],
    plans_dir: str = "plans",
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
    plans_dir: str = "plans",
) -> dict[str, Any]:
    """Write a previewed week to ``plans/<monday>_week.md`` and cache it.

    Refuses when a plan of record for the week already exists - the file carries
    prose the intent vocabulary cannot hold (paces, HR caps, the revision log), so
    revising an authored week stays a manual edit + re-import (issue #21). The
    written file goes back through the same parser as a hand-written plan, so
    there is exactly one ingestion path.
    """
    resolved, error = _validate_proposal(week_start, days, plans_dir)
    if error is not None:
        return _wrap(conn, {"week_start": week_start, "written": False, "error": error})

    try:
        path = plan.write_week_file(plans_dir, week_start, days)
        plan.import_dir(conn, plans_dir, week=week_start)
    except (FileExistsError, plan.PlanParseError) as exc:
        # A tool reports; it never raises out of the MCP call. Validation already
        # ran, so reaching here means the plans/ directory changed under us.
        return _wrap(conn, {"week_start": week_start, "written": False, "error": str(exc)})
    data = {
        "week_start": week_start,
        "written": True,
        "path": str(path),
        "days": resolved,
        "error": None,
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


def push_preview(
    conn: sqlite3.Connection,
    *,
    date: str,
    publisher: publish.WorkoutPublisher,
    reports_dir: str = "reports",
) -> dict[str, Any]:
    """Dry-run the push for a date: the resolved action, payload, and confirm token.

    The returned ``confirm_token`` is what ``push_confirm`` requires; it covers the
    workout *and* its date, so a push can only follow a preview the caller displayed.
    ``spec_hash`` is the separate account-side idempotency marker, shown for reference.
    """
    spec, error = _load_spec(date, reports_dir)
    if spec is None:
        return _wrap(conn, {"error": error})

    result = publish.publish(
        spec, publisher, confirm=False, activity_dates=_dates_with_activity(conn, date)
    )
    data = {
        "date": date,
        "action": result.action,
        "confirm_token": publish.confirm_token(spec),
        "spec_hash": result.spec_hash,
        "payload": result.payload,
        "message": result.message,
        "warnings": result.warnings,
        "error": None,
    }
    return _wrap(conn, data)


def push_confirm(
    conn: sqlite3.Connection,
    *,
    date: str,
    confirm_token: str,
    publisher: publish.WorkoutPublisher,
    replace: bool = False,
    reports_dir: str = "reports",
) -> dict[str, Any]:
    """Execute the push for a date, gated on the token from ``push_preview``.

    A mismatched token is refused without touching the account - the spec or its
    date changed since the preview (or no preview happened), so preview again.
    """
    spec, error = _load_spec(date, reports_dir)
    if spec is None:
        return _wrap(conn, {"error": error, "applied": False})

    if confirm_token != publish.confirm_token(spec):
        return _wrap(
            conn,
            {
                "error": "stale confirm_token: the spec or its date changed since the "
                "preview; run push_preview again",
                "applied": False,
            },
        )

    result = publish.publish(
        spec,
        publisher,
        confirm=True,
        replace=replace,
        activity_dates=_dates_with_activity(conn, date),
    )
    if result.applied or result.error is not None:
        _write_receipt(result, pathlib.Path(reports_dir) / date)
    data = {
        "date": date,
        "action": result.action,
        "applied": result.applied,
        "workout_id": result.workout_id,
        "schedule_id": result.schedule_id,
        "message": result.message,
        "warnings": result.warnings,
        "error": result.error,
    }
    return _wrap(conn, data)


def _load_spec(date: str, reports_dir: str) -> tuple[dict[str, Any] | None, str | None]:
    """Read the authored spec for a date, or explain why there is none.

    The folder names the target day, while ``publish`` schedules on the spec's own
    ``date``; a disagreement would push a different day than the one whose activity
    collision was checked, so it is refused rather than silently preferred.
    """
    path = pathlib.Path(reports_dir) / date / "workout.json"
    if not path.exists():
        return None, f"no workout.json for {date}; run author_workout first"
    spec = json.loads(path.read_text())
    if spec.get("date") != date:
        return None, (
            f"workout.json under {date} targets {spec.get('date')}; "
            "re-author it for the date you mean to push"
        )
    return spec, None


def _dates_with_activity(conn: sqlite3.Connection, date: str) -> set[str]:
    """The target date, when it already carries a logged activity in core."""
    row = conn.execute("SELECT 1 FROM activities WHERE date = ? LIMIT 1", (date,)).fetchone()
    return {date} if row else set()


def _write_receipt(result: publish.PublishResult, out_dir: pathlib.Path) -> None:
    """Write the push.json receipt, stamped with the push time."""
    receipt = result.as_receipt()
    receipt["pushed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    (out_dir / "push.json").write_text(json.dumps(receipt, indent=2))
