"""Phase 11 publish tests: the transport orchestration against a fake publisher.

Seam 2: the ``WorkoutPublisher`` protocol is injected (the same pattern ``sync``
uses for ``GarminClient``), so idempotency, the confirm interlock, and the receipt
are exercised without any live Garmin call. Prior art: ``tests/test_sync.py``.
"""

from __future__ import annotations

from typing import Any

from garmin_coach.publish import publish


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
            {"kind": "cooldown", "end": {"type": "time", "seconds": 600}, "target": {"type": "none"}},
        ],
        "warnings": [],
    }


class FakePublisher:
    """An in-memory account: a workout library and a schedule, recording its calls."""

    def __init__(self) -> None:
        self.workouts: dict[int, dict[str, Any]] = {}
        self.scheduled: dict[int, tuple[int, str]] = {}
        self._next_workout = 1000
        self._next_schedule = 5000
        self.calls: list[str] = []

    def list_workouts(self) -> list[dict[str, Any]]:
        return [{"workoutId": wid, **w} for wid, w in self.workouts.items()]

    def upload(self, payload: dict[str, Any]) -> int:
        self.calls.append("upload")
        wid = self._next_workout
        self._next_workout += 1
        self.workouts[wid] = {
            "workoutName": payload["workoutName"],
            "description": payload.get("description"),
        }
        return wid

    def schedule(self, workout_id: int, date: str) -> int:
        self.calls.append("schedule")
        sid = self._next_schedule
        self._next_schedule += 1
        self.scheduled[sid] = (workout_id, date)
        return sid

    def unschedule(self, schedule_id: int) -> None:
        self.calls.append("unschedule")
        self.scheduled.pop(schedule_id, None)

    def delete(self, workout_id: int) -> None:
        self.calls.append("delete")
        self.workouts.pop(workout_id, None)

    def list_scheduled(self, date: str) -> list[int]:
        return [wid for wid, d in self.scheduled.values() if d == date]


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
