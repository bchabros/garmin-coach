"""Phase 5 weekly rollup (mart) tests.

Seam: the DB boundary. Each test seeds ``daily_metrics`` (plus ``activities`` /
``plan_template`` where adherence is under test) into a temp SQLite DB, runs
``weekly.rollup(conn, ...)``, reads ``weekly_metrics`` back, and asserts on it -
never on internal helpers. Mirrors test_features.py.
"""

from __future__ import annotations

import datetime as _dt
import pathlib

from garmin_coach import db, features, weekly

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

DATA_START = "2026-06-08"  # a Monday


def _seed_day(conn, date: str, **cols) -> None:
    db.upsert_daily(conn, "daily_metrics", {"date": date, **cols})


def _seed_span(conn, start: str, end: str, **cols) -> None:
    """Seed every calendar day in [start, end] with the same columns."""
    d = _dt.date.fromisoformat(start)
    last = _dt.date.fromisoformat(end)
    while d <= last:
        _seed_day(conn, d.isoformat(), **cols)
        d += _dt.timedelta(days=1)


def _weeks(conn) -> list[dict]:
    cur = conn.execute("SELECT * FROM weekly_metrics ORDER BY week_start")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def test_rolls_up_only_complete_weeks(conn):
    """Only Monday-Sunday weeks fully at/before the cutoff are emitted; the
    in-progress trailing week is skipped."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=10)  # complete week 1
    _seed_span(conn, "2026-06-15", "2026-06-21", load_day=10)  # complete week 2
    _seed_span(conn, "2026-06-22", "2026-06-24", load_day=10)  # partial week 3

    weekly.rollup(conn, data_start_date=DATA_START)

    starts = [w["week_start"] for w in _weeks(conn)]
    assert starts == ["2026-06-08", "2026-06-15"]


def test_load_totals_and_shares(conn):
    """load_total sums daily load; the buckets sum too; shares are over total."""
    _seed_span(conn, "2026-06-08", "2026-06-14",
               load_day=0, load_low=0, load_high=0, load_anaerobic=0)
    _seed_day(conn, "2026-06-08", load_day=100, load_low=100)
    _seed_day(conn, "2026-06-10", load_day=50, load_high=50)
    _seed_day(conn, "2026-06-12", load_day=50, load_anaerobic=50)

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert w["load_total"] == 200
    assert w["load_low"] == 100
    assert w["load_high"] == 50
    assert w["load_anaerobic"] == 50
    assert w["low_share"] == 0.5
    assert w["high_share"] == 0.25
    assert w["anaero_share"] == 0.25


def test_strength_load_totals_and_four_shares_sum_to_one(conn):
    """load_strength sums the blended strength days; strength_share joins the three
    cardio shares to sum to 1.0; load_total (and thus monotony/strain) includes it."""
    _seed_span(conn, "2026-06-08", "2026-06-14",
               load_day=0, load_low=0, load_high=0, load_anaerobic=0, load_strength=0)
    _seed_day(conn, "2026-06-08", load_day=150, load_strength=150)
    _seed_day(conn, "2026-06-10", load_day=50, load_low=50)

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert w["load_total"] == 200
    assert w["load_strength"] == 150
    assert w["strength_share"] == 0.75
    assert w["low_share"] == 0.25
    assert (
        abs(w["low_share"] + w["high_share"] + w["anaero_share"] + w["strength_share"] - 1.0)
        < 1e-6
    )


def test_monotony_and_strain_finite_for_varied_week(conn):
    """Foster monotony = mean daily load / sample SD over the 7 calendar days;
    strain = load_total * monotony. Worked example: loads [100,0,100,0,100,0,0]
    -> mean 42.857, sample SD 53.452, monotony 0.8018, strain 240.5."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=0)
    for d in ("2026-06-08", "2026-06-10", "2026-06-12"):
        _seed_day(conn, d, load_day=100)

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert abs(w["monotony"] - 0.8018) < 0.001
    assert abs(w["strain"] - 240.5) < 0.5


def test_monotony_null_with_fewer_than_two_training_days(conn):
    """A week with a single training day has no meaningful monotony -> NULL, and
    strain is NULL too (never inf)."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=0)
    _seed_day(conn, "2026-06-08", load_day=100)

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert w["monotony"] is None
    assert w["strain"] is None


def test_max_consec_hard_is_longest_run_not_count(conn):
    """max_consec_hard = longest run of consecutive days at/above hard_te_load
    (seeded 150). A lone hard Monday plus a Fri->Sat stack yields 2, not 3."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=0)
    _seed_day(conn, "2026-06-08", load_day=160)  # lone hard day (run of 1)
    _seed_day(conn, "2026-06-12", load_day=200)  # Fri
    _seed_day(conn, "2026-06-13", load_day=200)  # Sat -> run of 2

    weekly.rollup(conn, data_start_date=DATA_START)

    assert _weeks(conn)[0]["max_consec_hard"] == 2


def test_plan_adherence_and_intent_counts(conn):
    """Actual intent is classified by load (quality >= hard OR anaerobic>0; easy
    for lighter activity; rest for none) and matched against the seeded
    plan_template. Adherence = matches / 7.

    Plan (seeded): Mon rest, Tue quality, Wed easy, Thu rest, Fri quality,
    Sat quality, Sun easy. Actual below matches all but Fri (planned quality,
    actually rested) -> 6/7."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=0, load_anaerobic=0)
    _seed_day(conn, "2026-06-09", load_day=40, load_anaerobic=40)  # Tue: quality (anaerobic)
    _seed_day(conn, "2026-06-10", load_day=50)                     # Wed: easy
    _seed_day(conn, "2026-06-13", load_day=200)                    # Sat: quality
    _seed_day(conn, "2026-06-14", load_day=60)                     # Sun: easy
    # Mon/Thu rest match; Fri stays rest -> mismatch vs planned quality.

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert abs(w["plan_adherence"] - 6 / 7) < 1e-9
    assert w["n_sessions"] == 4      # Tue, Wed, Sat, Sun
    assert w["n_quality"] == 2       # Tue, Sat
    assert w["n_rest_days"] == 3     # Mon, Thu, Fri
    assert conn.execute("SELECT COUNT(*) FROM weekly_plan_actual").fetchone()[0] == 7


def test_recovery_means_skip_nulls_and_zone_rollup(conn):
    """Recovery means average non-null days only; zone minutes sum (threshold =
    z3+z4); acwr_end is the week's Sunday ACWR."""
    _seed_span(conn, "2026-06-08", "2026-06-14", load_day=0)
    _seed_day(conn, "2026-06-08", hrv=70, rhr=50, sleep_score=80,
              z2_min=10, z3_min=5, z4_min=5, z5_min=3)
    _seed_day(conn, "2026-06-10", hrv=80, sleep_score=90, z2_min=20)
    _seed_day(conn, "2026-06-14", acwr=1.2)  # Sunday

    weekly.rollup(conn, data_start_date=DATA_START)

    w = _weeks(conn)[0]
    assert w["hrv_mean"] == 75          # (70 + 80) / 2, null days skipped
    assert w["rhr_mean"] == 50          # only one measured day
    assert w["sleep_score_mean"] == 85  # (80 + 90) / 2
    assert w["z2_min"] == 30
    assert w["threshold_min"] == 10     # z3 + z4 = 5 + 5
    assert w["z5_min"] == 3
    assert w["acwr_end"] == 1.2


def test_hrv_trend_is_delta_vs_prior_week(conn):
    """hrv_trend = this week's hrv_mean minus the prior week's; NULL for the
    first week (no predecessor)."""
    _seed_span(conn, "2026-06-08", "2026-06-21", load_day=0)
    _seed_day(conn, "2026-06-08", hrv=70)  # week 1 mean 70
    _seed_day(conn, "2026-06-15", hrv=75)  # week 2 mean 75

    weekly.rollup(conn, data_start_date=DATA_START)

    weeks = _weeks(conn)
    assert weeks[0]["hrv_trend"] is None
    assert weeks[1]["hrv_trend"] == 5


def test_features_command_also_populates_weekly_metrics(conn):
    """Running features (the one 'recompute the marts' command) rolls up complete
    weeks too - no separate weekly command."""
    for i in range(7):  # a full Mon-Sun week of core activity
        db.upsert_activity(conn, {
            "activity_id": i + 1,
            "start_local": f"2026-06-{8 + i:02d} 12:00:00",
            "date": f"2026-06-{8 + i:02d}",
            "gtype": "running",
            "training_load": 50,
            "aero_te": 3.0,
        })

    features.features(conn, data_start_date=DATA_START)

    assert [w["week_start"] for w in _weeks(conn)] == ["2026-06-08"]
    assert _weeks(conn)[0]["load_total"] == 350


def test_partial_features_recompute_expands_to_affected_week(conn):
    """A partial mart recompute refreshes the whole affected week before rollup."""
    for i in range(7):
        date = f"2026-06-{8 + i:02d}"
        db.upsert_activity(conn, {
            "activity_id": i + 1,
            "start_local": f"{date} 12:00:00",
            "date": date,
            "gtype": "running",
            "training_load": 10,
            "aero_te": 3.0,
        })
    _seed_day(conn, "2026-06-08", load_day=999, load_low=999)
    _seed_day(conn, "2026-06-09", load_day=999, load_low=999)

    features.features(
        conn,
        data_start_date=DATA_START,
        from_date="2026-06-10",
        to_date="2026-06-14",
    )

    assert _weeks(conn)[0]["load_total"] == 70
    daily_loads = conn.execute(
        "SELECT date, load_day FROM daily_metrics ORDER BY date"
    ).fetchall()
    assert daily_loads == [
        ("2026-06-08", 10),
        ("2026-06-09", 10),
        ("2026-06-10", 10),
        ("2026-06-11", 10),
        ("2026-06-12", 10),
        ("2026-06-13", 10),
        ("2026-06-14", 10),
    ]


def test_golden_regression_weekly_rollup(conn):
    """Seed the frozen real core data (2026-06-08..07-03), run features then the
    weekly rollup, and cross-check each week's load_total against an independent
    sum of the week's activity training load (core -> mart regression guard)."""
    conn.executescript((FIXTURES / "features_golden.sql").read_text())

    features.features(conn, data_start_date=DATA_START)
    weekly.rollup(conn, data_start_date=DATA_START)

    weeks = _weeks(conn)
    # Complete weeks only: the fixture ends 07-03, so the 06-29 week is incomplete.
    assert [w["week_start"] for w in weeks] == ["2026-06-08", "2026-06-15", "2026-06-22"]

    for w in weeks:
        monday = w["week_start"]
        sunday = (_dt.date.fromisoformat(monday) + _dt.timedelta(days=6)).isoformat()
        (independent_total,) = conn.execute(
            "SELECT COALESCE(SUM(training_load), 0) FROM activities "
            "WHERE date(start_local) BETWEEN ? AND ?",
            (monday, sunday),
        ).fetchone()
        assert abs(w["load_total"] - independent_total) < 1e-6
        assert abs(
            w["load_low"] + w["load_high"] + w["load_anaerobic"] - w["load_total"]
        ) < 1e-6
        assert 0.0 <= w["plan_adherence"] <= 1.0
        assert 0 <= w["max_consec_hard"] <= 7


# --- Phase 9: the week's block, copied from plan_block ---


def test_week_row_carries_its_block_and_countdown(conn):
    from garmin_coach import db, periodize

    db.upsert_goal_event(conn, {
        "date": "2026-10-17", "type": "hyrox", "priority": "A", "status": "confirmed",
        "date_precision": "approx", "target_s": 3600, "note": None,
    })
    _seed_span(conn, "2026-06-08", "2026-06-21", load_day=50)
    periodize.rollup(conn, data_start_date="2026-06-08", through_date="2026-06-21")

    weekly.rollup(conn, data_start_date="2026-06-08", through_date="2026-06-21")

    row = conn.execute(
        "SELECT block, weeks_to_event FROM weekly_metrics WHERE week_start='2026-06-08'"
    ).fetchone()
    assert row == ("base", 18)


def test_week_row_block_is_null_without_an_anchor(conn):
    _seed_span(conn, "2026-06-08", "2026-06-21", load_day=50)

    weekly.rollup(conn, data_start_date="2026-06-08", through_date="2026-06-21")

    row = conn.execute(
        "SELECT block, weeks_to_event FROM weekly_metrics WHERE week_start='2026-06-08'"
    ).fetchone()
    assert row == (None, None)
