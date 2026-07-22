"""Shared test infrastructure: fixture loading, in-memory DB, fake Garmin client."""

from __future__ import annotations

import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    """Load a JSON fixture by filename (with or without .json suffix)."""
    if not name.endswith(".json"):
        name += ".json"
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fixture():
    return load_fixture


@pytest.fixture
def conn():
    """A fresh in-memory SQLite connection with the package schema applied."""
    from garmin_coach.core import db

    c = db.connect(":memory:")
    db.bootstrap(c)
    yield c
    c.close()


class FakeGarminClient:
    """Injectable stand-in for the real transport client (see sync seam).

    Returns fixtures keyed by (endpoint, ref_date). Unknown dates return an
    empty payload so per-day streams can be exercised over a range.
    """

    def __init__(
        self,
        activities=None,
        by_day=None,
        weather_by_id=None,
        lactate=None,
        lactate_range=None,
        sets_by_id=None,
    ):
        # activities: list returned for the whole range
        # by_day: {endpoint: {date: payload}}
        # weather_by_id: {activity_id: weather payload}
        # lactate: latest LTHR payload; lactate_range: ranged LTHR payload
        # sets_by_id: {activity_id: exercise-sets payload | Exception to raise}
        self.activities = activities or []
        self.by_day = by_day or {}
        self.weather_by_id = weather_by_id or {}
        self.lactate = lactate
        self.lactate_range = lactate_range
        self.sets_by_id = sets_by_id or {}
        self.calls: list[tuple[str, str]] = []

    def get_activities(self, start_date: str, end_date: str):
        self.calls.append(("activities", f"{start_date}..{end_date}"))
        return self.activities

    def _day(self, endpoint: str, date: str):
        self.calls.append((endpoint, date))
        return self.by_day.get(endpoint, {}).get(date)

    def get_sleep(self, date: str):
        return self._day("sleep", date)

    def get_hrv(self, date: str):
        return self._day("hrv", date)

    def get_wellness(self, date: str):
        return self._day("wellness", date)

    def get_readiness(self, date: str):
        return self._day("readiness", date)

    def get_status(self, date: str):
        return self._day("status", date)

    def get_activity_weather(self, activity_id: int):
        self.calls.append(("weather", str(activity_id)))
        payload = self.weather_by_id.get(activity_id)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_activity_exercise_sets(self, activity_id: int):
        self.calls.append(("sets", str(activity_id)))
        payload = self.sets_by_id.get(activity_id)
        if isinstance(payload, Exception):
            raise payload
        return payload

    def get_lactate_threshold(self, start_date=None, end_date=None):
        if start_date is None:
            self.calls.append(("lactate", "latest"))
            return self.lactate
        self.calls.append(("lactate", f"{start_date}..{end_date}"))
        return self.lactate_range


@pytest.fixture
def fake_client():
    return FakeGarminClient


class FakePublisher:
    """An in-memory Garmin account: a workout library and a schedule, recording calls."""

    def __init__(self) -> None:
        self.workouts: dict = {}
        self.scheduled: dict = {}
        self._next_workout = 1000
        self._next_schedule = 5000
        self.calls: list[str] = []

    def list_workouts(self):
        return [{"workoutId": wid, **w} for wid, w in self.workouts.items()]

    def upload(self, payload):
        self.calls.append("upload")
        wid = self._next_workout
        self._next_workout += 1
        self.workouts[wid] = {
            "workoutName": payload["workoutName"],
            "description": payload.get("description"),
        }
        return wid

    def schedule(self, workout_id, date):
        self.calls.append("schedule")
        sid = self._next_schedule
        self._next_schedule += 1
        self.scheduled[sid] = (workout_id, date)
        return sid

    def unschedule(self, schedule_id):
        self.calls.append("unschedule")
        self.scheduled.pop(schedule_id, None)

    def delete(self, workout_id):
        self.calls.append("delete")
        self.workouts.pop(workout_id, None)

    def list_scheduled(self, date):
        return [
            {"scheduleId": sid, "workoutId": wid}
            for sid, (wid, d) in self.scheduled.items()
            if d == date
        ]


@pytest.fixture
def fake_publisher():
    return FakePublisher
