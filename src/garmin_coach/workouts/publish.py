"""Workout publish: the outbound transport that pushes a workout spec to Garmin.

This is the only module in the system that writes to Garmin, and it bends the
golden rule exactly like ``client.py``/``sync.py`` do for reads (see ADR 0013).
The orchestration depends on an injected ``WorkoutPublisher`` protocol - not a
concrete client - so idempotency, the confirm interlock, and the receipt are all
tested offline against a fake, and the live wrapper is wired in a later ticket.

Idempotency uses the Garmin account as the source of truth, never a local ledger:
each system-authored workout carries a hash of its canonical spec in the workout
``description`` (``gc-hash:...``), so a re-push compares against what the account
actually holds. ``author`` owns the pure spec; ``publish`` reads it and calls
``author.to_garmin`` to build the payload. ``author`` never imports ``publish``.

The account state resolves to one action: create, no-op, schedule (a library-only
match), refuse (a changed workout without ``--replace``), or replace (unschedule +
delete + re-push). A schedule that fails after a successful upload is left for the
next idempotent push to complete - never rolled back.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import author as _author

logger = logging.getLogger(__name__)

# The workout ``description`` tag that carries the canonical-spec hash on the account.
_HASH_PREFIX = "gc-hash:"


class WorkoutPublisher(Protocol):
    """The Garmin write surface the orchestration depends on (injected, mockable)."""

    def list_workouts(self) -> list[dict[str, Any]]:
        """Return the account's workout library (each with ``workoutId``/``workoutName``)."""
        ...

    def get_workout(self, workout_id: int) -> dict[str, Any]:
        """Return one workout in full, including its steps (the listing omits them)."""
        ...

    def upload(self, payload: dict[str, Any]) -> int:
        """Upload a workout payload to the library; return its new ``workoutId``."""
        ...

    def schedule(self, workout_id: int, date: str) -> int:
        """Schedule a library workout to a date; return the new schedule id."""
        ...

    def unschedule(self, schedule_id: int) -> None:
        """Remove a scheduled-workout calendar entry."""
        ...

    def delete(self, workout_id: int) -> None:
        """Delete a workout from the library."""
        ...

    def list_scheduled(self, date: str) -> list[dict[str, Any]]:
        """Return the calendar entries on a date (each with ``scheduleId``/``workoutId``)."""
        ...


@dataclass
class PublishResult:
    """The outcome of a publish attempt - the plan, whether it was applied, and ids."""

    action: str
    applied: bool
    spec_hash: str
    date: str
    payload: dict[str, Any]
    message: str
    workout_id: int | None = None
    schedule_id: int | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    def as_receipt(self) -> dict[str, Any]:
        """The ``push.json`` receipt body (the caller adds the push timestamp)."""
        return {
            "action": self.action,
            "applied": self.applied,
            "name": self.payload["workoutName"],
            "date": self.date,
            "workout_id": self.workout_id,
            "schedule_id": self.schedule_id,
            "spec_hash": self.spec_hash,
            "error": self.error,
            "warnings": self.warnings,
        }


def publish(
    spec: dict[str, Any],
    publisher: WorkoutPublisher,
    *,
    confirm: bool,
    replace: bool = False,
    activity_dates: frozenset[str] | set[str] = frozenset(),
) -> PublishResult:
    """Push a workout spec to Garmin, idempotently and behind a confirm interlock.

    Args:
        spec: The finished workout spec from ``author``.
        publisher: The injected Garmin write surface.
        confirm: When False (the default caller state), plan only and touch nothing.
        replace: Overwrite a different workout of the same name (unschedule + delete +
            upload + schedule) instead of refusing.
        activity_dates: Dates that already have a logged activity, to warn on collision.

    Returns:
        A ``PublishResult`` describing the resolved action and, when confirmed, the
        ids it created (or the partial state and error when scheduling failed).
    """
    date = spec["date"]
    marker = spec_hash(spec)
    payload = _author.to_garmin(spec)
    payload["description"] = f"{_HASH_PREFIX}{marker}"

    warnings = list(spec.get("warnings", []))
    if date in activity_dates:
        warnings.append(f"{date} already has a logged activity; is this the right date?")

    existing = _find_by_name(publisher, spec["name"])
    action = _resolve_action(existing, marker, publisher, date, replace)
    result = PublishResult(
        action=action,
        applied=False,
        spec_hash=marker,
        date=date,
        payload=payload,
        message=_message(action),
        warnings=warnings,
    )
    if existing is not None:
        result.workout_id = existing["workoutId"]

    if not confirm or action in ("refuse", "noop"):
        result.applied = action == "noop"
        return result

    return _execute(result, spec, payload, publisher, existing)


def _resolve_action(
    existing: dict[str, Any] | None,
    spec_hash: str,
    publisher: WorkoutPublisher,
    date: str,
    replace: bool,
) -> str:
    """Decide create / replace / refuse / noop / schedule from the account's state."""
    if existing is None:
        return "create"
    if _existing_hash(existing) != spec_hash:
        return "replace" if replace else "refuse"
    scheduled_ids = {entry["workoutId"] for entry in publisher.list_scheduled(date)}
    if existing["workoutId"] in scheduled_ids:
        return "noop"
    return "schedule"


def _execute(
    result: PublishResult,
    spec: dict[str, Any],
    payload: dict[str, Any],
    publisher: WorkoutPublisher,
    existing: dict[str, Any] | None,
) -> PublishResult:
    """Apply the resolved action, recording a partial state if scheduling fails.

    Scheduling failure is not rolled back: the uploaded (but unscheduled) workout is a
    harmless orphan the next idempotent push completes (see ADR 0013).
    """
    if result.action == "replace":
        assert existing is not None
        _unschedule_existing(publisher, existing["workoutId"], spec["date"])
        publisher.delete(existing["workoutId"])
        result.workout_id = publisher.upload(payload)
    elif result.action == "create":
        result.workout_id = publisher.upload(payload)
    elif result.action == "schedule":
        assert existing is not None
        result.workout_id = existing["workoutId"]

    assert result.workout_id is not None
    try:
        result.schedule_id = publisher.schedule(result.workout_id, spec["date"])
    except Exception as exc:  # noqa: BLE001 - any transport failure records a partial push
        result.error = str(exc)
        result.applied = False
        result.message = (
            f"uploaded to the library as workout {result.workout_id}, but scheduling "
            "failed; re-run push to complete (no duplicate is created)"
        )
        return result
    result.applied = True
    return result


def _unschedule_existing(publisher: WorkoutPublisher, workout_id: int, date: str) -> None:
    """Remove any calendar entries for a workout on a date before replacing it."""
    for entry in publisher.list_scheduled(date):
        if entry["workoutId"] == workout_id:
            publisher.unschedule(entry["scheduleId"])


def _find_by_name(publisher: WorkoutPublisher, name: str) -> dict[str, Any] | None:
    """The account's workout with this exact name, or None."""
    for workout in publisher.list_workouts():
        if workout.get("workoutName") == name:
            return workout
    return None


def reconcile(
    connect: Callable[[], WorkoutPublisher], receipt: Any, spec: Any, date: str
) -> dict[str, Any] | None:
    """Resolve a push receipt against the account's library and calendar (issue #41).

    A receipt records what a push did; only the account can say what became of it.
    Keyed on the receipt's ``workout_id`` alone - the question is whether what was
    pushed is still there, not whether something like it is - so a workout the
    library no longer holds is ``missing`` even with a near-identical one beside it.

    Args:
        connect: Builds the Garmin read surface. Called only once there is something
            to check, so a receipt-free date never logs in; any failure it raises
            leaves the receipt ``unverified``.
        receipt: The parsed ``push.json`` body, or None when the date has no push.
        spec: The authored spec, used to tell an edit from a rename (issue #42).
        date: The day whose calendar decides ``live`` against ``unscheduled``.

    Returns:
        The finding, or None when the receipt names no workout to check.
    """
    workout_id = receipt.get("workout_id") if isinstance(receipt, dict) else None
    if workout_id is None:
        return None
    try:
        publisher = connect()
        entry = _library_entry(publisher, workout_id)
        if entry is None:
            return _finding("missing", scheduled=False)
        scheduled = _is_scheduled(publisher, workout_id, date)
        steps_changed = _steps_changed(publisher, entry, receipt, spec)
    except Exception as exc:  # noqa: BLE001 - an unreachable account leaves it unverified
        logger.info("reconcile: %s unverified for workout %s: %s", date, workout_id, exc)
        return _unverified(receipt)
    return _finding(
        _state(scheduled, steps_changed),
        scheduled=scheduled,
        steps_changed=steps_changed,
        renamed_to=_renamed_to(entry, receipt),
    )


def _unverified(receipt: dict[str, Any]) -> dict[str, Any]:
    """The finding for a read that could not reach the account.

    Carries whatever the receipt last recorded under ``last_known``, so an offline
    read degrades to older information rather than to none. Nothing else is filled
    in: this read confirmed nothing, and stale facts must not read as fresh ones.
    """
    known = receipt.get("reconciled")
    finding = _finding("unverified")
    finding["last_known"] = known if isinstance(known, dict) else None
    return finding


def _state(scheduled: bool, steps_changed: bool | None) -> str:
    """Collapse the facts to the one state a caller branches on.

    ``unscheduled`` outranks ``edited``: a workout that is not on the day is not on
    the watch, whatever its steps now say. The edit stays visible in ``steps_changed``.
    """
    if not scheduled:
        return "unscheduled"
    return "edited" if steps_changed else "live"


def _finding(
    state: str,
    *,
    scheduled: bool | None = None,
    steps_changed: bool | None = None,
    renamed_to: str | None = None,
) -> dict[str, Any]:
    """One reconciliation finding: the state to branch on, plus the facts behind it."""
    return {
        "state": state,
        "scheduled": scheduled,
        "steps_changed": steps_changed,
        "renamed_to": renamed_to,
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _steps_changed(
    publisher: WorkoutPublisher, entry: dict[str, Any], receipt: dict[str, Any], spec: Any
) -> bool | None:
    """Whether the account's steps differ from the ones the receipt says were pushed.

    The ``gc-hash:`` tag cannot answer this: it records what was *pushed* and Garmin
    leaves the description alone when steps change, so it agrees with the receipt
    forever. Only the steps themselves can tell, and fetching them costs a call - so
    ``updateDate`` gates it, and an untouched account copy answers for free.

    Returns:
        None when it cannot be judged - the local spec no longer hashes to the
        receipt, so it is not evidence of what the push actually sent.
    """
    if not _touched_since_push(entry, receipt):
        return False
    if not isinstance(spec, dict) or spec_hash(spec) != receipt.get("spec_hash"):
        return None
    account = publisher.get_workout(entry["workoutId"])
    return _authored_shape(account) != _authored_shape(_author.to_garmin(spec))


def _touched_since_push(entry: dict[str, Any], receipt: dict[str, Any]) -> bool:
    """Whether the account's copy moved after the push that produced the receipt.

    An unreadable timestamp on either side counts as touched: checking properly
    costs one call, while assuming nothing moved would hide a real edit.
    """
    pushed_at = _timestamp(receipt.get("pushed_at"))
    updated = _timestamp(entry.get("updateDate"))
    if pushed_at is None or updated is None:
        return True
    return updated > pushed_at


def _timestamp(value: Any) -> dt.datetime | None:
    """Parse an ISO timestamp (Garmin's carries a fractional-second suffix), or None."""
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _authored_shape(payload: dict[str, Any]) -> list[Any]:
    """A workout's steps reduced to the fields this system authors.

    The account decorates every step with ids, units, and defaults no upload ever
    sent (``stepId``, ``weightValue``, ``strokeType``, ``endConditionCompare``), so
    comparing raw payloads always differs. This keeps only what was authored.
    """
    segments = payload.get("workoutSegments") or []
    steps = segments[0].get("workoutSteps") if segments else []
    return _authored_steps(steps)


def _authored_steps(steps: Any) -> list[Any]:
    """The authored shape of a step list, recursing into repeat groups."""
    return [_authored_step(step) for step in steps or []]


def _authored_step(step: dict[str, Any]) -> Any:
    """The authored shape of one step: a repeat group, or one executable step."""
    if step.get("type") == "RepeatGroupDTO":
        return ("repeat", step.get("numberOfIterations"), _authored_steps(step.get("workoutSteps")))
    return (
        _nested_key(step.get("stepType"), "stepTypeKey"),
        _nested_key(step.get("endCondition"), "conditionTypeKey"),
        _rounded(step.get("endConditionValue")),
        _nested_key(step.get("targetType"), "workoutTargetTypeKey"),
        _rounded(step.get("targetValueOne")),
        _rounded(step.get("targetValueTwo")),
        step.get("zoneNumber"),
        step.get("exerciseName"),
        _authored_weight(step.get("weightValue")),
    )


def _authored_weight(value: Any) -> float | None:
    """A step's authored weight, with the account's ``-1`` no-weight default read as none.

    Garmin stamps ``weightValue: -1`` on every step it returns, including the run
    steps no upload ever gave a weight to; taking it at face value would make every
    pushed run look edited.
    """
    rounded = _rounded(value)
    return None if rounded is None or rounded < 0 else rounded


def _nested_key(block: Any, field: str) -> Any:
    """One field out of a Garmin enum block, tolerating a missing block."""
    return block.get(field) if isinstance(block, dict) else None


def _rounded(value: Any) -> float | None:
    """A number rounded past the noise Garmin's float round-tripping introduces."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3)


def _library_entry(publisher: WorkoutPublisher, workout_id: int) -> dict[str, Any] | None:
    """The account's library entry for a workout id, or None when it is gone."""
    for workout in publisher.list_workouts():
        if workout.get("workoutId") == workout_id:
            return workout
    return None


def _is_scheduled(publisher: WorkoutPublisher, workout_id: int, date: str) -> bool:
    """Whether the workout holds a calendar entry on the date being asked about."""
    return any(entry["workoutId"] == workout_id for entry in publisher.list_scheduled(date))


def _renamed_to(entry: dict[str, Any], receipt: dict[str, Any]) -> str | None:
    """The account's current name when the athlete renamed the workout, else None.

    A receipt with no name recorded cannot evidence a rename, so it reports none
    rather than presenting the account's own name as a change.
    """
    pushed_as = receipt.get("name")
    current = entry.get("workoutName")
    if pushed_as is None or current == pushed_as:
        return None
    return current


def _existing_hash(workout: dict[str, Any]) -> str | None:
    """The canonical-spec hash tagged in a workout's description, or None."""
    description = workout.get("description") or ""
    if description.startswith(_HASH_PREFIX):
        return description[len(_HASH_PREFIX) :]
    return None


class GarminWorkoutPublisher:
    """The live ``WorkoutPublisher`` over an authenticated garminconnect client.

    Out-of-seam transport, isolated exactly like ``client.py`` and never unit-tested
    (network/auth); the orchestration is covered by the fake, and this wrapper's
    response-field extraction is settled by the manual live-push acceptance step in
    ``docs/OPERATIONS.md``.
    """

    def __init__(self, api: Any) -> None:
        self._api = api

    def list_workouts(self) -> list[dict[str, Any]]:
        """Return the account's workout library summaries."""
        return list(self._api.get_workouts())

    def get_workout(self, workout_id: int) -> dict[str, Any]:
        """Return one workout in full, including its steps."""
        return dict(self._api.get_workout_by_id(workout_id))

    def upload(self, payload: dict[str, Any]) -> int:
        """Upload a workout payload; return its new ``workoutId``."""
        created = self._api.upload_workout(payload)
        return int(created["workoutId"])

    def schedule(self, workout_id: int, date: str) -> int:
        """Schedule a library workout to a date; return the schedule id."""
        scheduled = self._api.schedule_workout(workout_id, date)
        return int(scheduled.get("workoutScheduleId") or scheduled["id"])

    def unschedule(self, schedule_id: int) -> None:
        """Remove a scheduled-workout calendar entry."""
        self._api.unschedule_workout(schedule_id)

    def delete(self, workout_id: int) -> None:
        """Delete a workout from the library."""
        self._api.delete_workout(workout_id)

    def list_scheduled(self, date: str) -> list[dict[str, Any]]:
        """Return the calendar entries on a date (each with ``scheduleId``/``workoutId``)."""
        year, month, _ = date.split("-")
        payload = self._api.get_scheduled_workouts(int(year), int(month))
        entries = payload.get("calendarItems", payload) if isinstance(payload, dict) else payload
        scheduled = []
        for item in entries or []:
            if item.get("date") == date and item.get("workoutId") is not None:
                scheduled.append(
                    {
                        "scheduleId": item.get("workoutScheduleId") or item.get("id"),
                        "workoutId": item["workoutId"],
                    }
                )
        return scheduled


def connect_publisher(settings: Any) -> WorkoutPublisher:
    """Log in and build the live Garmin write surface.

    Reuses ``client.login_api`` for authentication (token cache, MFA, retry-on-expired),
    then wraps the authenticated client in the write-side publisher.
    """
    from ..etl import client

    return GarminWorkoutPublisher(client.login_api(settings))


def _canonical_hash(fields: dict[str, Any]) -> str:
    """The stable short hash of a canonically serialized field set."""
    canonical = json.dumps(fields, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def spec_hash(spec: dict[str, Any]) -> str:
    """A stable short hash of the canonical spec (its name and steps).

    Deliberately date-free: this is the idempotency marker carried in the Garmin
    workout description, and rescheduling a workout must not make it look like a
    different one. For the preview/confirm handshake use :func:`confirm_token`.
    """
    return _canonical_hash({"name": spec["name"], "steps": spec["steps"]})


def confirm_token(spec: dict[str, Any]) -> str:
    """A token covering everything a preview showed and a confirm acts on.

    The date is included because it decides what the push schedules and which day
    the activity-collision check ran against: a spec retargeted between preview and
    confirm must invalidate the preview even though the workout itself is unchanged.
    """
    return _canonical_hash({"name": spec["name"], "steps": spec["steps"], "date": spec["date"]})


def _message(action: str) -> str:
    """A human summary line for a resolved action."""
    return {
        "create": "will create and schedule a new workout",
        "replace": "a different workout with this name exists; will replace and reschedule it",
        "noop": "already scheduled with an identical workout; nothing to do",
        "schedule": "workout exists in the library; will schedule it to the date",
        "refuse": "a different workout with this name exists; re-run with --replace to overwrite",
    }[action]
