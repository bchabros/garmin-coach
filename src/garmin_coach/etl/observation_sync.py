"""Raw-first observation imports and a resumable seven-night history scan."""

from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..core import db, observations

if TYPE_CHECKING:
    from .sync import GarminClient, SyncContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    """One validated observation family and its transport/persistence boundary."""

    name: str
    endpoint: str
    method: str
    replace: Callable[[sqlite3.Connection, str, Any], None]


SOURCES = (
    Source("weight", "get_daily_weigh_ins", "get_weight", observations.replace_weight_day),
    Source(
        "race_forecasts",
        "get_race_predictions",
        "get_race_forecasts",
        observations.replace_forecast_day,
    ),
)


def pull_day(
    client: GarminClient,
    conn: sqlite3.Connection,
    source: Source,
    date: str,
    *,
    fetch: Callable[[Callable[[], Any]], Any] | None = None,
    stop_on_error: Callable[[Exception], bool] | None = None,
) -> str | None:
    """Commit raw before atomic source/check replacement, returning any failure kind.

    Raises:
        Exception: The caller's stop predicate matches, or failed storage cannot
            even retain the source-check outcome.
    """
    try:

        def action() -> Any:
            return getattr(client, source.method)(date)

        payload = fetch(action) if fetch is not None else action()
        db.insert_raw(conn, source.endpoint, date, json.dumps(payload))
        conn.commit()
        with conn:
            source.replace(conn, date, payload)
            observations.record_check(conn, source.name, date)
    except Exception as exc:  # noqa: BLE001 - optional sources remain independent
        if stop_on_error is not None and stop_on_error(exc):
            raise
        kind = type(exc).__name__
        with conn:
            observations.record_check(conn, source.name, date, kind)
        logger.warning("observation_sync: %s failed for %s: %s", source.name, date, kind)
        return kind
    return None


def run(ctx: SyncContext) -> None:
    """Import each source's pending/recent days, then one old-history bucket."""
    results = [(source, *_recent(ctx, source)) for source in SOURCES]
    for source, checked, failed in results:
        _history(ctx, source, checked, failed)


def _recent(ctx: SyncContext, source: Source) -> tuple[set[str], bool]:
    start = _start(ctx, source.name)
    checked: set[str] = set()
    if start > ctx.end:
        return checked, False
    ctx.result.attempted_streams.add(source.name)
    day = start
    while day <= ctx.end:
        date = day.isoformat()
        error = pull_day(ctx.client, ctx.conn, source, date, fetch=ctx.retry)
        if error:
            ctx.result.warnings.append(f"{source.name} failed for {date}: {error}")
            return checked, True
        checked.add(date)
        ctx.record_synced(source.name, date)
        day += dt.timedelta(days=1)
    return checked, False


def _start(ctx: SyncContext, stream: str) -> dt.date:
    watermark = db.get_sync_watermark(ctx.conn, stream)
    if watermark is None:
        next_missing = ctx.data_start
        for (date,) in ctx.conn.execute(
            "SELECT date FROM observation_checks WHERE stream=? AND date>=? "
            "AND checked_at IS NOT NULL AND error_kind IS NULL ORDER BY date",
            (stream, ctx.data_start.isoformat()),
        ):
            if date != next_missing.isoformat():
                break
            next_missing += dt.timedelta(days=1)
        watermark = (next_missing - dt.timedelta(days=1)).isoformat()
        db.set_sync_watermark(ctx.conn, stream, min(watermark, ctx.window_start.isoformat()))
    return max(
        min(dt.date.fromisoformat(watermark) + dt.timedelta(days=1), ctx.window_start),
        ctx.data_start,
    )


def _history(ctx: SyncContext, source: Source, checked: set[str], failed: bool) -> None:
    state = observations.scan_state(ctx.conn, source.name)
    if (
        failed
        or ctx.data_start > ctx.end
        or (state["last_successful_run"] and state["last_successful_run"] >= ctx.end.isoformat())
    ):
        return
    for date in _history_dates(ctx, state["next_bucket"], checked):
        error = pull_day(ctx.client, ctx.conn, source, date, fetch=ctx.retry)
        if error:
            ctx.result.warnings.append(f"{source.name} history failed for {date}: {error}")
            return
    with ctx.conn:
        observations.complete_scan(
            ctx.conn, source.name, ctx.end.isoformat(), state["successful_runs"]
        )


def _history_dates(ctx: SyncContext, bucket: int, checked: set[str]) -> Iterator[str]:
    last = min(ctx.end, ctx.window_start - dt.timedelta(days=1))
    day = ctx.data_start
    while day <= last:
        date = day.isoformat()
        if day.toordinal() % 7 == bucket and date not in checked:
            yield date
        day += dt.timedelta(days=1)
