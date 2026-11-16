"""Dated weekly Garmin-estimate evidence, never a replacement Training Status."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

COMPLETED_WEEK_COUNT = 4
REPORTING_CADENCE_DAYS = 7


def _span(first: str | None, last: str | None) -> int | None:
    return (
        (dt.date.fromisoformat(last) - dt.date.fromisoformat(first)).days
        if first and last
        else None
    )


def _age(observed: str | None, estimated: str | None, end: dt.date) -> dict[str, Any]:
    observation_age = _span(observed, end.isoformat())
    estimate_age = _span(estimated, end.isoformat())
    age = estimate_age if estimated else observation_age
    stale = age >= REPORTING_CADENCE_DAYS if age is not None else None
    if not estimated and stale is False:
        stale = None  # A recent observation cannot establish a fresh source estimate.
    return {
        "observation_age_days": observation_age,
        "estimate_age_days": estimate_age,
        "age_days": age,
        "age_source": "estimate_date" if estimated else "observation_date" if observed else None,
        "stale": stale,
    }


def _endpoint(conn: sqlite3.Connection, end: dt.date, start: dt.date) -> dict[str, Any]:
    week = end - dt.timedelta(days=end.weekday())
    row = conn.execute(
        "SELECT date,vo2max,vo2max_estimate_date FROM training_status_daily "
        "WHERE date>=? AND date<=? AND vo2max IS NOT NULL "
        "AND (vo2max_estimate_date IS NULL OR vo2max_estimate_date<=?) "
        "ORDER BY date DESC LIMIT 1",
        (start.isoformat(), end.isoformat(), end.isoformat()),
    ).fetchone()
    observed, value, estimated = row if row else (None, None, None)
    return {
        "week_start": week.isoformat(),
        "week_end": (week + dt.timedelta(days=6)).isoformat(),
        "through_date": end.isoformat(),
        "partial": end.weekday() != 6,
        "full_history_week": week >= start,
        "value": value,
        "observation_date": observed,
        "estimate_date": estimated,
        **_age(observed, estimated, end),
        "carried": bool(
            observed
            and (observed < week.isoformat() or (estimated and estimated < week.isoformat()))
        ),
        "observation_in_week": bool(observed and observed >= week.isoformat()),
        "source": "Garmin training-status generic VO2max",
        "estimate_event_count": None,
        "event_count_reason": "source_event_identity_unavailable",
        "unavailable_reason": "before_data_start"
        if end < start
        else "no_saved_estimate"
        if row is None
        else None,
        "provenance_limit": "estimate_date_unknown" if row and not estimated else None,
    }


def _unavailable(
    base: dict[str, Any], end: dict[str, Any], span: int | None, requested: int
) -> list[str]:
    reasons = []
    if not base["full_history_week"] or not end["full_history_week"]:
        reasons.append("onboarding_partial_week")
    if base["value"] is None or end["value"] is None:
        reasons.append("missing_endpoint")
    if not base["observation_in_week"] or not end["observation_in_week"]:
        reasons.append("missing_week_observation")
    if base["estimate_date"] and base["estimate_date"] == end["estimate_date"]:
        reasons.append("same_source_date_not_independent_estimates")
    if base["stale"] is True or end["stale"] is True:
        reasons.append("stale_endpoint")
    if span is not None and span < requested:
        reasons.append("insufficient_span")
    return reasons


def _comparison(base: dict[str, Any], end: dict[str, Any]) -> dict[str, Any]:
    observed_span = _span(base["observation_date"], end["observation_date"])
    estimate_span = _span(base["estimate_date"], end["estimate_date"])
    span = estimate_span if estimate_span is not None else observed_span
    requested = (
        dt.date.fromisoformat(end["through_date"]) - dt.date.fromisoformat(base["through_date"])
    ).days
    reasons = _unavailable(base, end, span, requested)
    limited = [p["provenance_limit"] for p in (base, end) if p["provenance_limit"]]
    if base["carried"] or end["carried"]:
        limited.append("carried_estimate")
    return {
        "requested_from": base["through_date"],
        "requested_to": end["through_date"],
        "actual_from": base["observation_date"],
        "actual_to": end["observation_date"],
        "from_estimate_date": base["estimate_date"],
        "to_estimate_date": end["estimate_date"],
        "from_value": base["value"],
        "to_value": end["value"],
        "span_days": observed_span,
        "estimate_span_days": estimate_span,
        "comparison_span_days": span,
        "required_span_days": requested,
        "span_source": "estimate_dates" if estimate_span is not None else "observation_dates",
        "delta": end["value"] - base["value"] if not reasons else None,
        "unavailable_reasons": reasons,
        "limitations": sorted(set(limited)),
        "interpretation": "Comparison of stored snapshots, not independent measurement events.",
    }


def _garmin_status(
    conn: sqlite3.Connection, start: dt.date, through_date: str
) -> dict[str, Any] | None:
    status = conn.execute(
        "SELECT date,status_source_date,status_phrase,fitness_trend FROM training_status_daily "
        "WHERE date BETWEEN ? AND ? AND (status_source_date IS NULL OR status_source_date<=?) "
        "AND (status_phrase IS NOT NULL OR fitness_trend IS NOT NULL) ORDER BY date DESC LIMIT 1",
        (start.isoformat(), through_date, through_date),
    ).fetchone()
    return (
        dict(
            zip(("observation_date", "source_date", "phrase", "fitness_trend"), status, strict=True)
        )
        if status
        else None
    )


def build(
    conn: sqlite3.Connection, through_date: str, *, data_start_date: str | None = None
) -> dict[str, Any]:
    """Build four completed weeks plus baseline/current context at a fixed horizon.

    Comparisons require the advertised actual evidence span and non-stale endpoints.
    One reporting cadence marks evidence stale, without a physiological claim.
    A Sunday horizon closes that week; otherwise the current week is partial.
    """
    end = dt.date.fromisoformat(through_date)
    start = dt.date.fromisoformat(data_start_date or "0001-01-01")
    last_sunday = end - dt.timedelta(days=(end.weekday() + 1) % 7)
    points = [
        _endpoint(conn, last_sunday - dt.timedelta(weeks=i), start)
        for i in range(COMPLETED_WEEK_COUNT, -1, -1)
    ]
    weeks = [
        {**p, "change_from_previous_week": _comparison(points[i], p)}
        for i, p in enumerate(points[1:])
    ]
    current = _endpoint(conn, end, start) if end.weekday() != 6 else None
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
        "garmin_status": _garmin_status(conn, start, through_date),
        "evidence_policy": {
            "stale_after_days": REPORTING_CADENCE_DAYS,
            "minimum_span": "requested_comparison_span",
        },
        "caution": (
            "Garmin estimates are not laboratory measurements. Quarterly improvement does not "
            "establish recent improvement or explain Training Status."
        ),
    }
