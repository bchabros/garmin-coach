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
from tests.conftest import FakeGarminClient, FakePublisher, run_spec

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


def _status(conn, tmp_path, pub, date=PUSH_DATE):
    return tools.get_workout_status(conn, date=date, connect=lambda: pub, reports_dir=str(tmp_path))


# --- workout status: steps edited in Garmin Connect (issue #42) -------------

TOUCHED_AT = "2026-07-16T16:16:31.0"


# --- workout status: the finding persisted to the receipt (issue #43) -------


def test_status_returns_the_receipt_and_spec_unchanged_beside_the_finding(conn, tmp_path):
    _seed_pushed(tmp_path)
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)

    out = _status(conn, tmp_path, pub)

    assert out["data"]["workout"]["name"] == PUSHED_NAME
    assert out["data"]["push"]["action"] == "create"
    assert out["data"]["push"]["pushed_at"] == "2026-07-15T17:28:00"
    assert "partial_fields" in out["freshness"]


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
        "saved": True,
        "recomputed": True,
        "error": None,
    }
    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 3


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


def test_push_confirm_with_matching_token_uploads_and_schedules(
    conn, tmp_path, fixture, fake_publisher
):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, connect=lambda: pub, reports_dir=str(tmp_path))

    out = tools.push_confirm(
        conn,
        date=FUTURE,
        confirm_token=preview["data"]["confirm_token"],
        connect=lambda: pub,
        reports_dir=str(tmp_path),
    )

    assert out["data"]["error"] is None
    assert out["data"]["applied"] is True
    assert "upload" in pub.calls and "schedule" in pub.calls
    assert (tmp_path / FUTURE / "push.json").exists()


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
        reports_dir=str(tmp_path / "reports"),
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
        reports_dir=str(tmp_path / "reports"),
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
        reports_dir=str(tmp_path / "reports"),
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
        reports_dir=str(tmp_path / "reports"),
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


# --- Issue #22: the plan of record guards what is authored and pushed ---


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

    out = tools.plan_import(
        conn,
        reports_dir=str(tmp_path / "reports"),
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )["data"]

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


def test_partial_plan_import_reports_accepted_weeks_conflicts_and_fresh_metrics(conn, tmp_path):
    plans, reports = tmp_path / "plans", tmp_path / "reports"
    plans.mkdir()
    reports.mkdir()
    _plan_file(plans)
    _plan_file(plans, week_start="2026-07-20", intents=["luz"] * 7)
    _seed_pushed(reports, session_type="tempo")
    for i in range(7):
        _seed_core_day(
            conn,
            (dt.date.fromisoformat(WEEK) + dt.timedelta(days=i)).isoformat(),
            activity_id=900 + i,
        )

    out = tools.plan_import(
        conn, plans_dir=str(plans), reports_dir=str(reports), data_start_date=DATA_START
    )["data"]

    assert out["weeks"] == [WEEK]
    assert "2026-07-20_week.md" in out["error"]
    assert [c["date"] for c in out["invalidated_pushes"]] == [PUSH_DATE]
    grid = tools.get_weekly(conn, week_start=WEEK)["data"]["plan_actual"]
    assert len(grid) == 7
    assert grid[4]["planned"] == "easy"


def test_plan_import_without_a_plan_file_says_so(conn, tmp_path):
    out = tools.plan_import(
        conn,
        reports_dir=str(tmp_path / "reports"),
        plans_dir=str(tmp_path),
        data_start_date=DATA_START,
    )["data"]

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
        reports_dir=str(tmp_path / "reports"),
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


def test_the_digest_and_the_envelope_share_one_recheck_window(conn, monkeypatch):
    """Two windows in one response would contradict each other (ADR 0026)."""
    monkeypatch.setattr(tools, "get_settings", lambda: types.SimpleNamespace(sync_recheck_days=5))
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


# --- issue #74: taking a pushed workout off a day from chat ------------------


def _off_preview(conn, tmp_path, pub, date=PUSH_DATE):
    return tools.unschedule_preview(conn, date=date, connect=lambda: pub, reports_dir=str(tmp_path))


def _off_confirm(conn, tmp_path, pub, token, date=PUSH_DATE):
    return tools.unschedule_confirm(
        conn, date=date, confirm_token=token, connect=lambda: pub, reports_dir=str(tmp_path)
    )


def _taken_off(conn, tmp_path, pub, date=PUSH_DATE):
    """Preview and confirm in one go, returning the confirm's response."""
    preview = _off_preview(conn, tmp_path, pub, date)
    return _off_confirm(conn, tmp_path, pub, preview["data"]["confirm_token"], date)


def test_unschedule_confirm_clears_the_day_and_appends_the_outcome(conn, tmp_path):
    """The push's own fields stay as written; the removal sits beside them (ADR 0014)."""
    day_dir = _seed_pushed(tmp_path)
    original = json.loads((day_dir / "push.json").read_text())
    pub = _account_with(FakePublisher(), scheduled_on=PUSH_DATE)

    out = _taken_off(conn, tmp_path, pub)

    assert out["data"]["applied"] is True
    assert out["data"]["action"] == "unschedule"
    assert pub.calls == ["unschedule"]
    assert pub.list_scheduled(PUSH_DATE) == []
    assert 1000 in pub.workouts
    receipt = json.loads((day_dir / "push.json").read_text())
    assert receipt["unscheduled_at"]
    assert receipt["reconciled"]["state"] == "unscheduled"
    assert receipt["reconciled"]["scheduled"] is False
    assert {
        k: v for k, v in receipt.items() if k not in ("unscheduled_at", "reconciled")
    } == original


def test_weekly_without_an_agreed_plan_reports_unknown_matches(conn):
    from garmin_coach.marts import features

    conn.execute("DELETE FROM plan_template")
    features.features(conn, data_start_date="2026-07-06", to_date="2026-07-12")

    out = tools.get_weekly(conn, week_start="2026-07-06")["data"]
    assert out["weeks"][0]["plan_adherence"] is None
    assert len(out["plan_actual"]) == 7
    assert all(day["planned"] is None for day in out["plan_actual"])
    assert all(day["matched"] is None for day in out["plan_actual"])


@pytest.mark.parametrize(
    "broken",
    [
        "{broken",
        '{"workout_id": 1000, "hardness": ["hard"]}',
        '{"workout_id": 1000, "session_type": {"type": "quality"}}',
    ],
)
def test_plan_import_keeps_accepted_weeks_and_rebuilds_when_push_evidence_is_damaged(
    conn, tmp_path, broken
):
    plans, reports = tmp_path / "plans", tmp_path / "reports"
    plans.mkdir()
    _plan_file(plans)
    for i in range(7):
        _seed_core_day(
            conn,
            (dt.date.fromisoformat(WEEK) + dt.timedelta(days=i)).isoformat(),
            activity_id=900 + i,
        )
    day = reports / PUSH_DATE
    day.mkdir(parents=True)
    path = day / "push.json"
    path.write_text(broken)

    result = tools.plan_import(
        conn, plans_dir=str(plans), reports_dir=str(reports), data_start_date=DATA_START
    )["data"]

    assert result["weeks"] == [WEEK]
    assert result["error"] is None
    assert PUSH_DATE in result["warnings"][0]
    assert "cannot assess" in result["warnings"][0]
    assert len(tools.get_weekly(conn, week_start=WEEK)["data"]["plan_actual"]) == 7
    assert path.read_text() == broken


def test_rpe_response_reports_a_saved_fact_when_recomputation_fails(conn):
    _seed_activity(conn)
    conn.commit()
    conn.execute(
        "CREATE TEMP TRIGGER fail_snapshot BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
    )
    conn.commit()

    result = tools.log_rpe(conn, activity_id=1, rpe=8, data_start_date=DATA_START)

    assert result["data"]["saved"] is True
    assert result["data"]["recomputed"] is False
    assert result["data"]["activity_id"] == 1
    assert "record saved" in result["data"]["error"]
    assert "freshness" in result


@pytest.mark.parametrize("pace_field", ["fast_s_per_km", "slow_s_per_km"])
def test_plan_import_rebuilds_when_saved_workout_pace_is_zero(conn, tmp_path, pace_field):
    plans, reports = tmp_path / "plans", tmp_path / "reports"
    plans.mkdir()
    reports.mkdir()
    _plan_file(plans)
    _seed_core_day(conn, PUSH_DATE)
    day = _seed_pushed(reports)
    path = day / "workout.json"
    spec = json.loads(path.read_text())
    spec["steps"][1]["target"][pace_field] = 0
    path.write_text(json.dumps(spec))
    before = path.read_bytes()
    receipt_before = (day / "push.json").read_bytes()
    assert db.latest_mart_date(conn) is None

    result = tools.plan_import(
        conn, plans_dir=str(plans), reports_dir=str(reports), data_start_date=DATA_START
    )

    assert result["data"]["weeks"] == [WEEK]
    assert result["data"]["error"] is None
    assert _cached_days(conn) == 7
    assert len(result["data"]["warnings"]) == 1
    assert PUSH_DATE in result["data"]["warnings"][0]
    assert "workout.json" in result["data"]["warnings"][0]
    assert result["freshness"]["data_through"] == PUSH_DATE
    assert db.latest_mart_date(conn) == PUSH_DATE
    assert path.read_bytes() == before
    assert (day / "push.json").read_bytes() == receipt_before
