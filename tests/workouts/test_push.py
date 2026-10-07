"""Complete push operation: real SQLite/files, fake account, public outcomes."""

from __future__ import annotations

import json

import pytest

from garmin_coach.core import plan
from garmin_coach.workouts import push
from tests.conftest import FakePublisher, run_spec

DATE = "2026-07-17"


def _file_spec(tmp_path, spec=None):
    day = tmp_path / DATE
    day.mkdir(exist_ok=True)
    (day / "workout.json").write_text(json.dumps(spec or run_spec(date=DATE)))
    return day


def test_mismatched_date_is_refused_without_connecting(conn, tmp_path):
    day = _file_spec(tmp_path, run_spec(date="2026-07-18"))

    def connect():
        pytest.fail("a mismatched date must not connect to Garmin")

    with pytest.raises(push.PushRefused, match="targets 2026-07-18"):
        push.push_for_date(conn, date=DATE, connect=connect, reports_dir=tmp_path, confirm=True)

    assert not (day / "push.json").exists()


def test_changed_plan_invalidates_preview_before_connecting(conn, tmp_path):
    day = _file_spec(tmp_path)
    preview = push.push_for_date(conn, date=DATE, connect=FakePublisher, reports_dir=tmp_path)
    plan.upsert_week(
        conn,
        [{"week_start": "2026-07-13", "dow": 4, "planned": "easy", "intent": "easy"}],
    )

    def connect():
        pytest.fail("a stale preview must not connect to Garmin")

    with pytest.raises(push.PushRefused, match="stale confirm_token"):
        push.push_for_date(
            conn,
            date=DATE,
            connect=connect,
            reports_dir=tmp_path,
            confirm=True,
            expected_token=preview.confirm_token,
        )
    assert not (day / "push.json").exists()


class _ScheduleFailsOnce(FakePublisher):
    def schedule(self, workout_id, date):
        if not hasattr(self, "failed"):
            self.failed = True
            raise TimeoutError("schedule unavailable")
        return super().schedule(workout_id, date)


def test_partial_push_is_recorded_and_retry_finishes_without_another_upload(conn, tmp_path):
    day = _file_spec(tmp_path)
    pub = _ScheduleFailsOnce()

    first = push.push_for_date(
        conn, date=DATE, connect=lambda: pub, reports_dir=tmp_path, confirm=True
    )

    receipt = json.loads((day / "push.json").read_text())
    assert first.result.applied is False
    assert receipt["workout_id"] == 1000
    assert receipt["date"] == DATE
    assert receipt["error"] == "schedule unavailable"
    assert receipt["pushed_at"]

    retry = push.push_for_date(
        conn, date=DATE, connect=lambda: pub, reports_dir=tmp_path, confirm=True
    )

    assert retry.result.applied is True
    assert pub.calls == ["upload", "schedule"]
    assert pub.scheduled == {5000: (1000, DATE)}
    receipt = json.loads((day / "push.json").read_text())
    assert receipt["error"] is None
    assert receipt["schedule_id"] == 5000


def test_preview_of_an_existing_push_preserves_its_receipt(conn, tmp_path):
    day = _file_spec(tmp_path)
    pub = FakePublisher()
    push.push_for_date(conn, date=DATE, connect=lambda: pub, reports_dir=tmp_path, confirm=True)
    path = day / "push.json"
    receipt = json.loads(path.read_text())
    receipt["pushed_at"] = "2026-07-15T17:28:00"
    receipt["last_state"] = "live"
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()

    preview = push.push_for_date(conn, date=DATE, connect=lambda: pub, reports_dir=tmp_path)

    assert preview.result.action == "noop"
    assert path.read_bytes() == before
