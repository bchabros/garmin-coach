"""Push the requested day's workout, including local validation and its receipt.

CLI and MCP supply confirmation and a publisher factory; this module owns the
date, plan, activity collision, account lookup candidate, and durable outcome.
The existing publish policy owns account actions and never reads files or SQLite.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..core import plan
from . import publish


class PushRefused(ValueError):
    """Local validation refused a push before connecting to the account."""


@dataclass(frozen=True)
class PushOutcome:
    """The resolved publish result and the token covering its current inputs."""

    result: publish.PublishResult
    confirm_token: str


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
    if not path.exists():
        raise PushRefused(f"no workout.json for {date}; run author_workout first")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PushRefused(f"invalid workout.json for {date}: {exc}") from exc
    if not isinstance(spec, dict):
        raise PushRefused(f"workout.json for {date} must contain an object")
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
    reports_dir: str | pathlib.Path = "reports",
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
        The publish result and a token for the current workout, date, and plan.

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
    activity = conn.execute("SELECT 1 FROM activities WHERE date = ? LIMIT 1", (date,)).fetchone()
    result = publish.publish(
        spec,
        connect(),
        confirm=confirm,
        replace=replace,
        activity_dates={date} if activity else set(),
        known_workout_id=publish.receipt_workout_id(day_dir),
        planned_intent=planned,
    )
    if confirm and (result.applied or result.error is not None):
        receipt = result.as_receipt()
        receipt["pushed_at"] = dt.datetime.now().isoformat(timespec="seconds")
        (day_dir / "push.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return PushOutcome(result=result, confirm_token=token)
