"""Phase 11 publish tests: the transport orchestration against a fake publisher.

Seam 2: the ``WorkoutPublisher`` protocol is injected (the same pattern ``sync``
uses for ``GarminClient``), so idempotency, the confirm interlock, and the receipt
are exercised without any live Garmin call. Prior art: ``tests/test_sync.py``.
"""

from __future__ import annotations

from garmin_coach.publish import publish
from tests.conftest import FakePublisher


def _spec(date="2026-07-17", name=None, work_s=1200):
    return {
        "sport": "run",
        "origin": "recommender",
        "date": date,
        "session_type": "tempo",
        "name": name or f"GC {date} tempo",
        "steps": [
            {"kind": "warmup", "end": {"type": "time", "seconds": 600}, "target": {"type": "none"}},
            {
                "kind": "work",
                "end": {"type": "time", "seconds": work_s},
                "target": {"type": "pace_band", "fast_s_per_km": 265, "slow_s_per_km": 275},
            },
            {
                "kind": "cooldown",
                "end": {"type": "time", "seconds": 600},
                "target": {"type": "none"},
            },
        ],
        "warnings": [],
    }


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
