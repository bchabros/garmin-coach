"""Tool functions behind the coach MCP server (epic #18).

Seam: the pure functions in ``mcp.tools`` - each wraps a reader the CLI already
uses and returns a freshness envelope. Tests seed the temp DB (or a tmp reports
dir) and assert on the returned dicts; the MCP protocol layer is not involved.
"""

from __future__ import annotations

import datetime as dt
import json

from garmin_coach import cli
from garmin_coach.core import db
from garmin_coach.marts import snapshot
from garmin_coach.mcp import tools

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


def test_get_workout_status_reads_spec_and_receipt(conn, tmp_path):
    day_dir = tmp_path / "2026-07-17"
    day_dir.mkdir()
    (day_dir / "workout.json").write_text(json.dumps({"name": "GC 2026-07-17 quality"}))
    (day_dir / "push.json").write_text(json.dumps({"action": "create", "workout_id": 5}))

    out = tools.get_workout_status(conn, date="2026-07-17", reports_dir=str(tmp_path))

    assert out["data"]["workout"]["name"] == "GC 2026-07-17 quality"
    assert out["data"]["push"]["action"] == "create"


def test_get_workout_status_with_no_artifacts_is_explicit(conn, tmp_path):
    out = tools.get_workout_status(conn, date="2026-07-17", reports_dir=str(tmp_path))

    assert out["data"]["workout"] is None
    assert out["data"]["push"] is None


# --- action tools -----------------------------------------------------------

FUTURE = (dt.date.today() + dt.timedelta(days=2)).isoformat()


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


def test_push_confirm_refuses_a_stale_hash(conn, tmp_path, fixture, fake_publisher):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()

    out = tools.push_confirm(
        conn, date=FUTURE, spec_hash="deadbeef", publisher=pub, reports_dir=str(tmp_path)
    )

    assert out["data"]["error"] is not None
    assert "stale" in out["data"]["error"]
    assert pub.calls == []


def test_push_confirm_with_matching_hash_uploads_and_schedules(
    conn, tmp_path, fixture, fake_publisher
):
    request = fixture("tempo_request")
    tools.author_workout(conn, date=FUTURE, request=request, reports_dir=str(tmp_path))
    pub = fake_publisher()
    preview = tools.push_preview(conn, date=FUTURE, publisher=pub, reports_dir=str(tmp_path))

    out = tools.push_confirm(
        conn,
        date=FUTURE,
        spec_hash=preview["data"]["spec_hash"],
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
