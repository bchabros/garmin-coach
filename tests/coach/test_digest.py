"""Phase 3 coach digest tests.

Seam: the DB boundary. Each test seeds ``daily_metrics`` (+ ``training_status_daily``)
rows into a temp SQLite DB, calls ``build_digest(conn, ...)``, and asserts on the
returned digest dict - never on internal helpers. Mirrors test_features.py.
"""

from __future__ import annotations

import datetime as _dt
import pathlib

from garmin_coach.coach.digest import build_digest
from garmin_coach.core import db
from garmin_coach.marts import weekly

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


def _mart(conn, **row):
    db.upsert_daily(conn, "daily_metrics", row)


def _status(conn, **row):
    db.upsert_daily(conn, "training_status_daily", row)


def test_empty_window_has_shape_and_no_data_signals(conn):
    """An empty mart over an explicit range yields the window, no data-driven
    signal, and a null-ish headline. PLAN_MISSING still fires: whether the week
    was authored is a fact about the plan, not about the data."""
    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")

    assert d["window"] == {"from": "2026-06-08", "to": "2026-06-08", "days": 1}
    assert _codes(d) == {"PLAN_MISSING"}
    assert d["headline"]["acwr"] is None
    assert d["headline"]["hrv_latest"] is None
    assert "disclaimer" in d


def test_headline_reflects_latest_day_and_7d_load(conn):
    """The headline carries the latest day's ACWR/HRV band and a trailing-7-day
    load total with easy/hard shares; acwr_reliable follows n_chronic."""
    _mart(
        conn,
        date="2026-06-08",
        load_day=100,
        load_low=60,
        load_high=40,
        load_anaerobic=0,
        acwr=0.9,
        n_chronic=20,
        hrv=70,
        hrv_baseline=68,
        hrv_sd=11,
    )
    _mart(
        conn,
        date="2026-06-09",
        load_day=200,
        load_low=50,
        load_high=100,
        load_anaerobic=50,
        acwr=1.1,
        n_chronic=27,
        hrv=61,
        hrv_baseline=68,
        hrv_sd=11,
    )

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")

    h = d["headline"]
    assert h["acwr"] == 1.1
    assert h["n_chronic"] == 27
    assert h["acwr_reliable"] is False  # 27 < 28
    assert h["hrv_latest"] == 61
    assert h["hrv_baseline"] == 68
    assert h["hrv_sd"] == 11
    assert h["load_7d"] == 300  # 100 + 200
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
    assert s["facts"]["threshold"] == 68 - 1 * 11  # baseline - k*SD

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
    s = _signal(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"), "ACWR_OUT_OF_RANGE"
    )
    assert s["severity"] == "warn"
    assert s["facts"]["acwr"] == 1.4
    assert s["facts"]["reliable"] is True

    _mart(conn, date="2026-06-08", acwr=1.6, n_chronic=28)  # above risk_high, reliable
    s = _signal(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"), "ACWR_OUT_OF_RANGE"
    )
    assert s["severity"] == "alert"

    _mart(conn, date="2026-06-08", acwr=1.6, n_chronic=20)  # above risk_high, unreliable
    s = _signal(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"), "ACWR_OUT_OF_RANGE"
    )
    assert s["severity"] == "warn"  # capped
    assert s["facts"]["reliable"] is False


def test_two_hard_days_needs_a_consecutive_pair(conn):
    """A single hard day does not fire; two consecutive days at/above hard_te_load
    do, and a pair ending at the window edge is flagged as an upcoming stack."""
    _mart(conn, date="2026-06-08", load_day=200)
    assert "TWO_HARD_DAYS" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")
    )

    _mart(conn, date="2026-06-09", load_day=160)  # second hard day, back-to-back
    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    s = _signal(d, "TWO_HARD_DAYS")
    assert s["severity"] == "warn"
    assert s["facts"]["first"] == "2026-06-08"
    assert s["facts"]["second"] == "2026-06-09"
    assert s["facts"]["trailing"] is True  # pair ends at to_date


def test_two_hard_days_not_consecutive_does_not_fire(conn):
    """Two hard days separated by an easy day do not stack."""
    _mart(conn, date="2026-06-08", load_day=200)
    _mart(conn, date="2026-06-09", load_day=30)
    _mart(conn, date="2026-06-10", load_day=200)
    assert "TWO_HARD_DAYS" not in _codes(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-10")
    )


def test_digest_carries_zones_block_when_present(conn):
    """The digest exposes the athlete_zones standing (ceiling, source, staleness)."""
    _mart(conn, date="2026-06-08", load_day=100, load_low=100, load_high=0, load_anaerobic=0)
    db.upsert_zones(
        conn,
        {
            "id": 1,
            "lthr_bpm": 175,
            "z1_hi_bpm": 140,
            "z2_hi_bpm": 156,
            "z3_hi_bpm": 164,
            "z4_hi_bpm": 173,
            "threshold_pace_s_per_km": 257.1,
            "z2_pace_ceiling_s_per_km": 334.2,
            "source": "threshold_pace_fallback+lthr",
            "lthr_detected_on": "2026-05-01",
            "computed_at": "2026-06-08",
            "stale": 1,
        },
    )

    z = build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")["zones"]
    assert z["lthr_bpm"] == 175
    assert z["z2_hi_bpm"] == 156
    assert z["z2_pace_ceiling_s_per_km"] == 334.2
    assert z["source"] == "threshold_pace_fallback+lthr"
    assert z["stale"] == 1
    assert z["lthr_age_days"] == 38  # 2026-05-01 -> 2026-06-08


def test_digest_zones_block_is_none_without_a_row(conn):
    _mart(conn, date="2026-06-08", load_day=100, load_low=100, load_high=0, load_anaerobic=0)
    assert build_digest(conn, from_date="2026-06-08", to_date="2026-06-08")["zones"] is None


def test_aerobic_low_shortage_fires_on_polarized_load_and_cross_checks_garmin(conn):
    """Too much grey zone: easy share below target AND hard share above target.
    garmin_agrees mirrors the latest training_status_daily.balance_phrase."""
    _mart(conn, date="2026-06-08", load_day=200, load_low=50, load_high=150, load_anaerobic=0)
    _mart(conn, date="2026-06-09", load_day=200, load_low=50, load_high=150, load_anaerobic=0)
    _status(conn, date="2026-06-09", balance_phrase="AEROBIC_LOW_SHORTAGE")

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    s = _signal(d, "AEROBIC_LOW_SHORTAGE")
    assert s["severity"] == "warn"
    assert abs(s["facts"]["low_share"] - 100 / 400) < 1e-9  # 0.25
    assert abs(s["facts"]["high_share"] - 300 / 400) < 1e-9  # 0.75
    assert s["garmin_agrees"] is True

    # Garmin disagrees -> flag stays but garmin_agrees is False
    _status(conn, date="2026-06-09", balance_phrase="BALANCED")
    d2 = build_digest(conn, from_date="2026-06-08", to_date="2026-06-09")
    assert _signal(d2, "AEROBIC_LOW_SHORTAGE")["garmin_agrees"] is False


def test_aerobic_low_shortage_carries_personal_z2_minute_share(conn):
    """When a Z2 ceiling exists, the grey-zone signal reports the personal read
    alongside the load-bucket read: share of run minutes at avg HR <= ceiling."""
    _mart(conn, date="2026-06-08", load_day=200, load_low=50, load_high=150, load_anaerobic=0)
    db.upsert_zones(
        conn, {"id": 1, "lthr_bpm": 175, "z2_hi_bpm": 156, "stale": 0, "source": "regression"}
    )
    # 30 min under the 156 ceiling (hr 150), 90 min over (hr 165) -> 0.25 share
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "start_local": "2026-06-08 08:00:00",
            "date": "2026-06-08",
            "gtype": "running",
            "discipline": "Bieganie",
            "avg_hr": 150,
            "dur_s": 1800,
        },
    )
    db.upsert_activity(
        conn,
        {
            "activity_id": 2,
            "start_local": "2026-06-08 18:00:00",
            "date": "2026-06-08",
            "gtype": "running",
            "discipline": "Bieganie",
            "avg_hr": 165,
            "dur_s": 5400,
        },
    )

    s = _signal(
        build_digest(conn, from_date="2026-06-08", to_date="2026-06-08"), "AEROBIC_LOW_SHORTAGE"
    )
    assert abs(s["facts"]["personal_z2_minute_share"] - 0.25) < 1e-9


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
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20, hrv_low_flag=1 if i == 0 else 0)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-15")
    s = _signal(d, "HRV_SLEEP_CONFOUND")
    assert s["severity"] == "info"
    assert s["facts"]["r"] > 0.9
    assert s["facts"]["n"] == 8


def test_hrv_sleep_confound_needs_enough_pairs(conn):
    """Too few paired nights -> no confound caveat, even if perfectly correlated."""
    for i, hrv in enumerate([52, 60, 68]):
        day = f"2026-06-{8 + i:02d}"
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20, hrv_low_flag=1 if i == 0 else 0)
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
        conn,
        from_date="2026-06-08",
        to_date="2026-06-08",
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
        conn,
        from_date="2026-06-08",
        to_date="2026-06-08",
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
        conn,
        from_date="2026-06-08",
        to_date="2026-06-08",
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
    assert d["window"]["from"] == "2026-06-06"  # 2026-07-03 minus 27 days
    assert d["window"]["days"] == 28


def test_signals_are_ordered_by_severity(conn):
    """The signal list is ordered alert > warn > info for the report."""
    # info (confound over 7+ correlated nights, one low) + alert (ACWR 1.6 reliable)
    hrvs = [52, 58, 62, 66, 70, 61, 64]
    for i, hrv in enumerate(hrvs):
        day = f"2026-06-{8 + i:02d}"
        _mart(conn, date=day, hrv=hrv, sleep_score=hrv + 20, hrv_low_flag=1 if i == 0 else 0)
    _mart(conn, date="2026-06-14", hrv=64, sleep_score=84, hrv_low_flag=0, acwr=1.6, n_chronic=28)

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
    _week(
        conn,
        week_start="2026-06-22",
        load_total=800,
        acwr_end=1.6,
        monotony=1.0,
        plan_adherence=6 / 7,
    )
    # Daily rows for the latest week so plan-vs-actual can classify by load.
    _mart(conn, date="2026-06-22", load_day=0)  # Mon: rest (plan rest -> match)
    _mart(conn, date="2026-06-23", load_day=200)  # Tue: quality (plan quality -> match)
    _mart(conn, date="2026-06-26", load_day=0)  # Fri: rest (plan quality -> mismatch)

    d = build_digest(conn, from_date="2026-06-22", to_date="2026-06-28")

    wk = d["weekly"]
    assert wk["week_start"] == "2026-06-22"
    assert abs(wk["plan_adherence"] - 6 / 7) < 1e-9
    pva = wk["plan_vs_actual"]
    assert len(pva) == 7
    assert pva[0] == {
        "dow": 0,
        "date": "2026-06-22",
        "planned": "rest",
        "actual": "rest",
        "match": True,
    }
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
    assert h["acwr_reliable"] is False  # 26 < 28
    assert h["hrv_latest"] is None  # 2026-07-03 has no HRV night

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


def _niggle(conn, date, body_part, severity, note=None):
    db.upsert_niggle(
        conn, {"date": date, "body_part": body_part, "severity": severity, "note": note}
    )


def test_niggle_reduced_mode_fires_for_active_niggle(conn):
    """An active niggle at/above the severity floor arms NIGGLE_REDUCED_MODE with
    flat facts (worst body part, severity, counts)."""
    _niggle(conn, "2026-06-14", "kolano", 4)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-14")

    assert "NIGGLE_REDUCED_MODE" in _codes(d)
    s = _signal(d, "NIGGLE_REDUCED_MODE")
    assert s["severity"] == "warn"
    assert s["facts"] == {
        "body_part": "kolano",
        "severity": 4,
        "n_active": 1,
        "days_active": 0,
    }


def test_niggle_below_severity_floor_is_silent(conn):
    """A logged niggle below niggle_reduced_mode_severity does not fire."""
    _niggle(conn, "2026-06-14", "kolano", 2)

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-14")
    assert "NIGGLE_REDUCED_MODE" not in _codes(d)


def test_niggle_outside_active_window_is_silent(conn):
    """A niggle older than niggle_active_days (7) no longer arms reduced-mode."""
    _niggle(conn, "2026-06-06", "kolano", 5)  # 8 days before to_date

    d = build_digest(conn, from_date="2026-06-01", to_date="2026-06-14")
    assert "NIGGLE_REDUCED_MODE" not in _codes(d)


def test_niggle_cleared_by_lower_severity_relog(conn):
    """A later, lower-severity entry for the same body part supersedes the first."""
    _niggle(conn, "2026-06-12", "kolano", 4)
    _niggle(conn, "2026-06-14", "kolano", 1)  # re-log lower -> latest wins

    d = build_digest(conn, from_date="2026-06-08", to_date="2026-06-14")
    assert "NIGGLE_REDUCED_MODE" not in _codes(d)


# --- Phase 8: movement-overlap signals + coverage ----------------------------


def _overlap(conn, date, dim, key, overlap):
    conn.execute(
        "INSERT INTO pattern_overlap(date, dim, key, load_d, load_prev, overlap) "
        "VALUES (?,?,?,?,?,?)",
        (date, dim, key, overlap, overlap, overlap),
    )


def _set(conn, activity_id, subcategory, set_idx=0):
    conn.execute(
        "INSERT OR IGNORE INTO activities(activity_id, start_local, date, gtype) VALUES (?,?,?,?)",
        (activity_id, "2026-06-12 17:00:00", "2026-06-12", "strength_training"),
    )
    conn.execute(
        "INSERT INTO activity_sets(activity_id, set_idx, subcategory) VALUES (?,?,?)",
        (activity_id, set_idx, subcategory),
    )


def test_overlap_signals_fire_on_a_constructed_stack(conn):
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    _overlap(conn, "2026-07-11", "pattern", "hinge", 63.0)
    _overlap(conn, "2026-07-11", "muscle", "grip", 55.0)
    _overlap(conn, "2026-07-11", "muscle", "posterior", 48.0)

    d = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")

    assert "PATTERN_STACK" in _codes(d)
    muscle = _signal(d, "MUSCLE_OVERLAP")
    assert muscle["facts"]["keys"] == "grip,posterior"
    assert muscle["severity"] == "warn"


def test_overlap_signals_silent_below_threshold(conn):
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    _overlap(conn, "2026-07-11", "pattern", "hinge", 30.0)  # below pattern_overlap_high

    d = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")
    assert "PATTERN_STACK" not in _codes(d)


def test_digest_reports_movement_coverage(conn):
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    _set(conn, 1, "BARBELL_DEADLIFT", set_idx=0)
    _set(conn, 1, "MYSTERY_LIFT", set_idx=1)

    cov = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")["movement"]
    assert cov["sets_total"] == 2
    assert cov["sets_unmapped"] == 1
    assert cov["unmapped"] == ["MYSTERY_LIFT"]


def test_digest_movement_none_without_sets(conn):
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    assert build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")["movement"] is None


# --- Phase 9: the plan section + TAPER_ACTIVE / RACE_PROXIMITY ---


def _goal(conn, date="2026-10-17", status="confirmed", priority="A", type="hyrox"):
    from garmin_coach.core import db

    db.insert_goal_event(
        conn,
        {
            "date": date,
            "type": type,
            "priority": priority,
            "status": status,
            "date_precision": "approx",
            "target_s": 3600,
            "note": None,
        },
    )


def test_digest_carries_the_current_block_and_countdown(conn):
    from garmin_coach.marts import periodize

    _mart(conn, date="2026-07-14", load_day=50)
    _goal(conn)
    periodize.rollup(conn, data_start_date="2026-06-08")

    digest = build_digest(conn, from_date="2026-07-14", to_date="2026-07-14")

    assert digest["plan"]["block"] == "base"
    assert digest["plan"]["weeks_to_event"] == 13
    assert not _codes(digest) & {"TAPER_ACTIVE", "RACE_PROXIMITY"}


def test_digest_fires_taper_active_and_race_proximity_in_the_taper(conn):
    from garmin_coach.marts import periodize

    _mart(conn, date="2026-10-07", load_day=50)
    _goal(conn)
    periodize.rollup(conn, data_start_date="2026-06-08")

    digest = build_digest(conn, from_date="2026-10-07", to_date="2026-10-07")

    assert digest["plan"]["block"] == "taper"
    assert {"TAPER_ACTIVE", "RACE_PROXIMITY"} <= _codes(digest)


def test_digest_plan_is_none_without_an_anchor(conn):
    _mart(conn, date="2026-07-14", load_day=50)

    digest = build_digest(conn, from_date="2026-07-14", to_date="2026-07-14")

    assert digest["plan"] is None
    assert "TAPER_ACTIVE" not in _codes(digest)


# --- Phase 10: HARD_RPE_YESTERDAY through the digest --------------------------


def _rated_activity(conn, activity_id, date, rpe):
    db.upsert_activity(
        conn,
        {
            "activity_id": activity_id,
            "start_local": f"{date} 08:00:00",
            "date": date,
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    db.upsert_session_rpe(conn, {"activity_id": activity_id, "rpe": rpe})


def test_hard_rpe_yesterday_fires_on_latest_day_rated_session(conn):
    _mart(conn, date="2026-06-14", load_day=100)
    _rated_activity(conn, 1, "2026-06-14", 9)
    s = _signal(
        build_digest(conn, from_date="2026-06-14", to_date="2026-06-14"), "HARD_RPE_YESTERDAY"
    )
    assert s["facts"]["rpe"] == 9
    assert s["facts"]["activity_id"] == 1


def test_hard_rpe_yesterday_silent_for_a_soft_session(conn):
    _mart(conn, date="2026-06-14", load_day=100)
    _rated_activity(conn, 1, "2026-06-14", 5)
    assert "HARD_RPE_YESTERDAY" not in _codes(
        build_digest(conn, from_date="2026-06-14", to_date="2026-06-14")
    )


def test_hard_rpe_yesterday_silent_when_hard_session_is_not_on_to_date(conn):
    _mart(conn, date="2026-06-13", load_day=100)
    _mart(conn, date="2026-06-14", load_day=100)
    _rated_activity(conn, 1, "2026-06-13", 9)  # hard, but a day before to_date
    assert "HARD_RPE_YESTERDAY" not in _codes(
        build_digest(conn, from_date="2026-06-13", to_date="2026-06-14")
    )


# --- Phase 10: recommendation block through the digest ------------------------


def test_recommendation_rides_in_digest_for_tomorrow(conn):
    _mart(conn, date="2026-06-14", load_day=100)
    dg = build_digest(conn, from_date="2026-06-14", to_date="2026-06-14")
    rec = dg["recommendation"]
    assert rec["target_date"] == "2026-06-15"
    expected = conn.execute(
        "SELECT intent FROM plan_template WHERE dow = ?",
        (_dt.date(2026, 6, 15).weekday(),),
    ).fetchone()[0]
    assert rec["planned_intent"] == expected
    assert rec["avoid"] == []
    assert rec["replan"] is None


def test_no_recommendation_without_a_horizon(conn):
    dg = build_digest(conn)  # empty mart -> to_date is None
    assert "recommendation" not in dg


# --- issue #21: the plan of record drives the digest --------------------------


def _seed_plan_week(conn, week_start, intents):
    conn.executemany(
        "INSERT INTO plan_week(week_start, dow, planned, intent) VALUES (?,?,?,?)",
        [(week_start, dow, intent, intent) for dow, intent in enumerate(intents)],
    )
    conn.commit()


def test_recommendation_starts_from_the_authored_plan_not_the_template(conn):
    """The core of issue #21: recommend() only ever softens, so a template `rest`
    could never be raised to the `quality` the athlete actually planned."""
    _mart(conn, date="2026-06-10", load_day=50)
    # 2026-06-11 is a Thursday; the template says rest.
    _seed_plan_week(conn, "2026-06-08", ["rest"] * 3 + ["quality"] + ["rest"] * 3)

    dg = build_digest(conn, from_date="2026-06-10", to_date="2026-06-10")

    assert dg["recommendation"]["target_date"] == "2026-06-11"
    assert dg["recommendation"]["planned_intent"] == "quality"


def test_plan_missing_fires_for_an_unplanned_week(conn):
    _mart(conn, date="2026-06-10", load_day=50)

    dg = build_digest(conn, from_date="2026-06-10", to_date="2026-06-10")

    signal = next(s for s in dg["signals"] if s["code"] == "PLAN_MISSING")
    assert signal["severity"] == "info"
    assert signal["facts"]["week_start"] == "2026-06-08"


def test_plan_missing_clears_once_the_week_is_authored(conn):
    _mart(conn, date="2026-06-10", load_day=50)
    _seed_plan_week(conn, "2026-06-08", ["rest"] * 7)

    dg = build_digest(conn, from_date="2026-06-10", to_date="2026-06-10")

    assert "PLAN_MISSING" not in _codes(dg)


def test_plan_missing_tracks_the_week_being_planned_not_the_data_horizon(conn):
    """The Monday trap: the mart stops at yesterday, so on Monday the horizon still
    sits in last week. The signal must follow the recommendation's target date -
    the day being planned - or it goes silent exactly when a fresh unplanned week
    starts and the coach most needs the cue."""
    _mart(conn, date="2026-06-14", load_day=50)  # Sunday horizon
    _seed_plan_week(conn, "2026-06-08", ["rest"] * 7)  # last week WAS authored

    dg = build_digest(conn, from_date="2026-06-14", to_date="2026-06-14")

    # The recommendation targets Monday 2026-06-15 - a new, unplanned week.
    assert dg["recommendation"]["target_date"] == "2026-06-15"
    signal = next(s for s in dg["signals"] if s["code"] == "PLAN_MISSING")
    assert signal["facts"]["week_start"] == "2026-06-15"


def test_plan_missing_is_silent_when_the_week_being_planned_is_authored(conn):
    _mart(conn, date="2026-06-14", load_day=50)
    _seed_plan_week(conn, "2026-06-15", ["rest"] * 7)  # next week authored

    dg = build_digest(conn, from_date="2026-06-14", to_date="2026-06-14")

    assert "PLAN_MISSING" not in _codes(dg)


# --- Issue #36: the report horizon bounds every section it can bound ---


def _dated_set(conn, activity_id, subcategory, date, set_idx=0):
    conn.execute(
        "INSERT OR IGNORE INTO activities(activity_id, start_local, date, gtype) VALUES (?,?,?,?)",
        (activity_id, f"{date} 17:00:00", date, "strength_training"),
    )
    conn.execute(
        "INSERT INTO activity_sets(activity_id, set_idx, subcategory) VALUES (?,?,?)",
        (activity_id, set_idx, subcategory),
    )


def test_movement_coverage_ignores_sets_after_the_horizon(conn):
    """Coverage is a fact about the sets captured by the horizon, not about today."""
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    _dated_set(conn, 1, "BARBELL_DEADLIFT", date="2026-07-10")
    _dated_set(conn, 2, "MYSTERY_LIFT", date="2026-07-20")  # after the horizon

    cov = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")["movement"]

    assert cov["sets_total"] == 1
    assert cov["sets_unmapped"] == 0
    assert cov["unmapped"] == []


def test_zones_section_flags_a_standing_row_computed_off_the_horizon(conn):
    """The zones singleton is 'current'; the digest must say when that is not the horizon."""
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    db.upsert_zones(
        conn,
        {"id": 1, "lthr_bpm": 175, "z2_hi_bpm": 156, "computed_at": "2026-07-20", "stale": 0},
    )

    zones_section = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")["zones"]

    assert zones_section["computed_at"] == "2026-07-20"
    assert zones_section["matches_horizon"] is False


def test_zones_section_confirms_a_standing_row_computed_at_the_horizon(conn):
    _mart(conn, date="2026-07-11", load_day=100, acwr=1.0, n_chronic=28)
    db.upsert_zones(
        conn,
        {"id": 1, "lthr_bpm": 175, "z2_hi_bpm": 156, "computed_at": "2026-07-11", "stale": 0},
    )

    zones_section = build_digest(conn, from_date="2026-07-11", to_date="2026-07-11")["zones"]

    assert zones_section["matches_horizon"] is True
