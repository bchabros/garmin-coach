"""Dated training-week evidence through snapshot materialization."""

from garmin_coach.core import db, models
from garmin_coach.marts import snapshot, weekly_fitness

import pytest
import datetime as dt
import json
from garmin_coach import cli
from garmin_coach.coach import report
from garmin_coach.mcp import server
from garmin_coach.runtime import InstallationContext


def test_daily_copies_of_one_source_estimate_do_not_establish_no_change(conn):
    for day in ["2026-09-06", "2026-09-13"]:
        db.upsert_daily(
            conn,
            "training_status_daily",
            {"date": day, "vo2max": 50, "vo2max_estimate_date": "2026-09-06"},
        )
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    change = result["completed_weeks"][-1]["change_from_previous_week"]
    assert change["delta"] is None
    assert "same_source_date_not_independent_estimates" in change["unavailable_reasons"]


def test_weekly_change_requires_enough_actual_estimate_span(conn):
    for observed, estimated, value in [
        ("2026-09-06", "2026-09-06", 50),
        ("2026-09-13", "2026-09-07", 51),
    ]:
        db.upsert_daily(
            conn,
            "training_status_daily",
            {"date": observed, "vo2max_estimate_date": estimated, "vo2max": value},
        )
    change = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")[
        "completed_weeks"
    ][-1]["change_from_previous_week"]
    assert change["delta"] is None
    assert change["estimate_span_days"] == 1
    assert change["required_span_days"] == 7
    assert "insufficient_span" in change["unavailable_reasons"]


def test_carried_legacy_value_has_observation_age_and_stale_evidence_is_unavailable(conn):
    db.upsert_daily(conn, "training_status_daily", {"date": "2026-09-01", "vo2max": 50})
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")
    point = result["completed_weeks"][-1]
    assert point["carried"] is True
    assert point["estimate_age_days"] is None
    assert point["observation_age_days"] == point["age_days"] == 12
    assert point["age_source"] == "observation_date"
    assert point["stale"] is True
    assert "stale_endpoint" in point["change_from_previous_week"]["unavailable_reasons"]


def test_latest_populated_garmin_feedback_is_not_hidden_by_an_empty_newer_row(conn):
    db.upsert_daily(
        conn,
        "training_status_daily",
        {
            "date": "2026-09-12",
            "status_source_date": "2026-09-11",
            "status_phrase": "MAINTAINING",
            "fitness_trend": 7,
        },
    )
    db.upsert_daily(conn, "training_status_daily", {"date": "2026-09-13", "vo2max": 50})
    status = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-06-08")["garmin_status"]
    assert status == {
        "observation_date": "2026-09-12",
        "source_date": "2026-09-11",
        "phrase": "MAINTAINING",
        "fitness_trend": 7,
    }


def test_fresh_and_upgraded_source_columns_have_the_same_order(conn):
    tables = {
        "training_status_daily": ["vo2max_estimate_date", "status_source_date"],
        "athlete_status": ["weekly_fitness_json"],
    }
    expected = {
        table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] for table in tables
    }
    for table, columns in tables.items():
        for column in columns:
            conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    db.bootstrap(conn)
    assert {
        table: [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] for table in tables
    } == expected


@pytest.mark.parametrize(
    "value,expected",
    [("2026-06-09", "2026-06-09"), (None, None), ("bad", None), ({"date": "2026-06-09"}, None)],
)
def test_status_normalization_retains_only_valid_source_calendar_dates(fixture, value, expected):
    payload = fixture("status_day")
    status = next(iter(payload["mostRecentTrainingStatus"]["latestTrainingStatusData"].values()))
    status["calendarDate"] = value
    payload["mostRecentVO2Max"]["generic"]["calendarDate"] = value
    result = models.normalize_status("2026-06-10", payload)
    assert result["status_source_date"] == result["vo2max_estimate_date"] == expected
    empty = models.normalize_status("2026-06-10", {})
    assert empty["status_source_date"] is empty["vo2max_estimate_date"] is None


def test_no_readings_single_estimate_and_sunday_calendar_completion(conn):
    empty = weekly_fitness.build(conn, "2026-09-13", data_start_date="2026-06-08")
    assert empty["current_week"] is None
    assert all(w["partial"] is False for w in empty["completed_weeks"])
    assert all(w["unavailable_reason"] == "no_saved_estimate" for w in empty["completed_weeks"])
    assert empty["comparisons"]["four_weeks"]["delta"] is None
    db.upsert_daily(
        conn,
        "training_status_daily",
        {"date": "2026-09-13", "vo2max": 50, "vo2max_estimate_date": "2026-09-13"},
    )
    single = weekly_fitness.build(conn, "2026-09-13", data_start_date="2026-06-08")
    assert single["completed_weeks"][-1]["value"] == 50
    assert single["completed_weeks"][-1]["change_from_previous_week"]["delta"] is None


def test_before_onboarding_and_partial_onboarding_week_have_distinct_reasons(conn):
    db.upsert_daily(
        conn,
        "training_status_daily",
        {"date": "2026-09-09", "vo2max": 50, "vo2max_estimate_date": "2026-09-09"},
    )
    result = weekly_fitness.build(conn, "2026-09-14", data_start_date="2026-09-09")
    assert result["completed_weeks"][-2]["unavailable_reason"] == "before_data_start"
    assert (
        "onboarding_partial_week"
        in result["completed_weeks"][-1]["change_from_previous_week"]["unavailable_reasons"]
    )


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


def test_weekly_evidence_is_identical_in_mcp_cli_and_saved_report_and_hrv_is_unchanged(
    athlete_home, tmp_path
):
    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
        for day, value in [("2026-06-28", 50), ("2026-07-05", 51)]:
            db.upsert_daily(
                connection,
                "training_status_daily",
                {"date": day, "vo2max": value, "vo2max_estimate_date": day},
            )
        for day, value in [("2026-06-25", 64), ("2026-07-08", 69)]:
            db.upsert_daily(connection, "hrv_nightly", {"date": day, "weekly_avg": value})
        db.upsert_daily(
            connection,
            "daily_metrics",
            {
                "date": "2026-07-08",
                "load_day": 100,
                "load_low": 60,
                "load_high": 30,
                "load_anaerobic": 10,
                "hrv": 70,
                "hrv_baseline": 68,
                "hrv_sd": 11,
                "acwr": 1.1,
                "n_chronic": 30,
            },
        )
        snapshot.rollup(connection, through_date="2026-07-08", data_start_date="2026-06-08")
        connection.commit()
        expected = snapshot.read(connection)
        assert expected["hrv_delta"] == 5
        with context.use():
            generated = report.generate_report(
                connection, to_date="2026-07-08", reports_dir=str(tmp_path)
            )
    with context.use():
        assert server.get_snapshot()["data"]["weekly_fitness"] == expected["weekly_fitness"]
    assert cli.main(["--home", str(athlete_home), "snapshot"]) == 0
    command = json.loads(
        (context.reports_dir / dt.date.today().isoformat() / "snapshot.json").read_text()
    )
    saved = json.loads((generated / "snapshot.json").read_text())
    assert command["weekly_fitness"] == saved["weekly_fitness"] == expected["weekly_fitness"]
    assert command["hrv_delta"] == saved["hrv_delta"] == 5
