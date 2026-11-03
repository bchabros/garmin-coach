"""Dated training-week evidence through snapshot materialization."""

from garmin_coach.core import db, models
from garmin_coach.marts import snapshot, weekly_fitness


def test_estimate_date_is_distinct_from_daily_observation(fixture):
    normalized = models.normalize_status("2026-06-10", fixture("status_day"))
    assert normalized["vo2max_estimate_date"] == "2026-06-08"


def test_recent_decline_is_distinct_from_positive_quarter_and_survives_persistence(conn):
    for day, value in [
        ("2026-06-17", 45),
        ("2026-08-16", 52),
        ("2026-08-23", 51),
        ("2026-08-30", 50),
        ("2026-09-06", 49),
        ("2026-09-13", 48),
    ]:
        db.upsert_daily(
            conn,
            "training_status_daily",
            {"date": day, "vo2max": value, "vo2max_estimate_date": day},
        )
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    assert [w["value"] for w in result["completed_weeks"]] == [51, 50, 49, 48]
    assert result["comparisons"]["four_weeks"]["delta"] == -4
    assert result["current_week"]["partial"] is True
    snapshot.rollup(conn, through_date="2026-09-14", data_start_date="2026-06-08")
    assert snapshot.read(conn)["weekly_fitness"] == result
    assert snapshot.read(conn)["vo2max_delta"] > 0


def test_missing_week_does_not_become_one_week_delta_and_future_is_excluded(conn):
    for day, value in [("2026-12-20", 50), ("2027-01-03", 51), ("2027-01-10", 99)]:
        db.upsert_daily(conn, "training_status_daily", {"date": day, "vo2max": value})
    result = weekly_fitness.build(conn, "2027-01-04", data_start_date="2026-12-16")
    weeks = result["completed_weeks"]
    assert weeks[-2]["carried"] is True
    assert weeks[-1]["change_from_previous_week"]["delta"] is None
    assert (
        "missing_week_observation" in weeks[-1]["change_from_previous_week"]["unavailable_reasons"]
    )
    assert result["current_week"]["value"] == 51


def test_equal_numeric_values_with_distinct_source_dates_do_not_invent_event_counts(conn):
    for day in ["2026-08-30", "2026-09-06", "2026-09-13"]:
        db.upsert_daily(
            conn, "training_status_daily", {"date": day, "vo2max": 50, "vo2max_estimate_date": day}
        )
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    assert result["comparisons"]["two_weeks"]["delta"] == 0
    assert all(w["estimate_event_count"] is None for w in result["completed_weeks"])


def test_existing_database_bootstrap_adds_metadata_without_inventing_history(conn):
    conn.execute("ALTER TABLE training_status_daily DROP COLUMN vo2max_estimate_date")
    conn.execute("ALTER TABLE training_status_daily DROP COLUMN status_source_date")
    conn.execute("ALTER TABLE athlete_status DROP COLUMN weekly_fitness_json")
    db.upsert_daily(conn, "training_status_daily", {"date": "2026-09-13", "vo2max": 50})
    db.bootstrap(conn)
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    assert result["completed_weeks"][-1]["estimate_date"] is None


def test_future_status_source_is_excluded_from_historical_horizon(conn):
    db.upsert_daily(
        conn,
        "training_status_daily",
        {"date": "2026-09-13", "status_source_date": "2026-09-20", "status_phrase": "FUTURE"},
    )
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    assert result["garmin_status"] is None
