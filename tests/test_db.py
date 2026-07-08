"""Persistence seam: schema bootstrap + upsert/append helpers.

The database IS the public contract of this layer, so reading back via SQL here
is the interface, not a side channel.
"""

from __future__ import annotations


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
