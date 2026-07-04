"""Incremental-free backfill (Phase 0): pull a date range, store raw, normalize.

Deliberately simple: no retry, no per-day fallback, no watermark (Phase 1). The
guarantee here is idempotency — re-running converges the core tables — and
"raw first" so a re-run after a crash reprocesses without re-hitting Garmin.
Every write for a given day happens in one transaction (raw + core atomic).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Protocol

from . import db, models


class GarminClient(Protocol):
    """Transport seam. sync depends on this, not on garminconnect directly."""

    def get_activities(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Fetch activity summaries for an inclusive date range."""
        ...

    def get_sleep(self, date: str) -> dict[str, Any] | None:
        """Fetch sleep data for one date."""
        ...

    def get_hrv(self, date: str) -> dict[str, Any] | None:
        """Fetch nightly HRV data for one date."""
        ...

    def get_wellness(self, date: str) -> dict[str, Any] | None:
        """Fetch daily wellness summary data for one date."""
        ...

    def get_readiness(self, date: str) -> Any:
        """Fetch training readiness data for one date."""
        ...

    def get_status(self, date: str) -> dict[str, Any] | None:
        """Fetch training status data for one date."""
        ...


# Per-day streams: (endpoint label, client method, normalizer, target table).
_DAY_STREAMS = (
    ("get_sleep_data", "get_sleep", models.normalize_sleep, "sleep"),
    ("get_hrv_data", "get_hrv", models.normalize_hrv, "hrv_nightly"),
    ("get_user_summary", "get_wellness", models.normalize_wellness, "daily_wellness"),
    ("get_training_readiness", "get_readiness", models.normalize_readiness, "training_readiness"),
    ("get_training_status", "get_status", models.normalize_status, "training_status_daily"),
)


def _daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def backfill(
    client: GarminClient,
    conn: sqlite3.Connection,
    from_date: str,
    to_date: str | None = None,
) -> None:
    """Backfill the inclusive date range from `from_date` to `to_date`.

    to_date defaults to yesterday because today is incomplete; HRV and sleep
    land after the night.
    """
    start = dt.date.fromisoformat(from_date)
    end = dt.date.fromisoformat(to_date) if to_date else dt.date.today() - dt.timedelta(days=1)

    # Activities come as one range call; store raw once, upsert each activity.
    activities = client.get_activities(from_date, end.isoformat()) or []
    db.insert_raw(conn, "get_activities_by_date", from_date, json.dumps(activities))
    for act in activities:
        db.upsert_activity(conn, models.normalize_activity(act))
    conn.commit()

    for d in _daterange(start, end):
        date = d.isoformat()
        for endpoint, method, normalize, table in _DAY_STREAMS:
            payload = getattr(client, method)(date)
            if payload is None:
                continue
            db.insert_raw(conn, endpoint, date, json.dumps(payload))
            db.upsert_daily(conn, table, normalize(date, payload))
        conn.commit()
