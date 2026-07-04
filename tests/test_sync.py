"""Backfill orchestration seam. Observes resulting DB state; client is injected."""
from __future__ import annotations

import datetime as dt

from garmin_coach import sync


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


def test_backfill_fills_core_tables(conn, fake_client, fixture):
    client = _client_with_day(fake_client, fixture)
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")

    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == 1
    for table in ("sleep", "hrv_nightly", "daily_wellness",
                  "training_readiness", "training_status_daily"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
    # raw captured for every successful pull
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] >= 6


def test_backfill_is_idempotent_for_core(conn, fake_client, fixture):
    client = _client_with_day(fake_client, fixture)
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")
    sync.backfill(client, conn, "2026-06-08", "2026-06-10")

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("activities", "sleep", "hrv_nightly", "daily_wellness",
                        "training_readiness", "training_status_daily")}
    assert counts == {"activities": 1, "sleep": 1, "hrv_nightly": 1,
                      "daily_wellness": 1, "training_readiness": 1,
                      "training_status_daily": 1}


def test_backfill_marks_empty_wellness_day(conn, fake_client, fixture):
    client = fake_client(by_day={"wellness": {"2026-05-20": fixture("wellness_empty")}})
    sync.backfill(client, conn, "2026-05-20", "2026-05-20")
    has_data = conn.execute(
        "SELECT has_data FROM daily_wellness WHERE date=?", ("2026-05-20",)
    ).fetchone()[0]
    assert has_data == 0


def test_backfill_excludes_today_when_to_date_omitted(conn, fake_client):
    today = dt.date.today()
    start = today - dt.timedelta(days=2)
    client = fake_client()
    sync.backfill(client, conn, start.isoformat())  # to_date defaults to yesterday

    day_calls = [d for (ep, d) in client.calls if ep == "sleep"]
    assert (today - dt.timedelta(days=1)).isoformat() in day_calls
    assert today.isoformat() not in day_calls
