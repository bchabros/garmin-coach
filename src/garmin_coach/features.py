"""Phase 2 metrics layer: compute the ``daily_metrics`` mart from core tables.

Reads only core (never Garmin live), recomputes one row per calendar day from
``data_start`` through the latest core date, and upserts by ``date``. The mart is
fully reproducible from core and is never a system of record.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
import statistics

from . import db, load, overlap, periodize, snapshot, thresholds, weekly, zones

HRV_WINDOW_NIGHTS = 60


def _date_range(start: str, end: str) -> list[str]:
    d0 = _dt.date.fromisoformat(start)
    d1 = _dt.date.fromisoformat(end)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _mart_start(data_start_date: str, from_date: str | None) -> str:
    """Start date for mart materialization, expanded for weekly coherence."""
    if from_date is None:
        return data_start_date
    data_start = _dt.date.fromisoformat(data_start_date)
    requested = _dt.date.fromisoformat(from_date)
    week_start = requested - _dt.timedelta(days=requested.weekday())
    return max(data_start, week_start).isoformat()


def _latest_core_date(conn: sqlite3.Connection) -> str | None:
    dates: list[str] = []
    row = conn.execute("SELECT MAX(date(start_local)) FROM activities").fetchone()
    if row and row[0]:
        dates.append(row[0])
    for table in ("hrv_nightly", "daily_wellness", "sleep"):
        row = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()
        if row and row[0]:
            dates.append(row[0])
    return max(dates) if dates else None


_EMPTY_LOAD = {
    "load_day": 0.0,
    "load_low": 0.0,
    "load_high": 0.0,
    "load_anaerobic": 0.0,
    "load_strength": 0.0,
}


def _load_by_day(
    conn: sqlite3.Connection, *, scale: float, sila_default_rpe: float
) -> dict[str, dict[str, float]]:
    """Aggregate per-day blended load and its four buckets.

    Each activity's load is the session-RPE blend (see :mod:`load`): strength work
    is scored from RPE and lands in ``load_strength``; cardio keeps its Garmin load,
    raised by a logged RPE, and buckets by Training Effect (anaerobic when
    anaero_te >= 1.0; else low when aero_te < 2.5, else high). The four buckets
    always sum to ``load_day``; strength stays out of the aerobic (low/high/anaero)
    shares that feed ``AEROBIC_LOW_SHORTAGE``.
    """
    rpe_by_activity = dict(conn.execute("SELECT activity_id, rpe FROM session_rpe"))
    days: dict[str, dict[str, float]] = {}
    for aid, date, discipline, aero_te, anaero_te, dur_s, garmin_load in conn.execute(
        "SELECT activity_id, date(start_local), discipline, aero_te, anaero_te, "
        "dur_s, training_load FROM activities"
    ):
        agg = days.setdefault(date, dict(_EMPTY_LOAD))
        srpe = load.srpe_load(
            discipline, rpe_by_activity.get(aid), dur_s,
            scale=scale, sila_default_rpe=sila_default_rpe,
        )
        blended = load.blend(discipline, garmin_load, srpe)
        agg["load_day"] += blended
        if discipline == load.STRENGTH_DISCIPLINE:
            agg["load_strength"] += blended
        elif (anaero_te or 0.0) >= 1.0:
            agg["load_anaerobic"] += blended
        elif (aero_te or 0.0) < 2.5:
            agg["load_low"] += blended
        else:
            agg["load_high"] += blended
    return days


def _zone_minutes_by_day(conn: sqlite3.Connection) -> dict[str, dict[str, float]]:
    """z1..z5 minutes per activity day: sum of hr_zX_s / 60."""
    days: dict[str, dict[str, float]] = {}
    for date, z1, z2, z3, z4, z5 in conn.execute(
        "SELECT date(start_local), COALESCE(SUM(hr_z1_s),0), COALESCE(SUM(hr_z2_s),0), "
        "COALESCE(SUM(hr_z3_s),0), COALESCE(SUM(hr_z4_s),0), COALESCE(SUM(hr_z5_s),0) "
        "FROM activities GROUP BY date(start_local)"
    ):
        days[date] = {
            "z1_min": z1 / 60, "z2_min": z2 / 60, "z3_min": z3 / 60,
            "z4_min": z4 / 60, "z5_min": z5 / 60,
        }
    return days


def _recovery_by_day(conn: sqlite3.Connection) -> dict[str, dict[str, int | None]]:
    """sleep_score and rhr (daily_wellness.rhr, else sleep.resting_hr) per day."""
    rec: dict[str, dict[str, int | None]] = {}
    for date, score, resting_hr in conn.execute(
        "SELECT date, score, resting_hr FROM sleep"
    ):
        rec[date] = {"sleep_score": score, "rhr": resting_hr}
    for date, rhr in conn.execute("SELECT date, rhr FROM daily_wellness"):
        entry = rec.setdefault(date, {"sleep_score": None, "rhr": None})
        if rhr is not None:
            entry["rhr"] = rhr
    return rec


def _hrv_by_day(conn: sqlite3.Connection, through_date: str) -> dict[str, int]:
    """Nightly avg_hrv keyed by date, excluding NULL nights."""
    return {
        date: hrv
        for date, hrv in conn.execute(
            "SELECT date, avg_hrv FROM hrv_nightly WHERE avg_hrv IS NOT NULL AND date <= ?",
            (through_date,),
        )
    }


def _hrv_band(hrv_by_day: dict[str, int]) -> tuple[float | None, float | None]:
    """Compute the whole-window HRV baseline and sample SD.

    Baseline is the median and SD is the sample std (ddof=1) over the last
    HRV_WINDOW_NIGHTS nights. Returns (None, None) if there is no data, and an
    SD of None when fewer than two nights are available.
    """
    nights = [hrv_by_day[d] for d in sorted(hrv_by_day)][-HRV_WINDOW_NIGHTS:]
    if not nights:
        return None, None
    baseline = statistics.median(nights)
    sd = statistics.stdev(nights) if len(nights) >= 2 else None
    return baseline, sd


def features(
    conn: sqlite3.Connection,
    *,
    data_start_date: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> None:
    """Recompute the ``daily_metrics`` mart and upsert it by date.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First real-data date; earlier days are explicit gaps.
        from_date: First changed day to emit. The actual recompute may expand
            backward to the Monday of that week so ``weekly_metrics`` never reads
            stale earlier days from the same week.
        to_date: Last day to emit (default: latest core date).
    """
    end = to_date or _latest_core_date(conn)
    if end is None:
        return
    start = _mart_start(data_start_date, from_date)

    thr = thresholds.read(conn)
    load_by_day = _load_by_day(
        conn, scale=thr["srpe_load_scale"], sila_default_rpe=thr["sila_default_rpe"]
    )

    def _load_day(d: _dt.date) -> float:
        return load_by_day.get(d.isoformat(), _EMPTY_LOAD)["load_day"]

    start_d = _dt.date.fromisoformat(data_start_date)

    def _acwr(day: str) -> dict[str, float | int | None]:
        d = _dt.date.fromisoformat(day)
        acute = sum(_load_day(d - _dt.timedelta(days=i)) for i in range(7)) / 7
        chronic = sum(_load_day(d - _dt.timedelta(days=i)) for i in range(28)) / 28
        n_chronic = sum(1 for i in range(28) if (d - _dt.timedelta(days=i)) >= start_d)
        return {
            "acute7": acute,
            "chronic28": chronic,
            "acwr": (acute / chronic) if chronic else None,
            "n_chronic": n_chronic,
        }

    zones_by_day = _zone_minutes_by_day(conn)
    empty_zones = {"z1_min": 0.0, "z2_min": 0.0, "z3_min": 0.0, "z4_min": 0.0, "z5_min": 0.0}
    recovery_by_day = _recovery_by_day(conn)
    empty_recovery: dict[str, int | None] = {"sleep_score": None, "rhr": None}
    hrv_by_day = _hrv_by_day(conn, end)
    hrv_baseline, hrv_sd = _hrv_band(hrv_by_day)
    threshold = hrv_baseline - hrv_sd if hrv_baseline is not None and hrv_sd is not None else None

    for date in _date_range(start, end):
        hrv = hrv_by_day.get(date)
        low_flag: int | None = None
        if hrv is not None and threshold is not None:
            low_flag = 1 if hrv < threshold else 0
        day_load = load_by_day.get(date, _EMPTY_LOAD)
        row = {
            "date": date,
            "load_day": day_load["load_day"],
            "load_low": day_load["load_low"],
            "load_high": day_load["load_high"],
            "load_anaerobic": day_load["load_anaerobic"],
            "load_strength": day_load["load_strength"],
            "hrv": hrv,
            "hrv_baseline": hrv_baseline,
            "hrv_sd": hrv_sd,
            "hrv_low_flag": low_flag,
            **_acwr(date),
            **zones_by_day.get(date, empty_zones),
            **recovery_by_day.get(date, empty_recovery),
        }
        db.upsert_daily(conn, "daily_metrics", row)
    conn.commit()

    # Roll the freshly-written daily mart up into weekly_metrics (complete weeks).
    weekly.rollup(conn, data_start_date=data_start_date, through_date=to_date)

    # Rebuild the block calendar counted back from the anchor race (spans the future).
    periodize.rollup(conn, data_start_date=data_start_date, through_date=end)

    # Recompute personal training zones from the LTHR anchor + aerobic runs.
    zones.rollup(conn, through_date=end)

    # Rebuild the movement-pattern overlap mart from per-set data.
    overlap.rollup(conn, through_date=end)

    # Compose the current standing last, so it copies the fresh weekly + zones rows.
    snapshot.rollup(conn, through_date=end)
