"""Phase 5 weekly rollups: derive ``weekly_metrics`` from ``daily_metrics``.

A mart-from-mart step. ``rollup`` reads the finished daily mart and materialises
one ``weekly_metrics`` row per *complete* week (Monday-Sunday, all seven days
at/before the cutoff). It never touches Garmin. See docs/adr/0005 and docs/glossary.md.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import statistics as _stats

from . import db
from . import thresholds as _thresholds


def _latest_mart_date(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()
    return row[0] if row and row[0] else None


def _plan_intents(conn: sqlite3.Connection) -> dict[int, str]:
    return {dow: intent for dow, intent in conn.execute(
        "SELECT dow, intent FROM plan_template"
    )}


def _actual_intent(row: dict | None, hard: float) -> str:
    """Classify a day to the plan vocabulary by load (see docs/glossary.md)."""
    if row is None:
        return "rest"
    load = row.get("load_day") or 0
    anaerobic = row.get("load_anaerobic") or 0
    if load == 0 and anaerobic == 0:
        return "rest"
    if load >= hard or anaerobic > 0:
        return "quality"
    return "easy"


def _max_consec_hard(loads: list[float], hard: float) -> int:
    best = run = 0
    for x in loads:
        run = run + 1 if x >= hard else 0
        best = max(best, run)
    return best


def _week_grid(monday: _dt.date, daily: dict[str, dict]) -> list[dict | None]:
    """Return the 7 daily-mart rows (or None) for the week starting on ``monday``."""
    dates = [(monday + _dt.timedelta(days=i)).isoformat() for i in range(7)]
    return [daily.get(d) for d in dates]


def _daily_rows(conn: sqlite3.Connection) -> dict[str, dict]:
    cur = conn.execute("SELECT * FROM daily_metrics")
    cols = [c[0] for c in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _share(part: float, total: float) -> float | None:
    return (part / total) if total else None


def _complete_weeks(data_start: str, end: str) -> list[_dt.date]:
    """Mondays of every complete week within [first full week, end]."""
    start_d = _dt.date.fromisoformat(data_start)
    end_d = _dt.date.fromisoformat(end)
    # First Monday on/after data_start (drop a partial leading week).
    first_monday = start_d + _dt.timedelta(days=(-start_d.weekday()) % 7)
    weeks: list[_dt.date] = []
    monday = first_monday
    while monday + _dt.timedelta(days=6) <= end_d:
        weeks.append(monday)
        monday += _dt.timedelta(days=7)
    return weeks


def _intent_rows(
    monday: _dt.date, week_rows: list[dict | None], plan: dict[int, str], hard: float
) -> list[dict]:
    """Per-day planned and actual intent facts for one completed week."""
    out: list[dict] = []
    for dow in range(7):
        date = (monday + _dt.timedelta(days=dow)).isoformat()
        planned = plan.get(dow)
        actual = _actual_intent(week_rows[dow], hard)
        out.append(
            {
                "dow": dow,
                "date": date,
                "planned": planned,
                "actual": actual,
                "match": planned == actual,
            }
        )
    return out


def plan_vs_actual(conn: sqlite3.Connection, week_start: str, hard: float) -> list[dict]:
    """Return the stored per-day plan-vs-actual facts for one completed week.

    Falls back to deriving the facts when a DB predates the materialized detail
    table contents, preserving the public interface for older local data.
    """
    stored = conn.execute(
        "SELECT dow, date, planned, actual, matched FROM weekly_plan_actual "
        "WHERE week_start = ? ORDER BY dow",
        (week_start,),
    ).fetchall()
    if stored:
        return [
            {
                "dow": dow,
                "date": date,
                "planned": planned,
                "actual": actual,
                "match": bool(matched),
            }
            for dow, date, planned, actual, matched in stored
        ]

    plan = _plan_intents(conn)
    daily = _daily_rows(conn)
    monday = _dt.date.fromisoformat(week_start)
    week_rows = _week_grid(monday, daily)
    return _intent_rows(monday, week_rows, plan, hard)


def rollup(
    conn: sqlite3.Connection,
    *,
    data_start_date: str,
    through_date: str | None = None,
) -> None:
    """Recompute ``weekly_metrics`` for every complete week from the daily mart.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First real-data date; weeks before it are not emitted.
        through_date: Cutoff (default: latest ``daily_metrics`` date). A week is
            emitted only when its Sunday is at/before this date.
    """
    end = through_date or _latest_mart_date(conn)
    if end is None:
        return

    hard = _thresholds.read(conn)["hard_te_load"]
    plan = _plan_intents(conn)
    daily = _daily_rows(conn)
    prev_hrv_mean: float | None = None
    for monday in _complete_weeks(data_start_date, end):
        week_rows = _week_grid(monday, daily)
        intent_rows = _intent_rows(monday, week_rows, plan, hard)
        row = _week_row(monday, week_rows, intent_rows, hard)
        hrv_mean = row["hrv_mean"]
        row["hrv_trend"] = (
            hrv_mean - prev_hrv_mean
            if hrv_mean is not None and prev_hrv_mean is not None
            else None
        )
        db.upsert_weekly(conn, row)
        db.replace_weekly_plan_actual(conn, row["week_start"], intent_rows)
        prev_hrv_mean = hrv_mean if hrv_mean is not None else prev_hrv_mean
    conn.commit()


def _monotony(loads: list[float]) -> float | None:
    """Foster monotony: mean daily load / sample SD over the 7 calendar days.

    None when fewer than two training days or the SD is zero (uncomputable).
    """
    if sum(1 for x in loads if x > 0) < 2:
        return None
    sd = _stats.stdev(loads)
    if sd == 0:
        return None
    return _stats.fmean(loads) / sd


def _sum(days: list[dict], col: str) -> float:
    return sum((d.get(col) or 0) for d in days)


def _mean_nonnull(days: list[dict], col: str) -> float | None:
    vals = [d[col] for d in days if d.get(col) is not None]
    return _stats.fmean(vals) if vals else None


def _week_row(
    monday: _dt.date, week_rows: list[dict | None], intent_rows: list[dict], hard: float
) -> dict:
    days = [r for r in week_rows if r is not None]
    loads = [(r.get("load_day") or 0) if r else 0 for r in week_rows]
    load_total = _sum(days, "load_day")
    load_low = _sum(days, "load_low")
    load_high = _sum(days, "load_high")
    load_anaerobic = _sum(days, "load_anaerobic")
    load_strength = _sum(days, "load_strength")
    monotony = _monotony(loads)
    strain = load_total * monotony if monotony is not None else None

    actual = [row["actual"] for row in intent_rows]
    matches = sum(1 for row in intent_rows if row["match"])
    return {
        "week_start": monday.isoformat(),
        "load_total": load_total,
        "load_low": load_low,
        "load_high": load_high,
        "load_anaerobic": load_anaerobic,
        "load_strength": load_strength,
        "low_share": _share(load_low, load_total),
        "high_share": _share(load_high, load_total),
        "anaero_share": _share(load_anaerobic, load_total),
        "strength_share": _share(load_strength, load_total),
        "monotony": monotony,
        "strain": strain,
        "max_consec_hard": _max_consec_hard(loads, hard),
        "plan_adherence": matches / 7,
        "n_sessions": sum(1 for x in loads if x > 0),
        "n_quality": sum(1 for a in actual if a == "quality"),
        "n_rest_days": sum(1 for a in actual if a == "rest"),
        "z2_min": _sum(days, "z2_min"),
        "threshold_min": _sum(days, "z3_min") + _sum(days, "z4_min"),
        "z5_min": _sum(days, "z5_min"),
        "hrv_mean": _mean_nonnull(days, "hrv"),
        "rhr_mean": _mean_nonnull(days, "rhr"),
        "sleep_score_mean": _mean_nonnull(days, "sleep_score"),
        "acwr_end": (week_rows[6] or {}).get("acwr"),
    }
