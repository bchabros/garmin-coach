"""Dated-workout workflows: public operations, temporary SQLite/files, fake account."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from garmin_coach.core import db
from garmin_coach.core import plan as plan_mod
from garmin_coach.workouts import author, publish, push
from tests.conftest import FakePublisher, as_account_read_back, run_spec


YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _seed_mart(conn, date: str, **cols) -> None:
    db.upsert_daily(conn, "daily_metrics", {"date": date, **cols})


PUSH_DATE = "2026-07-17"


class UnreachablePublisher(FakePublisher):
    """A publisher whose every read fails, to model an unreachable account."""

    def list_workouts(self):
        raise RuntimeError("garmin: login failed")

    def list_scheduled(self, date):
        raise RuntimeError("garmin: login failed")


PUSHED_AT = "2026-07-15T17:28:00"


PUSHED_NAME = run_spec(date=PUSH_DATE)["name"]


def _as_account_clock(local_iso, *, minutes_after=0):
    """The same instant as Garmin renders it: naive UTC, fractional-second suffix.

    The receipt's clock is this machine's local time and the account's is UTC, so a
    fake that reuses one literal for both cannot see a skew bug (issue #42).
    """
    instant = dt.datetime.fromisoformat(local_iso).astimezone(dt.UTC) + dt.timedelta(
        minutes=minutes_after
    )
    return instant.replace(tzinfo=None).isoformat() + ".0"


def _seed_pushed(
    tmp_path, *, spec=None, workout_id=1000, date=PUSH_DATE, spec_hash=None, session_type=None
):
    """Write a workout spec and an applied push receipt for a date.

    ``session_type`` is left off by default: receipts written before issue #22 carry
    no such field, and the divergence check has to keep working on them.
    """
    spec = spec or run_spec(date=date)
    day_dir = tmp_path / date
    day_dir.mkdir(exist_ok=True)
    (day_dir / "workout.json").write_text(json.dumps(spec))
    receipt = {
        "action": "create",
        "applied": True,
        "name": spec["name"],
        "date": date,
        "workout_id": workout_id,
        "spec_hash": spec_hash or publish.spec_hash(spec),
        "pushed_at": PUSHED_AT,
    }
    if session_type is not None:
        receipt["session_type"] = session_type
    (day_dir / "push.json").write_text(json.dumps(receipt))
    return day_dir


def _account_with(
    pub, *, name=PUSHED_NAME, workout_id=1000, scheduled_on=None, spec=None, update_date=None
):
    """Put a workout in the fake library, holding the steps the given spec authors."""
    payload = author.to_garmin(spec or run_spec())
    update_date = update_date or _as_account_clock(PUSHED_AT)
    pub.workouts[workout_id] = {
        "workoutName": name,
        "description": "gc-hash:297803a3d3505fe3",
        "updateDate": update_date,
        "workoutSegments": payload["workoutSegments"],
    }
    if scheduled_on is not None:
        pub.scheduled[5000] = (workout_id, scheduled_on)
    return pub


def _recording_connect(pub):
    """A publisher factory that records each time the account is contacted."""
    calls: list[str] = []

    def connect():
        calls.append("connect")
        return pub

    return (connect, calls)


def _status(conn, tmp_path, pub, date=PUSH_DATE):
    return push.get_workout_status(conn, date=date, connect=lambda: pub, reports_dir=str(tmp_path))


def test_status_reports_missing_when_the_account_no_longer_holds_the_workout(conn, tmp_path):
    """The live 2026-07-17 case: the receipt claims a workout the account deleted."""
    _seed_pushed(tmp_path)
    pub = FakePublisher()
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "missing"
    assert out["reconciled"]["scheduled"] is False


def test_status_reports_unscheduled_when_the_workout_is_only_in_the_library(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher())
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "unscheduled"
    assert out["reconciled"]["scheduled"] is False


def test_status_reports_unscheduled_when_the_workout_moved_to_another_date(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on="2026-07-18")
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "unscheduled"


def test_status_reports_live_when_scheduled_on_the_date(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["reconciled"]["scheduled"] is True
    assert out["reconciled"]["renamed_to"] is None


def test_status_names_the_current_account_name_when_the_athlete_renamed_it(conn, tmp_path):
    """Renaming in Connect is the athlete's prerogative: reported, never a fault state."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), name="Hyrox Tempo", scheduled_on=PUSH_DATE)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["reconciled"]["renamed_to"] == "Hyrox Tempo"


def test_status_reports_unverified_when_the_account_cannot_be_reached(conn, tmp_path):
    _seed_pushed(tmp_path)
    out = _status(conn, tmp_path, UnreachablePublisher())
    assert out["reconciled"]["state"] == "unverified"
    assert out["push"]["applied"] is True


def test_status_reports_unverified_when_logging_in_fails(conn, tmp_path):
    """A failed login is data on this path, not an error: the read degrades."""
    _seed_pushed(tmp_path)

    def connect():
        raise RuntimeError("garmin: login failed")

    out = push.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert out["reconciled"]["state"] == "unverified"


def test_status_without_a_receipt_never_logs_in(conn, tmp_path):
    """A date that was never pushed has nothing to check, so it costs no login."""
    connect, calls = _recording_connect(FakePublisher())
    out = push.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert out["reconciled"] is None
    assert calls == []


def test_status_with_a_receipt_carrying_no_workout_id_never_logs_in(conn, tmp_path):
    day_dir = tmp_path / PUSH_DATE
    day_dir.mkdir()
    (day_dir / "push.json").write_text(json.dumps({"action": "refuse", "workout_id": None}))
    connect, calls = _recording_connect(FakePublisher())
    out = push.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert out["reconciled"] is None
    assert calls == []


TOUCHED_AT = "2026-07-16T16:16:31.0"


def test_status_reports_edited_when_the_account_steps_differ_from_the_pushed_spec(conn, tmp_path):
    """The live 1633354389 case: the athlete rewrote the steps after the push."""
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=run_spec(work_s=2400), update_date=TOUCHED_AT
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "edited"
    assert out["reconciled"]["steps_changed"] is True


def test_status_reports_live_when_only_the_name_changed(conn, tmp_path):
    """Renaming bumps updateDate too; without the step check every rename would read as edited."""
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(), name="Hyrox Tempo", scheduled_on=PUSH_DATE, update_date=TOUCHED_AT
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["reconciled"]["renamed_to"] == "Hyrox Tempo"
    assert out["reconciled"]["steps_changed"] is False


def test_status_skips_the_detail_call_when_the_account_copy_was_never_touched(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, update_date=_as_account_clock(PUSHED_AT)
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["reconciled"]["steps_changed"] is False
    assert "get_workout" not in pub.reads


def test_status_reports_unscheduled_over_edited_but_keeps_the_edit_visible(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), spec=run_spec(work_s=2400), update_date=TOUCHED_AT)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "unscheduled"
    assert out["reconciled"]["steps_changed"] is True


def test_status_ignores_account_added_decoration_on_the_steps(conn, tmp_path):
    """The account decorates every step with fields no upload ever sent."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)
    entry = pub.workouts[1000]
    entry["workoutSegments"] = as_account_read_back(entry)["workoutSegments"]
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is False


def test_status_cannot_judge_the_steps_when_the_spec_was_re_authored(conn, tmp_path):
    """A local spec that no longer hashes to the receipt is not evidence of what was pushed."""
    _seed_pushed(tmp_path, spec_hash="a-hash-from-an-older-spec")
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["reconciled"]["steps_changed"] is None


def _repeat_spec(reps=4, work_s=180):
    """A quality spec, so the projection's repeat-group recursion is exercised."""
    return {
        "sport": "run",
        "origin": "recommender",
        "date": PUSH_DATE,
        "session_type": "quality",
        "name": PUSHED_NAME,
        "steps": [
            {"kind": "warmup", "end": {"type": "time", "seconds": 600}, "target": {"type": "none"}},
            {
                "kind": "repeat",
                "reps": reps,
                "steps": [
                    {
                        "kind": "work",
                        "end": {"type": "time", "seconds": work_s},
                        "target": {"type": "hr_band", "low_bpm": 164, "high_bpm": 173},
                    },
                    {
                        "kind": "recovery",
                        "end": {"type": "time", "seconds": 120},
                        "target": {"type": "none"},
                    },
                ],
            },
        ],
        "warnings": [],
    }


def test_status_sees_an_edit_inside_a_repeat_group(conn, tmp_path):
    """The interval itself was shortened; nothing outside the repeat block moved."""
    _seed_pushed(tmp_path, spec=_repeat_spec())
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=_repeat_spec(work_s=90),
        update_date=TOUCHED_AT,
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is True


def test_status_sees_a_changed_repeat_count(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_repeat_spec(reps=4))
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=_repeat_spec(reps=6), update_date=TOUCHED_AT
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is True


def test_status_accepts_an_untouched_repeat_workout(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_repeat_spec())
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=_repeat_spec(), update_date=TOUCHED_AT
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is False


def test_an_edit_soon_after_the_push_is_not_hidden_by_the_clock_offset(
    conn, tmp_path, local_timezone
):
    """The receipt's clock is local, Garmin's is UTC; comparing them raw hides an edit.

    Pinned to a fixed zone because the bug is invisible where the two agree: with the
    offset unhandled, an edit inside the first UTC+2 hours reads as untouched.
    """
    local_timezone("Europe/Warsaw")
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=run_spec(work_s=2400),
        update_date=_as_account_clock(PUSHED_AT, minutes_after=20),
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "edited"


def _strength_spec(weight_kg=100, exercise="back_squat"):
    """A strength spec: the projection's exercise fields are ones a run never carries."""
    return author.author(
        {
            "sport": "strength",
            "origin": "athlete",
            "date": PUSH_DATE,
            "session_type": "strength",
            "structure": {
                "exercises": [{"exercise": exercise, "sets": 2, "reps": 5, "weight_kg": weight_kg}]
            },
        },
        {"zones": None, "today": "2026-07-15"},
    )


def test_status_accepts_a_strength_workout_the_account_only_decorated(conn, tmp_path):
    """weightValue: -1 and the exercise blocks come back on every read; none is an edit."""
    _seed_pushed(tmp_path, spec=_strength_spec())
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=_strength_spec(), update_date=TOUCHED_AT
    )
    entry = pub.workouts[1000]
    entry["workoutSegments"] = as_account_read_back(entry)["workoutSegments"]
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is False


def test_status_sees_a_reweighted_strength_set(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_strength_spec(weight_kg=100))
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=_strength_spec(weight_kg=120),
        update_date=TOUCHED_AT,
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "edited"
    assert out["reconciled"]["steps_changed"] is True


def test_status_sees_a_swapped_exercise(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_strength_spec(exercise="back_squat"))
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=_strength_spec(exercise="deadlift"),
        update_date=TOUCHED_AT,
    )
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["steps_changed"] is True


class UnreadableDetailPublisher(FakePublisher):
    """A publisher whose library reads work but whose detail call times out."""

    def get_workout(self, workout_id):
        raise RuntimeError("garmin: timed out")


def test_status_reports_unverified_when_the_detail_call_fails(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(UnreadableDetailPublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "unverified"


def _receipt(day_dir):
    return json.loads((day_dir / "push.json").read_text())


def test_status_appends_the_finding_to_the_receipt_on_a_state_change(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    assert _receipt(day_dir)["reconciled"]["state"] == "missing"


def test_status_leaves_the_receipts_own_fields_untouched_when_it_writes(conn, tmp_path):
    """The receipt records an event that did happen; only the finding is added."""
    day_dir = _seed_pushed(tmp_path)
    before = _receipt(day_dir)
    _status(conn, tmp_path, FakePublisher())
    after = _receipt(day_dir)
    assert {k: v for k, v in after.items() if k != "reconciled"} == before


def test_status_does_not_rewrite_the_receipt_when_the_state_is_unchanged(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    stamped = _receipt(day_dir)
    stamped["reconciled"]["checked_at"] = "2000-01-01T00:00:00"
    (day_dir / "push.json").write_text(json.dumps(stamped))
    _status(conn, tmp_path, FakePublisher())
    assert _receipt(day_dir)["reconciled"]["checked_at"] == "2000-01-01T00:00:00"


def test_status_never_overwrites_a_finding_with_an_unverified_read(conn, tmp_path):
    """Absence of information is not information: one offline read must not erase it."""
    day_dir = _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    _status(conn, tmp_path, UnreachablePublisher())
    assert _receipt(day_dir)["reconciled"]["state"] == "missing"


def test_status_serves_the_last_known_finding_when_the_account_is_unreachable(conn, tmp_path):
    _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    out = _status(conn, tmp_path, UnreachablePublisher())
    assert out["reconciled"]["state"] == "unverified"
    assert out["reconciled"]["last_known"]["state"] == "missing"


def test_status_returns_the_receipt_without_doubling_the_stored_finding(conn, tmp_path):
    """The receipt is returned as it records the push; the finding is reported once."""
    day_dir = _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    stored = _receipt(day_dir)
    out = _status(conn, tmp_path, FakePublisher())
    assert out["push"] == {k: v for k, v in stored.items() if k != "reconciled"}
    assert out["reconciled"]["state"] == "missing"


def test_status_writes_nothing_when_an_unverified_read_has_no_prior_finding(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    out = _status(conn, tmp_path, UnreachablePublisher())
    assert "reconciled" not in _receipt(day_dir)
    assert out["reconciled"]["last_known"] is None


def test_a_successful_push_replaces_the_receipt_and_drops_the_stale_finding(
    conn, tmp_path, fixture
):
    """A new push is a new event; the previous finding describes a workout that is gone."""
    request = fixture("tempo_request")
    push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    day_dir = tmp_path / FUTURE
    spec = json.loads((day_dir / "workout.json").read_text())
    (day_dir / "push.json").write_text(
        json.dumps({"workout_id": 1, "reconciled": {"state": "missing"}})
    )
    pub = FakePublisher()
    push.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=_token(conn, spec),
        connect=lambda: pub,
        reports_dir=str(tmp_path),
    )
    assert "reconciled" not in _receipt(day_dir)


def test_status_reads_the_library_and_the_calendar_once_each(conn, tmp_path):
    """One login and one read of each surface per date - the cost OPERATIONS.md quotes."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    connect, calls = _recording_connect(pub)
    push.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert calls == ["connect"]
    assert pub.reads == ["list_workouts", "list_scheduled"]


def test_status_records_when_the_account_was_consulted(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["checked_at"].startswith(dt.date.today().isoformat())


def test_get_workout_status_with_no_artifacts_is_explicit(conn, tmp_path):
    out = _status(conn, tmp_path, FakePublisher())
    assert out["workout"] is None
    assert out["push"] is None


FUTURE = (dt.date.today() + dt.timedelta(days=2)).isoformat()


LATER = (dt.date.today() + dt.timedelta(days=3)).isoformat()


def _seed_plan(conn, date: str, intent: str = "quality") -> None:
    """Author the plan of record over ``date``'s whole week at one intent.

    The action tools resolve the plan themselves to guard what may be authored and
    pushed (issue #22), and ``FUTURE`` is relative to the wall clock - so without a
    pinned plan the static template decides, and a tempo request would author on a
    Tuesday and be refused on a Wednesday.
    """
    monday = dt.date.fromisoformat(date)
    monday -= dt.timedelta(days=monday.weekday())
    plan_mod.upsert_week(
        conn,
        [
            {"week_start": monday.isoformat(), "dow": dow, "planned": "sesja", "intent": intent}
            for dow in range(7)
        ],
    )


@pytest.fixture(autouse=True)
def _planned_future(conn):
    """Pin that plan for every test below, so they measure the push path, not the guard."""
    _seed_plan(conn, FUTURE)


def test_author_workout_refuses_an_invalid_strength_session_type(conn, tmp_path):
    request = {"sport": "strength", "origin": "athlete", "session_type": "quality"}
    out = push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    assert out["spec"] is None
    assert out["error"] is not None


def test_author_workout_without_recommendation_errors(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(push.digest, "build_digest", lambda *a, **k: {})
    out = push.author_workout(conn, date=FUTURE, reports_dir=str(tmp_path))
    assert out["spec"] is None
    assert "recommendation" in out["error"]


def test_push_preview_returns_action_hash_and_payload(conn, tmp_path, fixture, fake_publisher):
    request = fixture("tempo_request")
    push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()
    out = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    assert out["action"] == "create"
    assert out["spec_hash"]
    assert out["payload"]["workoutName"].startswith("GC ")
    assert pub.calls == []


def test_push_confirm_refuses_a_stale_token(conn, tmp_path, fixture, fake_publisher):
    request = fixture("tempo_request")
    push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()
    out = push.push_confirm(
        conn, date=FUTURE, confirm_token="deadbeef", connect=lambda: pub, reports_dir=str(tmp_path)
    )
    assert out["error"] is not None
    assert "stale" in out["error"]
    assert pub.calls == []


def _seed_spec(tmp_path, spec):
    """Write a spec straight to the day's report dir, as ``author_workout`` would."""
    day_dir = tmp_path / spec["date"]
    day_dir.mkdir(exist_ok=True)
    (day_dir / "workout.json").write_text(json.dumps(spec))
    return spec


def _token(conn, spec):
    """The token a preview would have minted: the spec against the plan of record."""
    return publish.confirm_token(spec, plan_mod.planned_intent(conn, spec["date"]))


def _confirm(conn, tmp_path, pub, spec, **kw):
    return push.push_confirm(
        conn,
        date=spec["date"],
        confirm_token=_token(conn, spec),
        connect=lambda: pub,
        reports_dir=str(tmp_path),
        **kw,
    )


def test_push_confirm_does_not_duplicate_a_workout_renamed_in_connect(conn, tmp_path):
    """The MCP push must resolve a renamed workout exactly as `garmin-coach push` does (#40)."""
    spec = _seed_spec(tmp_path, run_spec(date=FUTURE))
    pub = FakePublisher()
    _confirm(conn, tmp_path, pub, spec)
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"
    pub.calls.clear()
    out = _confirm(conn, tmp_path, pub, spec)
    assert out["action"] == "noop"
    assert len(pub.workouts) == 1
    assert pub.calls == []


def test_push_confirm_resolves_a_renamed_workout_by_the_receipts_id(conn, tmp_path):
    """Rename plus a changed spec: only the receipt's id still finds the workout."""
    pub = FakePublisher()
    _confirm(conn, tmp_path, pub, _seed_spec(tmp_path, run_spec(date=FUTURE)))
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"
    changed = _seed_spec(tmp_path, run_spec(date=FUTURE, work_s=2400))
    out = _confirm(conn, tmp_path, pub, changed)
    assert out["action"] == "refuse"
    assert len(pub.workouts) == 1


def test_push_preview_without_a_spec_is_explicit(conn, tmp_path, fake_publisher):
    out = push.push_preview(conn, date=FUTURE, connect=fake_publisher, reports_dir=str(tmp_path))
    assert out["error"] is not None
    assert "workout.json" in out["error"]


def _retarget_spec(tmp_path, from_date, to_date):
    """Move the authored spec's own date without touching its name or steps."""
    path = tmp_path / from_date / "workout.json"
    spec = json.loads(path.read_text())
    spec["date"] = to_date
    path.write_text(json.dumps(spec))


def test_push_confirm_refuses_a_token_from_before_a_date_change(
    conn, tmp_path, fixture, fake_publisher
):
    """A token from one day must not confirm the identical workout on another day.

    The LATER spec is a byte-for-byte copy of FUTURE's with only the date changed,
    so the date-free gc-hash is identical for both pushes - only the token can tell
    them apart, and the guard assertion below keeps this test discriminating.
    """
    request = fixture("tempo_request")
    push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    spec = json.loads((tmp_path / FUTURE / "workout.json").read_text())
    retargeted = {**spec, "date": LATER}
    (tmp_path / LATER).mkdir()
    (tmp_path / LATER / "workout.json").write_text(json.dumps(retargeted))
    assert publish.spec_hash(spec) == publish.spec_hash(retargeted)
    pub = fake_publisher()
    preview = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    out = push.push_confirm(
        conn,
        date=LATER,
        confirm_token=preview["confirm_token"],
        connect=lambda: pub,
        reports_dir=str(tmp_path),
    )
    assert out["error"] is not None
    assert "stale" in out["error"]
    assert pub.calls == []


def test_a_spec_filed_under_another_date_is_refused(conn, tmp_path, fixture, fake_publisher):
    """The folder is the target day; a spec pointing elsewhere would push the wrong day."""
    request = fixture("tempo_request")
    push.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    _retarget_spec(tmp_path, FUTURE, LATER)
    out = push.push_preview(conn, date=FUTURE, connect=fake_publisher, reports_dir=str(tmp_path))
    assert out["error"] is not None
    assert LATER in out["error"]


def test_author_workout_refuses_a_session_harder_than_the_plan(conn, tmp_path, fixture):
    """The 2026-07-17 case, at the seam the coach skill actually calls."""
    _seed_plan(conn, FUTURE, "easy")
    out = push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    assert out["spec"] is None
    assert "planned as easy" in out["error"]
    assert not (tmp_path / FUTURE / "workout.json").exists()


def test_author_workout_still_allows_a_session_the_plan_asked_for(conn, tmp_path, fixture):
    _seed_plan(conn, FUTURE, "quality")
    out = push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    assert out["error"] is None


def test_push_preview_refuses_a_spec_the_revised_plan_no_longer_allows(
    conn, tmp_path, fixture, fake_publisher
):
    """Authoring passed under the old plan; the revision landed before the push."""
    push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    _seed_plan(conn, FUTURE, "easy")
    pub = fake_publisher()
    out = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    assert out["action"] == "refuse"
    assert "planned as easy" in out["message"]
    assert pub.calls == []


def test_push_confirm_refuses_a_spec_the_revised_plan_no_longer_allows(
    conn, tmp_path, fixture, fake_publisher
):
    push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    pub = fake_publisher()
    preview = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    _seed_plan(conn, FUTURE, "easy")
    out = push.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["confirm_token"],
        connect=lambda: pub,
        reports_dir=str(tmp_path),
    )
    assert out["applied"] is False
    assert "stale" in out["error"]
    assert pub.calls == []


def test_push_confirm_names_the_plan_among_the_things_a_stale_token_means(
    conn, tmp_path, fixture, fake_publisher
):
    """A refusal the athlete cannot act on is a dead end: the message has to say
    that a revised plan is one of the ways the preview goes stale."""
    push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    out = push.push_confirm(
        conn,
        date=FUTURE,
        confirm_token="deadbeef",
        connect=fake_publisher,
        reports_dir=str(tmp_path),
    )
    assert "plan" in out["error"]


def test_the_push_receipt_records_the_plan_it_was_measured_against(
    conn, tmp_path, fixture, fake_publisher
):
    push.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    pub = fake_publisher()
    preview = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    push.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["confirm_token"],
        connect=lambda: pub,
        reports_dir=str(tmp_path),
    )
    receipt = json.loads((tmp_path / FUTURE / "push.json").read_text())
    assert receipt["planned_intent"] == "quality"


def test_status_reports_no_divergence_while_the_plan_still_allows_the_push(conn, tmp_path):
    _seed_pushed(tmp_path)
    out = _status(conn, tmp_path, FakePublisher())
    assert out["plan_divergence"] is None


def test_status_reports_divergence_once_the_plan_drops_below_the_pushed_session(conn, tmp_path):
    """The 2026-07-17 case as a read: the session is on the watch and the plan that
    would have justified it is gone."""
    _seed_pushed(tmp_path)
    _seed_plan(conn, PUSH_DATE, "easy")
    out = _status(conn, tmp_path, FakePublisher())
    assert out["plan_divergence"] == {
        "pushed_type": "tempo",
        "planned_intent": "easy",
        "pushed_at": PUSHED_AT,
    }


def test_the_divergence_reads_the_type_the_receipt_recorded_over_a_respec(conn, tmp_path):
    """A spec re-authored after the push says nothing about what is on the watch."""
    _seed_pushed(tmp_path, session_type="quality")
    _seed_plan(conn, PUSH_DATE, "easy")
    out = _status(conn, tmp_path, FakePublisher())
    assert out["plan_divergence"]["pushed_type"] == "quality"


def test_a_date_that_was_never_pushed_has_nothing_to_diverge(conn, tmp_path):
    _seed_plan(conn, PUSH_DATE, "rest")
    out = _status(conn, tmp_path, FakePublisher())
    assert out["plan_divergence"] is None


def _authorable_day(conn) -> str:
    """A near-future date whose plan of record allows anything (the guards are not the test)."""
    date = dt.date.today() + dt.timedelta(days=2)
    monday = (date - dt.timedelta(days=date.weekday())).isoformat()
    plan_mod.upsert_week(
        conn,
        [
            {"week_start": monday, "dow": dow, "planned": "quality", "intent": "quality"}
            for dow in range(7)
        ],
    )
    return date.isoformat()


def test_author_workout_carries_the_athletes_label_into_the_spec(conn, tmp_path):
    """The name on the watch comes from the conversation, not from the session type (#58)."""
    _seed_mart(conn, YESTERDAY, hrv=60)
    day = _authorable_day(conn)
    request = {
        "sport": "strength",
        "origin": "athlete",
        "date": day,
        "session_type": "strength",
        "label": "FBB A",
        "structure": {"exercises": [{"exercise": "back_squat", "sets": 2, "reps": 5}]},
    }
    out = push.author_workout(conn, day, request=request, reports_dir=str(tmp_path))
    assert out["spec"]["name"] == f"GC {day} FBB A"


def test_author_workout_reports_a_malformed_label_as_tool_text(conn, tmp_path):
    _seed_mart(conn, YESTERDAY, hrv=60)
    day = _authorable_day(conn)
    request = {
        "sport": "strength",
        "origin": "athlete",
        "date": day,
        "session_type": "strength",
        "label": "x" * 40,
        "structure": {"exercises": [{"exercise": "back_squat", "sets": 2, "reps": 5}]},
    }
    out = push.author_workout(conn, day, request=request, reports_dir=str(tmp_path))
    assert out["spec"] is None
    assert "30" in out["error"]


def _push_receipt(reports, date, **over):
    day = reports / date
    day.mkdir(parents=True, exist_ok=True)
    receipt = {
        "action": "create",
        "applied": True,
        "workout_id": 1000,
        "spec_hash": "abc123",
        "name": "GC FBB A",
        "session_type": "strength",
        "planned_intent": "strength",
        "pushed_at": f"{date}T18:00:00",
    }
    receipt.update(over)
    (day / "push.json").write_text(json.dumps(receipt))
    return receipt


def test_author_workout_repeats_a_spec_from_another_day(conn, tmp_path):
    """ "Repeat FBB A from the 19th on Friday" needs no retyping of the steps (#58)."""
    _seed_mart(conn, YESTERDAY, hrv=60)
    source, target = (_authorable_day(conn), (dt.date.today() + dt.timedelta(days=3)).isoformat())
    day = tmp_path / source
    day.mkdir(parents=True)
    spec = {
        "sport": "strength",
        "origin": "athlete",
        "date": source,
        "session_type": "strength",
        "name": "GC FBB A",
        "steps": [
            {"kind": "work", "end": {"type": "reps", "count": 5}, "target": {"type": "none"}}
        ],
        "warnings": [],
    }
    (day / "workout.json").write_text(json.dumps(spec))
    out = push.author_workout(conn, target, reuse_from=source, reports_dir=str(tmp_path))
    assert out["error"] is None
    assert out["spec"]["date"] == target
    assert out["spec"]["name"] == "GC FBB A"
    assert out["spec"]["steps"] == spec["steps"]
    assert json.loads((tmp_path / target / "workout.json").read_text())["name"] == "GC FBB A"


def test_repeating_a_session_the_new_days_plan_forbids_is_refused(conn, tmp_path):
    _seed_mart(conn, YESTERDAY, hrv=60)
    source = _authorable_day(conn)
    target = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    monday = dt.date.fromisoformat(target)
    plan_mod.upsert_week(
        conn,
        [
            {
                "week_start": (monday - dt.timedelta(days=monday.weekday())).isoformat(),
                "dow": dow,
                "planned": "rest",
                "intent": "rest",
            }
            for dow in range(7)
        ],
    )
    day = tmp_path / source
    day.mkdir(parents=True)
    (day / "workout.json").write_text(
        json.dumps(
            {
                "sport": "strength",
                "origin": "athlete",
                "date": source,
                "session_type": "strength",
                "name": "GC FBB A",
                "steps": [],
                "warnings": [],
            }
        )
    )
    out = push.author_workout(conn, target, reuse_from=source, reports_dir=str(tmp_path))
    assert out["spec"] is None
    assert "rest" in out["error"]


def test_repeating_a_day_with_no_spec_says_so(conn, tmp_path):
    _seed_mart(conn, YESTERDAY, hrv=60)
    target = _authorable_day(conn)
    out = push.author_workout(conn, target, reuse_from="2026-07-01", reports_dir=str(tmp_path))
    assert out["spec"] is None
    assert "2026-07-01" in out["error"]


def test_get_pushed_workouts_carries_the_last_known_account_state(conn, tmp_path):
    _push_receipt(tmp_path, "2026-09-19", reconciled={"state": "live", "checked_at": "2026-09-20"})
    out = push.get_pushed_workouts(conn, reports_dir=str(tmp_path))
    assert out["workouts"][0]["last_state"] == "live"


def test_get_pushed_workouts_can_start_at_a_date(conn, tmp_path):
    _push_receipt(tmp_path, "2026-09-19")
    _push_receipt(tmp_path, "2026-08-01")
    out = push.get_pushed_workouts(conn, since="2026-09-01", reports_dir=str(tmp_path))
    assert [w["date"] for w in out["workouts"]] == ["2026-09-19"]


def test_get_pushed_workouts_without_any_receipts_is_empty(conn, tmp_path):
    out = push.get_pushed_workouts(conn, reports_dir=str(tmp_path))
    assert out["workouts"] == []


def _connect_recording(pub):
    """A publisher factory that records each login, for the no-login refusals."""
    logins: list[str] = []

    def connect():
        logins.append("login")
        return pub

    return (connect, logins)


def _off_preview(conn, tmp_path, pub, date=PUSH_DATE):
    return push.unschedule_preview(conn, date=date, connect=lambda: pub, reports_dir=str(tmp_path))


def _off_confirm(conn, tmp_path, pub, token, date=PUSH_DATE):
    return push.unschedule_confirm(
        conn, date=date, confirm_token=token, connect=lambda: pub, reports_dir=str(tmp_path)
    )


def _taken_off(conn, tmp_path, pub, date=PUSH_DATE):
    """Preview and confirm in one go, returning the confirm's response."""
    preview = _off_preview(conn, tmp_path, pub, date)
    return _off_confirm(conn, tmp_path, pub, preview["confirm_token"], date)


def test_unschedule_preview_without_a_receipt_never_logs_in(conn, tmp_path):
    connect, logins = _connect_recording(FakePublisher())
    out = push.unschedule_preview(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert "no push receipt" in out["error"]
    assert out["confirm_token"] is None
    assert logins == []


def test_unschedule_preview_with_a_receipt_naming_no_workout_never_logs_in(conn, tmp_path):
    """A push that failed before uploading recorded no id: nothing on the account to take off."""
    day_dir = _seed_pushed(tmp_path)
    receipt = json.loads((day_dir / "push.json").read_text())
    (day_dir / "push.json").write_text(json.dumps({**receipt, "workout_id": None}))
    connect, logins = _connect_recording(FakePublisher())
    out = push.unschedule_preview(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert "never reached the account" in out["error"]
    assert logins == []


def test_unschedule_preview_names_the_workout_and_hands_over_a_token(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    out = _off_preview(conn, tmp_path, pub)
    data = out
    assert data["action"] == "unschedule"
    assert data["name"] == PUSHED_NAME
    assert data["workout_id"] == 1000
    assert data["schedule_ids"] == [5000]
    assert data["confirm_token"]
    assert data["error"] is None
    assert pub.calls == []


def test_unschedule_preview_names_the_current_name_when_the_athlete_renamed_it(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), name="Hyrox Tempo", scheduled_on=PUSH_DATE)
    out = _off_preview(conn, tmp_path, pub)
    assert out["renamed_to"] == "Hyrox Tempo"
    assert "Hyrox Tempo" in out["message"]


def test_unschedule_confirm_refuses_a_stale_token_and_leaves_the_receipt_alone(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    before = (day_dir / "push.json").read_text()
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    out = _off_confirm(conn, tmp_path, pub, "deadbeef")
    assert out["action"] == "refuse"
    assert out["applied"] is False
    assert "stale" in out["message"]
    assert pub.calls == []
    assert (day_dir / "push.json").read_text() == before


def test_a_removal_the_account_refuses_writes_nothing(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    before = (day_dir / "push.json").read_text()
    pub = _account_with(FakePublisher(), scheduled_on=None)
    preview = _off_preview(conn, tmp_path, pub)
    out = _off_confirm(conn, tmp_path, pub, "0000000000000000")
    assert preview["action"] == "refuse"
    assert preview["confirm_token"] is None
    assert "not on" in preview["message"]
    assert out["applied"] is False
    assert (day_dir / "push.json").read_text() == before


class _FailingUnschedule(FakePublisher):
    def unschedule(self, schedule_id):
        self.calls.append("unschedule-fail")
        raise RuntimeError("calendar timed out")


def test_a_failed_removal_reports_the_error_and_marks_nothing(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)
    before = (day_dir / "push.json").read_text()
    pub = _account_with(_FailingUnschedule(), scheduled_on=PUSH_DATE)
    out = _taken_off(conn, tmp_path, pub)
    assert out["applied"] is False
    assert out["error"] == "calendar timed out"
    assert "0 of 1" in out["message"]
    assert (day_dir / "push.json").read_text() == before


def test_an_offline_status_read_after_a_removal_serves_unscheduled(conn, tmp_path):
    _seed_pushed(tmp_path)
    _taken_off(conn, tmp_path, _account_with(FakePublisher(), scheduled_on=PUSH_DATE))
    out = _status(conn, tmp_path, UnreachablePublisher())
    assert out["reconciled"]["state"] == "unverified"
    assert out["reconciled"]["last_known"]["state"] == "unscheduled"
    assert out["push"]["unscheduled_at"]


def test_the_listing_marks_a_day_taken_off(conn, tmp_path):
    _seed_pushed(tmp_path)
    _taken_off(conn, tmp_path, _account_with(FakePublisher(), scheduled_on=PUSH_DATE))
    row = push.get_pushed_workouts(conn, reports_dir=str(tmp_path))["workouts"][0]
    assert row["date"] == PUSH_DATE
    assert row["last_state"] == "unscheduled"
    assert row["unscheduled_at"]


def test_status_stops_reporting_divergence_for_a_day_taken_off(conn, tmp_path):
    """Off the calendar is off the watch: the plan has nothing to diverge from (#74)."""
    _seed_pushed(tmp_path)
    _seed_plan(conn, PUSH_DATE, "easy")
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    assert _status(conn, tmp_path, pub)["plan_divergence"] is not None
    _taken_off(conn, tmp_path, pub)
    assert _status(conn, tmp_path, pub)["plan_divergence"] is None


def test_a_push_after_a_removal_schedules_the_workout_back(conn, tmp_path):
    """Recovering from a sick week is one preview and one confirm, with no second upload."""
    spec = _seed_spec(tmp_path, run_spec(date=FUTURE))
    pub = FakePublisher()
    _confirm(conn, tmp_path, pub, spec)
    _taken_off(conn, tmp_path, pub, date=FUTURE)
    pub.calls.clear()
    out = push.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))
    assert out["action"] == "schedule"
    assert len(pub.workouts) == 1


def test_divergence_returns_when_the_athlete_puts_the_workout_back_by_hand(conn, tmp_path):
    """Off the calendar is off the watch - until a read finds it back on the day."""
    _seed_pushed(tmp_path)
    _seed_plan(conn, PUSH_DATE, "easy")
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    _taken_off(conn, tmp_path, pub)
    pub.schedule(1000, PUSH_DATE)
    out = _status(conn, tmp_path, pub)
    assert out["reconciled"]["state"] == "live"
    assert out["plan_divergence"] is not None


def test_unschedule_preview_with_an_unreadable_receipt_never_logs_in(conn, tmp_path):
    (tmp_path / PUSH_DATE).mkdir()
    (tmp_path / PUSH_DATE / "push.json").write_text("not json")
    connect, logins = _connect_recording(FakePublisher())
    out = push.unschedule_preview(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))
    assert "cannot be read" in out["error"]
    assert logins == []
