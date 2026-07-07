"""Phase 3 coach digest tests.

Seam: the DB boundary. Each test seeds ``daily_metrics`` (+ ``training_status_daily``)
rows into a temp SQLite DB, calls ``build_digest(conn, ...)``, and asserts on the
returned digest dict - never on internal helpers. Mirrors test_features.py.
"""

from __future__ import annotations

import pathlib

from garmin_coach import db, weekly
from garmin_coach.digest import build_digest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _mart(conn, **row):
    db.upsert_daily(conn, "daily_metrics", row)


def _status(conn, **row):
    db.upsert_daily(conn, "training_status_daily", row)


def test_empty_window_has_shape_and_no_signals(conn):
    """An empty mart over an explicit range yields the window, an empty signal
    list, and a null-ish headline."""
    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")

    assert d["window"] == {"from": "2026-06-08", "to": "2026-06-08", "days": 1}
    assert d["signals"] == []
    assert d["headline"]["acwr"] is None
    assert d["headline"]["hrv_latest"] is None
    assert "disclaimer" in d


def test_headline_reflects_latest_day_and_7d_load(conn):
    """The headline carries the latest day's ACWR/HRV band and a trailing-7-day
    load total with easy/hard shares; acwr_reliable follows n_chronic."""
    _mart(conn, date="2026-06-08", load_day=100, load_low=60, load_high=40,
          load_anaerobic=0, acwr=0.9, n_chronic=20, hrv=70, hrv_baseline=68, hrv_sd=11)
    _mart(conn, date="2026-06-09", load_day=200, load_low=50, load_high=100,
          load_anaerobic=50, acwr=1.1, n_chronic=27, hrv=61, hrv_baseline=68, hrv_sd=11)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")

    h = d["headline"]
    assert h["acwr"] == 1.1
    assert h["n_chronic"] == 27
    assert h["acwr_reliable"] is False        # 27 < 28
    assert h["hrv_latest"] == 61
    assert h["hrv_baseline"] == 68
    assert h["hrv_sd"] == 11
    assert h["load_7d"] == 300                # 100 + 200
    assert abs(h["load_low_share"] - 110 / 300) < 1e-9
    assert abs(h["load_high_share"] - 140 / 300) < 1e-9


def _codes(d):
    return {s["code"] for s in d["signals"]}


def _signal(d, code):
    return next(s for s in d["signals"] if s["code"] == code)


def test_hrv_low_morning_fires_only_on_latest_low_night(conn):
    """HRV_LOW_MORNING fires when the latest day has hrv_low_flag==1, carrying
    the band facts; it does not fire when the latest night is not flagged."""
    _mart(conn, date="2026-06-08", hrv=55, hrv_baseline=68, hrv_sd=11, hrv_low_flag=1)
    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")

    assert "HRV_LOW_MORNING" in _codes(d)
    s = _signal(d, "HRV_LOW_MORNING")
    assert s["severity"] == "warn"
    assert s["facts"]["hrv"] == 55
    assert s["facts"]["baseline"] == 68
    assert s["facts"]["threshold"] == 68 - 1 * 11   # baseline - k*SD

    # latest night not flagged -> no signal
    _mart(conn, date="2026-06-09", hrv=70, hrv_baseline=68, hrv_sd=11, hrv_low_flag=0)
    d2 = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    assert "HRV_LOW_MORNING" not in _codes(d2)


def test_acwr_in_comfort_zone_does_not_fire(conn):
    """ACWR inside [risk_low, sweet_hi] and on a comfort bound does not flag."""
    _mart(conn, date="2026-06-08", acwr=1.0, n_chronic=28)
    assert "ACWR_OUT_OF_RANGE" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    )
    _mart(conn, date="2026-06-08", acwr=1.3, n_chronic=28)  # exactly sweet_hi
    assert "ACWR_OUT_OF_RANGE" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    )


def test_acwr_out_of_range_severity_and_reliability(conn):
    """Above sweet_hi -> warn; above risk_high -> alert; but while n_chronic is
    short the signal is indicative and never escalates past warn."""
    _mart(conn, date="2026-06-08", acwr=1.4, n_chronic=28)
    s = _signal(build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"),
                "ACWR_OUT_OF_RANGE")
    assert s["severity"] == "warn"
    assert s["facts"]["acwr"] == 1.4
    assert s["facts"]["reliable"] is True

    _mart(conn, date="2026-06-08", acwr=1.6, n_chronic=28)   # above risk_high, reliable
    s = _signal(build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"),
                "ACWR_OUT_OF_RANGE")
    assert s["severity"] == "alert"

    _mart(conn, date="2026-06-08", acwr=1.6, n_chronic=20)   # above risk_high, unreliable
    s = _signal(build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"),
                "ACWR_OUT_OF_RANGE")
    assert s["severity"] == "warn"                            # capped
    assert s["facts"]["reliable"] is False


def test_two_hard_days_needs_a_consecutive_pair(conn):
    """A single hard day does not fire; two consecutive days at/above hard_te_load
    do, and a pair ending at the window edge is flagged as an upcoming stack."""
    _mart(conn, date="2026-06-08", load_day=200)
    assert "TWO_HARD_DAYS" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    )

    _mart(conn, date="2026-06-09", load_day=160)   # second hard day, back-to-back
    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    s = _signal(d, "TWO_HARD_DAYS")
    assert s["severity"] == "warn"
    assert s["facts"]["first"] == "2026-06-08"
    assert s["facts"]["second"] == "2026-06-09"
    assert s["facts"]["trailing"] is True          # pair ends at to_date


def test_two_hard_days_not_consecutive_does_not_fire(conn):
    """Two hard days separated by an easy day do not stack."""
    _mart(conn, date="2026-06-08", load_day=200)
    _mart(conn, date="2026-06-09", load_day=30)
    _mart(conn, date="2026-06-10", load_day=200)
    assert "TWO_HARD_DAYS" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-10")
    )


def test_aerobic_low_shortage_fires_on_polarized_load_and_cross_checks_garmin(conn):
    """Too much grey zone: easy share below target AND hard share above target.
    garmin_agrees mirrors the latest training_status_daily.balance_phrase."""
    _mart(conn, date="2026-06-08", load_day=200, load_low=50, load_high=150, load_anaerobic=0)
    _mart(conn, date="2026-06-09", load_day=200, load_low=50, load_high=150, load_anaerobic=0)
    _status(conn, date="2026-06-09", balance_phrase="AEROBIC_LOW_SHORTAGE")

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    s = _signal(d, "AEROBIC_LOW_SHORTAGE")
    assert s["severity"] == "warn"
    assert abs(s["facts"]["low_share"] - 100 / 400) < 1e-9   # 0.25
    assert abs(s["facts"]["high_share"] - 300 / 400) < 1e-9  # 0.75
    assert s["garmin_agrees"] is True

    # Garmin disagrees -> flag stays but garmin_agrees is False
    _status(conn, date="2026-06-09", balance_phrase="BALANCED")
    d2 = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    assert _signal(d2, "AEROBIC_LOW_SHORTAGE")["garmin_agrees"] is False


def test_aerobic_low_shortage_does_not_fire_on_balanced_load(conn):
    """A predominantly-easy week does not flag a grey-zone shortage."""
    _mart(conn, date="2026-06-08", load_day=200, load_low=160, load_high=40, load_anaerobic=0)
    assert "AEROBIC_LOW_SHORTAGE" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    )


def test_hrv_sleep_confound_fires_when_correlated_with_a_low_night(conn):
    """When HRV tracks sleep score across enough paired nights and a low-HRV night
    is present, emit an info caveat that the dip may be sleep-driven."""
    # 8 nights, HRV and sleep_score move together (strong positive r), day 1 low.
    hrvs = [52, 58, 62, 66, 70, 61, 64, 68]
    for i, hrv in enumerate(hrvs):
        day = f"2026-06-{8 + i:02d}"
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20,
              hrv_low_flag=1 if i == 0 else 0)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-15")
    s = _signal(d, "HRV_SLEEP_CONFOUND")
    assert s["severity"] == "info"
    assert s["facts"]["r"] > 0.9
    assert s["facts"]["n"] == 8


def test_hrv_sleep_confound_needs_enough_pairs(conn):
    """Too few paired nights -> no confound caveat, even if perfectly correlated."""
    for i, hrv in enumerate([52, 60, 68]):
        day = f"2026-06-{8 + i:02d}"
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20,
              hrv_low_flag=1 if i == 0 else 0)
    assert "HRV_SLEEP_CONFOUND" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-10")
    )


def test_thresholds_override_changes_which_signals_fire(conn):
    """A tighter acwr_sweet_hi from coach_thresholds flags an ACWR that the code
    default would leave in the comfort zone."""
    _mart(conn, date="2026-06-08", acwr=1.2, n_chronic=28)
    d_default = build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    assert "ACWR_OUT_OF_RANGE" not in _codes(d_default)

    d_override = build_digest(
        conn, from_date="2026-06-08", to_date="2026-06-08",
        thresholds={"acwr_sweet_hi": 1.0},
    )
    assert "ACWR_OUT_OF_RANGE" in _codes(d_override)


def test_hrv_band_falls_back_to_thresholds_when_mart_has_no_band(conn):
    """When a row has an HRV reading but no computed baseline/SD (e.g. too few
    onboarding nights), coach_thresholds.hrv_baseline_ms/hrv_sd_ms fill the gap
    for the headline, HRV_LOW_MORNING, and hrv_low_flag - never overriding a
    mart-provided band."""
    _mart(conn, date="2026-06-08", hrv=50)  # no hrv_baseline/hrv_sd/hrv_low_flag

    d = build_digest(
        conn, from_date="2026-06-08", to_date="2026-06-08",
        thresholds={"hrv_baseline_ms": 68, "hrv_sd_ms": 11},
    )

    assert d["headline"]["hrv_baseline"] == 68
    assert d["headline"]["hrv_sd"] == 11
    s = _signal(d, "HRV_LOW_MORNING")  # 50 < 68 - 1*11 = 57
    assert s["facts"]["baseline"] == 68
    assert s["facts"]["sd"] == 11


def test_hrv_band_fallback_never_overrides_a_mart_band(conn):
    """A row with its own mart-computed band ignores the threshold fallback."""
    _mart(conn, date="2026-06-08", hrv=65, hrv_baseline=60, hrv_sd=5, hrv_low_flag=0)

    d = build_digest(
        conn, from_date="2026-06-08", to_date="2026-06-08",
        thresholds={"hrv_baseline_ms": 68, "hrv_sd_ms": 11},
    )

    assert d["headline"]["hrv_baseline"] == 60
    assert d["headline"]["hrv_sd"] == 5
    assert "HRV_LOW_MORNING" not in _codes(d)


def test_default_window_is_trailing_28_days_ending_at_latest_mart_day(conn):
    """With no explicit range, the window is the trailing 28 calendar days ending
    at the latest daily_metrics date (the mart's latest day == yesterday)."""
    _mart(conn, date="2026-06-08", load_day=0)
    _mart(conn, date="2026-07-03", load_day=0)

    d = build_digest(conn)

    assert d["window"]["to"] == "2026-07-03"
    assert d["window"]["from"] == "2026-06-06"   # 2026-07-03 minus 27 days
    assert d["window"]["days"] == 28


def test_signals_are_ordered_by_severity(conn):
    """The signal list is ordered alert > warn > info for the report."""
    # info (confound over 7+ correlated nights, one low) + alert (ACWR 1.6 reliable)
    hrvs = [52, 58, 62, 66, 70, 61, 64]
    for i, hrv in enumerate(hrvs):
        day = f"2026-06-{8 + i:02d}"
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20,
              hrv_low_flag=1 if i == 0 else 0)
    _mart(conn, date="2026-06-14", hrv=64, sleep_score=84, hrv_low_flag=0,
          acwr=1.6, n_chronic=28)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-14")
    severities = [s["severity"] for s in d["signals"]]
    order = {"alert": 0, "warn": 1, "info": 2}
    assert severities == sorted(severities, key=lambda s: order[s])
    assert severities[0] == "alert"
    assert severities[-1] == "info"


def _week(conn, **row):
    db.upsert_weekly(conn, row)


def test_weekly_section_carries_plan_vs_actual_and_deload_fires(conn):
    """build_digest exposes a ``weekly`` section for the latest complete week
    (facts + a 7-day plan-vs-actual table) and appends DELOAD_ADVISED when the
    weekly history warrants it."""
    _week(conn, week_start="2026-06-08", load_total=400, acwr_end=1.0, monotony=1.0)
    _week(conn, week_start="2026-06-15", load_total=600, acwr_end=1.2, monotony=1.0)
    _week(conn, week_start="2026-06-22", load_total=800, acwr_end=1.6, monotony=1.0,
          plan_adherence=6 / 7)
    # Daily rows for the latest week so plan-vs-actual can classify by load.
    _mart(conn, date="2026-06-22", load_day=0)      # Mon: rest (plan rest -> match)
    _mart(conn, date="2026-06-23", load_day=200)    # Tue: quality (plan quality -> match)
    _mart(conn, date="2026-06-26", load_day=0)      # Fri: rest (plan quality -> mismatch)

    d = build_digest(conn, from_date="2026-06-22", to_date="2026-06-28")

    wk = d["weekly"]
    assert wk["week_start"] == "2026-06-22"
    assert abs(wk["plan_adherence"] - 6 / 7) < 1e-9
    pva = wk["plan_vs_actual"]
    assert len(pva) == 7
    assert pva[0] == {"dow": 0, "date": "2026-06-22", "planned": "rest",
                      "actual": "rest", "match": True}
    fri = next(p for p in pva if p["date"] == "2026-06-26")
    assert fri["planned"] == "quality" and fri["actual"] == "rest" and fri["match"] is False

    assert "DELOAD_ADVISED" in _codes(d)


def test_weekly_section_uses_rollup_time_plan_vs_actual(conn):
    """The digest reads the weekly plan facts materialized by rollup."""
    for date, load in (
        ("2026-06-08", 0),
        ("2026-06-09", 200),
        ("2026-06-10", 50),
        ("2026-06-11", 0),
        ("2026-06-12", 0),
        ("2026-06-13", 200),
        ("2026-06-14", 50),
    ):
        db.upsert_daily(conn, "daily_metrics", {"date": date, "load_day": load})
    weekly.rollup(conn, data_start_date="2026-06-08", through_date="2026-06-14")
    conn.execute("UPDATE plan_template SET intent = 'rest'")

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-14")

    tue = next(p for p in d["weekly"]["plan_vs_actual"] if p["date"] == "2026-06-09")
    assert tue == {
        "dow": 1,
        "date": "2026-06-09",
        "planned": "quality",
        "actual": "quality",
        "match": True,
    }


def test_weekly_section_is_scoped_to_digest_window(conn):
    """A historical digest must not read weekly facts after its report horizon."""
    _week(conn, week_start="2026-06-08", load_total=100, acwr_end=1.0, monotony=1.0)
    _week(conn, week_start="2026-06-15", load_total=200, acwr_end=1.1, monotony=1.0)
    _week(conn, week_start="2026-06-22", load_total=400, acwr_end=1.8, monotony=3.0)

    d = build_digest(conn, from_date="2026-06-15", to_date="2026-06-21")

    assert d["weekly"]["week_start"] == "2026-06-15"
    assert "DELOAD_ADVISED" not in _codes(d)


def test_weekly_section_flags_retrospective_deload_on_big_load_drop(conn):
    """``was_deload`` is true when the latest week's load_total fell by at least
    ``deload_drop_pct`` vs the prior week; false on a mild drop; None with no history."""
    _week(conn, week_start="2026-06-08", load_total=1000)
    _week(conn, week_start="2026-06-15", load_total=500)  # 50% drop >= 40% default

    d = build_digest(conn, from_date="2026-06-15", to_date="2026-06-21")
    assert d["weekly"]["was_deload"] is True

    conn.execute("DELETE FROM weekly_metrics")
    _week(conn, week_start="2026-06-08", load_total=1000)
    _week(conn, week_start="2026-06-15", load_total=800)  # 20% drop < 40% default

    d = build_digest(conn, from_date="2026-06-15", to_date="2026-06-21")
    assert d["weekly"]["was_deload"] is False

    conn.execute("DELETE FROM weekly_metrics")
    _week(conn, week_start="2026-06-15", load_total=500)  # no predecessor week

    d = build_digest(conn, from_date="2026-06-15", to_date="2026-06-21")
    assert d["weekly"]["was_deload"] is None


def test_golden_regression_over_real_mart_slice(conn):
    """Seed the frozen real mart (2026-06-08..07-03) and assert the deterministic
    digest against independently-verified values (see docs/prd/phase-3.md)."""
    conn.executescript((FIXTURES / "digest_golden.sql").read_text())

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-07-03")

    h = d["headline"]
    assert abs(h["acwr"] - 1.0649) < 1e-3
    assert h["n_chronic"] == 26
    assert h["acwr_reliable"] is False          # 26 < 28
    assert h["hrv_latest"] is None              # 2026-07-03 has no HRV night

    codes = _codes(d)
    # Recent 7d (06-27..07-03) is all hard/anaerobic, zero easy -> shortage fires,
    # and Garmin's own balance_phrase agrees on 2026-07-03.
    assert "AEROBIC_LOW_SHORTAGE" in codes
    als = _signal(d, "AEROBIC_LOW_SHORTAGE")
    assert als["facts"]["low_share"] == 0.0
    assert als["garmin_agrees"] is True

    # Only consecutive hard pair in the window is 2026-06-19 / 2026-06-20.
    th = _signal(d, "TWO_HARD_DAYS")
    assert th["facts"]["first"] == "2026-06-19"
    assert th["facts"]["second"] == "2026-06-20"
    assert th["facts"]["trailing"] is False

    # Latest ACWR 1.06 sits in the comfort zone; latest night is unflagged.
    assert "ACWR_OUT_OF_RANGE" not in codes
    assert "HRV_LOW_MORNING" not in codes

    # Every signal is well-formed: scalar facts and a known severity.
    for s in d["signals"]:
        assert s["severity"] in {"info", "warn", "alert"}
        assert all(not isinstance(v, (dict, list)) for v in s["facts"].values())
