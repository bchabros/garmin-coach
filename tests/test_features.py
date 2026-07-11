"""Phase 2 metrics (mart) tests.

Seam: the DB boundary. Each test seeds core rows into a temp SQLite DB, runs
``features(conn, ...)``, reads ``daily_metrics`` back, and asserts on it -
never on internal helpers. Mirrors test_sync.py / test_db.py.
"""

from __future__ import annotations

import pathlib

from garmin_coach import db, features

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DATA_START = "2026-06-08"


def _rows(conn) -> list[dict]:
    """Read daily_metrics back as a list of dicts, ordered by date."""
    cur = conn.execute("SELECT * FROM daily_metrics ORDER BY date")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _by_date(conn) -> dict[str, dict]:
    return {r["date"]: r for r in _rows(conn)}


def test_emits_one_gap_filled_row_per_calendar_day(conn):
    """Every calendar day from data_start to the latest core date gets a row,
    including days with no core data at all."""
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-06-08", "avg_hrv": 70})
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-06-10", "avg_hrv": 66})

    features.features(conn, data_start_date=DATA_START)

    dates = [r["date"] for r in _rows(conn)]
    assert dates == ["2026-06-08", "2026-06-09", "2026-06-10"]
    # the gap day (06-09) exists with zero training load, not missing
    assert _by_date(conn)["2026-06-09"]["load_day"] == 0


def test_hrv_baseline_sd_and_low_flag(conn):
    """Baseline is the whole-window median; SD is sample std (ddof=1); a night
    flags when avg_hrv < baseline - 1*SD (strict)."""
    # series [60, 64, 68, 72, 76]: median 68, sample SD sqrt(160/4)=6.32455...,
    # threshold 61.675 -> only 60 (day 1) is strictly below.
    for i, hrv in enumerate([60, 64, 68, 72, 76]):
        db.upsert_daily(conn, "hrv_nightly", {"date": f"2026-06-{8 + i:02d}", "avg_hrv": hrv})

    features.features(conn, data_start_date=DATA_START)

    by_date = _by_date(conn)
    for r in by_date.values():
        assert r["hrv_baseline"] == 68
        assert abs(r["hrv_sd"] - 6.324555) < 1e-4
    assert by_date["2026-06-08"]["hrv"] == 60
    assert by_date["2026-06-08"]["hrv_low_flag"] == 1
    assert by_date["2026-06-09"]["hrv_low_flag"] == 0
    assert by_date["2026-06-12"]["hrv_low_flag"] == 0


def test_hrv_window_respects_to_date(conn):
    """A narrowed recompute must not let future HRV nights affect the band."""
    for day, hrv in (("2026-06-08", 60), ("2026-06-09", 64), ("2026-06-10", 68)):
        db.upsert_daily(conn, "hrv_nightly", {"date": day, "avg_hrv": hrv})
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-06-11", "avg_hrv": 200})

    features.features(conn, data_start_date=DATA_START, to_date="2026-06-10")

    by_date = _by_date(conn)
    assert sorted(by_date) == ["2026-06-08", "2026-06-09", "2026-06-10"]
    for r in by_date.values():
        assert r["hrv_baseline"] == 64
        assert r["hrv_sd"] == 4.0


def _activity(conn, aid, date, aero, anaero, load):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 12:00:00",
            "date": date,
            "gtype": "running",
            "aero_te": aero,
            "anaero_te": anaero,
            "training_load": load,
        },
    )


def _sila(conn, aid, date, load, dur_s=4200, aero=1.4, anaero=0.3):
    """A strength session (discipline set, so the blend routes it to load_strength)."""
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "start_local": f"{date} 18:00:00",
            "date": date,
            "gtype": "strength_training",
            "discipline": "Siła",
            "aero_te": aero,
            "anaero_te": anaero,
            "training_load": load,
            "dur_s": dur_s,
        },
    )


def test_sila_default_rpe_contributes_blended_strength_load(conn):
    """An unrated Siła session is scored at the default RPE (~150), not Garmin's ~22,
    and lands in load_strength - out of the TE buckets."""
    _sila(conn, 1, "2026-06-08", load=22.0)  # 70 min, default RPE 7 -> 0.3*7*70 = 147

    features.features(conn, data_start_date=DATA_START, to_date="2026-06-08")

    r = _by_date(conn)["2026-06-08"]
    assert abs(r["load_strength"] - 147.0) < 1e-6
    assert abs(r["load_day"] - 147.0) < 1e-6
    assert r["load_low"] == 0 and r["load_high"] == 0 and r["load_anaerobic"] == 0


def test_logged_rpe_overrides_the_strength_default(conn):
    """A logged RPE replaces the Siła default in the blend."""
    _sila(conn, 1, "2026-06-08", load=22.0)
    db.upsert_session_rpe(conn, {"activity_id": 1, "rpe": 9, "source": "manual"})

    features.features(conn, data_start_date=DATA_START, to_date="2026-06-08")

    r = _by_date(conn)["2026-06-08"]
    assert abs(r["load_strength"] - 189.0) < 1e-6  # 0.3 * 9 * 70


def test_strength_day_lifts_load_day_and_the_four_buckets_sum(conn):
    """load_day = load_low + load_high + load_anaerobic + load_strength, and a
    strength day leaves the cardio buckets untouched."""
    _activity(conn, 1, "2026-06-08", aero=3.0, anaero=0.0, load=120)  # running -> high
    _sila(conn, 2, "2026-06-08", load=22.0)                            # strength -> 147

    features.features(conn, data_start_date=DATA_START, to_date="2026-06-08")

    r = _by_date(conn)["2026-06-08"]
    assert r["load_high"] == 120
    assert abs(r["load_strength"] - 147.0) < 1e-6
    assert abs(
        r["load_low"] + r["load_high"] + r["load_anaerobic"] + r["load_strength"]
        - r["load_day"]
    ) < 1e-6


def test_load_buckets_by_te_are_total_over_nulls(conn):
    """anaero_te>=1 -> anaerobic; else aero_te<2.5 -> low, else high. NULL TE
    counts as 0 (falls into low); NULL load contributes 0. Buckets sum to load_day."""
    d = "2026-06-08"
    _activity(conn, 1, d, aero=1.0, anaero=1.5, load=100)   # anaerobic
    _activity(conn, 2, d, aero=2.0, anaero=0.0, load=50)    # low (aero < 2.5)
    _activity(conn, 3, d, aero=3.0, anaero=0.5, load=80)    # high
    _activity(conn, 4, d, aero=None, anaero=None, load=30)  # low (null TE -> 0)
    _activity(conn, 5, d, aero=4.0, anaero=None, load=None)  # high bucket, 0 load

    features.features(conn, data_start_date=DATA_START)

    r = _by_date(conn)[d]
    assert r["load_anaerobic"] == 100
    assert r["load_low"] == 80        # 50 + 30
    assert r["load_high"] == 80
    assert r["load_day"] == 260
    assert r["load_low"] + r["load_high"] + r["load_anaerobic"] == r["load_day"]


def test_activity_metrics_bucket_by_start_local_date(conn):
    """Activity load and HR zones use date(start_local), not a stale date column."""
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "start_local": "2026-06-08 23:30:00",
            "date": "2026-06-09",
            "gtype": "running",
            "aero_te": 3.0,
            "anaero_te": 0.0,
            "training_load": 90,
            "hr_z1_s": 60,
            "hr_z2_s": 120,
        },
    )

    features.features(conn, data_start_date=DATA_START, to_date="2026-06-09")

    by_date = _by_date(conn)
    assert by_date["2026-06-08"]["load_day"] == 90
    assert by_date["2026-06-08"]["load_high"] == 90
    assert by_date["2026-06-08"]["z1_min"] == 1.0
    assert by_date["2026-06-08"]["z2_min"] == 2.0
    assert by_date["2026-06-09"]["load_day"] == 0
    assert by_date["2026-06-09"]["z1_min"] == 0


def test_acwr_uses_fixed_denominators_and_reports_n_chronic(conn):
    """acute7 = trailing-7 sum / 7, chronic28 = trailing-28 sum / 28 (zero-fill).
    n_chronic counts in-window days on/after data_start, flagging incompleteness."""
    # single 140-load day on 2026-06-14 (7th day of data). Window 06-08..06-14.
    _activity(conn, 1, "2026-06-14", aero=3.0, anaero=0.5, load=140)

    features.features(conn, data_start_date=DATA_START)

    r = _by_date(conn)["2026-06-14"]
    assert r["acute7"] == 140 / 7        # = 20.0
    assert r["chronic28"] == 140 / 28    # = 5.0 (divided by 28, not by days present)
    assert r["acwr"] == 4.0
    assert r["n_chronic"] == 7           # only 7 of 28 chronic days exist yet


def test_hr_zone_minutes_sum_seconds_over_60(conn):
    """z1..z5_min = sum of hr_zX_s across the day's activities / 60."""
    d = "2026-06-08"
    for aid, z in ((1, (60, 120, 180, 0, 0)), (2, (30, 0, 0, 240, 300))):
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "start_local": f"{d} 12:00:00",
                "date": d,
                "gtype": "running",
                "hr_z1_s": z[0], "hr_z2_s": z[1], "hr_z3_s": z[2],
                "hr_z4_s": z[3], "hr_z5_s": z[4],
            },
        )

    features.features(conn, data_start_date=DATA_START)

    r = _by_date(conn)[d]
    assert r["z1_min"] == 90 / 60    # (60 + 30) / 60 = 1.5
    assert r["z2_min"] == 2.0
    assert r["z3_min"] == 3.0
    assert r["z4_min"] == 4.0
    assert r["z5_min"] == 5.0


def test_rhr_falls_back_to_sleep_and_sleep_score_is_carried(conn):
    """rhr = daily_wellness.rhr, falling back to sleep.resting_hr when NULL;
    sleep_score is carried from sleep.score."""
    db.upsert_daily(conn, "daily_wellness", {"date": "2026-06-08", "rhr": 50})
    db.upsert_daily(conn, "sleep", {"date": "2026-06-08", "score": 80, "resting_hr": 48})
    db.upsert_daily(conn, "daily_wellness", {"date": "2026-06-09", "rhr": None})
    db.upsert_daily(conn, "sleep", {"date": "2026-06-09", "score": 70, "resting_hr": 52})

    features.features(conn, data_start_date=DATA_START)

    by_date = _by_date(conn)
    assert by_date["2026-06-08"]["rhr"] == 50          # wellness wins
    assert by_date["2026-06-08"]["sleep_score"] == 80
    assert by_date["2026-06-09"]["rhr"] == 52          # fallback to sleep.resting_hr
    assert by_date["2026-06-09"]["sleep_score"] == 70


def test_recompute_is_idempotent(conn):
    """Running features twice yields identical daily_metrics (upsert by date)."""
    _activity(conn, 1, "2026-06-10", aero=3.0, anaero=1.2, load=150)
    db.upsert_daily(conn, "hrv_nightly", {"date": "2026-06-10", "avg_hrv": 60})

    features.features(conn, data_start_date=DATA_START)
    first = _rows(conn)
    features.features(conn, data_start_date=DATA_START)
    second = _rows(conn)

    assert first == second
    assert len(first) == 3  # 06-08..06-10, no duplicate rows


def test_golden_regression_reproduces_reference_analysis(conn):
    """Seed the frozen real core data (2026-06-08..07-03) and reproduce the
    reference hand-analysis. Expected values are the algorithm's deterministic
    output; see docs/prd/phase-2.md 'Reference vs. computed flag set'."""
    conn.executescript((FIXTURES / "features_golden.sql").read_text())

    features.features(conn, data_start_date=DATA_START)

    by_date = _by_date(conn)

    # HRV band (21 non-null nights): median 68, sample SD ~11, threshold ~57.
    any_row = next(iter(by_date.values()))
    assert any_row["hrv_baseline"] == 68
    assert abs(any_row["hrv_sd"] - 11.0) < 0.1
    assert abs((any_row["hrv_baseline"] - any_row["hrv_sd"]) - 57.0) < 0.1

    flagged = sorted(d for d, r in by_date.items() if r["hrv_low_flag"] == 1)
    assert flagged == ["2026-06-11", "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-27"]

    # ACWR on 2026-07-03 (own ~1.0-1.1; Garmin ref 1.1), chronic window still short.
    end = by_date["2026-07-03"]
    assert abs(end["acwr"] - 1.0) < 0.15
    assert end["n_chronic"] == 26

    # Invariant: load buckets always sum to the day's total load.
    for r in by_date.values():
        assert (
            abs(
                r["load_low"] + r["load_high"] + r["load_anaerobic"]
                + (r["load_strength"] or 0) - r["load_day"]
            )
            < 1e-6
        )

    # Spot-check zone minutes against a known fixture activity (2026-07-02).
    assert abs(by_date["2026-07-02"]["z3_min"] - 2930.578 / 60) < 1e-6


def test_features_recomputes_personal_zones(conn):
    """features writes the singleton athlete_zones row from the LTHR anchor."""
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-06-20", "lactate_thr_hr": 175, "lactate_thr_pace": 257.1})
    db.upsert_activity(conn, {
        "activity_id": 1, "start_local": "2026-06-22 08:00:00", "date": "2026-06-22",
        "gtype": "running", "discipline": "Bieganie", "avg_hr": 150,
        "avg_speed_mps": 3.0, "temp_c": 15.0,
    })

    features.features(conn, data_start_date=DATA_START)

    rows = conn.execute(
        "SELECT lthr_bpm, z2_hi_bpm, source FROM athlete_zones"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 175
    assert rows[0][1] == 156


def test_features_writes_one_athlete_status_row(conn):
    """features composes the singleton athlete_status snapshot after weekly + zones."""
    db.upsert_daily(conn, "fitness_markers",
                    {"date": "2026-06-20", "lactate_thr_hr": 175,
                     "lactate_thr_pace": 257.1})
    db.upsert_daily(conn, "training_status_daily",
                    {"date": "2026-06-22", "vo2max": 52.0})
    db.upsert_activity(conn, {
        "activity_id": 1, "start_local": "2026-06-22 08:00:00", "date": "2026-06-22",
        "gtype": "running", "discipline": "Bieganie", "avg_hr": 150,
        "avg_speed_mps": 3.0, "temp_c": 15.0, "training_load": 80.0, "aero_te": 3.0,
    })

    features.features(conn, data_start_date=DATA_START)
    features.features(conn, data_start_date=DATA_START)  # idempotent

    rows = conn.execute(
        "SELECT computed_at, vo2max, z2_hi_bpm FROM athlete_status"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 52.0  # from training_status_daily.vo2max
    assert rows[0][2] == 156  # mirrored from athlete_zones (0.89 * 175)
