"""Own dated workouts and their saved evidence for terminal and conversation.

Authoring, reuse, push, reconciliation, listing, removal and plan-conflict checks
share one owner for workout.json and push.json. Pure authoring and account policy
remain in their modules; adapters supply presentation, consent and lazy transport.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..coach import digest, thresholds
from ..core import db, plan
from ..core.config import DEFAULT_RECHECK_DAYS
from . import author, publish


class PushRefused(ValueError):
    """Local validation refused a push before connecting to the account."""


@dataclass(frozen=True)
class PushOutcome:
    """The resolved publish result, its token, and the receipt a confirmed push wrote."""

    result: publish.PublishResult
    confirm_token: str
    receipt_path: pathlib.Path | None = None


def load_spec(date: str, reports_dir: str | pathlib.Path) -> dict[str, Any]:
    """Read a workout filed under its own target date.

    Args:
        date: Requested target day in ISO format.
        reports_dir: Root of the dated report folders.

    Returns:
        The authored workout spec for that day.

    Raises:
        PushRefused: The date or file is invalid, missing, or targets another day.
    """
    try:
        if dt.date.fromisoformat(date).isoformat() != date:
            raise ValueError("expected YYYY-MM-DD")
    except ValueError as exc:
        raise PushRefused(f"invalid push date {date!r}; expected YYYY-MM-DD") from exc
    path = pathlib.Path(reports_dir) / date / "workout.json"
    spec = _read_json(path)
    if spec is None:
        raise PushRefused(f"no workout.json for {date}; author a workout for that day first")
    if spec.get("date") != date:
        raise PushRefused(
            f"workout.json under {date} targets {spec.get('date')}; "
            "re-author it for the date you mean to push"
        )
    return spec


def push_for_date(
    conn: sqlite3.Connection,
    *,
    date: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str | pathlib.Path,
    confirm: bool = False,
    expected_token: str | None = None,
    replace: bool = False,
) -> PushOutcome:
    """Resolve or execute a day's push and record confirmed outcomes.

    Args:
        conn: Open connection containing the finished data and plan cache.
        date: Requested target day; must agree with the filed spec.
        connect: Publisher factory, called only after local validation.
        reports_dir: Root of the dated workout specs and receipts.
        confirm: Explicit terminal confirmation or a confirmed chat request.
        expected_token: Chat's required preview token; terminal calls omit it.
        replace: Allow replacing a different account workout.

    Returns:
        The publish result, a token for the current workout, date, and plan, and
        the receipt path when a confirmed push reached the account.

    Raises:
        PushRefused: The spec is invalid or the supplied token is stale.
        Exception: A publisher connection or local receipt write failed.
    """
    spec = load_spec(date, reports_dir)
    planned = plan.planned_intent(conn, date)
    token = publish.confirm_token(spec, planned)
    if expected_token is not None and expected_token != token:
        raise PushRefused(
            "stale confirm_token: the spec, its date, or the plan of record "
            "for it changed since the preview; run push_preview again"
        )
    day_dir = pathlib.Path(reports_dir) / date
    known_workout_id = receipt_workout_id(day_dir)
    result = publish.publish(
        spec,
        connect(),
        confirm=confirm,
        replace=replace,
        activity_dates={date} if db.has_activity_on(conn, date) else set(),
        known_workout_id=known_workout_id,
        planned_intent=planned,
    )
    receipt_path = None
    if confirm and (result.applied or result.error is not None):
        receipt_path = _write_receipt(day_dir, result)
    return PushOutcome(result=result, confirm_token=token, receipt_path=receipt_path)


def _write_receipt(day_dir: pathlib.Path, result: publish.PublishResult) -> pathlib.Path:
    """Record a confirmed push's outcome beside its spec and return the receipt path."""
    receipt = result.as_receipt()
    receipt["pushed_at"] = dt.datetime.now().isoformat(timespec="seconds")
    path = day_dir / "push.json"
    path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return path


def author_workout(
    conn: sqlite3.Connection,
    date: str,
    request: dict[str, Any] | None = None,
    sport: str | None = None,
    reuse_from: str | None = None,
    *,
    reports_dir: str,
    recheck_days: int = DEFAULT_RECHECK_DAYS,
) -> dict[str, Any]:
    """Author a workout spec for a date and write ``workout.json``.

    Mirrors the CLI author path: without ``request`` the spec comes from the
    recommendation targeting ``date`` (its intent picking the sport unless an
    explicit ``sport`` overrides it); with one, the request dict (athlete or
    hybrid, including a custom ``structure``) is authored as-is. With
    ``reuse_from``, that day's spec is repeated on this one, guarded by this day's
    plan of record (issue #58).
    """
    try:
        _day_dir(reports_dir, date)
    except PushRefused as exc:
        return {"spec": None, "error": str(exc)}
    if request is not None and not isinstance(request, dict):
        return {"spec": None, "error": "workout request must be a JSON object"}
    if reuse_from is not None:
        if request is not None or sport is not None:
            return {"spec": None, "error": "reuse_from cannot be combined with request or sport"}
        return _repeat_spec(conn, date, reuse_from, reports_dir)
    try:
        spec = _build_spec(conn, date, request, sport, recheck_days)
    except (author.HyroxSplitRequired, ValueError) as exc:
        return {"spec": None, "error": str(exc)}
    if spec is None:
        return {"spec": None, "error": None, "note": "rest - nothing to author"}
    path = _write_spec(spec, date, reports_dir)
    return {"spec": spec, "error": None, "path": str(path)}


def _build_spec(
    conn: sqlite3.Connection,
    date: str,
    request: dict[str, Any] | None,
    sport: str | None,
    recheck_days: int,
) -> dict[str, Any] | None:
    """Build a request against the target day's stored recommendation and plan."""
    horizon = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    dg = digest.build_digest(
        conn, to_date=horizon, thresholds=thresholds.read(conn), recheck_days=recheck_days
    )
    recommendation = dg.get("recommendation")
    if request is not None:
        request = dict(request)
        request["date"] = date
    else:
        if recommendation is None:
            raise ValueError(f"no recommendation for {date}; run features first")
        request = author.request_from_recommendation(recommendation, sport=sport)
    context = {
        "zones": dg.get("zones"),
        "today": dt.date.today().isoformat(),
        "recommendation": recommendation,
        "planned_intent": plan.planned_intent(conn, date),
    }
    return author.author(request, context)


def _repeat_spec(
    conn: sqlite3.Connection, date: str, reuse_from: str, reports_dir: str
) -> dict[str, Any]:
    """Copy the spec authored for one day onto another, then write it there."""
    try:
        source = load_spec(reuse_from, reports_dir)
    except PushRefused as exc:
        return {"spec": None, "error": str(exc)}
    try:
        spec = author.copy_to_date(
            source,
            date=date,
            planned_intent=plan.planned_intent(conn, date),
            today=dt.date.today().isoformat(),
        )
    except ValueError as exc:
        return {"spec": None, "error": str(exc)}
    return {"spec": spec, "error": None, "path": str(_write_spec(spec, date, reports_dir))}


def _write_spec(spec: dict[str, Any], date: str, reports_dir: str) -> pathlib.Path:
    """Write a spec into its day's report directory."""
    out_dir = _day_dir(reports_dir, date)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "workout.json"
    path.write_text(json.dumps(spec, indent=2))
    return path


def _read_json(path: pathlib.Path) -> dict[str, Any] | None:
    """Read evidence without treating corruption or permission failures as absence."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise PushRefused(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PushRefused(f"invalid {path}: expected a JSON object")
    if path.name == "push.json":
        _validate_receipt(value, path)
    if path.name == "workout.json":
        _validate_spec(value, path)
    return value


def _validate_spec(spec: dict[str, Any], path: pathlib.Path) -> None:
    """Validate saved workout content locally before it can cause an account lookup."""
    if spec.get("date") != path.parent.name:
        raise PushRefused(
            f"workout.json under {path.parent.name} targets {spec.get('date')}; "
            "re-author it for the date you mean to push"
        )
    try:
        author.to_garmin(spec)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise PushRefused(f"invalid {path}: {exc}") from exc


def _validate_receipt(receipt: dict[str, Any], path: pathlib.Path) -> None:
    """Refuse unusable account identity or stored reconciliation, preserving old fields."""
    workout_id = receipt.get("workout_id")
    if workout_id is not None and (type(workout_id) is not int or workout_id <= 0):
        raise PushRefused(f"invalid {path}: workout_id must be a positive integer or null")
    if receipt.get("reconciled") is not None and not isinstance(receipt["reconciled"], dict):
        raise PushRefused(f"invalid {path}: reconciled must be an object or null")
    if receipt.get("date") is not None and receipt["date"] != path.parent.name:
        raise PushRefused(f"invalid {path}: receipt targets another date")


def receipt_workout_id(day_dir: pathlib.Path | str) -> int | None:
    """Return a saved account identity, refusing unreadable evidence before login."""
    receipt = _read_json(pathlib.Path(day_dir) / "push.json")
    return receipt.get("workout_id") if receipt is not None else None


def get_workout_status(
    conn: sqlite3.Connection,
    date: str,
    *,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
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
        conn: The finished DB, for the plan of record.
        date: The day whose workout is being asked about.
        connect: Builds the Garmin read surface, called only when a receipt names a
            workout to check - so a date with no push never logs in.
        reports_dir: Root of the per-day report artifacts.

    Returns:
        The ``date``/``workout``/``push``/``reconciled``/``plan_divergence``
        block; the last two are None when there is no receipt to check.
    """
    try:
        day_dir = _day_dir(reports_dir, date)
        push = _read_json(day_dir / "push.json")
        workout = _read_json(day_dir / "workout.json")
    except PushRefused as exc:
        return {
            "date": date,
            "workout": None,
            "push": None,
            "reconciled": None,
            "plan_divergence": None,
            "error": str(exc),
        }
    finding = publish.reconcile(connect, push, workout, date)
    _persist_finding(day_dir, push, finding)
    data = {
        "date": date,
        "workout": workout,
        "push": _receipt_view(push),
        "reconciled": finding.as_finding() if finding is not None else None,
        "plan_divergence": publish.plan_divergence(
            _with_finding(push, finding), workout, plan.planned_intent(conn, date)
        ),
    }
    return data


def get_pushed_workouts(
    conn: sqlite3.Connection, *, reports_dir: str, since: str | None = None
) -> dict[str, Any]:
    """The workouts already pushed, newest first, from the receipts on disk.

    Transport-free: the receipts are the listing, so finding the session the athlete
    means ("the FBB A from the 19th") costs nothing and works offline. ``last_state``
    is what the last status read found on the account, when one ran.
    """
    if since is not None:
        try:
            _day_dir(reports_dir, since)
        except PushRefused as exc:
            return {"workouts": [], "warnings": [], "error": str(exc)}
    root = pathlib.Path(reports_dir)
    workouts = []
    warnings = []
    for path in sorted(root.glob("*/push.json"), reverse=True):
        if since is not None and path.parent.name < since:
            continue
        try:
            _day_dir(reports_dir, path.parent.name)
            summary = _pushed_summary(path)
        except PushRefused as exc:
            warnings.append(str(exc))
            continue
        if summary is not None:
            workouts.append(summary)
    return {"workouts": workouts, "warnings": warnings, "error": None}


def push_preview(
    conn: sqlite3.Connection,
    *,
    date: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
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
    try:
        outcome = push_for_date(conn, date=date, connect=connect, reports_dir=reports_dir)
    except PushRefused as exc:
        return {"error": str(exc)}
    result = outcome.result
    data = {
        "date": date,
        "action": result.action,
        "confirm_token": outcome.confirm_token,
        "spec_hash": result.spec_hash,
        "payload": result.payload,
        "message": result.message,
        "warnings": result.warnings,
        "error": None,
    }
    return data


def push_confirm(
    conn: sqlite3.Connection,
    *,
    date: str,
    confirm_token: str,
    connect: Callable[[], publish.WorkoutPublisher],
    replace: bool = False,
    reports_dir: str,
) -> dict[str, Any]:
    """Execute the push for a date, gated on the token from ``push_preview``.

    A mismatched token is refused without touching the account - the spec, its date,
    or the plan of record for it changed since the preview (or no preview happened),
    so preview again.
    """
    try:
        outcome = push_for_date(
            conn,
            date=date,
            connect=connect,
            reports_dir=reports_dir,
            confirm=True,
            expected_token=confirm_token,
            replace=replace,
        )
    except PushRefused as exc:
        return {"error": str(exc), "applied": False}
    result = outcome.result
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
    return data


def unschedule_preview(
    conn: sqlite3.Connection,
    *,
    date: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Show what taking the coach's workout off a day would remove, and hand over a token.

    The day's push receipt names the workout; the account says whether it is still
    there and on the day. The account is read because the refusals need it - a
    workout deleted or moved in Connect is refused with a plain reason, never guessed
    at - and a token must never be handed over for a removal that cannot happen.
    ``connect`` is called only once the receipt names a workout, so a day the coach
    never pushed costs no login.
    """
    try:
        receipt, error = _pushed_receipt(_day_dir(reports_dir, date), date)
    except PushRefused as exc:
        receipt, error = None, str(exc)
    if receipt is None:
        return {"date": date, "action": None, "confirm_token": None, "error": error}
    result = _unschedule(connect(), date, receipt, confirm=False)
    return {**_unschedule_view(result), "confirm_token": result.confirm_token}


def unschedule_confirm(
    conn: sqlite3.Connection,
    *,
    date: str,
    confirm_token: str,
    connect: Callable[[], publish.WorkoutPublisher],
    reports_dir: str,
) -> dict[str, Any]:
    """Take the previewed workout off the day's calendar, gated on the preview's token.

    Removes that day's calendar entries for the workout and nothing else: the workout
    stays in the library and on every other day it is on (ADR 0029). The outcome is
    appended to the receipt beside the push's own fields, so a later offline read sees
    the day as off the watch. A mismatched token is refused without touching the
    calendar.
    """
    try:
        day_dir = _day_dir(reports_dir, date)
        receipt, error = _pushed_receipt(day_dir, date)
    except PushRefused as exc:
        receipt, error = None, str(exc)
    if receipt is None:
        return {"date": date, "action": None, "applied": False, "error": error}
    result = _unschedule(connect(), date, receipt, confirm=True, confirm_token=confirm_token)
    if result.applied:
        _record_unschedule(day_dir, receipt, result)
    return {**_unschedule_view(result), "applied": result.applied}


def _with_finding(push: Any, finding: publish.Reconciliation | None) -> Any:
    """The receipt as this read leaves it, so the divergence check reads the fresh finding.

    A workout the coach took off the day silences the divergence read until a finding
    says it is back on the calendar (issue #74); that finding must count on the read
    that made it, not one read later. An unverified read replaces nothing, exactly as
    it persists nothing.
    """
    if not isinstance(push, dict) or finding is None or finding.state == publish.UNVERIFIED:
        return push
    return {**push, "reconciled": finding.as_finding()}


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


def _day_dir(reports_dir: str, date: str) -> pathlib.Path:
    """The per-day report directory holding a date's spec and receipt."""
    try:
        if dt.date.fromisoformat(date).isoformat() != date:
            raise ValueError("expected YYYY-MM-DD")
    except (ValueError, TypeError) as exc:
        raise PushRefused(f"invalid workout date {date!r}; expected YYYY-MM-DD") from exc
    return pathlib.Path(reports_dir) / date


def _pushed_summary(receipt_path: pathlib.Path) -> dict[str, Any] | None:
    """Read one listing row; missing files are absent, damaged evidence is refused."""
    receipt = _read_json(receipt_path)
    if receipt is None:
        return None
    reconciled = receipt.get("reconciled") or {}
    return {
        "date": receipt_path.parent.name,
        "name": receipt.get("name"),
        "workout_id": receipt.get("workout_id"),
        "action": receipt.get("action"),
        "applied": receipt.get("applied"),
        "session_type": receipt.get("session_type"),
        "pushed_at": receipt.get("pushed_at"),
        "unscheduled_at": receipt.get("unscheduled_at"),
        "last_state": reconciled.get("state"),
    }


def _unschedule(
    publisher: publish.WorkoutPublisher,
    date: str,
    receipt: dict[str, Any],
    *,
    confirm: bool,
    confirm_token: str | None = None,
) -> publish.UnscheduleResult:
    """Run the removal for the workout a receipt names; ``publish`` owns the rules."""
    return publish.unschedule(
        publisher,
        date=date,
        workout_id=receipt["workout_id"],
        pushed_name=receipt.get("name"),
        confirm=confirm,
        confirm_token=confirm_token,
    )


def _pushed_receipt(day_dir: pathlib.Path, date: str) -> tuple[dict[str, Any] | None, str | None]:
    """The day's push receipt when it names a workout on the account, or why it does not."""
    try:
        receipt = _read_json(day_dir / "push.json")
        _read_json(day_dir / "workout.json")
    except PushRefused as exc:
        return None, f"the saved evidence for {date} cannot be read: {exc}"
    if not isinstance(receipt, dict):
        return (
            None,
            f"no push receipt for {date}: the coach put no workout on this day, so there is nothing to take off (a workout scheduled by hand is removed in Garmin Connect)",
        )
    if receipt.get("workout_id") is None:
        return (
            None,
            f"the push for {date} never reached the account (its receipt names no workout); nothing to take off",
        )
    return (receipt, None)


def _unschedule_view(result: publish.UnscheduleResult) -> dict[str, Any]:
    """The removal as both tools report it; the confirm adds ``applied``, the preview the token."""
    return {
        "date": result.date,
        "action": result.action,
        "workout_id": result.workout_id,
        "name": result.name,
        "renamed_to": result.renamed_to,
        "schedule_ids": result.schedule_ids,
        "message": result.message,
        "error": result.error,
    }


def _record_unschedule(
    day_dir: pathlib.Path, receipt: dict[str, Any], result: publish.UnscheduleResult
) -> None:
    """Append the removal to the receipt; the push's own fields stay as written (ADR 0014).

    ``unscheduled_at`` is the event and ``reconciled`` the state it leaves: the first is
    what the divergence read and the listing key on, the second what an offline status
    read serves as ``last_known``. A later push rewrites the receipt whole and takes both
    with it - a new push is a new event.
    """
    updated = {
        **receipt,
        "unscheduled_at": dt.datetime.now().isoformat(timespec="seconds"),
        "reconciled": result.finding().as_finding(),
    }
    (day_dir / "push.json").write_text(json.dumps(updated, indent=2))


@dataclass(frozen=True)
class PlanCheck:
    """Known plan conflicts and dated evidence failures, neither implying the other."""

    conflicts: list[dict[str, Any]]
    warnings: list[str]


def invalidated_pushes(
    reports_dir: pathlib.Path | str, planned_by_date: dict[str, str | None]
) -> PlanCheck:
    """Check saved push evidence against a new plan, reporting unjudgeable days."""
    conflicts = []
    warnings = []
    for date in sorted(planned_by_date):
        try:
            day = _day_dir(str(reports_dir), date)
            receipt = _read_json(day / "push.json")
            spec = _read_json(day / "workout.json")
            divergence = publish.plan_divergence(receipt, spec, planned_by_date[date])
        except PushRefused as exc:
            warnings.append(f"cannot assess plan conflict for {date}: {exc}")
            continue
        if divergence is not None:
            conflicts.append({"date": date, **divergence})
    return PlanCheck(conflicts, warnings)
