"""Tool functions behind the coach MCP server (epic #18).

Seam: the pure functions in ``mcp.tools`` - each wraps a reader the CLI already
uses and returns a freshness envelope. Tests seed the temp DB (or a tmp reports
dir) and assert on the returned dicts; the MCP protocol layer is not involved.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time

from garmin_coach import cli
from garmin_coach.core import db
from garmin_coach.marts import snapshot
from garmin_coach.mcp import tools
from garmin_coach.workouts import author, publish
from tests.conftest import FakePublisher

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
    cli.add_goal_event(
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
PUSHED_NAME = "GC 2026-07-17 quality"


def _as_account_clock(local_iso, *, minutes_after=0):
    """The same instant as Garmin renders it: naive UTC, fractional-second suffix.

    The receipt's clock is this machine's local time and the account's is UTC, so a
    fake that reuses one literal for both cannot see a skew bug (issue #42).
    """
    instant = dt.datetime.fromisoformat(local_iso).astimezone(dt.UTC) + dt.timedelta(
        minutes=minutes_after
    )
    return instant.replace(tzinfo=None).isoformat() + ".0"


def _spec(*, name=PUSHED_NAME, date=PUSH_DATE, work_s=1200):
    """A run spec of the shape ``author`` produces, for round-tripping through to_garmin."""
    return {
        "sport": "run",
        "origin": "recommender",
        "date": date,
        "session_type": "tempo",
        "name": name,
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


def _seed_pushed(tmp_path, *, spec=None, workout_id=1000, date=PUSH_DATE, spec_hash=None):
    """Write a workout spec and an applied push receipt for a date."""
    spec = spec or _spec(date=date)
    day_dir = tmp_path / date
    day_dir.mkdir(exist_ok=True)
    (day_dir / "workout.json").write_text(json.dumps(spec))
    (day_dir / "push.json").write_text(
        json.dumps(
            {
                "action": "create",
                "applied": True,
                "name": spec["name"],
                "date": date,
                "workout_id": workout_id,
                "spec_hash": spec_hash or publish.spec_hash(spec),
                "pushed_at": PUSHED_AT,
            }
        )
    )
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
    payload = author.to_garmin(spec or _spec())
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
        spec=_spec(work_s=2400),
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
    pub = _account_with(FakePublisher(), spec=_spec(work_s=2400), update_date=TOUCHED_AT)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["reconciled"]["state"] == "unscheduled"
    assert out["data"]["reconciled"]["steps_changed"] is True


def test_status_ignores_account_added_decoration_on_the_steps(conn, tmp_path):
    """The account decorates every step with fields no upload ever sent."""
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)
    for step in pub.workouts[1000]["workoutSegments"][0]["workoutSteps"]:
        step.update(
            {
                "stepId": 13996412277,
                "childStepId": None,
                "weightValue": -1,
                "strokeType": {"strokeTypeId": 0, "strokeTypeKey": None},
                "equipmentType": {"equipmentTypeId": 0, "equipmentTypeKey": None},
                "endConditionCompare": "",
                "preferredEndConditionUnit": None,
            }
        )

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


def test_an_edit_soon_after_the_push_is_not_hidden_by_the_clock_offset(conn, tmp_path):
    """The receipt's clock is local, Garmin's is UTC; comparing them raw hides an edit.

    Pinned with a fixed zone because the bug is invisible where the two agree: with
    the offset unhandled, an edit inside the first UTC+2 hours reads as untouched.
    """
    original = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Warsaw"
    time.tzset()
    try:
        _seed_pushed(tmp_path)
        pub = _account_with(
            FakePublisher(),
            scheduled_on=PUSH_DATE,
            spec=_spec(work_s=2400),
            update_date=_as_account_clock(PUSHED_AT, minutes_after=20),
        )

        out = _status(conn, tmp_path, pub)

        assert out["data"]["reconciled"]["state"] == "edited"
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def test_status_reports_unverified_when_the_detail_call_fails(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE, update_date=TOUCHED_AT)

    def explode(workout_id):
        raise RuntimeError("garmin: timed out")

    pub.get_workout = explode

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
        confirm_token=publish.confirm_token(spec),
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

    assert out["data"]["workout"]["name"] == "GC 2026-07-17 quality"
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


def test_push_preview_without_a_spec_is_explicit(conn, tmp_path, fake_publisher):
    out = tools.push_preview(
        conn, date=FUTURE, publisher=fake_publisher(), reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is not None
    assert "workout.json" in out["data"]["error"]


# --- issue #21: the plan of record over MCP ---------------------------------

WEEK = "2026-07-13"  # a Monday


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
    assert conn.execute("SELECT COUNT(*) FROM plan_week").fetchone()[0] == 0


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

    out = tools.plan_confirm(conn, week_start=WEEK, days=PROPOSAL, plans_dir=str(tmp_path))

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

    out = tools.plan_confirm(conn, week_start=WEEK, days=PROPOSAL, plans_dir=str(tmp_path))

    assert out["data"]["written"] is False
    assert "exists" in out["data"]["error"]
    assert (tmp_path / f"{WEEK}_week.md").read_text(encoding="utf-8") == before


def test_plan_confirm_rejects_an_invalid_proposal_without_writing(conn, tmp_path):
    days = [dict(d) for d in PROPOSAL]
    days[0]["intent"] = "sprint"

    out = tools.plan_confirm(conn, week_start=WEEK, days=days, plans_dir=str(tmp_path))

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

    out = tools.plan_confirm(conn, week_start=WEEK, days=days, plans_dir=str(tmp_path))

    assert out["data"]["written"] is False
    assert out["data"]["error"] is not None
    assert list(tmp_path.iterdir()) == []
    assert conn.execute("SELECT COUNT(*) FROM plan_week").fetchone()[0] == 0


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
