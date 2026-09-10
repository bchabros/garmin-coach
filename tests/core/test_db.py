"""Persistence seam: schema bootstrap + upsert/append helpers.

The database IS the public contract of this layer, so reading back via SQL here
is the interface, not a side channel.
"""

from __future__ import annotations


import pytest

from garmin_coach.core import db, models


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
        "date": "2026-10-17",
        "type": "hyrox",
        "priority": "A",
        "status": "confirmed",
        "date_precision": "approx",
        "target_s": 3600,
        "note": "PB 1:01:46",
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
    db.insert_goal_event(
        conn,
        _goal_event(
            date="2026-09-05",
            type="run_race",
            priority="B",
            status="tentative",
            date_precision="exact",
            target_s=5400,
        ),
    )
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


# --- Issue #34: raw identity must not lose a distinct payload ---


def test_two_distinct_payloads_in_the_same_second_both_survive(conn):
    """The raw layer promises reprocessing; a same-second collision must not eat one."""
    stamp = "2026-07-04T10:00:00"
    db.insert_raw(conn, "get_activity_weather", "2026-06-10", '{"temp": 19}', fetched_at=stamp)
    db.insert_raw(conn, "get_activity_weather", "2026-06-10", '{"temp": 24}', fetched_at=stamp)

    payloads = [
        r[0]
        for r in conn.execute(
            "SELECT payload FROM raw_payloads WHERE endpoint=? AND ref_date=? ORDER BY payload",
            ("get_activity_weather", "2026-06-10"),
        )
    ]
    assert payloads == ['{"temp": 19}', '{"temp": 24}']


def test_the_identical_payload_in_the_same_second_stays_one_row(conn):
    """A repeated identical call is a genuine no-op, not a second copy."""
    stamp = "2026-07-04T10:00:00"
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at=stamp)
    db.insert_raw(conn, "get_sleep_data", "2026-06-10", "{}", fetched_at=stamp)

    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 1


def test_bootstrap_migrates_a_pre_sha_raw_table(tmp_path):
    """A DB created before the identity fix keeps every row and gains the new key."""
    path = str(tmp_path / "legacy.db")
    c = db.connect(path)
    c.executescript(
        "CREATE TABLE raw_payloads ("
        "  fetched_at TEXT NOT NULL, endpoint TEXT NOT NULL,"
        "  ref_date TEXT NOT NULL, payload TEXT NOT NULL,"
        "  PRIMARY KEY (endpoint, ref_date, fetched_at));"
    )
    c.execute(
        "INSERT INTO raw_payloads VALUES ('2026-07-04T10:00:00','get_sleep_data','2026-06-10','{}')"
    )
    c.commit()

    db.bootstrap(c)

    cols = {r[1] for r in c.execute("PRAGMA table_info(raw_payloads)")}
    assert "payload_sha" in cols
    rows = c.execute("SELECT endpoint, ref_date, payload, payload_sha FROM raw_payloads").fetchall()
    assert len(rows) == 1
    assert rows[0][:3] == ("get_sleep_data", "2026-06-10", "{}")
    assert rows[0][3]  # the sha was backfilled, not left NULL
    # the migration is one-shot: a second bootstrap neither raises nor duplicates
    db.bootstrap(c)
    assert c.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 1
    c.close()


# --- Issue #60: the manual set overlay is core ground truth beside the captured sets ---


def _activity(conn, aid, date="2026-07-28"):
    conn.execute(
        "INSERT INTO activities(activity_id, start_local, date, gtype, discipline, dur_s) "
        "VALUES (?,?,?,?,?,?)",
        (aid, f"{date} 19:30:00", date, "hiit", "Hyrox/HIIT", 3706.0),
    )


def _set_row(aid, idx, sub):
    return {
        "activity_id": aid,
        "set_idx": idx,
        "category": None,
        "subcategory": sub,
        "reps": None,
        "sets": None,
        "duration_s": None,
        "max_weight": None,
    }


def test_bootstrap_creates_the_manual_set_overlay_and_its_read_view(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    views = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}

    assert "manual_activity_sets" in tables
    assert "movement_sets" in views
    # same shape as the captured table, so the view can union the two
    captured = [r[1] for r in conn.execute("PRAGMA table_info(activity_sets)")]
    manual = [r[1] for r in conn.execute("PRAGMA table_info(manual_activity_sets)")]
    assert manual == captured


def test_replace_manual_activity_sets_supersedes_the_prior_rows(conn):
    _activity(conn, 1)
    db.replace_manual_activity_sets(conn, 1, [_set_row(1, i, s) for i, s in enumerate("ABC")])

    db.replace_manual_activity_sets(conn, 1, [_set_row(1, 0, "SLED_PULL")])

    rows = conn.execute(
        "SELECT set_idx, subcategory FROM manual_activity_sets WHERE activity_id=1"
    ).fetchall()
    assert rows == [(0, "SLED_PULL")]  # a re-log with fewer rows leaves no orphans


def test_movement_sets_view_reads_manual_rows_for_an_activity_that_has_them(conn):
    _activity(conn, 1)
    _activity(conn, 2)
    # activity 1: the watch's single nameless round, overlaid by two hand-logged stations
    db.replace_activity_sets(conn, 1, [_set_row(1, 0, "UNKNOWN")])
    db.replace_manual_activity_sets(
        conn, 1, [_set_row(1, 0, "SLED_PULL"), _set_row(1, 1, "SANDBAG_CARRY")]
    )
    # activity 2: captured rows only
    db.replace_activity_sets(conn, 2, [_set_row(2, 0, "BARBELL_DEADLIFT")])

    rows = conn.execute(
        "SELECT activity_id, subcategory, source FROM movement_sets ORDER BY activity_id, set_idx"
    ).fetchall()

    assert rows == [
        (1, "SLED_PULL", "manual"),
        (1, "SANDBAG_CARRY", "manual"),
        (2, "BARBELL_DEADLIFT", "captured"),
    ]


def test_a_resync_of_the_captured_sets_leaves_the_overlay_untouched(conn):
    _activity(conn, 1)
    db.replace_manual_activity_sets(conn, 1, [_set_row(1, 0, "SLED_PULL")])

    db.replace_activity_sets(conn, 1, [_set_row(1, 0, "UNKNOWN")])
    db.replace_activity_sets(conn, 1, [_set_row(1, 0, "UNKNOWN")])

    assert conn.execute("SELECT COUNT(*) FROM manual_activity_sets").fetchone()[0] == 1
    assert conn.execute("SELECT subcategory FROM movement_sets").fetchall() == [("SLED_PULL",)]
