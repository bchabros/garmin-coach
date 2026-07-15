"""Tool functions behind the coach MCP server (epic #18).

Seam: the pure functions in ``mcp_tools`` - each wraps a reader the CLI already
uses and returns a freshness envelope. Tests seed the temp DB (or a tmp reports
dir) and assert on the returned dicts; the MCP protocol layer is not involved.
"""

from __future__ import annotations

import datetime as dt
import json

from garmin_coach import cli, db, mcp_tools, snapshot

DATA_START = "2026-06-08"
TODAY = dt.date.today().isoformat()
YESTERDAY = (dt.date.today() - dt.timedelta(days=1)).isoformat()


def _seed_mart(conn, date: str, **cols) -> None:
    db.upsert_daily(conn, "daily_metrics", {"date": date, **cols})


# --- freshness envelope ---------------------------------------------------


def test_envelope_reports_mart_horizon_without_today(conn):
    """Mart through yesterday: data_through set, nothing flagged partial."""
    _seed_mart(conn, YESTERDAY, hrv=60)

    out = mcp_tools.get_zones(conn)

    assert out["freshness"]["data_through"] == YESTERDAY
    assert out["freshness"]["today_included"] is False
    assert out["freshness"]["partial_fields"] == []


def test_envelope_flags_intraday_fields_when_today_included(conn):
    """Mart through today (post refresh-today): intraday fields flagged partial."""
    _seed_mart(conn, TODAY, hrv=60)

    out = mcp_tools.get_zones(conn)

    assert out["freshness"]["today_included"] is True
    assert "load_day" in out["freshness"]["partial_fields"]
    assert "acwr" in out["freshness"]["partial_fields"]
    assert "hrv" not in out["freshness"]["partial_fields"]
    assert "sleep_score" not in out["freshness"]["partial_fields"]


# --- read tools -----------------------------------------------------------


def test_get_snapshot_returns_the_athlete_status_row(conn):
    _seed_mart(conn, YESTERDAY, hrv=61, load_day=80.0)
    snapshot.rollup(conn)

    out = mcp_tools.get_snapshot(conn)

    assert out["data"] is not None
    assert out["data"]["computed_at"] == YESTERDAY


def test_get_snapshot_without_a_rollup_returns_none_data(conn):
    out = mcp_tools.get_snapshot(conn)

    assert out["data"] is None


def test_get_digest_builds_the_cited_digest(conn):
    for i in range(3):
        d = (dt.date.fromisoformat("2026-07-01") + dt.timedelta(days=i)).isoformat()
        _seed_mart(conn, d, hrv=60 + i, load_day=50.0)

    out = mcp_tools.get_digest(conn, to_date="2026-07-03")

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

    out = mcp_tools.get_recent_activities(conn, n=2)

    dates = [a["date"] for a in out["data"]]
    assert dates == ["2026-07-13", "2026-07-12"]


def test_get_weekly_returns_the_requested_week(conn):
    conn.execute(
        "INSERT INTO weekly_metrics (week_start, load_total, n_sessions) VALUES (?, ?, ?)",
        ("2026-07-06", 320.0, 5),
    )

    out = mcp_tools.get_weekly(conn, week_start="2026-07-06")

    assert len(out["data"]["weeks"]) == 1
    assert out["data"]["weeks"][0]["load_total"] == 320.0


def test_get_weekly_without_week_start_returns_all_weeks(conn):
    for ws in ("2026-06-29", "2026-07-06"):
        conn.execute("INSERT INTO weekly_metrics (week_start) VALUES (?)", (ws,))

    out = mcp_tools.get_weekly(conn)

    assert [w["week_start"] for w in out["data"]["weeks"]] == ["2026-06-29", "2026-07-06"]


def test_get_zones_returns_the_singleton_zone_row(conn):
    conn.execute(
        "INSERT INTO athlete_zones (id, lthr_bpm, threshold_pace_s_per_km, source) "
        "VALUES (1, 171, 260.0, 'regression+lthr')"
    )

    out = mcp_tools.get_zones(conn)

    assert out["data"]["lthr_bpm"] == 171
    assert out["data"]["source"] == "regression+lthr"


def test_get_recommendation_returns_the_block_for_tomorrow(conn):
    for i in range(3):
        d = (dt.date.fromisoformat("2026-07-01") + dt.timedelta(days=i)).isoformat()
        _seed_mart(conn, d, hrv=60 + i, load_day=50.0)

    out = mcp_tools.get_recommendation(conn, date="2026-07-04")

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

    out = mcp_tools.get_events(conn, today="2026-07-15")

    assert out["data"][0]["type"] == "hyrox"
    assert out["data"][0]["weeks_to_event"] > 0


def test_get_workout_status_reads_spec_and_receipt(conn, tmp_path):
    day_dir = tmp_path / "2026-07-17"
    day_dir.mkdir()
    (day_dir / "workout.json").write_text(json.dumps({"name": "GC 2026-07-17 quality"}))
    (day_dir / "push.json").write_text(json.dumps({"action": "create", "workout_id": 5}))

    out = mcp_tools.get_workout_status(conn, date="2026-07-17", reports_dir=str(tmp_path))

    assert out["data"]["workout"]["name"] == "GC 2026-07-17 quality"
    assert out["data"]["push"]["action"] == "create"


def test_get_workout_status_with_no_artifacts_is_explicit(conn, tmp_path):
    out = mcp_tools.get_workout_status(conn, date="2026-07-17", reports_dir=str(tmp_path))

    assert out["data"]["workout"] is None
    assert out["data"]["push"] is None
