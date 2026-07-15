"""Phase 11 publish: the outbound transport that pushes a workout spec to Garmin.

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

This ticket handles create / no-op / schedule / refuse; ``--replace`` and the
partial-failure retry arrive in the next ticket.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import author as _author

# The workout ``description`` tag that carries the canonical-spec hash on the account.
_HASH_PREFIX = "gc-hash:"


class WorkoutPublisher(Protocol):
    """The Garmin write surface the orchestration depends on (injected, mockable)."""

    def list_workouts(self) -> list[dict[str, Any]]:
        """Return the account's workout library (each with ``workoutId``/``workoutName``)."""
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

    def list_scheduled(self, date: str) -> list[int]:
        """Return the workout ids scheduled on a date."""
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
            "warnings": self.warnings,
        }


def publish(
    spec: dict[str, Any],
    publisher: WorkoutPublisher,
    *,
    confirm: bool,
    activity_dates: frozenset[str] | set[str] = frozenset(),
) -> PublishResult:
    """Push a workout spec to Garmin, idempotently and behind a confirm interlock.

    Args:
        spec: The finished workout spec from ``author``.
        publisher: The injected Garmin write surface.
        confirm: When False (the default caller state), plan only and touch nothing.
        activity_dates: Dates that already have a logged activity, to warn on collision.

    Returns:
        A ``PublishResult`` describing the resolved action and, when confirmed, the
        ids it created.
    """
    date = spec["date"]
    spec_hash = _spec_hash(spec)
    payload = _author.to_garmin(spec)
    payload["description"] = f"{_HASH_PREFIX}{spec_hash}"

    warnings = list(spec.get("warnings", []))
    if date in activity_dates:
        warnings.append(f"{date} already has a logged activity; is this the right date?")

    existing = _find_by_name(publisher, spec["name"])
    action = _resolve_action(existing, spec_hash, publisher, date)
    result = PublishResult(
        action=action,
        applied=False,
        spec_hash=spec_hash,
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
) -> str:
    """Decide create / refuse / noop / schedule from the account's current state."""
    if existing is None:
        return "create"
    if _existing_hash(existing) != spec_hash:
        return "refuse"
    if existing["workoutId"] in publisher.list_scheduled(date):
        return "noop"
    return "schedule"


def _execute(
    result: PublishResult,
    spec: dict[str, Any],
    payload: dict[str, Any],
    publisher: WorkoutPublisher,
    existing: dict[str, Any] | None,
) -> PublishResult:
    """Apply the resolved create or schedule action against the account."""
    if result.action == "create":
        result.workout_id = publisher.upload(payload)
        result.schedule_id = publisher.schedule(result.workout_id, spec["date"])
    elif result.action == "schedule":
        assert existing is not None
        result.workout_id = existing["workoutId"]
        result.schedule_id = publisher.schedule(result.workout_id, spec["date"])
    result.applied = True
    return result


def _find_by_name(publisher: WorkoutPublisher, name: str) -> dict[str, Any] | None:
    """The account's workout with this exact name, or None."""
    for workout in publisher.list_workouts():
        if workout.get("workoutName") == name:
            return workout
    return None


def _existing_hash(workout: dict[str, Any]) -> str | None:
    """The canonical-spec hash tagged in a workout's description, or None."""
    description = workout.get("description") or ""
    if description.startswith(_HASH_PREFIX):
        return description[len(_HASH_PREFIX):]
    return None


def connect_publisher(settings: Any) -> WorkoutPublisher:
    """Build the live Garmin write surface.

    The real wrapper over ``client.login`` is wired in a later ticket (the one that
    also runs the manual live-push acceptance and settles the response shapes). Until
    then this raises so the orchestration and CLI can be built and reviewed against the
    fake without a live account.

    Raises:
        NotImplementedError: Always, until the live wrapper ticket lands.
    """
    raise NotImplementedError("live workout transport is wired in a later ticket (see PRD 06)")


def _spec_hash(spec: dict[str, Any]) -> str:
    """A stable short hash of the canonical spec (its name and steps)."""
    canonical = json.dumps({"name": spec["name"], "steps": spec["steps"]}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _message(action: str) -> str:
    """A human summary line for a resolved action."""
    return {
        "create": "will create and schedule a new workout",
        "noop": "already scheduled with an identical workout; nothing to do",
        "schedule": "workout exists in the library; will schedule it to the date",
        "refuse": "a different workout with this name exists; re-run with --replace to overwrite",
    }[action]
