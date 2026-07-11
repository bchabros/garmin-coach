"""Backfill orchestration seam. Observes resulting DB state; client is injected."""

from __future__ import annotations

import datetime as dt

from garmin_coach import db, models, sync


def _client_with_day(fake_client, fixture, date="2026-06-10"):
    return fake_client(
        activities=fixture("activities_range"),
        by_day={
            "sleep": {date: fixture("sleep_day")},
            "hrv": {date: fixture("hrv_day")},
            "wellness": {date: fixture("wellness_day")},
            "readiness": {date: fixture("readiness_day")},
            "status": {date: fixture("status_day")},
        },
    )


def test_backfill_enriches_activity_temp_and_ingests_lactate(conn, fake_client, fixture):
    """Backfill fans out weather per activity (temp_c) and backfills LTHR history."""
    client = fake_client(
        activities=fixture("activities_range"),
        weather_by_id={23176570790: {"temp": 67}},  # 67F -> 19.4C
        lactate_range=fixture("lactate_threshold_range"),
    )
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")

    temp = conn.execute(
        "SELECT temp_c FROM activities WHERE activity_id = 23176570790"
    ).fetchone()[0]
    assert temp is not None and abs(temp - 19.44) < 0.1
    # backfill uses the ranged form: the whole detection history lands, not just latest
    lthr = conn.execute(
        "SELECT lactate_thr_hr FROM fitness_markers WHERE date = '2026-07-02'"
    ).fetchone()
    assert lthr is not None and lthr[0] == 175
    history = conn.execute(
        "SELECT lactate_thr_hr FROM fitness_markers WHERE date = '2026-06-08'"
    ).fetchone()
    assert history is not None and history[0] == 179


def test_backfill_fills_core_tables(conn, fake_client, fixture):
    client = _client_with_day(fake_client, fixture)
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")

    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    for table in (
        "sleep",
        "hrv_nightly",
        "daily_wellness",
        "training_readiness",
        "training_status_daily",
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
    # raw captured for every successful pull
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] >= 6


def test_backfill_is_idempotent_for_core(conn, fake_client, fixture):
    client = _client_with_day(fake_client, fixture)
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")

    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in (
            "activities",
            "sleep",
            "hrv_nightly",
            "daily_wellness",
            "training_readiness",
            "training_status_daily",
        )
    }
    assert counts == {
        "activities": 1,
        "sleep": 1,
        "hrv_nightly": 1,
        "daily_wellness": 1,
        "training_readiness": 1,
        "training_status_daily": 1,
    }


def _strength_activity(activity_id=900001, date="2026-06-12"):
    return {
        "activityId": activity_id,
        "startTimeLocal": f"{date} 17:00:00",
        "activityType": {"typeKey": "strength_training"},
        "activityName": "Siła",
        "duration": 4200.0,
    }


def test_backfill_ingests_exercise_sets_per_activity(conn, fake_client, fixture):
    """Backfill fans out exercise sets per activity into activity_sets (raw-first)."""
    client = fake_client(
        activities=[_strength_activity()],
        sets_by_id={900001: fixture("exercise_sets_strength")},
    )
    sync.backfill(client, conn, "2026-06-12", "2026-06-12")

    subs = [
        r[0]
        for r in conn.execute(
            "SELECT subcategory FROM activity_sets WHERE activity_id=900001 ORDER BY set_idx"
        )
    ]
    assert subs == ["BARBELL_BENCH_PRESS", "BARBELL_DEADLIFT", "CABLE_ROW"]
    # raw payload captured for reprocessing
    assert conn.execute(
        "SELECT COUNT(*) FROM raw_payloads WHERE endpoint='get_activity_exercise_sets'"
    ).fetchone()[0] == 1


def test_backfill_isolates_exercise_sets_failure(conn, fake_client):
    """A sets fetch failure leaves the activity stored without sets, never aborts."""
    client = fake_client(
        activities=[_strength_activity()],
        sets_by_id={900001: RuntimeError("exerciseSets timed out")},
    )
    sync.backfill(client, conn, "2026-06-12", "2026-06-12")

    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM activity_sets").fetchone()[0] == 0


def test_backfill_exercise_sets_are_idempotent(conn, fake_client, fixture):
    client = fake_client(
        activities=[_strength_activity()],
        sets_by_id={900001: fixture("exercise_sets_strength")},
    )
    sync.backfill(client, conn, "2026-06-12", "2026-06-12")
    sync.backfill(client, conn, "2026-06-12", "2026-06-12")

    assert conn.execute("SELECT COUNT(*) FROM activity_sets").fetchone()[0] == 3


def test_backfill_marks_empty_wellness_day(conn, fake_client, fixture):
    client = fake_client(by_day={"wellness": {"2026-05-20": fixture("wellness_empty")}})
    sync.backfill(client, conn, "2026-05-20", "2026-05-20")
    has_data = conn.execute(
        "SELECT has_data FROM daily_wellness WHERE date=?", ("2026-05-20",)
    ).fetchone()[0]
    assert has_data == 0


def test_sync_result_classifies_total_outage_and_degraded():
    """SyncResult owns partial-success vs total-outage policy."""
    total = sync.SyncResult(
        attempted_streams={"sleep"},
        progressed_streams=set(),
        warnings=["sleep failed"],
    )
    partial = sync.SyncResult(
        attempted_streams={"sleep", "hrv"},
        progressed_streams={"hrv"},
        warnings=["sleep failed"],
    )

    assert total.total_outage is True
    assert total.degraded is False
    assert partial.total_outage is False
    assert partial.degraded is True


class SleepFailsOnDateClient:
    def __init__(self, base, failing_date):
        self.base = base
        self.failing_date = failing_date
        self.calls = base.calls

    def get_activities(self, start_date: str, end_date: str):
        return self.base.get_activities(start_date, end_date)

    def get_sleep(self, date: str):
        self.calls.append(("sleep", date))
        if date == self.failing_date:
            raise TimeoutError("sleep timeout")
        return self.base.by_day.get("sleep", {}).get(date)

    def get_hrv(self, date: str):
        return self.base.get_hrv(date)

    def get_wellness(self, date: str):
        return self.base.get_wellness(date)

    def get_readiness(self, date: str):
        return self.base.get_readiness(date)

    def get_status(self, date: str):
        return self.base.get_status(date)


class ActivitiesRangeFailsClient:
    def __init__(self, activities):
        self.activities = activities
        self.calls: list[tuple[str, str]] = []

    def get_activities(self, start_date: str, end_date: str):
        self.calls.append(("activities", f"{start_date}..{end_date}"))
        if start_date != end_date:
            raise TimeoutError("activities range timeout")
        return self.activities if start_date == "2026-06-08" else []

    def get_sleep(self, date: str):
        self.calls.append(("sleep", date))
        return None

    def get_hrv(self, date: str):
        self.calls.append(("hrv", date))
        return None

    def get_wellness(self, date: str):
        self.calls.append(("wellness", date))
        return None

    def get_readiness(self, date: str):
        self.calls.append(("readiness", date))
        return None

    def get_status(self, date: str):
        self.calls.append(("status", date))
        return None


def test_incremental_sync_falls_back_to_daily_activity_ranges(conn, fixture):
    client = ActivitiesRangeFailsClient(fixture("activities_range"))

    result = sync.sync_incremental(
        client,
        conn,
        data_start_date="2026-06-08",
        to_date="2026-06-09",
        max_attempts=1,
        retry_base_seconds=0,
    )

    activity_calls = [call for call in client.calls if call[0] == "activities"]
    assert activity_calls == [
        ("activities", "2026-06-08..2026-06-09"),
        ("activities", "2026-06-08..2026-06-08"),
        ("activities", "2026-06-09..2026-06-09"),
    ]
    assert db.get_sync_watermark(conn, "activities") == "2026-06-09"
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    assert "activities" in result.progressed_streams


def test_incremental_sync_isolates_failed_stream_and_keeps_other_watermarks(
    conn, fake_client, fixture
):
    base = fake_client(
        by_day={
            "sleep": {"2026-06-08": fixture("sleep_day")},
            "hrv": {"2026-06-09": fixture("hrv_day")},
        }
    )
    client = SleepFailsOnDateClient(base, failing_date="2026-06-09")

    result = sync.sync_incremental(
        client,
        conn,
        data_start_date="2026-06-08",
        to_date="2026-06-09",
        max_attempts=2,
        retry_base_seconds=0,
    )

    assert [call for call in client.calls if call == ("sleep", "2026-06-09")] == [
        ("sleep", "2026-06-09"),
        ("sleep", "2026-06-09"),
    ]
    assert db.get_sync_watermark(conn, "sleep") == "2026-06-08"
    assert db.get_sync_watermark(conn, "hrv") == "2026-06-09"
    assert any("sleep" in warning and "2026-06-09" in warning for warning in result.warnings)


def test_incremental_sync_bootstraps_from_core_and_fetches_only_missing_dates(
    conn, fake_client, fixture
):
    db.upsert_daily(conn, "sleep", models.normalize_sleep("2026-06-10", fixture("sleep_day")))
    client = fake_client(by_day={"sleep": {"2026-06-11": fixture("sleep_day")}})

    sync.sync_incremental(client, conn, data_start_date="2026-06-08", to_date="2026-06-11")

    sleep_calls = [date for endpoint, date in client.calls if endpoint == "sleep"]
    assert sleep_calls == ["2026-06-11"]
    assert db.get_sync_watermark(conn, "sleep") == "2026-06-11"
    assert conn.execute("SELECT COUNT(*) FROM sleep").fetchone()[0] == 2


def test_backfill_excludes_today_when_to_date_omitted(conn, fake_client):
    today = dt.date.today()
    start = today - dt.timedelta(days=2)
    client = fake_client()
    sync.backfill(client, conn, start.isoformat())  # to_date defaults to yesterday

    day_calls = [d for (ep, d) in client.calls if ep == "sleep"]
    assert (today - dt.timedelta(days=1)).isoformat() in day_calls
    assert today.isoformat() not in day_calls
