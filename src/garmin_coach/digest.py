"""Phase 3 coach engine: build a compact ``digest`` from the mart.

Reads only ``daily_metrics`` (+ ``training_status_daily``) - never Garmin live -
and returns a small dict (headline + coach signals) that the coach skill consumes
instead of raw mart rows. Non-durable, like the mart; not a system of record.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3

from . import signals as _signals

DISCLAIMER = (
    "This is a reading of your recorded data, not medical or coaching advice."
)

DEFAULT_THRESHOLDS: dict[str, float] = {
    "hrv_low_k_sd": 1,
    "acwr_risk_low": 0.8,
    "acwr_sweet_hi": 1.3,
    "acwr_risk_high": 1.5,
    "acwr_min_chronic_days": 28,
    "hard_te_load": 150,
    "aero_low_target_share": 0.60,
    "aero_high_target_share": 0.40,
    "hrv_sleep_r_min": 0.5,
    "hrv_sleep_min_pairs": 7,
}

LOAD_HIGHLIGHT_DAYS = 7
WINDOW_DAYS = 28
_SEVERITY_ORDER = {"alert": 0, "warn": 1, "info": 2}


def merge_thresholds(thresholds: dict[str, float] | None) -> dict[str, float]:
    """Effective thresholds: code defaults overridden by the ``coach_thresholds`` table."""
    return {**DEFAULT_THRESHOLDS, **(thresholds or {})}


def _resolve_window(
    conn: sqlite3.Connection, from_date: str | None, to_date: str | None
) -> tuple[str | None, str | None]:
    """Fill missing bounds: default ``to`` = latest mart day, ``from`` = to - 27d."""
    if to_date is None:
        row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
        to_date = row[0] if row else None
    if from_date is None and to_date is not None:
        from_date = (
            _dt.date.fromisoformat(to_date) - _dt.timedelta(days=WINDOW_DAYS - 1)
        ).isoformat()
    return from_date, to_date


def _date_range_days(from_date: str, to_date: str) -> int:
    d0 = _dt.date.fromisoformat(from_date)
    d1 = _dt.date.fromisoformat(to_date)
    return (d1 - d0).days + 1


def read_mart(conn: sqlite3.Connection, from_date: str, to_date: str) -> list[dict]:
    """Read daily_metrics rows in ``[from_date, to_date]`` as dicts, ordered by date."""
    cur = conn.execute(
        "SELECT * FROM daily_metrics WHERE date >= ? AND date <= ? ORDER BY date",
        (from_date, to_date),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _recent_rows(rows: list[dict], to_date: str) -> list[dict]:
    """The trailing LOAD_HIGHLIGHT_DAYS rows of the window (ending at to_date)."""
    cutoff = (
        _dt.date.fromisoformat(to_date) - _dt.timedelta(days=LOAD_HIGHLIGHT_DAYS - 1)
    ).isoformat()
    return [r for r in rows if r["date"] >= cutoff]


def _latest_balance_phrase(conn: sqlite3.Connection, from_date: str, to_date: str) -> str | None:
    row = conn.execute(
        "SELECT balance_phrase FROM training_status_daily "
        "WHERE date >= ? AND date <= ? ORDER BY date DESC LIMIT 1",
        (from_date, to_date),
    ).fetchone()
    return row[0] if row else None


def enrich_hrv_band(rows: list[dict], thresholds: dict[str, float]) -> list[dict]:
    """Fill a missing per-row HRV band from the ``coach_thresholds`` fallback.

    The mart's own ``hrv_baseline``/``hrv_sd`` are the source of truth;
    ``hrv_baseline_ms``/``hrv_sd_ms`` only stand in for a row that has an HRV
    reading but no computed band (e.g. too few nights for Phase 2 to compute one).
    """
    fb_baseline = thresholds.get("hrv_baseline_ms")
    fb_sd = thresholds.get("hrv_sd_ms")
    if fb_baseline is None or fb_sd is None:
        return rows
    k = thresholds["hrv_low_k_sd"]
    enriched = []
    for r in rows:
        if r.get("hrv") is not None and r.get("hrv_baseline") is None:
            r = {**r, "hrv_baseline": fb_baseline, "hrv_sd": fb_sd}
            if r.get("hrv_low_flag") is None:
                r["hrv_low_flag"] = 1 if r["hrv"] < fb_baseline - k * fb_sd else 0
        enriched.append(r)
    return enriched


def _headline(rows: list[dict], recent: list[dict], thresholds: dict[str, float]) -> dict:
    """Latest-day ACWR/HRV band plus a trailing-7-day load total and shares."""
    if not rows:
        return {
            "acwr": None, "n_chronic": None, "acwr_reliable": None,
            "hrv_latest": None, "hrv_baseline": None, "hrv_sd": None,
            "load_7d": None, "load_low_share": None, "load_high_share": None,
        }
    latest = rows[-1]
    n_chronic = latest["n_chronic"]
    min_days = thresholds["acwr_min_chronic_days"]
    reliable = None if n_chronic is None else n_chronic >= min_days

    load_7d = sum((r["load_day"] or 0) for r in recent)
    low_share, high_share, _ = _signals.load_shares(recent)
    return {
        "acwr": latest["acwr"],
        "n_chronic": n_chronic,
        "acwr_reliable": reliable,
        "hrv_latest": latest["hrv"],
        "hrv_baseline": latest["hrv_baseline"],
        "hrv_sd": latest["hrv_sd"],
        "load_7d": load_7d,
        "load_low_share": low_share,
        "load_high_share": high_share,
    }


def build_digest(
    conn: sqlite3.Connection,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict:
    """Build the coach digest for a window of the ``daily_metrics`` mart.

    Args:
        conn: Open SQLite connection with the mart populated.
        from_date: First day of the window (inclusive).
        to_date: Last day of the window (inclusive).
        thresholds: Coach thresholds; code defaults fill any missing key.

    Returns:
        A dict with ``window``, ``headline``, ``signals``, and ``disclaimer``.
    """
    thr = merge_thresholds(thresholds)
    from_date, to_date = _resolve_window(conn, from_date, to_date)
    if from_date is None or to_date is None:
        # Empty mart and no explicit range: nothing to report.
        return {
            "window": {"from": from_date, "to": to_date, "days": 0},
            "headline": _headline([], [], thr),
            "signals": [],
            "disclaimer": DISCLAIMER,
        }
    window = {
        "from": from_date,
        "to": to_date,
        "days": _date_range_days(from_date, to_date),
    }
    rows = enrich_hrv_band(read_mart(conn, from_date, to_date), thr)
    recent = _recent_rows(rows, to_date)
    balance_phrase = _latest_balance_phrase(conn, from_date, to_date)
    headline = _headline(rows, recent, thr)
    candidates = (
        _signals.aerobic_low_shortage(recent, thr, balance_phrase),
        _signals.acwr_out_of_range(rows, thr),
        _signals.hrv_low_morning(rows, thr),
        _signals.two_hard_days(rows, thr, to_date),
        _signals.hrv_sleep_confound(rows, thr),
    )
    signals = sorted(
        (s for s in candidates if s is not None),
        key=lambda s: _SEVERITY_ORDER[s["severity"]],
    )
    return {
        "window": window,
        "headline": headline,
        "signals": signals,
        "disclaimer": DISCLAIMER,
    }
