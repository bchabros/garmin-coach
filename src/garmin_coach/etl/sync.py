"""Backfill and incremental sync orchestration for Garmin data.

The orchestration layer depends on an injected GarminClient protocol instead of
real Garmin transport. It stores raw payloads first, then normalizes into core
SQLite tables. Backfill keeps the original full-window behavior; incremental sync adds
per-stream watermarks and stream isolation.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..core import db, models

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Observable outcome of one incremental sync run."""

    attempted_streams: set[str] = field(default_factory=set)
    progressed_streams: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)
    # Per-activity enrichment failures (weather, exercise sets). Deliberately not
    # warnings: a missing enrichment is partial success (ADR 0001), not a stream
    # outage, so it is reported without degrading the run's status.
    enrichment_misses: list[str] = field(default_factory=list)

    @property
    def had_progress(self) -> bool:
        """Return whether at least one stream advanced its watermark."""
        return bool(self.progressed_streams)

    @property
    def total_outage(self) -> bool:
        """Return whether attempted streams all failed to progress."""
        return bool(self.warnings and self.attempted_streams and not self.had_progress)

    @property
    def degraded(self) -> bool:
        """Return whether some stream failed after another stream progressed."""
        return bool(self.warnings and self.had_progress)


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

    def get_activity_weather(self, activity_id: int) -> dict[str, Any] | None:
        """Fetch per-activity weather (air temperature) for one activity."""
        ...

    def get_activity_exercise_sets(self, activity_id: int) -> dict[str, Any] | None:
        """Fetch per-activity exercise sets for one activity."""
        ...

    def get_lactate_threshold(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch the latest LTHR anchor (no range) or the ranged detection history."""
        ...


@dataclass
class SyncContext:
    """Shared execution config for one ``sync_incremental`` run's stream helpers."""

    client: GarminClient
    conn: sqlite3.Connection
    result: SyncResult
    max_attempts: int
    retry_base_seconds: float


@dataclass(frozen=True)
class SyncStream:
    """Definition of a daily data stream."""

    name: str
    endpoint: str
    method: str
    normalize: Callable[[str, Any], dict[str, Any]]
    table: str

    def fetch(self, client: GarminClient, date: str) -> Any:
        """Fetch this stream's payload for one date through the transport seam."""
        return getattr(client, self.method)(date)

    def store(self, conn: sqlite3.Connection, date: str, payload: Any) -> None:
        """Store one payload raw-first, then normalized into its core table."""
        if payload is None:
            return
        db.insert_raw(conn, self.endpoint, date, json.dumps(payload))
        db.upsert_daily(conn, self.table, self.normalize(date, payload))


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


def _call_with_retry(
    action: Callable[[], Any], max_attempts: int, retry_base_seconds: float
) -> Any:
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


def _fetch_enrichment(
    call: Callable[[], dict[str, Any] | None],
    kind: str,
    activity_id: Any,
    misses: list[str] | None,
) -> dict[str, Any] | None:
    """Run one best-effort enrichment call, recording a failure instead of hiding it.

    A failure leaves the enrichment absent and never aborts the run (ADR 0001), but
    it is logged and appended to ``misses`` so the gap can be found and repaired
    later. A ``None`` result is not a miss: Garmin simply has nothing for it.
    """
    if activity_id is None:
        return None
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - enrichment isolation: record, keep going
        message = f"{kind} enrichment failed for activity {activity_id}: {exc}"
        logger.warning("sync: %s", message)
        if misses is not None:
            misses.append(message)
        return None


def _store_activities(
    conn: sqlite3.Connection,
    ref_date: str,
    activities: list[dict[str, Any]],
    client: GarminClient | None = None,
    misses: list[str] | None = None,
) -> None:
    """Store activity range payload raw-first, then upsert each core activity.

    When ``client`` is given, each activity is enriched with its weather
    (``temp_c``) and its exercise sets; both raw payloads are appended for
    reprocessing, filed under the activity's own day rather than the requested
    range start, so reprocessing by date finds them. Each enrichment is
    best-effort: a failure is recorded in ``misses`` and never aborts the run.
    """
    db.insert_raw(conn, "get_activities_by_date", ref_date, json.dumps(activities))
    for act in activities:
        activity_id = act.get("activityId")
        day = models.date_of(act.get("startTimeLocal")) or ref_date
        weather: dict[str, Any] | None = None
        if client is not None:
            weather = _store_weather(conn, client, activity_id, day, misses)
        db.upsert_activity(conn, models.normalize_activity(act, weather))
        if client is not None and activity_id is not None:
            _store_exercise_sets(conn, day, activity_id, client, misses)


def _store_weather(
    conn: sqlite3.Connection,
    client: GarminClient,
    activity_id: Any,
    ref_date: str,
    misses: list[str] | None,
) -> dict[str, Any] | None:
    """Fetch one activity's weather raw-first; return it for the core row's temp_c."""
    weather = _fetch_enrichment(
        lambda: client.get_activity_weather(activity_id), "weather", activity_id, misses
    )
    if weather is not None:
        db.insert_raw(conn, "get_activity_weather", ref_date, json.dumps(weather))
    return weather


def _store_exercise_sets(
    conn: sqlite3.Connection,
    ref_date: str,
    activity_id: int,
    client: GarminClient,
    misses: list[str] | None = None,
) -> None:
    """Fetch one activity's exercise sets raw-first, then replace its `activity_sets`."""
    payload = _fetch_enrichment(
        lambda: client.get_activity_exercise_sets(activity_id), "sets", activity_id, misses
    )
    if payload is None:
        return
    db.insert_raw(conn, "get_activity_exercise_sets", ref_date, json.dumps(payload))
    db.replace_activity_sets(
        conn, activity_id, models.normalize_exercise_sets(activity_id, payload)
    )


def _sync_lactate(
    client: GarminClient,
    conn: sqlite3.Connection,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> None:
    """Ingest the LTHR anchor into fitness_markers (best-effort, isolated).

    Without a range, upserts the single current detection (nightly sync). With a
    range, backfills the detection history via the ranged form. A missing payload
    or empty result is a no-op; the raw payload is appended once per call.
    """
    try:
        payload = client.get_lactate_threshold(start_date=start_date, end_date=end_date)
    except Exception:
        return
    if payload is None:
        return
    if start_date is None:
        rows = [models.normalize_lactate(payload)]
    else:
        rows = models.normalize_lactate_range(payload)
    rows = [r for r in rows if r.get("date") is not None]
    if not rows:
        return
    ref_date = end_date or rows[-1]["date"]
    db.insert_raw(conn, "get_lactate_threshold", ref_date, json.dumps(payload))
    for row in rows:
        db.upsert_daily(conn, "fitness_markers", row)


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
    _store_activities(conn, from_date, activities, client)
    conn.commit()

    # LTHR anchor: backfill the detection history via the ranged form.
    _sync_lactate(client, conn, start_date=from_date, end_date=end.isoformat())
    conn.commit()

    for d in _daterange(start, end):
        date = d.isoformat()
        for stream in _DAY_STREAMS:
            stream.store(conn, date, stream.fetch(client, date))
        conn.commit()


def _sync_activities(ctx: SyncContext, start: dt.date, end: dt.date) -> None:
    """Advance the activities stream, using range fetch then per-day fallback."""
    ctx.result.attempted_streams.add("activities")
    start_s = start.isoformat()
    end_s = end.isoformat()

    def fetch_activity_range() -> list[dict[str, Any]]:
        return ctx.client.get_activities(start_s, end_s)

    try:
        activities = (
            _call_with_retry(fetch_activity_range, ctx.max_attempts, ctx.retry_base_seconds) or []
        )
    except Exception:
        for d in _daterange(start, end):
            date = d.isoformat()

            def fetch_activity_day(fetch_date: str = date) -> list[dict[str, Any]]:
                return ctx.client.get_activities(fetch_date, fetch_date)

            try:
                activities = (
                    _call_with_retry(
                        fetch_activity_day,
                        ctx.max_attempts,
                        ctx.retry_base_seconds,
                    )
                    or []
                )
            except Exception as exc:
                ctx.result.warnings.append(f"activities failed for {date}: {exc}")
                ctx.conn.commit()
                break

            _store_activities(ctx.conn, date, activities, ctx.client, ctx.result.enrichment_misses)
            db.set_sync_watermark(ctx.conn, "activities", date)
            ctx.result.progressed_streams.add("activities")
            ctx.conn.commit()
    else:
        _store_activities(ctx.conn, start_s, activities, ctx.client, ctx.result.enrichment_misses)
        db.set_sync_watermark(ctx.conn, "activities", end_s)
        ctx.result.progressed_streams.add("activities")
        ctx.conn.commit()


def _sync_daily_stream(stream: SyncStream, ctx: SyncContext, start: dt.date, end: dt.date) -> None:
    """Advance one daily stream from its first missing date to the cutoff."""
    ctx.result.attempted_streams.add(stream.name)
    for d in _daterange(start, end):
        date = d.isoformat()

        def fetch_daily_payload(fetch_date: str = date) -> Any:
            return stream.fetch(ctx.client, fetch_date)

        try:
            payload = _call_with_retry(
                fetch_daily_payload,
                ctx.max_attempts,
                ctx.retry_base_seconds,
            )
        except Exception as exc:
            ctx.result.warnings.append(f"{stream.name} failed for {date}: {exc}")
            ctx.conn.commit()
            break

        stream.store(ctx.conn, date, payload)
        db.set_sync_watermark(ctx.conn, stream.name, date)
        ctx.result.progressed_streams.add(stream.name)
        ctx.conn.commit()


def refresh_day(client: GarminClient, conn: sqlite3.Connection, date: str) -> SyncResult:
    """Pull one (possibly partial) day raw-first, with per-stream isolation.

    Unlike ``sync_incremental``, no watermark is ever written: the day is
    expected to be incomplete, and the nightly sync must re-pull it complete.
    Used by the same-day refresh path (``daily.run_refresh_today``); single
    attempt per stream because the call is interactive (re-run to retry).

    Args:
        client: Transport client satisfying the ``GarminClient`` protocol.
        conn: Open SQLite connection with the schema bootstrapped.
        date: The day to pull, normally today.

    Returns:
        A :class:`SyncResult` with per-stream progress and warnings.
    """
    result = SyncResult()

    result.attempted_streams.add("activities")
    try:
        activities = client.get_activities(date, date) or []
    except Exception as exc:  # noqa: BLE001 - stream isolation: record, keep going
        result.warnings.append(f"activities failed for {date}: {exc}")
    else:
        _store_activities(conn, date, activities, client, result.enrichment_misses)
        result.progressed_streams.add("activities")
    conn.commit()

    for stream in _DAY_STREAMS:
        result.attempted_streams.add(stream.name)
        try:
            payload = stream.fetch(client, date)
        except Exception as exc:  # noqa: BLE001 - stream isolation: record, keep going
            result.warnings.append(f"{stream.name} failed for {date}: {exc}")
        else:
            stream.store(conn, date, payload)
            result.progressed_streams.add(stream.name)
        conn.commit()

    return result


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
    ctx = SyncContext(client, conn, SyncResult(), max_attempts, retry_base_seconds)

    activity_watermark = db.bootstrap_sync_watermark(
        conn, stream="activities", core_table="activities", data_start_date=data_start_date
    )
    activity_start = _next_date(activity_watermark)
    if activity_start <= end:
        _sync_activities(ctx, activity_start, end)

    for stream in _DAY_STREAMS:
        watermark = db.bootstrap_sync_watermark(
            conn, stream=stream.name, core_table=stream.table, data_start_date=data_start_date
        )
        start = _next_date(watermark)
        if start > end:
            continue

        _sync_daily_stream(stream, ctx, start, end)

    # LTHR anchor: one best-effort fetch per run, isolated from stream status.
    _sync_lactate(ctx.client, conn)
    conn.commit()

    return ctx.result
