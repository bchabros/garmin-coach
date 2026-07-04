"""Persistence seam: schema bootstrap + upsert/append helpers.

The database IS the public contract of this layer, so reading back via SQL here
is the interface, not a side channel.
"""
from __future__ import annotations


from garmin_coach import db, models


def test_bootstrap_creates_core_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"raw_payloads", "activities", "daily_wellness", "sleep",
            "hrv_nightly", "training_readiness", "training_status_daily",
            "daily_metrics"} <= names


def test_bootstrap_is_idempotent():
    c = db.connect(":memory:")
    db.bootstrap(c)
    db.bootstrap(c)  # second run must not raise
    assert c.execute("SELECT COUNT(*) FROM coach_thresholds").fetchone()[0] > 0
    c.close()


def test_insert_raw_is_append_only(conn):
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at="2026-07-04T10:00:00")
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at="2026-07-04T11:00:00")
    n = conn.execute("SELECT COUNT(*) FROM raw_payloads WHERE endpoint=? AND ref_date=?",
                     ("get_sleep_data", "2026-06-10")).fetchone()[0]
    assert n == 2  # different fetched_at -> two rows, by design


def test_upsert_activity_idempotent(conn, fixture):
    row = models.normalize_activity(fixture("activities_range")[0])
    db.upsert_activity(conn, row)
    db.upsert_activity(conn, row)  # same activity_id -> update, not duplicate
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    stored = conn.execute(
        "SELECT training_load, discipline FROM activities WHERE activity_id=?",
        (row["activity_id"],)).fetchone()
    assert stored[0] == 285.2075500488281
    assert stored[1] == "Bieganie"


def test_upsert_daily_idempotent(conn, fixture):
    row = models.normalize_sleep("2026-06-10", fixture("sleep_day"))
    db.upsert_daily(conn, "sleep", row)
    db.upsert_daily(conn, "sleep", row)
    assert conn.execute("SELECT COUNT(*) FROM sleep").fetchone()[0] == 1
    assert conn.execute("SELECT score FROM sleep WHERE date=?",
                        ("2026-06-10",)).fetchone()[0] == 66
