"""Tool functions behind the coach MCP server (epic #18).

Seam: the pure functions in ``mcp.tools`` - each wraps a reader the CLI already
uses and returns a freshness envelope. Tests seed the temp DB (or a tmp reports
dir) and assert on the returned dicts; the MCP protocol layer is not involved.
"""

from __future__ import annotations

import datetime as dt
import json
import types

import pytest

from garmin_coach.core import db, events
from garmin_coach.core import plan as plan_mod
from garmin_coach.marts import snapshot
from garmin_coach.mcp import tools
from garmin_coach.workouts import author, publish
from tests.conftest import FakeGarminClient, FakePublisher, as_account_read_back, run_spec

DATA_START = "2026-06-08"
TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _seed_mart(conn, date: str, **cols) -> None:
    db.upsert_daily(conn, "daily_metrics", {"date": date, **cols})


# --- freshness envelope ---------------------------------------------------


def test_envelope_reports_mart_horizon_without_today(conn):
    """Mart through yesterday: data_through set, nothing flagged partial."""
    _seed_mart(conn, YESTERDAY, hrv=60)

    out = tools.get_zones(conn)

    assert out["freshness"]["data_through"] == YESTERDAY
    assert out["freshness"]["today_included"] is False
    assert out["freshness"]["partial_fields"] == []


def test_envelope_flags_intraday_fields_when_today_included(conn):
    """Mart through today (post refresh-today): intraday fields flagged partial."""
    _seed_mart(conn, TODAY, hrv=60)

    out = tools.get_zones(conn)

    assert out["freshness"]["today_included"] is True
    assert "load_day" in out["freshness"]["partial_fields"]
    assert "acwr" in out["freshness"]["partial_fields"]
    assert "hrv" not in out["freshness"]["partial_fields"]
    assert "sleep_score" not in out["freshness"]["partial_fields"]


# --- read tools -----------------------------------------------------------


def test_get_snapshot_returns_the_athlete_status_row(conn):
    _seed_mart(conn, YESTERDAY, hrv=61, load_day=80.0)
    snapshot.rollup(conn)

    out = tools.get_snapshot(conn)

    assert out["data"] is not None
    assert out["data"]["computed_at"] == YESTERDAY


def test_get_snapshot_without_a_rollup_returns_none_data(conn):
    out = tools.get_snapshot(conn)

    assert out["data"] is None


def test_get_digest_builds_the_cited_digest(conn):
    for i in range(3):
        d = (dt.date.fromisoformat("2026-07-01") + dt.timedelta(days=i)).isoformat()
        _seed_mart(conn, d, hrv=60 + i, load_day=50.0)

    out = tools.get_digest(conn, to_date="2026-07-03")

    assert "signals" in out["data"]
    assert out["data"]["window"]["to"] == "2026-07-03"


def test_get_recent_activities_orders_newest_first_and_limits(conn):
    for i in range(4):
        db.upsert_activity(
            conn,
            {
                "activity_id": i + 1,
                "start_local": f"2026-07-{10 + i:02d} 08:00:00",
                "date": f"2026-07-{10 + i:02d}",
                "gtype": "running",
                "discipline": "Bieganie",
                "dur_s": 1800.0,
            },
        )

    out = tools.get_recent_activities(conn, n=2)

    dates = [a["date"] for a in out["data"]]
    assert dates == ["2026-07-13", "2026-07-12"]
    assert all("partial_today" not in a for a in out["data"])


def test_get_recent_activities_flags_a_today_activity_as_partial(conn):
    """An activity dated today is marked partial (its TE may still settle)."""
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "start_local": f"{TODAY} 08:00:00",
            "date": TODAY,
            "gtype": "running",
            "discipline": "Bieganie",
            "aero_te": 2.1,
            "dur_s": 1800.0,
        },
    )

    out = tools.get_recent_activities(conn, n=1)

    assert out["data"][0]["partial_today"] is True


def test_get_weekly_returns_the_requested_week(conn):
    conn.execute(
        "INSERT INTO weekly_metrics (week_start, load_total, n_sessions) VALUES (?, ?, ?)",
        ("2026-07-06", 320.0, 5),
    )

    out = tools.get_weekly(conn, week_start="2026-07-06")

    assert len(out["data"]["weeks"]) == 1
    assert out["data"]["weeks"][0]["load_total"] == 320.0


def test_get_weekly_without_week_start_returns_all_weeks(conn):
    for ws in ("2026-06-29", "2026-07-06"):
        conn.execute("INSERT INTO weekly_metrics (week_start) VALUES (?)", (ws,))

    out = tools.get_weekly(conn)

    assert [w["week_start"] for w in out["data"]["weeks"]] == ["2026-06-29", "2026-07-06"]


def test_get_zones_returns_the_singleton_zone_row(conn):
    conn.execute(
        "INSERT INTO athlete_zones (id, lthr_bpm, threshold_pace_s_per_km, source) "
        "VALUES (1, 171, 260.0, 'regression+lthr')"
    )

    out = tools.get_zones(conn)

    assert out["data"]["lthr_bpm"] == 171
    assert out["data"]["source"] == "regression+lthr"


def test_get_recommendation_returns_the_block_for_tomorrow(conn):
    for i in range(3):
        d = (dt.date.fromisoformat("2026-07-01") + dt.timedelta(days=i)).isoformat()
        _seed_mart(conn, d, hrv=60 + i, load_day=50.0)

    out = tools.get_recommendation(conn, date="2026-07-04")

    assert out["data"]["target_date"] == "2026-07-04"
    assert "intended_type" in out["data"]
    assert "rationale" in out["data"]


def test_get_events_annotates_goal_events(conn):
    events.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="exact",
    )

    out = tools.get_events(conn, today="2026-07-15")

    assert out["data"][0]["type"] == "hyrox"
    assert out["data"][0]["weeks_to_event"] > 0


# --- workout status: receipt reconciled against the account (issue #41) -----

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
    pub,
    *,
    name=PUSHED_NAME,
    workout_id=1000,
    scheduled_on=None,
    spec=None,
    update_date=None,
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

    return connect, calls


def _status(conn, tmp_path, pub, date=PUSH_DATE):
    return tools.get_workout_status(conn, date=date, connect=lambda: pub, reports_dir=str(tmp_path))


def test_status_reports_missing_when_the_account_no_longer_holds_the_workout(conn, tmp_path):
    """The live 2026-07-17 case: the receipt claims a workout the account deleted."""
    _seed_pushed(tmp_path)
    pub = FakePublisher()

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "missing"
    assert out["data"]["reconciled"]["scheduled"] is False


def test_status_reports_unscheduled_when_the_workout_is_only_in_the_library(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher())

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "unscheduled"
    assert out["data"]["reconciled"]["scheduled"] is False


def test_status_reports_unscheduled_when_the_workout_moved_to_another_date(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on="2026-07-18")

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "unscheduled"


def test_status_reports_live_when_scheduled_on_the_date(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "live"
    assert out["data"]["reconciled"]["scheduled"] is True
    assert out["data"]["reconciled"]["renamed_to"] is None


def test_status_names_the_current_account_name_when_the_athlete_renamed_it(conn, tmp_path):
    """Renaming in Connect is the athlete's prerogative: reported, never a fault state."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), name="Hyrox Tempo", scheduled_on=PUSH_DATE)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "live"
    assert out["data"]["reconciled"]["renamed_to"] == "Hyrox Tempo"


def test_status_reports_unverified_when_the_account_cannot_be_reached(conn, tmp_path):
    _seed_pushed(tmp_path)

    out = _status(conn, tmp_path, UnreachablePublisher())

    assert out["data"]["reconciled"]["state"] == "unverified"
    assert out["data"]["push"]["applied"] is True


def test_status_reports_unverified_when_logging_in_fails(conn, tmp_path):
    """A failed login is data on this path, not an error: the read degrades."""
    _seed_pushed(tmp_path)

    def connect():
        raise RuntimeError("garmin: login failed")

    out = tools.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))

    assert out["data"]["reconciled"]["state"] == "unverified"


def test_status_without_a_receipt_never_logs_in(conn, tmp_path):
    """A date that was never pushed has nothing to check, so it costs no login."""
    connect, calls = _recording_connect(FakePublisher())

    out = tools.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))

    assert out["data"]["reconciled"] is None
    assert calls == []


def test_status_with_a_receipt_carrying_no_workout_id_never_logs_in(conn, tmp_path):
    day_dir = tmp_path / PUSH_DATE
    day_dir.mkdir()
    (day_dir / "push.json").write_text(json.dumps({"action": "refuse", "workout_id": None}))
    connect, calls = _recording_connect(FakePublisher())

    out = tools.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))

    assert out["data"]["reconciled"] is None
    assert calls == []


# --- workout status: steps edited in Garmin Connect (issue #42) -------------

TOUCHED_AT = "2026-07-16T16:16:31.0"


def test_status_reports_edited_when_the_account_steps_differ_from_the_pushed_spec(conn, tmp_path):
    """The live 1633354389 case: the athlete rewrote the steps after the push."""
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=run_spec(work_s=2400),
        update_date=TOUCHED_AT,
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "edited"
    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_reports_live_when_only_the_name_changed(conn, tmp_path):
    """Renaming bumps updateDate too; without the step check every rename would read as edited."""
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(), name="Hyrox Tempo", scheduled_on=PUSH_DATE, update_date=TOUCHED_AT
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "live"
    assert out["data"]["reconciled"]["renamed_to"] == "Hyrox Tempo"
    assert out["data"]["reconciled"]["steps_changed"] is False


def test_status_skips_the_detail_call_when_the_account_copy_was_never_touched(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, update_date=_as_account_clock(PUSHED_AT)
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "live"
    assert out["data"]["reconciled"]["steps_changed"] is False
    assert "get_workout" not in pub.reads


def test_status_reports_unscheduled_over_edited_but_keeps_the_edit_visible(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), spec=run_spec(work_s=2400), update_date=TOUCHED_AT)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "unscheduled"
    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_ignores_account_added_decoration_on_the_steps(conn, tmp_path):
    """The account decorates every step with fields no upload ever sent."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)
    entry = pub.workouts[1000]
    entry["workoutSegments"] = as_account_read_back(entry)["workoutSegments"]

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["steps_changed"] is False


def test_status_cannot_judge_the_steps_when_the_spec_was_re_authored(conn, tmp_path):
    """A local spec that no longer hashes to the receipt is not evidence of what was pushed."""
    _seed_pushed(tmp_path, spec_hash="a-hash-from-an-older-spec")
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "live"
    assert out["data"]["reconciled"]["steps_changed"] is None


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

    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_sees_a_changed_repeat_count(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_repeat_spec(reps=4))
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=_repeat_spec(reps=6), update_date=TOUCHED_AT
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_accepts_an_untouched_repeat_workout(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_repeat_spec())
    pub = _account_with(
        FakePublisher(), scheduled_on=PUSH_DATE, spec=_repeat_spec(), update_date=TOUCHED_AT
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["steps_changed"] is False


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

    assert out["data"]["reconciled"]["state"] == "edited"


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

    assert out["data"]["reconciled"]["steps_changed"] is False


def test_status_sees_a_reweighted_strength_set(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_strength_spec(weight_kg=100))
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=_strength_spec(weight_kg=120),
        update_date=TOUCHED_AT,
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "edited"
    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_sees_a_swapped_exercise(conn, tmp_path):
    _seed_pushed(tmp_path, spec=_strength_spec(exercise="back_squat"))
    pub = _account_with(
        FakePublisher(),
        scheduled_on=PUSH_DATE,
        spec=_strength_spec(exercise="deadlift"),
        update_date=TOUCHED_AT,
    )

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["steps_changed"] is True


class UnreadableDetailPublisher(FakePublisher):
    """A publisher whose library reads work but whose detail call times out."""

    def get_workout(self, workout_id):
        raise RuntimeError("garmin: timed out")


def test_status_reports_unverified_when_the_detail_call_fails(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(UnreadableDetailPublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "unverified"


# --- workout status: the finding persisted to the receipt (issue #43) -------


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

    assert out["data"]["reconciled"]["state"] == "unverified"
    assert out["data"]["reconciled"]["last_known"]["state"] == "missing"


def test_status_returns_the_receipt_without_doubling_the_stored_finding(conn, tmp_path):
    """The receipt is returned as it records the push; the finding is reported once."""
    day_dir = _seed_pushed(tmp_path)
    _status(conn, tmp_path, FakePublisher())
    stored = _receipt(day_dir)

    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["push"] == {k: v for k, v in stored.items() if k != "reconciled"}
    assert out["data"]["reconciled"]["state"] == "missing"


def test_status_writes_nothing_when_an_unverified_read_has_no_prior_finding(conn, tmp_path):
    day_dir = _seed_pushed(tmp_path)

    out = _status(conn, tmp_path, UnreachablePublisher())

    assert "reconciled" not in _receipt(day_dir)
    assert out["data"]["reconciled"]["last_known"] is None


def test_a_successful_push_replaces_the_receipt_and_drops_the_stale_finding(
    conn, tmp_path, fixture
):
    """A new push is a new event; the previous finding describes a workout that is gone."""
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    day_dir = tmp_path / FUTURE
    spec = json.loads((day_dir / "workout.json").read_text())
    (day_dir / "push.json").write_text(
        json.dumps({"workout_id": 1, "reconciled": {"state": "missing"}})
    )
    pub = FakePublisher()

    tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=_token(conn, spec),
        publisher=pub,
        reports_dir=str(tmp_path),
    )

    assert "reconciled" not in _receipt(day_dir)


def test_status_reads_the_library_and_the_calendar_once_each(conn, tmp_path):
    """One login and one read of each surface per date - the cost OPERATIONS.md quotes."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)
    connect, calls = _recording_connect(pub)

    tools.get_workout_status(conn, date=PUSH_DATE, connect=connect, reports_dir=str(tmp_path))

    assert calls == ["connect"]
    assert pub.reads == ["list_workouts", "list_scheduled"]


def test_status_returns_the_receipt_and_spec_unchanged_beside_the_finding(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["workout"]["name"] == PUSHED_NAME
    assert out["data"]["push"]["action"] == "create"
    assert out["data"]["push"]["pushed_at"] == "2026-07-15T17:28:00"
    assert "partial_fields" in out["freshness"]


def test_status_records_when_the_account_was_consulted(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["checked_at"].startswith(dt.date.today().isoformat())


def test_get_workout_status_with_no_artifacts_is_explicit(conn, tmp_path):
    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["workout"] is None
    assert out["data"]["push"] is None


# --- action tools -----------------------------------------------------------

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


def _seed_activity(conn, aid=1, date="2026-07-10") -> None:
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 18:00:00",
            "date": date,
            "gtype": "strength_training",
            "discipline": "Sila",
            "aero_te": 1.4,
            "anaero_te": 0.3,
            "training_load": 22.0,
            "dur_s": 4200,
        },
    )


def test_log_rpe_writes_and_recomputes_load(conn):
    _seed_activity(conn)

    out = tools.log_rpe(conn, activity_id=1, rpe=8, data_start_date=DATA_START)

    assert out["data"]["date"] == "2026-07-10"
    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 8


def test_log_rpe_unknown_activity_returns_error(conn):
    out = tools.log_rpe(conn, activity_id=999, rpe=8, data_start_date=DATA_START)

    assert "not found" in out["data"]["error"]


def test_log_niggle_writes_a_niggle_row(conn):
    out = tools.log_niggle(conn, body_part="achilles", severity=2, date="2026-07-10")

    assert out["data"]["date"] == "2026-07-10"
    assert conn.execute("SELECT severity FROM niggle WHERE body_part='achilles'").fetchone()[0] == 2


def test_log_sets_writes_the_overlay_and_returns_the_recompute_date(conn):
    _seed_activity(conn, aid=1, date="2026-07-28")

    out = tools.log_sets(
        conn,
        activity_id=1,
        stations=["SLED_PULL", "SANDBAG_CARRY", "WALL_WALK"],
        data_start_date=DATA_START,
    )

    assert out["data"] == {
        "activity_id": 1,
        "date": "2026-07-28",
        "n_sets": 3,
        "unmapped": ["WALL_WALK"],
        "error": None,
    }
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 3


def test_log_sets_unknown_activity_returns_error(conn):
    out = tools.log_sets(conn, activity_id=999, stations=["SLED_PULL"], data_start_date=DATA_START)

    assert "not found" in out["data"]["error"]
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 0


def test_log_sets_malformed_station_returns_error_without_writing(conn):
    _seed_activity(conn, aid=1, date="2026-07-28")

    out = tools.log_sets(
        conn, activity_id=1, stations=["SLED_PULL", ""], data_start_date=DATA_START
    )

    assert "station 1" in out["data"]["error"]
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 0


def test_refresh_today_tool_reports_status_and_envelope(conn, fake_client):
    client = fake_client()

    out = tools.refresh_today(conn, client, data_start_date=DATA_START, today=TODAY)

    assert out["data"]["status"] == "ok"
    assert out["data"]["features_ok"] is True
    assert out["freshness"]["today_included"] is True


def test_author_workout_from_request_writes_the_spec(conn, tmp_path, fixture):
    request = fixture("tempo_request")

    out = tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))

    assert out["data"]["error"] is None
    assert out["data"]["spec"]["date"] == FUTURE
    assert (tmp_path / FUTURE / "workout.json").exists()


def test_author_workout_defers_strength(conn, tmp_path):
    request = {"sport": "strength", "origin": "athlete", "session_type": "quality"}

    out = tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))

    assert out["data"]["spec"] is None
    assert out["data"]["error"] is not None


def test_author_workout_without_recommendation_errors(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(tools.digest, "build_digest", lambda *a, **k: {})

    out = tools.author_workout(conn, date=FUTURE, reports_dir=str(tmp_path))

    assert out["data"]["spec"] is None
    assert "recommendation" in out["data"]["error"]


def test_push_preview_returns_action_hash_and_payload(conn, tmp_path, fixture, fake_publisher):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()

    out = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    assert out["data"]["action"] == "create"
    assert out["data"]["spec_hash"]
    assert out["data"]["payload"]["workoutName"].startswith("GC ")
    assert pub.calls == []


def test_push_confirm_refuses_a_stale_token(conn, tmp_path, fixture, fake_publisher):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()

    out = tools.push_confirm(
        conn, date=FUTURE, confirm_token="deadbeef", publisher=pub, reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is not None
    assert "stale" in out["data"]["error"]
    assert pub.calls == []


def test_push_confirm_with_matching_token_uploads_and_schedules(
    conn, tmp_path, fixture, fake_publisher
):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    out = tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["data"]["confirm_token"],
        publisher=pub,
        reports_dir=str(tmp_path),
    )

    assert out["data"]["error"] is None
    assert out["data"]["applied"] is True
    assert "upload" in pub.calls and "schedule" in pub.calls
    assert (tmp_path / FUTURE / "push.json").exists()


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
    return tools.push_confirm(
        conn,
        date=spec["date"],
        confirm_token=_token(conn, spec),
        publisher=pub,
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

    assert out["data"]["action"] == "noop"
    assert len(pub.workouts) == 1
    assert pub.calls == []


def test_push_confirm_resolves_a_renamed_workout_by_the_receipts_id(conn, tmp_path):
    """Rename plus a changed spec: only the receipt's id still finds the workout."""
    pub = FakePublisher()
    _confirm(conn, tmp_path, pub, _seed_spec(tmp_path, run_spec(date=FUTURE)))
    pub.workouts[1000]["workoutName"] = "Hyrox Tempo"

    changed = _seed_spec(tmp_path, run_spec(date=FUTURE, work_s=2400))
    out = _confirm(conn, tmp_path, pub, changed)

    assert out["data"]["action"] == "refuse"  # refused rather than creating a second copy
    assert len(pub.workouts) == 1


def test_push_preview_without_a_spec_is_explicit(conn, tmp_path, fake_publisher):
    out = tools.push_preview(
        conn, date=FUTURE, publisher=fake_publisher(), reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is not None
    assert "workout.json" in out["data"]["error"]


# --- issue #21: the plan of record over MCP ---------------------------------

WEEK = "2026-07-13"  # a Monday


def _cached_days(conn, week_start=WEEK) -> int:
    """How many days of one week the plan cache holds (other weeks are seeded)."""
    return conn.execute(
        "SELECT COUNT(*) FROM plan_week WHERE week_start = ?", (week_start,)
    ).fetchone()[0]


def _plan_file(tmp_path, week_start=WEEK, intents=None):
    intents = intents or ["easy", "quality", "rest", "quality", "easy", "rest", "quality"]
    monday = dt.date.fromisoformat(week_start)
    rows = "\n".join(
        f"| {abbr} | {(monday + dt.timedelta(days=i)).strftime('%d.%m')} | sesja {i} "
        f"| {intent} | plan |"
        for i, (abbr, intent) in enumerate(
            zip(("Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"), intents)
        )
    )
    text = (
        "| Dzień | Data | Plan | Zamiar (dla silnika) | Status |\n"
        "|---|---|---|---|---|\n" + rows + "\n"
    )
    (tmp_path / f"{week_start}_week.md").write_text(text, encoding="utf-8")
    return tmp_path


def test_get_plan_returns_the_authored_week_with_its_source(conn, tmp_path):
    from garmin_coach.core import plan as plan_mod

    plan_mod.import_dir(conn, _plan_file(tmp_path))

    out = tools.get_plan(conn, week_start=WEEK)

    assert out["data"]["week_start"] == WEEK
    days = out["data"]["days"]
    assert len(days) == 7
    assert days[3] == {
        "date": "2026-07-16",
        "dow": 3,
        "planned": "sesja 3",
        "intent": "quality",
        "source": "plan_week",
    }
    assert out["data"]["has_plan"] is True
    assert "freshness" in out


def test_get_plan_reports_the_template_fallback_for_an_unplanned_week(conn):
    out = tools.get_plan(conn, week_start=WEEK)

    assert out["data"]["has_plan"] is False
    assert {d["source"] for d in out["data"]["days"]} == {"plan_template"}


def test_get_plan_defaults_to_the_current_week(conn):
    out = tools.get_plan(conn)

    monday = (dt.date.today() - dt.timedelta(days=dt.date.today().weekday())).isoformat()
    assert out["data"]["week_start"] == monday


def test_get_plan_rejects_a_non_monday(conn):
    out = tools.get_plan(conn, week_start="2026-07-14")

    assert "Monday" in out["data"]["error"]


# --- issue #21: the write path ----------------------------------------------

PROPOSAL = [
    {"planned": "bieg easy 10 km, Zone 2", "intent": "easy"},
    {"planned": "FBB + Hyrox", "intent": "quality"},
    {"planned": "rest", "intent": "rest"},
    {"planned": "tempo 8x1 km", "intent": "tempo"},
    {"planned": "bieg easy 10 km", "intent": "easy"},
    {"planned": "rest", "intent": "rest"},
    {"planned": "Crossfit", "intent": "crossfit"},
]


def test_plan_preview_validates_without_writing_anything(conn, tmp_path):
    out = tools.plan_preview(conn, week_start=WEEK, days=PROPOSAL, plans_dir=str(tmp_path))

    assert out["data"]["error"] is None
    assert out["data"]["week_start"] == WEEK
    assert [d["date"] for d in out["data"]["days"]][:2] == ["2026-07-13", "2026-07-14"]
    assert out["data"]["days"][6]["intent"] == "crossfit"
    assert list(tmp_path.iterdir()) == []  # nothing written
    assert _cached_days(conn) == 0


def test_plan_preview_lists_vocabulary_errors_without_side_effects(conn, tmp_path):
    days = [dict(d) for d in PROPOSAL]
    days[2]["intent"] = "chill"

    out = tools.plan_preview(conn, week_start=WEEK, days=days, plans_dir=str(tmp_path))

    assert "chill" in out["data"]["error"]
    assert list(tmp_path.iterdir()) == []


def test_plan_preview_rejects_a_short_week(conn, tmp_path):
    out = tools.plan_preview(conn, week_start=WEEK, days=PROPOSAL[:5], plans_dir=str(tmp_path))

    assert "7" in out["data"]["error"]


def test_plan_preview_rejects_a_non_monday(conn, tmp_path):
    out = tools.plan_preview(conn, week_start="2026-07-14", days=PROPOSAL, plans_dir=str(tmp_path))

    assert "Monday" in out["data"]["error"]


def test_plan_confirm_writes_the_file_and_imports_it(conn, tmp_path):
    tools.plan_preview(conn, week_start=WEEK, days=PROPOSAL, plans_dir=str(tmp_path))

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=PROPOSAL,
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )

    assert out["data"]["error"] is None
    assert out["data"]["written"] is True
    assert (tmp_path / f"{WEEK}_week.md").exists()
    # Imported through the same parser as a hand-written plan.
    from garmin_coach.core import plan as plan_mod

    assert plan_mod.resolve_day(conn, "2026-07-16")["intent"] == "tempo"
    assert plan_mod.resolve_day(conn, "2026-07-16")["source"] == "plan_week"


def test_plan_confirm_refuses_to_clobber_an_existing_plan(conn, tmp_path):
    _plan_file(tmp_path)
    before = (tmp_path / f"{WEEK}_week.md").read_text(encoding="utf-8")

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=PROPOSAL,
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )

    assert out["data"]["written"] is False
    assert "exists" in out["data"]["error"]
    assert (tmp_path / f"{WEEK}_week.md").read_text(encoding="utf-8") == before


def test_plan_confirm_rejects_an_invalid_proposal_without_writing(conn, tmp_path):
    days = [dict(d) for d in PROPOSAL]
    days[0]["intent"] = "sprint"

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=days,
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )

    assert out["data"]["written"] is False
    assert "sprint" in out["data"]["error"]
    assert list(tmp_path.iterdir()) == []


def test_plan_preview_warns_early_that_the_week_is_already_authored(conn, tmp_path):
    """Previewing a week that confirm would refuse is a trap; say so up front."""
    _plan_file(tmp_path)

    out = tools.plan_preview(conn, week_start=WEEK, days=PROPOSAL, plans_dir=str(tmp_path))

    assert "exists" in out["data"]["error"]


def test_plan_preview_rejects_a_pipe_before_anything_is_written(conn, tmp_path):
    """A pace/HR note with a pipe would corrupt the table row it lands in."""
    days = [dict(d) for d in PROPOSAL]
    days[3]["planned"] = "8x1 km @ 4:00 | HR <165"

    out = tools.plan_preview(conn, week_start=WEEK, days=days, plans_dir=str(tmp_path))

    assert "|" in out["data"]["error"]
    assert list(tmp_path.iterdir()) == []


def test_plan_confirm_with_a_pipe_reports_an_error_and_strands_nothing(conn, tmp_path):
    """Never write a file the importer would then reject: that would leave the week
    unconfirmable forever (file present, cache empty)."""
    days = [dict(d) for d in PROPOSAL]
    days[3]["planned"] = "8x1 km @ 4:00 | HR <165"

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=days,
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )

    assert out["data"]["written"] is False
    assert out["data"]["error"] is not None
    assert list(tmp_path.iterdir()) == []
    assert _cached_days(conn) == 0


# --- Issue #37: the confirm token must cover the date the push acts on ---


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
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    spec = json.loads((tmp_path / FUTURE / "workout.json").read_text())
    retargeted = {**spec, "date": LATER}
    (tmp_path / LATER).mkdir()
    (tmp_path / LATER / "workout.json").write_text(json.dumps(retargeted))
    assert publish.spec_hash(spec) == publish.spec_hash(retargeted)

    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    out = tools.push_confirm(
        conn,
        date=LATER,
        confirm_token=preview["data"]["confirm_token"],
        publisher=pub,
        reports_dir=str(tmp_path),
    )

    assert out["data"]["error"] is not None
    assert "stale" in out["data"]["error"]
    assert pub.calls == []


def test_a_spec_filed_under_another_date_is_refused(conn, tmp_path, fixture, fake_publisher):
    """The folder is the target day; a spec pointing elsewhere would push the wrong day."""
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    _retarget_spec(tmp_path, FUTURE, LATER)

    out = tools.push_preview(
        conn, date=FUTURE, publisher=fake_publisher(), reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is not None
    assert LATER in out["data"]["error"]


# --- Issue #22: the plan of record guards what is authored and pushed ---


def test_author_workout_refuses_a_session_harder_than_the_plan(conn, tmp_path, fixture):
    """The 2026-07-17 case, at the seam the coach skill actually calls."""
    _seed_plan(conn, FUTURE, "easy")

    out = tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )

    assert out["data"]["spec"] is None
    assert "planned as easy" in out["data"]["error"]
    assert not (tmp_path / FUTURE / "workout.json").exists()


def test_author_workout_still_allows_a_session_the_plan_asked_for(conn, tmp_path, fixture):
    _seed_plan(conn, FUTURE, "quality")

    out = tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is None


def test_push_preview_refuses_a_spec_the_revised_plan_no_longer_allows(
    conn, tmp_path, fixture, fake_publisher
):
    """Authoring passed under the old plan; the revision landed before the push."""
    tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    _seed_plan(conn, FUTURE, "easy")
    pub = fake_publisher()

    out = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    assert out["data"]["action"] == "refuse"
    assert "planned as easy" in out["data"]["message"]
    assert pub.calls == []


def test_push_confirm_refuses_a_spec_the_revised_plan_no_longer_allows(
    conn, tmp_path, fixture, fake_publisher
):
    tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))
    _seed_plan(conn, FUTURE, "easy")

    out = tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["data"]["confirm_token"],
        publisher=pub,
        reports_dir=str(tmp_path),
    )

    assert out["data"]["applied"] is False
    assert "stale" in out["data"]["error"]
    assert pub.calls == []


def test_push_confirm_names_the_plan_among_the_things_a_stale_token_means(
    conn, tmp_path, fixture, fake_publisher
):
    """A refusal the athlete cannot act on is a dead end: the message has to say
    that a revised plan is one of the ways the preview goes stale."""
    tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )

    out = tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token="deadbeef",
        publisher=fake_publisher(),
        reports_dir=str(tmp_path),
    )

    assert "plan" in out["data"]["error"]


def test_the_push_receipt_records_the_plan_it_was_measured_against(
    conn, tmp_path, fixture, fake_publisher
):
    tools.author_workout(
        conn, date=FUTURE, request=fixture("tempo_request"), reports_dir=str(tmp_path)
    )
    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["data"]["confirm_token"],
        publisher=pub,
        reports_dir=str(tmp_path),
    )

    receipt = json.loads((tmp_path / FUTURE / "push.json").read_text())
    assert receipt["planned_intent"] == "quality"


def test_status_reports_no_divergence_while_the_plan_still_allows_the_push(conn, tmp_path):
    _seed_pushed(tmp_path)  # a tempo push; 2026-07-17 is a quality day by template

    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["plan_divergence"] is None


def test_status_reports_divergence_once_the_plan_drops_below_the_pushed_session(conn, tmp_path):
    """The 2026-07-17 case as a read: the session is on the watch and the plan that
    would have justified it is gone."""
    _seed_pushed(tmp_path)
    _seed_plan(conn, PUSH_DATE, "easy")

    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["plan_divergence"] == {
        "pushed_type": "tempo",
        "planned_intent": "easy",
        "pushed_at": PUSHED_AT,
    }


def test_the_divergence_reads_the_type_the_receipt_recorded_over_a_respec(conn, tmp_path):
    """A spec re-authored after the push says nothing about what is on the watch."""
    _seed_pushed(tmp_path, session_type="quality")
    _seed_plan(conn, PUSH_DATE, "easy")

    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["plan_divergence"]["pushed_type"] == "quality"


def test_a_date_that_was_never_pushed_has_nothing_to_diverge(conn, tmp_path):
    _seed_plan(conn, PUSH_DATE, "rest")

    out = _status(conn, tmp_path, FakePublisher())

    assert out["data"]["plan_divergence"] is None


def test_plan_confirm_reports_a_pushed_workout_the_new_plan_invalidates(conn, tmp_path):
    """Confirming a week that drops Friday to easy must surface the tempo session
    already sitting on the watch for that Friday."""
    reports, plans = tmp_path / "reports", tmp_path / "plans"
    reports.mkdir()
    plans.mkdir()
    _seed_pushed(reports, session_type="tempo")

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=PROPOSAL,
        plans_dir=str(plans),
        reports_dir=str(reports),
        data_start_date=DATA_START,
    )

    assert out["data"]["written"] is True
    assert out["data"]["invalidated_pushes"] == [
        {
            "date": PUSH_DATE,
            "pushed_type": "tempo",
            "planned_intent": "easy",
            "pushed_at": PUSHED_AT,
        }
    ]


def test_plan_confirm_reports_nothing_when_the_new_plan_still_allows_the_push(conn, tmp_path):
    reports, plans = tmp_path / "reports", tmp_path / "plans"
    reports.mkdir()
    plans.mkdir()
    _seed_pushed(reports, session_type="easy")

    out = tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=PROPOSAL,
        plans_dir=str(plans),
        reports_dir=str(reports),
        data_start_date=DATA_START,
    )

    assert out["data"]["invalidated_pushes"] == []


# --- goal events and plan import from chat (issue #72) ----------------------

RACE = (dt.date.today() + dt.timedelta(days=21)).isoformat()


def _seed_core_day(conn, date: str = "2026-07-13", activity_id: int = 900) -> None:
    """One stored run, so the mart rebuild the writers trigger has something to build."""
    db.upsert_activity(
        conn,
        {
            "activity_id": activity_id,
            "start_local": f"{date} 12:00:00",
            "date": date,
            "gtype": "running",
            "dur_s": 2400,
            "aero_te": 2.4,
            "anaero_te": 0.2,
            "training_load": 90.0,
        },
    )
    conn.commit()


def _add_race(conn, date: str = RACE, **over):
    fields = {
        "date": date,
        "type": "hyrox",
        "priority": "A",
        "status": "confirmed",
        "date_precision": "approx",
        "data_start_date": DATA_START,
    }
    fields.update(over)
    return tools.event_add(conn, **fields)


def test_event_add_records_the_race_and_dates_the_periodization(conn):
    """The race lands and the block calendar is rebuilt in the same call (#72)."""
    _seed_core_day(conn)

    out = _add_race(conn, target="1:00:00")["data"]

    assert out["error"] is None
    assert out["event"]["date"] == RACE
    assert out["event"]["target_s"] == 3600
    assert out["event"]["is_anchor"] is True
    assert conn.execute("SELECT count(*) FROM plan_block").fetchone()[0] > 0


def test_event_add_reports_the_block_before_and_after(conn):
    """The athlete sees the periodization move, not just that a row was written (#72)."""
    _seed_core_day(conn)

    out = _add_race(conn)["data"]

    assert out["block_before"] is None
    assert out["block_after"]["block"] in ("base", "build", "peak", "taper", "race")
    assert out["block_after"]["weeks_to_event"] == 3
    assert out["block_after"]["anchor_event_id"] == out["event"]["id"]


def test_event_add_refuses_a_malformed_date_as_tool_text(conn):
    out = _add_race(conn, date="17/10/2026")["data"]

    assert "date" in out["error"]
    assert out["event"] is None
    assert db.list_goal_events(conn) == []


def test_event_add_refuses_a_race_already_recorded(conn):
    _seed_core_day(conn)
    _add_race(conn)

    out = _add_race(conn, priority="B")["data"]

    assert "already recorded" in out["error"]
    assert len(db.list_goal_events(conn)) == 1


def test_event_update_pins_the_date_and_moves_the_block(conn):
    """Pinning a race one week earlier moves every week's countdown at once (#72)."""
    _seed_core_day(conn)
    _add_race(conn)
    event_id = db.list_goal_events(conn)[0]["id"]
    pinned = (dt.date.today() + dt.timedelta(days=14)).isoformat()

    out = tools.event_update(
        conn,
        event_id=event_id,
        date=pinned,
        date_precision="exact",
        data_start_date=DATA_START,
    )["data"]

    assert out["error"] is None
    assert out["event"]["date"] == pinned
    assert out["event"]["date_precision"] == "exact"
    assert out["block_after"]["weeks_to_event"] == out["block_before"]["weeks_to_event"] - 1


def test_event_update_refuses_an_unknown_event(conn):
    out = tools.event_update(conn, event_id=999, status="confirmed", data_start_date=DATA_START)

    assert "999" in out["data"]["error"]


def test_event_update_refuses_an_empty_change(conn):
    _seed_core_day(conn)
    _add_race(conn)
    event_id = db.list_goal_events(conn)[0]["id"]

    out = tools.event_update(conn, event_id=event_id, data_start_date=DATA_START)

    assert "nothing to update" in out["data"]["error"]


def test_event_tools_carry_the_freshness_envelope(conn):
    _seed_core_day(conn)

    assert "freshness" in _add_race(conn)


def test_plan_import_reads_the_week_and_rebuilds_plan_versus_actual(conn, tmp_path):
    """A hand-edited plan file becomes the plan of record from chat (#72)."""
    for i in range(7):
        _seed_core_day(
            conn,
            (dt.date.fromisoformat(WEEK) + dt.timedelta(days=i)).isoformat(),
            activity_id=900 + i,
        )
    _plan_file(tmp_path)

    out = tools.plan_import(conn, plans_dir=str(tmp_path), data_start_date=DATA_START)["data"]

    assert out["error"] is None
    assert out["weeks"] == [WEEK]
    assert plan_mod.resolve_day(conn, "2026-07-16")["source"] == "plan_week"
    rows = conn.execute(
        "SELECT count(*) FROM weekly_plan_actual WHERE week_start = ?", (WEEK,)
    ).fetchone()[0]
    assert rows == 7


def test_plan_import_reports_a_push_the_edited_plan_invalidates(conn, tmp_path):
    reports, plans = tmp_path / "reports", tmp_path / "plans"
    reports.mkdir()
    plans.mkdir()
    _seed_pushed(reports, session_type="tempo")
    _plan_file(plans)

    out = tools.plan_import(
        conn, plans_dir=str(plans), reports_dir=str(reports), data_start_date=DATA_START
    )["data"]

    assert [c["date"] for c in out["invalidated_pushes"]] == [PUSH_DATE]


def test_plan_import_without_a_plan_file_says_so(conn, tmp_path):
    out = tools.plan_import(conn, plans_dir=str(tmp_path), data_start_date=DATA_START)["data"]

    assert out["weeks"] == []
    assert "no plan file" in out["error"]


def test_plan_confirm_rebuilds_plan_versus_actual(conn, tmp_path):
    """A week agreed in chat is compared against actuals without waiting a night (#72)."""
    for i in range(7):
        _seed_core_day(
            conn,
            (dt.date.fromisoformat(WEEK) + dt.timedelta(days=i)).isoformat(),
            activity_id=900 + i,
        )

    tools.plan_confirm(
        conn,
        week_start=WEEK,
        days=PROPOSAL,
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )

    rows = conn.execute(
        "SELECT count(*) FROM weekly_plan_actual WHERE week_start = ?", (WEEK,)
    ).fetchone()[0]
    assert rows == 7


def test_envelope_names_the_unconfirmed_days(conn):
    """A weekly read, not only the digest, must carry what the trailing days are worth."""
    today = dt.date.today()
    for back in (1, 2, 3):
        day = today - dt.timedelta(days=back)
        monday = (day - dt.timedelta(days=day.weekday())).isoformat()
        plan_mod.upsert_week(
            conn,
            [
                {"week_start": monday, "dow": dow, "planned": "tempo", "intent": "tempo"}
                for dow in range(7)
            ],
        )
    _seed_mart(conn, YESTERDAY, hrv=60)

    envelope = tools.get_weekly(conn)["freshness"]

    assert [u["date"] for u in envelope["unconfirmed_days"]] == [
        (today - dt.timedelta(days=back)).isoformat() for back in (3, 2, 1)
    ]


def test_envelope_reports_no_unconfirmed_days_when_every_planned_day_arrived(conn):
    _seed_core_day(conn, YESTERDAY)
    _seed_mart(conn, YESTERDAY, hrv=60)

    envelope = tools.get_zones(conn)["freshness"]

    assert YESTERDAY not in [u["date"] for u in envelope["unconfirmed_days"]]


# --- gap repair: what the data holds, then a bounded re-pull (issue #72) ------


def _in_window(back: int) -> str:
    return (dt.date.today() - dt.timedelta(days=back)).isoformat()


def _preview(conn, *, from_back=3, to_back=1, **over):
    fields = {
        "from_date": _in_window(from_back),
        "to_date": _in_window(to_back),
        "data_start_date": DATA_START,
    }
    fields.update(over)
    return tools.repair_preview(conn, **fields)["data"]


def test_repair_preview_reports_what_each_day_holds(conn):
    """The Saturday that had sleep but no run must read as exactly that (#72)."""
    day = _in_window(2)
    _seed_core_day(conn, day, activity_id=910)
    db.upsert_daily(conn, "sleep", {"date": _in_window(1), "score": 80})

    out = _preview(conn)

    assert out["error"] is None
    assert [d["date"] for d in out["days"]] == [_in_window(3), _in_window(2), _in_window(1)]
    assert out["days"][1]["activities"] == 1
    assert out["days"][2]["activities"] == 0
    assert out["days"][2]["sleep"] is True
    assert out["days"][2]["hrv"] is False
    assert out["days"][0]["planned"] is not None


def test_repair_preview_hands_over_a_confirm_token(conn):
    out = _preview(conn)

    assert out["confirm_token"]
    assert out["confirm_token"] == _preview(conn)["confirm_token"]


def test_repair_preview_token_changes_when_a_day_fills_in(conn):
    before = _preview(conn)["confirm_token"]

    _seed_core_day(conn, _in_window(2), activity_id=911)

    assert _preview(conn)["confirm_token"] != before


def test_repair_preview_refuses_today(conn):
    """Today is what refresh_today is for; the repair is about finished days."""
    out = _preview(conn, to_back=0)

    assert "today" in out["error"]
    assert out["days"] is None


def test_repair_preview_refuses_a_range_over_the_cap(conn):
    out = _preview(conn, from_back=20)

    assert "14" in out["error"]


def test_repair_preview_refuses_a_reversed_range(conn):
    out = _preview(conn, from_back=1, to_back=3)

    assert "before" in out["error"]


def test_repair_preview_refuses_a_start_before_the_data_start(conn):
    out = _preview(conn, from_date="2026-01-01", to_date="2026-01-05")

    assert DATA_START in out["error"]


def test_repair_preview_refuses_a_malformed_date(conn):
    out = _preview(conn, from_date="11.09.2026")

    assert "YYYY-MM-DD" in out["error"]


def test_repair_preview_never_contacts_garmin(conn):
    """It reads the DB only - that is what makes it safe to run before deciding."""
    out = _preview(conn)

    assert out["days"] is not None


def _late_run(date: str, activity_id: int = 900123):
    """The run that reached Garmin Connect after the nightly sync had passed the day."""
    return {
        "activityId": activity_id,
        "startTimeLocal": f"{date} 09:00:00",
        "activityType": {"typeKey": "running"},
        "activityName": "Sobotni bieg",
        "duration": 3600.0,
    }


def _run_repair(conn, client, *, from_back=3, to_back=1, token=None, **over):
    from_date, to_date = _in_window(from_back), _in_window(to_back)
    if token is None:
        token = _preview(conn, from_back=from_back, to_back=to_back)["confirm_token"]
    fields = {
        "from_date": from_date,
        "to_date": to_date,
        "confirm_token": token,
        "data_start_date": DATA_START,
    }
    fields.update(over)
    return tools.repair_confirm(conn, client, **fields)["data"]


def test_repair_confirm_pulls_the_range_and_stores_the_late_run(conn, fake_client):
    """The whole point: the gap closes from chat instead of from the terminal (#72)."""
    day = _in_window(2)
    client = fake_client(activities=[_late_run(day)])

    out = _run_repair(conn, client)

    assert out["error"] is None
    assert out["days"] == 3
    assert out["activities_stored"] == 1
    assert out["features_ok"] is True
    assert conn.execute("SELECT count(*) FROM activities").fetchone()[0] == 1


def test_repair_confirm_asks_garmin_for_the_whole_range(conn, fake_client):
    """A day missing only one stream is repaired too, so the range is pulled whole."""
    client = fake_client()

    _run_repair(conn, client)

    assert ("activities", f"{_in_window(3)}..{_in_window(1)}") in client.calls
    assert ("sleep", _in_window(2)) in client.calls


def test_repair_confirm_refuses_a_stale_token_without_touching_garmin(conn, fake_client):
    client = fake_client(activities=[_late_run(_in_window(2))])

    out = _run_repair(conn, client, token="0000000000000000")

    assert "preview" in out["error"]
    assert out["applied"] is False
    assert client.calls == []


def test_repair_confirm_refuses_a_token_from_a_moved_picture(conn, fake_client):
    """A nightly run that filled the gap between preview and confirm invalidates it."""
    stale = _preview(conn)["confirm_token"]
    _seed_core_day(conn, _in_window(2), activity_id=912)
    client = fake_client()

    out = _run_repair(conn, client, token=stale)

    assert "preview" in out["error"]
    assert client.calls == []


def test_repair_confirm_refuses_a_bad_range_before_the_token(conn, fake_client):
    client = fake_client()

    out = _run_repair(conn, client, to_back=0, token="whatever")

    assert "today" in out["error"]
    assert client.calls == []


def test_repair_confirm_leaves_the_watermarks_alone(conn, fake_client):
    """The nightly run owns the watermarks; a repair must not let it skip a day."""
    db.set_sync_watermark(conn, "activities", _in_window(5))
    client = fake_client(activities=[_late_run(_in_window(2))])

    _run_repair(conn, client)

    assert db.get_sync_watermark(conn, "activities") == _in_window(5)


def test_repair_confirm_reports_a_failed_pull_without_raising(conn, fake_client):
    class _Failing(FakeGarminClient):
        def get_activities(self, start_date, end_date):
            raise RuntimeError("garmin timed out")

    out = _run_repair(conn, _Failing())

    assert "garmin timed out" in out["error"]
    assert out["applied"] is False


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

    out = tools.author_workout(conn, day, request=request, reports_dir=str(tmp_path))

    assert out["data"]["spec"]["name"] == f"GC {day} FBB A"


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

    out = tools.author_workout(conn, day, request=request, reports_dir=str(tmp_path))

    assert out["data"]["spec"] is None
    assert "30" in out["data"]["error"]


# --- repeating a pushed session on another day (issue #58) -------------------


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
    source, target = _authorable_day(conn), (dt.date.today() + dt.timedelta(days=3)).isoformat()
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

    out = tools.author_workout(conn, target, reuse_from=source, reports_dir=str(tmp_path))["data"]

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

    out = tools.author_workout(conn, target, reuse_from=source, reports_dir=str(tmp_path))["data"]

    assert out["spec"] is None
    assert "rest" in out["error"]


def test_repeating_a_day_with_no_spec_says_so(conn, tmp_path):
    _seed_mart(conn, YESTERDAY, hrv=60)
    target = _authorable_day(conn)

    out = tools.author_workout(conn, target, reuse_from="2026-07-01", reports_dir=str(tmp_path))[
        "data"
    ]

    assert out["spec"] is None
    assert "2026-07-01" in out["error"]


def test_get_pushed_workouts_lists_the_receipts_newest_first(conn, tmp_path):
    """So the coach can find "FBB A" without asking the athlete for the date (#58)."""
    _push_receipt(tmp_path, "2026-09-19", name="GC FBB A")
    _push_receipt(tmp_path, "2026-09-12", name="GC 2026-09-12 tempo", session_type="tempo")

    out = tools.get_pushed_workouts(conn, reports_dir=str(tmp_path))["data"]

    assert [w["date"] for w in out["workouts"]] == ["2026-09-19", "2026-09-12"]
    assert out["workouts"][0]["name"] == "GC FBB A"
    assert out["workouts"][0]["workout_id"] == 1000
    assert out["workouts"][0]["session_type"] == "strength"
    assert out["workouts"][0]["applied"] is True


def test_get_pushed_workouts_carries_the_last_known_account_state(conn, tmp_path):
    _push_receipt(tmp_path, "2026-09-19", reconciled={"state": "live", "checked_at": "2026-09-20"})

    out = tools.get_pushed_workouts(conn, reports_dir=str(tmp_path))["data"]

    assert out["workouts"][0]["last_state"] == "live"


def test_get_pushed_workouts_can_start_at_a_date(conn, tmp_path):
    _push_receipt(tmp_path, "2026-09-19")
    _push_receipt(tmp_path, "2026-08-01")

    out = tools.get_pushed_workouts(conn, since="2026-09-01", reports_dir=str(tmp_path))["data"]

    assert [w["date"] for w in out["workouts"]] == ["2026-09-19"]


def test_get_pushed_workouts_without_any_receipts_is_empty(conn, tmp_path):
    out = tools.get_pushed_workouts(conn, reports_dir=str(tmp_path))["data"]

    assert out["workouts"] == []


def test_the_digest_and_the_envelope_share_one_recheck_window(conn, monkeypatch):
    """Two windows in one response would contradict each other (ADR 0026)."""
    monkeypatch.setattr(
        tools, "get_settings", lambda: types.SimpleNamespace(sync_recheck_days=5)
    )
    today = dt.date.today()
    for back in range(1, 6):
        day = today - dt.timedelta(days=back)
        monday = (day - dt.timedelta(days=day.weekday())).isoformat()
        plan_mod.upsert_week(
            conn,
            [
                {"week_start": monday, "dow": dow, "planned": "tempo", "intent": "tempo"}
                for dow in range(7)
            ],
        )
    _seed_mart(conn, YESTERDAY, hrv=60, load_day=10)

    out = tools.get_digest(conn)

    assert len(out["freshness"]["unconfirmed_days"]) == 5
    assert out["data"]["window"]["unconfirmed_days"] == out["freshness"]["unconfirmed_days"]


def test_event_add_reports_the_row_it_stored_whatever_shape_the_date_came_in(conn):
    """The writer normalises the date, so matching on what was typed would miss it."""
    _seed_core_day(conn)
    compact = (dt.date.today() + dt.timedelta(days=21)).strftime("%Y%m%d")

    out = tools.event_add(
        conn,
        date=compact,
        type="run_race",
        priority="B",
        status="tentative",
        date_precision="exact",
        data_start_date=DATA_START,
    )["data"]

    assert out["error"] is None
    assert out["event"] is not None
    assert out["event"]["date"] == RACE
