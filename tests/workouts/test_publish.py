"""Phase 11 publish tests: the transport orchestration against a fake publisher.

Seam 2: the ``WorkoutPublisher`` protocol is injected (the same pattern ``sync``
uses for ``GarminClient``), so idempotency, the confirm interlock, and the receipt
are exercised without any live Garmin call. Prior art: ``tests/test_sync.py``.
"""

from __future__ import annotations

from garmin_coach.workouts.publish import confirm_token, publish, spec_hash
from tests.conftest import FakePublisher
from tests.conftest import run_spec as _spec


# --- account lookup: id, then hash, then name (issue #40) -------------------


def _pushed(pub, spec=None):
    """Push a spec for real, returning the account id it landed on."""
    result = publish(spec or _spec(), pub, confirm=True)
    pub.calls.clear()
    return result.workout_id


def test_a_renamed_workout_still_resolves_to_noop():
    """Renaming in Connect is ordinary; it must not make the same session look new."""
    pub = FakePublisher()
    _pushed(pub)
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"

    result = publish(_spec(), pub, confirm=True)

    assert result.action == "noop"
    assert pub.calls == []
    assert len(pub.workouts) == 1


def test_a_renamed_workout_with_a_changed_spec_resolves_against_the_receipt_id():
    """Rename plus edit breaks both mutable keys; only the receipt's id survives it."""
    pub = FakePublisher()
    workout_id = _pushed(pub)
    pub.workouts[workout_id]["workoutName"] = "Hyrox Tempo"

    result = publish(_spec(work_s=2400), pub, confirm=True, known_workout_id=workout_id)

    assert result.action == "refuse"
    assert len(pub.workouts) == 1


def test_a_renamed_workout_with_a_changed_spec_replaces_in_place_when_asked():
    pub = FakePublisher()
    workout_id = _pushed(pub)
    pub.workouts[workout_id]["workoutName"] = "Hyrox Tempo"

    result = publish(
        _spec(work_s=2400), pub, confirm=True, replace=True, known_workout_id=workout_id
    )

    assert result.action == "replace"
    assert len(pub.workouts) == 1


def test_a_candidate_id_the_account_forgot_falls_through_to_the_hash():
    pub = FakePublisher()
    _pushed(pub)
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"

    result = publish(_spec(), pub, confirm=True, known_workout_id=999999)

    assert result.action == "noop"


def test_the_name_still_matches_a_workout_carrying_no_hash_tag():
    """The name fallback is what detects a changed spec; it is not narrowed to untagged."""
    pub = FakePublisher()
    pub.workouts[1] = {"workoutName": _spec()["name"], "description": None}

    result = publish(_spec(), pub, confirm=False)

    assert result.action == "refuse"


def test_two_workouts_sharing_a_hash_refuse_and_name_both():
    """Guessing is unsafe: --replace deletes whichever candidate the lookup picked."""
    pub = FakePublisher()
    _pushed(pub)
    pub.workouts[2000] = dict(pub.workouts[1000], workoutName="Hyrox Tempo")

    result = publish(_spec(), pub, confirm=True, replace=True)

    assert result.action == "refuse"
    assert "1000 (GC 2026-07-17 tempo)" in result.message
    assert "2000 (Hyrox Tempo)" in result.message
    assert pub.calls == []


def test_two_workouts_sharing_a_name_refuse_and_name_both():
    pub = FakePublisher()
    for wid in (4242, 7777):
        pub.workouts[wid] = {"workoutName": _spec()["name"], "description": None}

    result = publish(_spec(), pub, confirm=True, replace=True)

    assert result.action == "refuse"
    assert "4242 (GC 2026-07-17 tempo)" in result.message
    assert "7777 (GC 2026-07-17 tempo)" in result.message
    assert pub.calls == []


def test_a_candidate_id_the_account_forgot_falls_all_the_way_through_to_the_name():
    """Stale id, no hash match (the spec moved on): the name is the last key left."""
    pub = FakePublisher()
    _pushed(pub)

    result = publish(_spec(work_s=2400), pub, confirm=True, known_workout_id=999999)

    assert result.action == "refuse"
    assert result.workout_id == 1000
    assert len(pub.workouts) == 1


class FailingSchedulePublisher(FakePublisher):
    """A publisher whose first schedule call fails, to model a partial push."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_schedule = True

    def schedule(self, workout_id: int, date: str) -> int:
        if self.fail_next_schedule:
            self.fail_next_schedule = False
            self.calls.append("schedule-fail")
            raise RuntimeError("scheduling timed out")
        return super().schedule(workout_id, date)


# --- confirm interlock ------------------------------------------------------


def test_dry_run_plans_a_create_but_touches_nothing():
    pub = FakePublisher()
    result = publish(_spec(), pub, confirm=False)
    assert result.action == "create"
    assert result.applied is False
    assert pub.calls == []
    assert result.payload["workoutName"] == "GC 2026-07-17 tempo"


def test_confirm_uploads_then_schedules_atomically():
    pub = FakePublisher()
    result = publish(_spec(), pub, confirm=True)
    assert result.action == "create"
    assert result.applied is True
    assert pub.calls == ["upload", "schedule"]
    assert result.workout_id is not None
    assert result.schedule_id is not None


# --- idempotency ------------------------------------------------------------


def test_second_identical_push_is_a_no_op():
    pub = FakePublisher()
    publish(_spec(), pub, confirm=True)
    pub.calls.clear()
    result = publish(_spec(), pub, confirm=True)
    assert result.action == "noop"
    assert pub.calls == []


def test_library_only_match_schedules_without_reuploading():
    pub = FakePublisher()
    publish(_spec(), pub, confirm=True)
    # the athlete deleted the calendar entry from their phone; the library copy remains
    pub.scheduled.clear()
    pub.calls.clear()
    result = publish(_spec(), pub, confirm=True)
    assert result.action == "schedule"
    assert pub.calls == ["schedule"]


def test_different_payload_same_name_refuses_without_replace():
    pub = FakePublisher()
    publish(_spec(), pub, confirm=True)
    pub.calls.clear()
    result = publish(_spec(work_s=1800), pub, confirm=True)  # same name, changed structure
    assert result.action == "refuse"
    assert result.applied is False
    assert pub.calls == []
    assert "replace" in result.message


# --- activity-collision warning ---------------------------------------------


def test_activity_on_the_date_warns_but_does_not_block():
    pub = FakePublisher()
    result = publish(_spec(), pub, confirm=True, activity_dates={"2026-07-17"})
    assert result.action == "create"
    assert result.applied is True
    assert any("already" in w for w in result.warnings)


def test_a_spec_warning_survives_into_the_preview():
    # issue #47: a target the author could not honour has to be visible at the
    # last gate before the watch, not only in the spec file.
    dropped_target = "warmup_target: no Z2 heart-rate band in your zones"
    spec = _spec()
    spec["warnings"] = [dropped_target]
    result = publish(spec, FakePublisher(), confirm=False)
    assert dropped_target in result.warnings


# --- replace ----------------------------------------------------------------


def test_replace_overwrites_a_different_workout_of_the_same_name():
    pub = FakePublisher()
    publish(_spec(), pub, confirm=True)
    old_id = next(iter(pub.workouts))
    pub.calls.clear()
    result = publish(_spec(work_s=1800), pub, confirm=True, replace=True)
    assert result.action == "replace"
    assert result.applied is True
    assert pub.calls == ["unschedule", "delete", "upload", "schedule"]
    assert old_id not in pub.workouts  # the old template is gone
    assert result.workout_id in pub.workouts


def test_replace_not_needed_when_payload_is_identical():
    pub = FakePublisher()
    publish(_spec(), pub, confirm=True)
    pub.calls.clear()
    result = publish(_spec(), pub, confirm=True, replace=True)
    assert result.action == "noop"
    assert pub.calls == []


# --- partial-failure retry --------------------------------------------------


def test_schedule_failure_leaves_an_orphan_and_no_rollback():
    pub = FailingSchedulePublisher()
    result = publish(_spec(), pub, confirm=True)
    assert result.applied is False
    assert result.error is not None
    assert result.workout_id is not None  # uploaded to the library
    assert result.schedule_id is None
    assert "delete" not in pub.calls  # no compensating rollback
    assert result.workout_id in pub.workouts


def test_rerun_after_partial_failure_completes_the_schedule():
    pub = FailingSchedulePublisher()
    first = publish(_spec(), pub, confirm=True)  # upload ok, schedule fails
    pub.calls.clear()
    second = publish(_spec(), pub, confirm=True)  # retry
    assert second.action == "schedule"
    assert pub.calls == ["schedule"]  # skips the upload, completes the schedule
    assert second.applied is True
    assert second.workout_id == first.workout_id


# --- receipt ----------------------------------------------------------------


def test_receipt_carries_ids_hash_and_action():
    pub = FakePublisher()
    result = publish(_spec(), pub, confirm=True)
    receipt = result.as_receipt()
    assert receipt["action"] == "create"
    assert receipt["workout_id"] == result.workout_id
    assert receipt["schedule_id"] == result.schedule_id
    assert receipt["name"] == "GC 2026-07-17 tempo"
    assert receipt["spec_hash"]


# --- Issue #37: two hashes, two jobs ---


def test_the_gc_hash_ignores_the_date_but_the_confirm_token_does_not():
    """Rescheduling is the same workout on the account, but a different push to confirm."""
    # Same name and steps on two days: only the token can tell the pushes apart.
    monday = _spec(date="2026-07-27", name="GC tempo")
    tuesday = _spec(date="2026-07-28", name="GC tempo")

    assert spec_hash(monday) == spec_hash(tuesday)
    assert confirm_token(monday) != confirm_token(tuesday)


def test_the_confirm_token_still_reacts_to_the_workout_itself():
    assert confirm_token(_spec()) != confirm_token(_spec(work_s=1800))


# --- Issue #22: nothing harder than the plan of record reaches the account ---


def test_a_spec_harder_than_the_plan_is_refused_before_the_account_is_touched():
    """A spec authored days earlier is stale evidence once the plan is revised:
    the author-time guard cannot see a plan that changed after it ran."""
    pub = FakePublisher()

    result = publish(_spec(), pub, confirm=True, planned_intent="easy")

    assert result.action == "refuse"
    assert "planned as easy" in result.message
    assert result.applied is False
    assert pub.calls == []


def test_replace_does_not_override_the_plan_guard():
    """--replace overwrites a *different workout*; it is not a licence to outrank
    the plan, so the refusal must not be reachable by re-running with it."""
    pub = FakePublisher()

    result = publish(_spec(), pub, confirm=True, replace=True, planned_intent="easy")

    assert result.action == "refuse"
    assert pub.calls == []


def test_a_spec_at_or_below_the_plan_pushes_normally():
    pub = FakePublisher()

    result = publish(_spec(), pub, confirm=True, planned_intent="quality")

    assert result.action == "create"
    assert result.applied is True


def test_the_receipt_records_the_session_that_was_pushed():
    """The spec on disk can be re-authored later; the receipt is what still says
    which session actually went to the account."""
    pub = FakePublisher()

    receipt = publish(_spec(), pub, confirm=True, planned_intent="quality").as_receipt()

    assert receipt["session_type"] == "tempo"


def test_the_receipt_records_the_plan_the_push_was_measured_against():
    """So a later read can tell a plan that changed under a workout from one that
    was already wrong when it was pushed."""
    pub = FakePublisher()

    receipt = publish(_spec(), pub, confirm=True, planned_intent="quality").as_receipt()

    assert receipt["planned_intent"] == "quality"


def test_the_confirm_token_reacts_to_the_plan_of_record():
    """A plan revised between preview and confirm invalidates the preview, the same
    way retargeting the spec does."""
    assert confirm_token(_spec(), "quality") != confirm_token(_spec(), "easy")
    assert confirm_token(_spec(), "quality") == confirm_token(_spec(), "quality")
