"""Phase 3 coach signals: pure functions from mart rows to coach findings.

Each function takes already-read ``daily_metrics`` rows (and, where needed,
``training_status_daily``) plus a thresholds dict, and returns a signal dict
``{code, severity, facts, garmin_agrees?}`` or ``None``. Semantics are pinned in
docs/adr/0003-phase-3-coach-signals.md. Facts are always flat scalars.
"""

from __future__ import annotations

import datetime as _dt
import statistics


def hrv_sleep_confound(rows: list[dict], thresholds: dict[str, float]) -> dict | None:
    """Rule 5: HRV tracks sleep score across the window and a low night is present.

    An info caveat - the worst HRV may be sleep-driven, not training-driven - not
    an alert. Requires enough paired nights and a non-constant series.
    """
    pairs = [
        (r["hrv"], r["sleep_score"])
        for r in rows
        if r.get("hrv") is not None and r.get("sleep_score") is not None
    ]
    if len(pairs) < thresholds["hrv_sleep_min_pairs"]:
        return None
    if not any(r.get("hrv_low_flag") == 1 for r in rows):
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    r = statistics.correlation(xs, ys)
    if r < thresholds["hrv_sleep_r_min"]:
        return None
    return {
        "code": "HRV_SLEEP_CONFOUND",
        "severity": "info",
        "facts": {"r": r, "n": len(pairs)},
    }


def two_hard_days(
    rows: list[dict], thresholds: dict[str, float], to_date: str
) -> dict | None:
    """Rule 4: two consecutive days each at/above ``hard_te_load``.

    Reports the most recent such pair; a pair ending at the window edge is flagged
    as an upcoming-stacking risk (the athlete's Friday-into-Saturday pattern).
    """
    hard = thresholds["hard_te_load"]
    pair: tuple[dict, dict] | None = None
    for prev, cur in zip(rows, rows[1:]):
        consecutive = (
            _dt.date.fromisoformat(cur["date"]) - _dt.date.fromisoformat(prev["date"])
        ).days == 1
        if consecutive and (prev.get("load_day") or 0) >= hard and (cur.get("load_day") or 0) >= hard:
            pair = (prev, cur)
    if pair is None:
        return None
    first, second = pair
    return {
        "code": "TWO_HARD_DAYS",
        "severity": "warn",
        "facts": {
            "first": first["date"],
            "second": second["date"],
            "first_load": first.get("load_day"),
            "second_load": second.get("load_day"),
            "trailing": second["date"] == to_date,
        },
    }


def load_shares(rows: list[dict]) -> tuple[float | None, float | None, float | None]:
    """Easy/hard load totals and shares (``low``, ``high``) over ``rows``.

    Returns ``(low_share, high_share, load_total)``; shares are ``None`` when
    ``rows`` carry no load at all.
    """
    low = sum((r.get("load_low") or 0) for r in rows)
    high = sum((r.get("load_high") or 0) for r in rows)
    anaero = sum((r.get("load_anaerobic") or 0) for r in rows)
    total = low + high + anaero
    if total <= 0:
        return None, None, total
    return low / total, high / total, total


def aerobic_low_shortage(
    recent_rows: list[dict],
    thresholds: dict[str, float],
    balance_phrase: str | None,
) -> dict | None:
    """Rule 1: too much grey zone over the recent window.

    Our own signal: the easy-load share is below target while the hard-load share
    is above target. ``garmin_agrees`` records whether Garmin's own
    ``balance_phrase`` concurs - never a passthrough.
    """
    low_share, high_share, total = load_shares(recent_rows)
    if low_share is None or high_share is None or not total:
        return None
    if not (
        low_share < thresholds["aero_low_target_share"]
        and high_share > thresholds["aero_high_target_share"]
    ):
        return None
    return {
        "code": "AEROBIC_LOW_SHORTAGE",
        "severity": "warn",
        "facts": {
            "low_share": low_share,
            "high_share": high_share,
            "target_low_share": thresholds["aero_low_target_share"],
        },
        "garmin_agrees": balance_phrase == "AEROBIC_LOW_SHORTAGE",
    }


def acwr_out_of_range(rows: list[dict], thresholds: dict[str, float]) -> dict | None:
    """Rule 2: the latest day's own ACWR sits outside the comfort zone.

    Warn above the sweet-spot ceiling, alert above the injury-risk ceiling, but
    while ``n_chronic`` is short the ratio is indicative and never escalates past
    warn.
    """
    if not rows:
        return None
    latest = rows[-1]
    acwr = latest.get("acwr")
    if acwr is None:
        return None
    low = thresholds["acwr_risk_low"]
    sweet_hi = thresholds["acwr_sweet_hi"]
    risk_hi = thresholds["acwr_risk_high"]
    if low <= acwr <= sweet_hi:
        return None
    n_chronic = latest.get("n_chronic")
    min_days = thresholds["acwr_min_chronic_days"]
    reliable = n_chronic is not None and n_chronic >= min_days
    severity = "alert" if (acwr > risk_hi and reliable) else "warn"
    return {
        "code": "ACWR_OUT_OF_RANGE",
        "severity": severity,
        "facts": {
            "acwr": acwr,
            "n_chronic": n_chronic,
            "reliable": reliable,
            "risk_low": low,
            "sweet_hi": sweet_hi,
            "risk_high": risk_hi,
        },
    }


def hrv_low_morning(rows: list[dict], thresholds: dict[str, float]) -> dict | None:
    """Rule 3: the latest day's HRV is flagged below its personal band.

    Recommends degrading today's quality session to easy.
    """
    if not rows:
        return None
    latest = rows[-1]
    if latest.get("hrv_low_flag") != 1:
        return None
    baseline = latest.get("hrv_baseline")
    sd = latest.get("hrv_sd")
    k = thresholds["hrv_low_k_sd"]
    threshold = baseline - k * sd if baseline is not None and sd is not None else None
    return {
        "code": "HRV_LOW_MORNING",
        "severity": "warn",
        "facts": {
            "hrv": latest.get("hrv"),
            "baseline": baseline,
            "sd": sd,
            "threshold": threshold,
        },
    }


def deload_advised(weekly_rows: list[dict], thresholds: dict[str, float]) -> dict | None:
    """Rule 6 (prospective): load has climbed into an overtraining flag.

    Fires when there is enough history and ``load_total`` rose strictly across
    the most recent ``deload_load_rise_weeks`` complete weeks and the latest week
    is either in hot ACWR (> ``acwr_risk_high``) or high monotony
    (> ``monotony_high``). Silent when history is too short - it never guesses.
    Retrospective "this week was a deload" stays a report fact, not a signal.
    """
    min_history = thresholds["deload_min_history_weeks"]
    rise = int(thresholds["deload_load_rise_weeks"])
    if len(weekly_rows) < max(min_history, rise) or rise < 2:
        return None

    recent = weekly_rows[-rise:]
    loads = [(w.get("load_total") or 0) for w in recent]
    rising = all(loads[i] < loads[i + 1] for i in range(len(loads) - 1))
    if not rising:
        return None

    latest = weekly_rows[-1]
    acwr_end = latest.get("acwr_end")
    monotony = latest.get("monotony")
    hot_acwr = acwr_end is not None and acwr_end > thresholds["acwr_risk_high"]
    high_monotony = monotony is not None and monotony > thresholds["monotony_high"]
    if not (hot_acwr or high_monotony):
        return None

    return {
        "code": "DELOAD_ADVISED",
        "severity": "warn",
        "facts": {
            "week_start": latest.get("week_start"),
            "load_total": latest.get("load_total"),
            "rise_weeks": rise,
            "acwr_end": acwr_end,
            "monotony": monotony,
        },
    }
