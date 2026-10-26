"""Shared evidence-date envelope for saved-data adapters."""

from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any

from ..core import db, plan

# Mart fields that accumulate during the day; they are only final after the
# nightly run. Morning-complete streams (sleep, HRV, readiness) never appear.
PARTIAL_INTRADAY_FIELDS = (
    "load_day",
    "acute7",
    "chronic28",
    "acwr",
    "load_low",
    "load_high",
    "load_anaerobic",
    "load_strength",
    "z1_min",
    "z2_min",
    "z3_min",
    "z4_min",
    "z5_min",
    "rhr",
    "rhr_delta",
    "stress_avg",
    "bb_min",
    "bb_max",
    "bb_recharge",
)


def wrap(conn: sqlite3.Connection, data: Any, *, recheck_days: int) -> dict[str, Any]:
    """Attach stored evidence dates, intraday limits and unconfirmed planned days."""
    data_through = db.latest_mart_date(conn)
    today_included = data_through == dt.date.today().isoformat()
    return {
        "data": data,
        "freshness": {
            "data_through": data_through,
            "today_included": today_included,
            "partial_fields": list(PARTIAL_INTRADAY_FIELDS) if today_included else [],
            "unconfirmed_days": plan.unconfirmed_days(conn, window_days=recheck_days),
        },
    }
