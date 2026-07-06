"""Phase 5 signal tests: ``deload_advised`` as a pure function.

Seam: the pure signal boundary (weekly rows + thresholds -> signal dict | None),
mirroring the other functions in signals.py. No DB, no Garmin.
"""

from __future__ import annotations

from garmin_coach.signals import deload_advised

THRESHOLDS = {
    "deload_min_history_weeks": 3,
    "deload_load_rise_weeks": 3,
    "acwr_risk_high": 1.5,
    "monotony_high": 2.0,
}


def _week(load_total, acwr_end, monotony):
    return {"load_total": load_total, "acwr_end": acwr_end, "monotony": monotony}


def test_fires_on_rising_load_into_hot_acwr():
    rows = [
        _week(400, 1.0, 1.0),
        _week(600, 1.2, 1.0),
        _week(800, 1.6, 1.0),  # hot ACWR > 1.5
    ]
    sig = deload_advised(rows, THRESHOLDS)
    assert sig is not None
    assert sig["code"] == "DELOAD_ADVISED"
    assert sig["severity"] == "warn"


def test_silent_when_history_too_short():
    rows = [_week(600, 1.2, 1.0), _week(800, 1.6, 1.0)]  # only 2 weeks
    assert deload_advised(rows, THRESHOLDS) is None


def test_fires_on_rising_load_into_high_monotony():
    rows = [
        _week(400, 1.0, 1.0),
        _week(600, 1.0, 1.5),
        _week(800, 1.0, 2.5),  # ACWR calm but monotony > 2.0
    ]
    assert deload_advised(rows, THRESHOLDS)["code"] == "DELOAD_ADVISED"


def test_silent_when_load_not_strictly_rising():
    rows = [
        _week(800, 1.6, 1.0),
        _week(600, 1.6, 1.0),  # dip breaks the rise
        _week(700, 1.6, 1.0),
    ]
    assert deload_advised(rows, THRESHOLDS) is None
