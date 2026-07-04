"""Backfill and incremental sync orchestration for Garmin data.

The orchestration layer depends on an injected GarminClient protocol instead of
real Garmin transport. It stores raw payloads first, then normalizes into core
SQLite tables. Backfill keeps Phase 0 behavior; incremental sync adds Phase 1
per-stream watermarks and stream isolation.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple, Protocol

from . import db, models


@dataclass
class SyncResult:
    """Observable outcome of one incremental sync run."""

    attempted_streams: set[str] = field(default_factory=set)
    progressed_streams: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    @property
    def had_progress(self) -> bool:
        """Return whether at least one stream advanced its watermark."""
        return bool(self.progressed_streams)


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


class SyncStream(NamedTuple):
    """Definition of a daily data stream."""

    name: str
    endpoint: str
    method: str
    normalize: Callable[[str, Any], dict[str, Any]]
    table: str


# Per-day streams definition
_DAY_STREAMS = (
    SyncStream("sleep", "get_sleep_data", "get_sleep", models.normalize_sleep, "sleep"),
    SyncStream("hrv", "get_hrv_data", "get_hrv", models.normalize_hrv, "hrv_nightly"),
    SyncStream(
        "wellness",
        "get_user_summary",
        "get_wellness",
        models.normalize_wellness,
        "daily_wellness",
    ),
    SyncStream(
        "readiness",
        "get_training_readiness",
        "get_readiness",
        models.normalize_readiness,
        "training_readiness",
    ),
    SyncStream(
        "status",
        "get_training_status",
        "get_status",
        models.normalize_status,
        "training_status_daily",
    ),
)


def _daterange(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def _default_end(to_date: str | None) -> dt.date:
    return dt.date.fromisoformat(to_date) if to_date else dt.date.today() - dt.timedelta(days=1)


def _next_date(date: str) -> dt.date:
    return dt.date.fromisoformat(date) + dt.timedelta(days=1)


def _call_with_retry(action: Callable[[], Any], max_attempts: int, retry_base_seconds: float) -> Any:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return action()
        except Exception as exc:  # pragma: no cover - last_error is always raised below
            last_error = exc
            if attempt < max_attempts - 1 and retry_base_seconds > 0:
                time.sleep(retry_base_seconds * (2**attempt))
    raise last_error or RuntimeError("retry failed without an exception")


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
    end = _default_end(to_date)

    # Activities come as one range call; store raw once, upsert each activity.
    activities = client.get_activities(from_date, end.isoformat()) or []
    db.insert_raw(conn, "get_activities_by_date", from_date, json.dumps(activities))
    for act in activities:
        db.upsert_activity(conn, models.normalize_activity(act))
    conn.commit()

    for d in _daterange(start, end):
        date = d.isoformat()
        for stream in _DAY_STREAMS:
            payload = getattr(client, stream.method)(date)
            if payload is None:
                continue
            db.insert_raw(conn, stream.endpoint, date, json.dumps(payload))
            db.upsert_daily(conn, stream.table, stream.normalize(date, payload))
        conn.commit()


def sync_incremental(
    client: GarminClient,
    conn: sqlite3.Connection,
    data_start_date: str,
    to_date: str | None = None,
    max_attempts: int = 3,
    retry_base_seconds: float = 1.0,
) -> SyncResult:
    """Pull only dates after each stream's watermark through `to_date`/yesterday."""
    end = _default_end(to_date)
    result = SyncResult()

    activity_watermark = db.bootstrap_sync_watermark(
        conn, stream="activities", core_table="activities", data_start_date=data_start_date
    )
    activity_start = _next_date(activity_watermark)
    if activity_start <= end:
        result.attempted_streams.add("activities")
        start_s = activity_start.isoformat()
        end_s = end.isoformat()
        def fetch_activity_range() -> list[dict[str, Any]]:
            return client.get_activities(start_s, end_s)

        try:
            activities = _call_with_retry(fetch_activity_range, max_attempts, retry_base_seconds) or []
        except Exception:
            for d in _daterange(activity_start, end):
                date = d.isoformat()
                def fetch_activity_day(fetch_date: str = date) -> list[dict[str, Any]]:
                    return client.get_activities(fetch_date, fetch_date)

                try:
                    activities = _call_with_retry(
                        fetch_activity_day,
                        max_attempts,
                        retry_base_seconds,
                    ) or []
                except Exception as exc:
                    result.warnings.append(f"activities failed for {date}: {exc}")
                    conn.commit()
                    break

                db.insert_raw(conn, "get_activities_by_date", date, json.dumps(activities))
                for act in activities:
                    db.upsert_activity(conn, models.normalize_activity(act))
                db.set_sync_watermark(conn, "activities", date)
                result.progressed_streams.add("activities")
                conn.commit()
        else:
            db.insert_raw(conn, "get_activities_by_date", start_s, json.dumps(activities))
            for act in activities:
                db.upsert_activity(conn, models.normalize_activity(act))
            db.set_sync_watermark(conn, "activities", end_s)
            result.progressed_streams.add("activities")
            conn.commit()

    for stream in _DAY_STREAMS:
        watermark = db.bootstrap_sync_watermark(
            conn, stream=stream.name, core_table=stream.table, data_start_date=data_start_date
        )
        start = _next_date(watermark)
        if start > end:
            continue

        result.attempted_streams.add(stream.name)
        for d in _daterange(start, end):
            date = d.isoformat()

            def fetch_daily_payload(s: SyncStream = stream, fetch_date: str = date) -> Any:
                return getattr(client, s.method)(fetch_date)

            try:
                payload = _call_with_retry(
                    fetch_daily_payload,
                    max_attempts,
                    retry_base_seconds,
                )
            except Exception as exc:
                result.warnings.append(f"{stream.name} failed for {date}: {exc}")
                conn.commit()
                break

            if payload is not None:
                db.insert_raw(conn, stream.endpoint, date, json.dumps(payload))
                db.upsert_daily(conn, stream.table, stream.normalize(date, payload))
            db.set_sync_watermark(conn, stream.name, date)
            result.progressed_streams.add(stream.name)
            conn.commit()

    return result
