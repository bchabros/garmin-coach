"""Workout publish: the outbound transport that pushes a workout spec to Garmin.

This is the only module in the system that writes to Garmin, and it bends the
golden rule exactly like ``client.py``/``sync.py`` do for reads (see ADR 0013).
The orchestration depends on an injected ``WorkoutPublisher`` protocol - not a
concrete client - so idempotency, the confirm interlock, and the receipt are all
tested offline against a fake, and the live wrapper is wired in a later ticket.

Idempotency uses the Garmin account as the source of truth, never a local ledger:
each system-authored workout carries a hash of its canonical spec in the workout
``description`` (``gc-hash:...``), so a re-push compares against what the account
actually holds. The receipt may offer a ``known_workout_id`` as the first lookup
candidate (issue #40) - the athlete can rename a workout and edit its steps, which
defeats the other two keys at once - but the account still decides: an id it no
longer knows falls through to the hash, and then to the name. ``author`` owns the
pure spec; ``publish`` reads it and calls ``author.to_garmin`` to build the payload.
``author`` never imports ``publish``.

The same account reads answer the other direction too: :func:`reconcile` resolves a
push receipt against the library and the calendar, so a status read reports what the
account holds rather than what a past push claimed (issues #41-#43).

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
import pathlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core import plan as _plan
from . import author as _author

logger = logging.getLogger(__name__)

# The workout ``description`` tag that carries the canonical-spec hash on the account.
_HASH_PREFIX = "gc-hash:"

# The reconciliation states a status read resolves to (issue #41). Named rather than
# spelled out at each site: a caller branches on them, and a bare literal invites a typo
# that would read as an unknown state. Precedence is decided in :func:`_state`.
LIVE = "live"
EDITED = "edited"
UNSCHEDULED = "unscheduled"
MISSING = "missing"
UNVERIFIED = "unverified"


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
    session_type: str | None = None
    planned_intent: str | None = None

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
            "session_type": self.session_type,
            "planned_intent": self.planned_intent,
            "error": self.error,
            "warnings": self.warnings,
        }


@dataclass(frozen=True)
class Reconciliation:
    """What the account holds for a pushed workout: one state, plus the facts behind it.

    The state is what a caller branches on; the fields are what stop it from hiding the
    detail (issue #41). The dataclass is what this module passes around and
    :meth:`as_finding` is what crosses the MCP boundary and lands on the receipt - the
    same split ``PublishResult``/:meth:`PublishResult.as_receipt` already uses.

    ``checked_at`` is stamped when the finding is built, so the response and the
    persisted copy of one read always carry the same timestamp.
    """

    state: str
    scheduled: bool | None = None
    steps_changed: bool | None = None
    renamed_to: str | None = None
    last_known: dict[str, Any] | None = None
    checked_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    def as_finding(self) -> dict[str, Any]:
        """The finding as the MCP response reports it and the receipt stores it.

        ``last_known`` appears only on ``unverified``, where it is the point: a read that
        reached the account has just replaced whatever came before it.
        """
        finding: dict[str, Any] = {
            "state": self.state,
            "scheduled": self.scheduled,
            "steps_changed": self.steps_changed,
            "renamed_to": self.renamed_to,
            "checked_at": self.checked_at,
        }
        if self.state == UNVERIFIED:
            finding["last_known"] = self.last_known
        return finding


def receipt_workout_id(day_dir: pathlib.Path | str) -> int | None:
    """The workout id a previous push recorded for a date, as a lookup candidate.

    The receipt's shape is defined here (:meth:`PublishResult.as_receipt`), so reading
    it back belongs here too - and keeping it in one place stops the CLI and the MCP
    push pair from drifting into two different lookups. ``publish`` itself still takes
    the id as an argument and touches no file.

    Returns:
        The recorded id, or None when the date has no receipt, the file is unreadable,
        or the push never got as far as naming a workout.
    """
    path = pathlib.Path(day_dir) / "push.json"
    if not path.exists():
        return None
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return receipt.get("workout_id") if isinstance(receipt, dict) else None


def publish(
    spec: dict[str, Any],
    publisher: WorkoutPublisher,
    *,
    confirm: bool,
    replace: bool = False,
    activity_dates: frozenset[str] | set[str] = frozenset(),
    known_workout_id: int | None = None,
    planned_intent: str | None = None,
) -> PublishResult:
    """Push a workout spec to Garmin, idempotently and behind a confirm interlock.

    Args:
        spec: The finished workout spec from ``author``.
        publisher: The injected Garmin write surface.
        confirm: When False (the default caller state), plan only and touch nothing.
        replace: Overwrite a different workout of the same name (unschedule + delete +
            upload + schedule) instead of refusing.
        activity_dates: Dates that already have a logged activity, to warn on collision.
        known_workout_id: The id a previous push recorded for this date, offered as the
            first lookup candidate (issue #40). The caller reads it from the receipt;
            this function does no file IO.
        planned_intent: The plan of record's intent for the spec's date. A spec harder
            than it is refused before the account is read (issue #22) - the author-time
            guard cannot see a plan revised after the spec was written. Recorded on the
            receipt either way, so a later read knows what the push was measured against.

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

    session_type = spec.get("session_type")
    too_hard = _plan.guard_error(date, session_type, planned_intent)
    if too_hard is not None:
        return PublishResult(
            action="refuse",
            applied=False,
            spec_hash=marker,
            date=date,
            payload=payload,
            message=too_hard,
            warnings=warnings,
            session_type=session_type,
            planned_intent=planned_intent,
        )

    existing, conflict = _find_target(publisher, spec["name"], marker, known_workout_id)
    action = "refuse" if conflict else _resolve_action(existing, marker, publisher, date, replace)
    result = PublishResult(
        action=action,
        applied=False,
        spec_hash=marker,
        date=date,
        payload=payload,
        message=conflict or _message(action),
        warnings=warnings,
        session_type=session_type,
        planned_intent=planned_intent,
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


def _find_target(
    publisher: WorkoutPublisher, name: str, marker: str, known_workout_id: int | None
) -> tuple[dict[str, Any] | None, str | None]:
    """The account workout this push should act on, or the conflict that stops it.

    Ordered by how stable each key is. The receipt's ``workout_id`` comes first: it is
    the only key the athlete cannot change, and it survives a rename and an edit
    together, which defeat the other two. The account still decides - an id it no
    longer knows falls through - so idempotency rests on the account, not the receipt.
    The ``gc-hash:`` tag comes next, and the name last, where it is what detects a
    changed spec and yields ``refuse``/``replace``.

    Returns:
        The matched workout (or None), and a message when the lookup was ambiguous -
        two candidates mean an earlier push already duplicated something, and guessing
        would let ``--replace`` delete the wrong one.
    """
    library = publisher.list_workouts()
    if known_workout_id is not None:
        for workout in library:
            if workout.get("workoutId") == known_workout_id:
                return workout, None
    by_hash = [w for w in library if _existing_hash(w) == marker]
    if by_hash:
        return (by_hash[0], None) if len(by_hash) == 1 else (None, _ambiguous(by_hash, "hash"))
    by_name = [w for w in library if w.get("workoutName") == name]
    if len(by_name) > 1:
        return None, _ambiguous(by_name, "name")
    return (by_name[0] if by_name else None), None


def _ambiguous(candidates: list[dict[str, Any]], key: str) -> str:
    """The refusal message for a lookup that matched more than one workout."""
    listed = ", ".join(f"{w.get('workoutId')} ({w.get('workoutName')})" for w in candidates)
    return (
        f"{len(candidates)} workouts on the account share this {key}: {listed}; "
        "delete the duplicates in Garmin Connect, then push again"
    )


def reconcile(
    connect: Callable[[], WorkoutPublisher], receipt: object, spec: object, date: str
) -> Reconciliation | None:
    """Resolve a push receipt against the account's library and calendar (issue #41).

    A receipt records what a push did; only the account can say what became of it.
    Keyed on the receipt's ``workout_id`` alone - the question is whether what was
    pushed is still there, not whether something like it is - so a workout the
    library no longer holds is ``missing`` even with a near-identical one beside it.

    Only :func:`_walk_account` runs under the transport catch; the finding is derived
    from what it found, outside it, so a bug in the deriving never reads as an
    unreachable account.

    Args:
        connect: Builds the Garmin read surface. Called only once there is something
            to check, so a receipt-free date never logs in; any failure it raises
            leaves the receipt ``unverified``.
        receipt: The parsed ``push.json`` body - whatever the file held, so its shape
            is checked here rather than assumed. None when the date has no push.
        spec: The parsed authored spec, checked the same way; used to tell an edit
            from a rename (issue #42).
        date: The day whose calendar decides ``live`` against ``unscheduled``.

    Returns:
        The finding, or None when the receipt names no workout to check.
    """
    body = _json_object(receipt)
    workout_id = body.get("workout_id") if body is not None else None
    if body is None or workout_id is None:
        return None
    pushed = _pushed_shape(body, spec)
    try:
        facts = _walk_account(connect(), workout_id, date, body, compare_steps=pushed is not None)
    except Exception as exc:  # noqa: BLE001 - an unreachable account leaves it unverified
        logger.info("reconcile: %s unverified for workout %s: %s", date, workout_id, exc)
        return _unverified(body)
    return _finding(facts, body, pushed)


def plan_divergence(receipt: object, spec: object, planned: str | None) -> dict[str, Any] | None:
    """Report a pushed workout the plan of record no longer allows (issue #22).

    Divergence means *harder than* the plan, not merely different from it: a session
    below the plan is the sanctioned downgrade the recommender produces daily, and
    reporting that would drown the real case. This is the same comparison the author
    and push guards refuse on, asked of a workout already on the account - which is
    the only way it can arise, by the plan being revised after the push.

    Pure over parsed artifacts, so both readers of it - a status read and a plan
    import - answer the same question about the same date.

    Args:
        receipt: The parsed ``push.json`` body, or None when the date has no push.
        spec: The parsed authored spec; consulted only for receipts written before
            the receipt carried its own ``session_type``.
        planned: The plan of record's intent for the date *now*.

    Returns:
        ``{pushed_type, planned_intent, pushed_at}``, or None when nothing was
        pushed, the session is at or below the plan, or the type is unknowable.
    """
    body = _json_object(receipt)
    if body is None or body.get("workout_id") is None:
        return None
    pushed_type = body.get("session_type") or (_json_object(spec) or {}).get("session_type")
    if not _plan.is_harder(pushed_type, planned):
        return None
    return {
        "pushed_type": pushed_type,
        "planned_intent": planned,
        "pushed_at": body.get("pushed_at"),
    }


@dataclass(frozen=True)
class _AccountFacts:
    """What one walk of the account found: read results only, nothing derived from them."""

    entry: dict[str, Any] | None
    scheduled: bool = False
    touched: bool = False
    detail: dict[str, Any] | None = None


def _walk_account(
    publisher: WorkoutPublisher,
    workout_id: int,
    date: str,
    receipt: dict[str, Any],
    *,
    compare_steps: bool,
) -> _AccountFacts:
    """Read what the account holds for a workout id: library, calendar, and its steps.

    Every Garmin call on the reconciliation path happens here, so the caller can wrap
    one call in the transport catch and derive the finding outside it. The
    ``updateDate`` gate lives here for the same reason: it exists only to avoid a
    call, so it belongs beside the calls.

    Args:
        publisher: The account read surface.
        workout_id: The id the receipt recorded.
        date: The day whose calendar entries are read.
        receipt: The push receipt, for the ``updateDate`` gate's ``pushed_at``.
        compare_steps: Whether the steps can settle anything - False skips the detail
            call, because fetching steps nothing will be compared against is waste.

    Returns:
        The library entry (None when the account no longer holds it), whether it is
        scheduled on the date, whether it moved since the push, and its steps when
        they were worth fetching.
    """
    entry = _library_entry(publisher, workout_id)
    if entry is None:
        return _AccountFacts(entry=None)
    scheduled = _is_scheduled(publisher, workout_id, date)
    touched = _touched_since_push(entry, receipt)
    detail = publisher.get_workout(workout_id) if touched and compare_steps else None
    return _AccountFacts(entry=entry, scheduled=scheduled, touched=touched, detail=detail)


def _finding(
    facts: _AccountFacts, receipt: dict[str, Any], pushed: _author.AuthoredShape | None
) -> Reconciliation:
    """Turn what the account walk found into the one finding a caller reads."""
    if facts.entry is None:
        return Reconciliation(state=MISSING, scheduled=False)
    steps_changed = _steps_changed(facts, pushed)
    return Reconciliation(
        state=_state(facts.scheduled, steps_changed),
        scheduled=facts.scheduled,
        steps_changed=steps_changed,
        renamed_to=_renamed_to(facts.entry, receipt),
    )


def _unverified(receipt: dict[str, Any]) -> Reconciliation:
    """The finding for a read that could not reach the account.

    Carries whatever the receipt last recorded under ``last_known``, so an offline
    read degrades to older information rather than to none. Nothing else is filled
    in: this read confirmed nothing, and stale facts must not read as fresh ones.
    """
    known = receipt.get("reconciled")
    return Reconciliation(state=UNVERIFIED, last_known=known if isinstance(known, dict) else None)


def _state(scheduled: bool, steps_changed: bool | None) -> str:
    """Collapse the facts to the one state a caller branches on.

    ``unscheduled`` outranks ``edited``: a workout that is not on the day is not on
    the watch, whatever its steps now say. The edit stays visible in ``steps_changed``.

    ``live`` claims the workout is in the library and on the date, and nothing about
    its steps: an unjudged comparison (``steps_changed`` null) reports there rather
    than as an edit, because "we could not tell" is not evidence of a rewrite.
    """
    if not scheduled:
        return UNSCHEDULED
    return EDITED if steps_changed else LIVE


def _steps_changed(facts: _AccountFacts, pushed: _author.AuthoredShape | None) -> bool | None:
    """Whether the account's steps differ from the ones the receipt says were pushed.

    The ``gc-hash:`` tag cannot answer this: it records what was *pushed* and Garmin
    leaves the description alone when steps change, so it agrees with the receipt
    forever. Only the steps themselves can tell, and fetching them costs a call - so
    ``updateDate`` gates it, and an untouched account copy answers for free.

    Returns:
        None when it could not be judged, either because the local spec is no longer
        evidence of what was pushed (see :func:`_pushed_shape`) or because that made
        the detail call not worth spending.
    """
    if not facts.touched:
        return False
    if pushed is None or facts.detail is None:
        return None
    return _author.authored_shape(facts.detail) != pushed


def _pushed_shape(receipt: dict[str, Any], spec: object) -> _author.AuthoredShape | None:
    """The steps the receipt says were pushed, in the shape the account compares against.

    Built before the account is walked, so a spec that cannot settle the question saves
    the detail call rather than paying for it.

    Returns:
        None when the local spec no longer hashes to the receipt's ``spec_hash`` - it
        has been re-authored since, so it is not evidence of what the push sent - or
        when it is too malformed to project, which says the same thing.
    """
    authored = _json_object(spec)
    if authored is None:
        return None
    try:
        if spec_hash(authored) != receipt.get("spec_hash"):
            return None
        return _author.authored_shape(_author.to_garmin(authored))
    except (KeyError, TypeError, ValueError) as exc:
        logger.info("reconcile: spec for %s cannot be projected: %s", receipt.get("date"), exc)
        return None


def _json_object(value: object) -> dict[str, Any] | None:
    """A parsed JSON artifact when it holds an object, else None - a file can hold anything."""
    return value if isinstance(value, dict) else None


def _touched_since_push(entry: dict[str, Any], receipt: dict[str, Any]) -> bool:
    """Whether the account's copy moved after the push that produced the receipt.

    The two timestamps come off different clocks: the receipt's ``pushed_at`` is
    written with this machine's local time, while Garmin reports ``updateDate`` in
    UTC. Comparing them raw hides every edit made within the local offset of a push -
    for a UTC+2 athlete, the first two hours - which is exactly the window an edit is
    most likely to land in. Both are normalised to an instant before comparing.

    An unreadable timestamp on either side counts as touched: checking properly costs
    one call, while assuming nothing moved would hide a real edit.
    """
    pushed_at = _instant(receipt.get("pushed_at"), naive_tz=None)
    updated = _instant(entry.get("updateDate"), naive_tz=dt.UTC)
    if pushed_at is None or updated is None:
        return True
    return updated > pushed_at


def _instant(value: object, *, naive_tz: dt.tzinfo | None) -> dt.datetime | None:
    """Parse an ISO timestamp to a UTC instant, or None when it is unreadable.

    Args:
        value: An ISO timestamp; Garmin's carries a fractional-second suffix.
        naive_tz: The zone to read a timestamp that carries no offset in. None means
            this machine's local clock - which is what writes the receipt, while the
            account answers in UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None and naive_tz is not None:
        parsed = parsed.replace(tzinfo=naive_tz)
    return parsed.astimezone(dt.UTC)


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


def confirm_token(spec: dict[str, Any], planned_intent: str | None = None) -> str:
    """A token covering everything a preview showed and a confirm acts on.

    The date is included because it decides what the push schedules and which day
    the activity-collision check ran against: a spec retargeted between preview and
    confirm must invalidate the preview even though the workout itself is unchanged.
    The plan of record joins it for the same reason (issue #22) - it decides whether
    the push is allowed at all, so a plan revised between the two invalidates what
    the preview showed.
    """
    return _canonical_hash(
        {
            "name": spec["name"],
            "steps": spec["steps"],
            "date": spec["date"],
            "planned_intent": planned_intent,
        }
    )


def _message(action: str) -> str:
    """A human summary line for a resolved action."""
    return {
        "create": "will create and schedule a new workout",
        "replace": "this date's workout on the account differs; will replace and reschedule it",
        "noop": "already scheduled with an identical workout; nothing to do",
        "schedule": "workout exists in the library; will schedule it to the date",
        "refuse": "this date's workout on the account differs; re-run with --replace to overwrite",
    }[action]
