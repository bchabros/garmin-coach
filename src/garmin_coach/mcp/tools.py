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
import hashlib
import json
import pathlib
import sqlite3
from collections.abc import Callable
from typing import Any

from .. import cli, daily
from ..coach import digest, report
from ..core import db, events, manual_sets, plan
from ..core.config import get_settings
from ..etl import sync
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
    """Build the freshness envelope from the mart horizon vs the actual today.

    ``unconfirmed_days`` rides along on every response, not only on the digest: the
    weekly review that misread a not-yet-uploaded run as a rest day was written from
    the weekly read (issue #72).
    """
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    data_through = row[0] if row else None
    today_included = data_through == dt.date.today().isoformat()
    return {
        "data_through": data_through,
        "today_included": today_included,
        "partial_fields": list(PARTIAL_INTRADAY_FIELDS) if today_included else [],
        "unconfirmed_days": plan.unconfirmed_days(
            conn, window_days=get_settings().sync_recheck_days
        ),
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
    """Return the authored spec, the push receipt, the account finding, and the plan check.

    The receipt records what a push did; it is never presented as what the account
    holds now (issue #41). ``reconciled`` is the fresh account-side finding and sits
    beside the untouched receipt, because "we pushed it and it worked" stays true
    even after the athlete deletes the workout.

    ``plan_divergence`` answers the other question a receipt cannot: whether the plan
    of record still allows what is on the watch (issue #22). It needs no account read -
    the plan lives in the DB - so it is reported even when Garmin is unreachable.

    Args:
        conn: The finished DB, for the plan of record and the freshness envelope.
        date: The day whose workout is being asked about.
        connect: Builds the Garmin read surface, called only when a receipt names a
            workout to check - so a date with no push never logs in.
        reports_dir: Root of the per-day report artifacts.

    Returns:
        The wrapped ``date``/``workout``/``push``/``reconciled``/``plan_divergence``
        block; the last two are None when there is no receipt to check.
    """
    day_dir = _day_dir(reports_dir, date)
    push = _read_json(day_dir / "push.json")
    workout = _read_json(day_dir / "workout.json")
    finding = publish.reconcile(connect, push, workout, date)
    _persist_finding(day_dir, push, finding)
    data = {
        "date": date,
        "workout": workout,
        "push": _receipt_view(push),
        "reconciled": finding.as_finding() if finding is not None else None,
        "plan_divergence": publish.plan_divergence(push, workout, plan.planned_intent(conn, date)),
    }
    return _wrap(conn, data)


def _receipt_view(push: Any) -> Any:
    """The receipt as a caller should read it: the push record, without the stored finding.

    The receipt's own fields - what the push did - are returned exactly as they sit on
    disk. The stored finding is the one key dropped: it is persisted so an offline read
    can still serve it, but the response reports it once, under ``reconciled``, and two
    copies in one response invite reading the stale one.
    """
    return {k: v for k, v in push.items() if k != "reconciled"} if isinstance(push, dict) else push


def _persist_finding(
    day_dir: pathlib.Path, push: Any, finding: publish.Reconciliation | None
) -> None:
    """Record a changed finding on the receipt, so a later read is not thrown back on it.

    Only a state change writes: rewriting on every read would churn ``checked_at``
    with no new information. An unverified read never writes - absence of information
    is not information, and would erase a known-missing workout. The receipt's own
    fields are never touched; the finding is appended beside them.
    """
    if not isinstance(push, dict) or finding is None or finding.state == publish.UNVERIFIED:
        return
    known = push.get("reconciled")
    if isinstance(known, dict) and known.get("state") == finding.state:
        return
    receipt = {**push, "reconciled": finding.as_finding()}
    (day_dir / "push.json").write_text(json.dumps(receipt, indent=2))


def _read_json(path: pathlib.Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _day_dir(reports_dir: str, date: str) -> pathlib.Path:
    """The per-day report directory holding a date's spec and receipt."""
    return pathlib.Path(reports_dir) / date


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


def log_sets(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    stations: list[str | manual_sets.ManualStationDetails],
    data_start_date: str,
) -> dict[str, Any]:
    """Log a circuit's stations from chat (issue #60); recomputes from that day.

    The station list is what the box published, in order - bare Garmin names or
    ``{subcategory, reps?, sets?, duration_s?, max_weight?}`` mappings. Names the
    movement map does not know come back as ``unmapped`` so the chat can say the
    overlap read is partial for them.
    """
    try:
        out = cli.log_activity_sets(
            conn, activity_id=activity_id, stations=stations, data_start_date=data_start_date
        )
    except ValueError as exc:
        return _wrap(conn, {"error": str(exc)})
    return _wrap(conn, {"activity_id": activity_id, **out, "error": None})


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

# The daily streams a preview reports on, by the core table that holds them.
_REPAIR_STREAMS = {
    "sleep": "sleep",
    "hrv": "hrv_nightly",
    "wellness": "daily_wellness",
    "readiness": "training_readiness",
}


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
    activities = conn.execute(
        "SELECT count(*) FROM activities WHERE date(start_local) = ?", (date,)
    ).fetchone()[0]
    planned = plan.resolve_day(conn, date)
    day: dict[str, Any] = {
        "date": date,
        "activities": activities,
        "planned": planned["intent"] if planned else None,
        "plan_source": planned["source"] if planned else None,
    }
    for stream, table in _REPAIR_STREAMS.items():
        row = conn.execute(f"SELECT 1 FROM {table} WHERE date = ? LIMIT 1", (date,)).fetchone()
        day[stream] = row is not None
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
    error = _repair_range_error(from_date, to_date, data_start_date)
    if error is not None:
        return _wrap(conn, {"applied": False, "error": error})

    days = [_repair_day(conn, date) for date in _repair_dates(from_date, to_date)]
    if confirm_token != repair_token(from_date, to_date, days):
        return _wrap(
            conn,
            {
                "applied": False,
                "error": "stale confirm_token: the range, or what the DB holds for it, "
                "changed since the preview; run repair_preview again",
            },
        )

    before = _stored_activities(conn, from_date, to_date)
    try:
        sync.backfill(client, conn, from_date, to_date)
    except Exception as exc:  # noqa: BLE001 - a tool reports; it never raises out of MCP
        return _wrap(conn, {"applied": False, "error": f"repair failed: {exc}"})

    data = {
        "applied": True,
        "from_date": from_date,
        "to_date": to_date,
        "days": len(days),
        "activities_stored": _stored_activities(conn, from_date, to_date) - before,
        "features_ok": False,
        "error": None,
    }
    try:
        cli.rebuild_marts(conn, data_start_date=data_start_date)
        data["features_ok"] = True
    except Exception as exc:  # noqa: BLE001 - the pull landed; say the rebuild did not
        data["error"] = f"data pulled, but the mart rebuild failed: {exc}"
    return _wrap(conn, data)


def _stored_activities(conn: sqlite3.Connection, from_date: str, to_date: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM activities WHERE date(start_local) BETWEEN ? AND ?",
        (from_date, to_date),
    ).fetchone()
    return int(row[0])



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
        "planned_intent": plan.planned_intent(conn, date),
    }
    try:
        spec = author.author(request, context)
    except (author.HyroxSplitRequired, ValueError) as exc:
        return _wrap(conn, {"spec": None, "error": str(exc)})

    if spec is None:
        return _wrap(conn, {"spec": None, "error": None, "note": "rest - nothing to author"})

    out_dir = _day_dir(reports_dir, date)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "workout.json"
    path.write_text(json.dumps(spec, indent=2))
    return _wrap(conn, {"spec": spec, "error": None, "path": str(path)})


# --- goal events and plan import: the writers that keep the plan correct (#72)


def _current_block(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The block calendar's row for the week that holds today, or None without an anchor."""
    return periodize.current_plan(conn, dt.date.today().isoformat())


def _event_row(conn: sqlite3.Connection, match: Callable[[dict[str, Any]], bool]):
    """The recorded race the write touched, annotated as ``event list`` shows it."""
    rows = periodize.annotate(db.list_goal_events(conn), dt.date.today().isoformat())
    return next((row for row in rows if match(row)), None)


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
    """Record a goal race from chat and re-date the periodization.

    Reports the block calendar's row for the current week before and after the
    write, so the athlete sees what the race did to the plan rather than being
    told a row was inserted.
    """
    before = _current_block(conn)
    try:
        events.add_goal_event(
            conn,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
        )
    except ValueError as exc:
        return _wrap(conn, _event_error(str(exc)))
    cli.rebuild_marts(conn, data_start_date=data_start_date)
    row = _event_row(conn, lambda e: e["date"] == date and e["type"] == type)
    return _wrap(conn, _event_result(row, before, _current_block(conn)))


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
    """Change a recorded race from chat (pin a date, commit a start) and re-date the plan.

    Fields left unset keep their value. Reports the block calendar's row for the
    current week before and after the write.
    """
    before = _current_block(conn)
    try:
        events.update_goal_event(
            conn,
            event_id,
            date=date,
            type=type,
            priority=priority,
            status=status,
            date_precision=date_precision,
            target=target,
            note=note,
        )
    except ValueError as exc:
        return _wrap(conn, _event_error(str(exc)))
    cli.rebuild_marts(conn, data_start_date=data_start_date)
    row = _event_row(conn, lambda e: e["id"] == event_id)
    return _wrap(conn, _event_result(row, before, _current_block(conn)))


def _event_error(message: str) -> dict[str, Any]:
    """A refused race write: nothing was written, so no block moved."""
    return {"event": None, "block_before": None, "block_after": None, "error": message}


def _event_result(
    row: dict[str, Any] | None, before: dict[str, Any] | None, after: dict[str, Any] | None
) -> dict[str, Any]:
    return {"event": row, "block_before": before, "block_after": after, "error": None}


def plan_import(
    conn: sqlite3.Connection,
    *,
    plans_dir: str = "plans",
    reports_dir: str = "reports",
    week: str | None = None,
    data_start_date: str,
) -> dict[str, Any]:
    """Re-read the authored plan files from chat, then rebuild the marts.

    The counterpart of a hand edit to ``plans/<monday>_week.md``: the file stays the
    plan of record (ADR 0015) and this refreshes the cache and everything derived
    from it. ``invalidated_pushes`` names the days whose already-pushed workout the
    edited plan no longer allows (issue #22).
    """
    try:
        imported = plan.import_dir(conn, plans_dir, week=week)
    except plan.PlanParseError as exc:
        return _wrap(conn, _import_error(str(exc)))
    if not imported:
        return _wrap(
            conn, _import_error(f"no plan file for {week or 'any week'} in {plans_dir}")
        )

    cli.rebuild_marts(conn, data_start_date=data_start_date)
    conflicts = [
        conflict
        for imported_week in imported
        for conflict in publish.invalidated_pushes(
            reports_dir, plan.planned_by_date(conn, imported_week)
        )
    ]
    return _wrap(conn, {"weeks": imported, "invalidated_pushes": conflicts, "error": None})


def _import_error(message: str) -> dict[str, Any]:
    return {"weeks": [], "invalidated_pushes": [], "error": message}


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
    reports_dir: str = "reports",
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
        plan.import_dir(conn, plans_dir, week=week_start)
    except (FileExistsError, plan.PlanParseError) as exc:
        # A tool reports; it never raises out of the MCP call. Validation already
        # ran, so reaching here means the plans/ directory changed under us.
        return _wrap(conn, {"week_start": week_start, "written": False, "error": str(exc)})
    cli.rebuild_marts(conn, data_start_date=data_start_date)
    data = {
        "week_start": week_start,
        "written": True,
        "path": str(path),
        "days": resolved,
        "invalidated_pushes": publish.invalidated_pushes(
            reports_dir, plan.planned_by_date(conn, week_start)
        ),
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
    workout, its date, *and* the plan of record for that date, so a push can only
    follow a preview the caller displayed and the plan it was measured against.
    ``spec_hash`` is the separate account-side idempotency marker, shown for reference.

    A spec harder than the plan of record resolves to ``refuse`` here rather than at
    confirm time: a plan revised after the spec was authored is exactly the case the
    author-time guard cannot see (issue #22).
    """
    spec, error = _load_spec(date, reports_dir)
    if spec is None:
        return _wrap(conn, {"error": error})

    planned = plan.planned_intent(conn, date)
    result = publish.publish(
        spec,
        publisher,
        confirm=False,
        activity_dates=_dates_with_activity(conn, date),
        known_workout_id=publish.receipt_workout_id(_day_dir(reports_dir, date)),
        planned_intent=planned,
    )
    data = {
        "date": date,
        "action": result.action,
        "confirm_token": publish.confirm_token(spec, planned),
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

    A mismatched token is refused without touching the account - the spec, its date,
    or the plan of record for it changed since the preview (or no preview happened),
    so preview again.
    """
    spec, error = _load_spec(date, reports_dir)
    if spec is None:
        return _wrap(conn, {"error": error, "applied": False})

    planned = plan.planned_intent(conn, date)
    if confirm_token != publish.confirm_token(spec, planned):
        return _wrap(
            conn,
            {
                "error": "stale confirm_token: the spec, its date, or the plan of record "
                "for it changed since the preview; run push_preview again",
                "applied": False,
            },
        )

    result = publish.publish(
        spec,
        publisher,
        confirm=True,
        replace=replace,
        activity_dates=_dates_with_activity(conn, date),
        known_workout_id=publish.receipt_workout_id(_day_dir(reports_dir, date)),
        planned_intent=planned,
    )
    if result.applied or result.error is not None:
        _write_receipt(result, _day_dir(reports_dir, date))
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
    path = _day_dir(reports_dir, date) / "workout.json"
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
