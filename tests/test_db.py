"""Persistence seam: schema bootstrap + upsert/append helpers.

The database IS the public contract of this layer, so reading back via SQL here
is the interface, not a side channel.
"""

from __future__ import annotations


import pytest

from garmin_coach import db, models


def test_bootstrap_creates_core_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "raw_payloads",
        "activities",
        "daily_wellness",
        "sleep",
        "hrv_nightly",
        "training_readiness",
        "training_status_daily",
        "daily_metrics",
    } <= names


def test_bootstrap_is_idempotent():
    c = db.connect(":memory:")
    db.bootstrap(c)
    db.bootstrap(c)  # second run must not raise
    assert c.execute("SELECT COUNT(*) FROM coach_thresholds").fetchone()[0] > 0
    c.close()


def test_bootstrap_adds_temp_c_to_a_preexisting_activities_table():
    """A DB created before Phase 6 (activities without temp_c) gains the column
    on bootstrap - CREATE IF NOT EXISTS alone cannot add a column."""
    c = db.connect(":memory:")
    # Minimal pre-Phase-6 activities: has the indexed columns but no temp_c.
    c.execute(
        "CREATE TABLE activities (activity_id INTEGER PRIMARY KEY, date TEXT, "
        "discipline TEXT, avg_hr INTEGER, avg_speed_mps REAL)"
    )
    c.commit()

    db.bootstrap(c)

    cols = {r[1] for r in c.execute("PRAGMA table_info(activities)")}
    assert "temp_c" in cols
    db.bootstrap(c)  # idempotent: adding an existing column must not raise
    c.close()


def test_insert_raw_is_append_only(conn):
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at="2026-07-04T10:00:00")
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at="2026-07-04T11:00:00")
    n = conn.execute(
        "SELECT COUNT(*) FROM raw_payloads WHERE endpoint=? AND ref_date=?",
        ("get_sleep_data", "2026-06-10"),
    ).fetchone()[0]
    assert n == 2  # different fetched_at -> two rows, by design


def test_upsert_activity_idempotent(conn, fixture):
    row = models.normalize_activity(fixture("activities_range")[0])
    db.upsert_activity(conn, row)
    db.upsert_activity(conn, row)  # same activity_id -> update, not duplicate
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    stored = conn.execute(
        "SELECT training_load, discipline FROM activities WHERE activity_id=?",
        (row["activity_id"],),
    ).fetchone()
    assert stored[0] == 285.2075500488281
    assert stored[1] == "Bieganie"


def test_upsert_daily_idempotent(conn, fixture):
    row = models.normalize_sleep("2026-06-10", fixture("sleep_day"))
    db.upsert_daily(conn, "sleep", row)
    db.upsert_daily(conn, "sleep", row)
    assert conn.execute("SELECT COUNT(*) FROM sleep").fetchone()[0] == 1
    assert conn.execute("SELECT score FROM sleep WHERE date=?", ("2026-06-10",)).fetchone()[0] == 66


def test_sync_watermark_round_trips(conn):
    db.set_sync_watermark(conn, "sleep", "2026-06-10")

    assert db.get_sync_watermark(conn, "sleep") == "2026-06-10"


def test_bootstrap_sync_watermark_uses_core_max_date(conn, fixture):
    db.upsert_daily(conn, "sleep", models.normalize_sleep("2026-06-10", fixture("sleep_day")))
    db.upsert_daily(conn, "sleep", models.normalize_sleep("2026-06-11", fixture("sleep_day")))

    watermark = db.bootstrap_sync_watermark(
        conn, stream="sleep", core_table="sleep", data_start_date="2026-06-08"
    )

    assert watermark == "2026-06-11"
    assert db.get_sync_watermark(conn, "sleep") == "2026-06-11"


def test_bootstrap_sync_watermark_uses_day_before_data_start_when_core_empty(conn):
    watermark = db.bootstrap_sync_watermark(
        conn, stream="sleep", core_table="sleep", data_start_date="2026-06-08"
    )

    assert watermark == "2026-06-07"
    assert db.get_sync_watermark(conn, "sleep") == "2026-06-07"


# --- goal_event (Phase 9): manually-logged ground truth, two uncertainty axes ---


def _goal_event(**over):
    row = {
        "date": "2026-10-17", "type": "hyrox", "priority": "A",
        "status": "confirmed", "date_precision": "approx",
        "target_s": 3600, "note": "PB 1:01:46",
    }
    return {**row, **over}


def test_insert_goal_event_round_trips_both_uncertainty_axes(conn):
    db.insert_goal_event(conn, _goal_event())

    events = db.list_goal_events(conn)
    assert len(events) == 1
    assert events[0]["status"] == "confirmed"
    assert events[0]["date_precision"] == "approx"


def test_goal_event_target_is_stored_as_seconds(conn):
    db.insert_goal_event(conn, _goal_event(target_s=3600))

    assert db.list_goal_events(conn)[0]["target_s"] == 3600


def test_insert_goal_event_refuses_a_duplicate_race(conn):
    """`add` must never overwrite: it would erase the fields the athlete did not retype."""
    import sqlite3

    db.insert_goal_event(conn, _goal_event())

    with pytest.raises(sqlite3.IntegrityError):
        db.insert_goal_event(conn, _goal_event(target_s=None, note=None))

    assert db.list_goal_events(conn)[0]["target_s"] == 3600


def test_update_goal_event_flips_status_and_date_precision(conn):
    db.insert_goal_event(conn, _goal_event(
        date="2026-09-05", type="run_race", priority="B",
        status="tentative", date_precision="exact", target_s=5400,
    ))
    event_id = db.list_goal_events(conn)[0]["id"]

    db.update_goal_event(conn, event_id, status="confirmed")

    updated = db.list_goal_events(conn)[0]
    assert updated["status"] == "confirmed"
    assert updated["date_precision"] == "exact"
    assert updated["target_s"] == 5400


def test_update_goal_event_pins_an_approx_date(conn):
    db.insert_goal_event(conn, _goal_event())
    event_id = db.list_goal_events(conn)[0]["id"]

    db.update_goal_event(conn, event_id, date="2026-10-24", date_precision="exact")

    updated = db.list_goal_events(conn)[0]
    assert updated["date"] == "2026-10-24"
    assert updated["date_precision"] == "exact"


def test_list_goal_events_is_ordered_by_date(conn):
    db.insert_goal_event(conn, _goal_event())
    db.insert_goal_event(conn, _goal_event(date="2026-09-05", type="run_race"))

    assert [e["date"] for e in db.list_goal_events(conn)] == ["2026-09-05", "2026-10-17"]
