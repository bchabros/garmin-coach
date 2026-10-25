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
    assert first.receipt_path == day / "push.json"
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
    assert preview.receipt_path is None
    assert path.read_bytes() == before


def test_author_requires_the_hyrox_variant_without_writing(conn, tmp_path):
    outcome = push.author_workout(
        conn,
        date="2099-07-17",
        request={"sport": "run", "origin": "athlete", "session_type": "hyrox"},
        reports_dir=str(tmp_path),
    )

    assert outcome["spec"] is None
    assert "hyrox" in outcome["error"].lower()
    assert not (tmp_path / "2099-07-17" / "workout.json").exists()


@pytest.mark.parametrize("broken", ["{broken", "[]", "null", '{"workout_id": "wrong"}'])
def test_corrupt_receipt_refuses_a_push_without_connecting(conn, tmp_path, broken):
    day = _file_spec(tmp_path)
    receipt = day / "push.json"
    receipt.write_text(broken)

    def connect():
        pytest.fail("unreadable push evidence must be refused before Garmin login")

    with pytest.raises(push.PushRefused, match="push.json"):
        push.push_for_date(conn, date=DATE, connect=connect, reports_dir=tmp_path, confirm=True)
    assert receipt.read_text() == broken


def test_status_reports_damaged_evidence_without_login_or_changes(conn, tmp_path):
    day = _file_spec(tmp_path)
    receipt = day / "push.json"
    receipt.write_text("{broken")

    def connect():
        pytest.fail("damaged evidence must not cause a status login")

    result = push.get_workout_status(conn, DATE, connect=connect, reports_dir=str(tmp_path))

    assert DATE in result["error"]
    assert "push.json" in result["error"]
    assert result["reconciled"] is None
    assert receipt.read_text() == "{broken"


def test_listing_preserves_readable_days_and_warns_about_damaged_receipts(conn, tmp_path):
    good = _file_spec(tmp_path)
    (good / "push.json").write_text(json.dumps({"workout_id": 1000, "name": "GC tempo"}))
    broken = tmp_path / "2026-07-18"
    broken.mkdir()
    path = broken / "push.json"
    path.write_text("[]")

    result = push.get_pushed_workouts(conn, reports_dir=str(tmp_path))

    assert [day["date"] for day in result["workouts"]] == [DATE]
    assert "2026-07-18" in result["warnings"][0]
    assert "push.json" in result["warnings"][0]
    assert path.read_text() == "[]"


def test_invalidated_pushes_skip_a_day_taken_off(tmp_path):
    """A plan re-import must not ask for a re-author of a session no longer on the watch."""
    for date, extra in (
        ("2026-07-17", {"unscheduled_at": "2026-07-16T08:00:00"}),
        ("2026-07-18", {}),
    ):
        (tmp_path / date).mkdir()
        (tmp_path / date / "push.json").write_text(
            json.dumps({"workout_id": 1000, "session_type": "quality", **extra})
        )

    conflicts = push.invalidated_pushes(tmp_path, {"2026-07-17": "easy", "2026-07-18": "easy"})

    assert [c["date"] for c in conflicts.conflicts] == ["2026-07-18"]


def test_invalid_spec_content_is_refused_before_login(conn, tmp_path):
    spec = run_spec(date=DATE)
    del spec["name"]
    day = _file_spec(tmp_path, spec)

    def connect():
        pytest.fail("invalid workout content must not cause a login")

    with pytest.raises(push.PushRefused, match="workout.json"):
        push.push_for_date(conn, date=DATE, connect=connect, reports_dir=tmp_path, confirm=True)
    assert not (day / "push.json").exists()


def test_author_reports_invalid_input_without_writing_outside_the_day(conn, tmp_path):
    result = push.author_workout(conn, "../outside", request={}, reports_dir=str(tmp_path))

    assert result["spec"] is None
    assert "YYYY-MM-DD" in result["error"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("hardness", ["hard"]),
        ("hardness", "unrecognized"),
        ("session_type", {"type": "quality"}),
        ("warnings", None),
    ],
)
def test_malformed_spec_metadata_is_refused_before_login(conn, tmp_path, field, value):
    spec = run_spec(date=DATE)
    spec[field] = value
    day = _file_spec(tmp_path, spec)
    original = (day / "workout.json").read_bytes()

    def connect():
        pytest.fail("malformed metadata must be refused before account connection")

    with pytest.raises(push.PushRefused, match=field):
        push.push_for_date(conn, date=DATE, connect=connect, reports_dir=tmp_path, confirm=True)
    assert (day / "workout.json").read_bytes() == original
