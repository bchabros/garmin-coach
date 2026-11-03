"""Dated weekly Garmin-estimate evidence, never a replacement Training Status."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any


def _endpoint(conn: sqlite3.Connection, end: dt.date, start: dt.date) -> dict[str, Any]:
    week = end - dt.timedelta(days=end.weekday())
    row = conn.execute(
        "SELECT date,vo2max,vo2max_estimate_date FROM training_status_daily WHERE date>=? AND date<=? AND vo2max IS NOT NULL AND (vo2max_estimate_date IS NULL OR vo2max_estimate_date<=?) ORDER BY date DESC LIMIT 1",
        (start.isoformat(), end.isoformat(), end.isoformat()),
    ).fetchone()
    observed, value, estimated = row if row else (None, None, None)
    return {
        "week_start": week.isoformat(),
        "week_end": (week + dt.timedelta(days=6)).isoformat(),
        "through_date": end.isoformat(),
        "partial": end.weekday() != 6,
        "calendar_complete": end.weekday() == 6,
        "full_history_week": week >= start,
        "value": value,
        "observation_date": observed,
        "estimate_date": estimated,
        "estimate_age_days": (end - dt.date.fromisoformat(estimated)).days if estimated else None,
        "carried": bool(
            observed
            and (observed < week.isoformat() or (estimated and estimated < week.isoformat()))
        ),
        "observation_in_week": bool(observed and observed >= week.isoformat()),
        "source": "Garmin training-status generic VO2max",
        "estimate_event_count": None,
        "event_count_reason": "source_event_identity_unavailable",
        "unavailable_reason": "no_saved_estimate" if row is None else None,
        "provenance_limit": "estimate_date_unknown" if row and not estimated else None,
    }


def _comparison(base: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    if not base["full_history_week"] or not end["full_history_week"]:
        reasons.append("onboarding_partial_week")
    if base["value"] is None or end["value"] is None:
        reasons.append("missing_endpoint")
    if not base["observation_in_week"] or not end["observation_in_week"]:
        reasons.append("missing_week_observation")
    limited = [reason for reason in [base["provenance_limit"], end["provenance_limit"]] if reason]
    if base["estimate_date"] and base["estimate_date"] == end["estimate_date"]:
        limited.append("same_source_date_not_independent_estimates")
    if base["carried"] or end["carried"]:
        limited.append("carried_estimate")
    dates = (base["observation_date"], end["observation_date"])
    return {
        "requested_from": base["through_date"],
        "requested_to": end["through_date"],
        "actual_from": dates[0],
        "actual_to": dates[1],
        "from_estimate_date": base["estimate_date"],
        "to_estimate_date": end["estimate_date"],
        "from_value": base["value"],
        "to_value": end["value"],
        "span_days": (dt.date.fromisoformat(dates[1]) - dt.date.fromisoformat(dates[0])).days
        if all(dates)
        else None,
        "delta": end["value"] - base["value"] if not reasons else None,
        "unavailable_reasons": reasons,
        "limitations": sorted(set(limited)),
        "interpretation": "Comparison of stored snapshots, not independent measurement events.",
    }


def build(
    conn: sqlite3.Connection, through_date: str, *, data_start_date: str | None = None
) -> dict[str, Any]:
    """Build four completed weeks plus baseline/current context at a fixed horizon.

    A Sunday horizon closes that calendar week; otherwise current week is partial.
    Missing observation weeks remain visible and cannot be compressed in changes.
    """
    end = dt.date.fromisoformat(through_date)
    start = dt.date.fromisoformat(data_start_date or "0001-01-01")
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    points = [_endpoint(conn, last_sunday - dt.timedelta(weeks=i), start) for i in range(4, -1, -1)]
    weeks = [
        {**point, "change_from_previous_week": _comparison(points[i], point)}
        for i, point in enumerate(points[1:])
    ]
    current = _endpoint(conn, end, start) if end.weekday() != 6 else None
    status = conn.execute(
        "SELECT date,status_source_date,status_phrase,fitness_trend FROM training_status_daily WHERE date BETWEEN ? AND ? ORDER BY date DESC LIMIT 1",
        (start.isoformat(), through_date),
    ).fetchone()
    return {
        "through_date": through_date,
        "data_start": data_start_date,
        "data_start_known": data_start_date is not None,
        "completed_weeks": weeks,
        "baseline_week": points[0],
        "current_week": current,
        "current_progress": _comparison(points[-1], current) if current else None,
        "comparisons": {
            "two_weeks": _comparison(points[-3], points[-1]),
            "four_weeks": _comparison(points[0], points[-1]),
        },
        "garmin_status": dict(
            zip(("observation_date", "source_date", "phrase", "fitness_trend"), status, strict=True)
        )
        if status
        else None,
        "caution": "Garmin estimates are not laboratory measurements. Quarterly improvement does not establish recent improvement or explain Training Status.",
    }
