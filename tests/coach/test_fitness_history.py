"""Offline before/after evidence keeps unknown measurement context explicit."""

from garmin_coach.coach import fitness_history
from garmin_coach.core import db, laps

import datetime as dt
import json

from garmin_coach import cli
from garmin_coach.mcp import server
from garmin_coach.runtime import InstallationContext
import pytest


@pytest.mark.parametrize(
    "arguments",
    [
        {"from_date": "2026-09-15", "to_date": "2026-09-14"},
        {"from_date": "2026-09-01", "to_date": "2026-09-15"},
        {"baseline_from": "2026-08-01"},
        {"baseline_from": "2026-09-01", "baseline_to": "2026-09-05"},
        {"baseline_from": "2026-09-10", "baseline_to": "2026-09-14"},
        {"reported_context": {}},
        {"reported_context": [None]},
        {"reported_context": [{"activity_id": True, "note": "strap"}]},
    ],
)
def test_invalid_periods_and_reported_context_refuse_cleanly(conn, arguments):
    kwargs = {
        "from_date": "2026-09-05",
        "to_date": "2026-09-14",
        "data_start_date": "2026-06-08",
        "today": dt.date(2026, 9, 14),
    }
    kwargs.update(arguments)
    with pytest.raises(ValueError):
        fitness_history.read(conn, **kwargs)


@pytest.mark.parametrize(
    "extra",
    [
        ["--baseline-from", "2026-08-01"],
        ["--baseline-from", "2026-09-10", "--baseline-to", "2026-09-14"],
        ["--reported-context-json", "{"],
        ["--reported-context-json", "{}"],
    ],
)
def test_cli_validation_prints_json_and_returns_two(athlete_home, capsys, extra):
    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
    result = cli.main(
        [
            "--home",
            str(athlete_home),
            "fitness-history",
            "--from",
            "2026-09-05",
            "--to",
            "2026-09-14",
            *extra,
        ]
    )
    assert result == 2
    assert json.loads(capsys.readouterr().out)["error"]
    with context.use():
        assert server.get_fitness_history("2026-09-15", "2026-09-14")["data"]["error"]


def test_a_baseline_wholly_after_selected_period_is_rejected(conn):
    with pytest.raises(ValueError, match="baseline must end before"):
        fitness_history.read(
            conn,
            from_date="2026-08-01",
            to_date="2026-08-02",
            baseline_from="2026-09-01",
            baseline_to="2026-09-02",
            data_start_date="2026-06-08",
            today=dt.date(2026, 9, 14),
        )


def test_explicit_clock_is_shared_with_nested_activity_evidence(conn):
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "date": "2026-09-14",
            "start_local": "2026-09-14 12:00:00",
            "gtype": "running",
        },
    )
    result = fitness_history.read(
        conn,
        from_date="2026-09-14",
        to_date="2026-09-14",
        data_start_date="2026-06-08",
        today=dt.date(2026, 9, 14),
    )
    period = result["periods"]["selected"]
    assert period["partial_today"] is True
    assert period["runs"][0]["lap_evidence"]["partial_today"] is True


def test_future_source_values_are_cleared_and_unknown_source_dates_stay_unknown(conn):
    db.upsert_daily(
        conn,
        "training_status_daily",
        {
            "date": "2026-09-10",
            "status_source_date": "2026-09-15",
            "status_phrase": "FUTURE",
            "fitness_trend": 7,
            "vo2max_estimate_date": "2026-09-15",
            "vo2max": 99,
        },
    )
    db.upsert_daily(
        conn,
        "training_status_daily",
        {"date": "2026-09-11", "status_phrase": "UNKNOWN_CODE", "vo2max": 50},
    )
    result = fitness_history.read(
        conn,
        from_date="2026-09-10",
        to_date="2026-09-14",
        data_start_date="2026-06-08",
        today=dt.date(2026, 9, 14),
    )
    future, unknown = result["periods"]["selected"]["status"]
    assert future["status_phrase"] is future["fitness_trend"] is future["vo2max"] is None
    assert unknown["status_source_date"] is unknown["vo2max_estimate_date"] is None
    assert unknown["status_phrase"] == "UNKNOWN_CODE"
    assert result["periods"]["selected"]["partial_today"] is True


def test_only_runs_are_included_with_lap_evidence_and_unknown_pace_for_absent_speed(conn, fixture):
    for aid, gtype, speed in [(1, "running", 0), (2, "trail_running", None), (3, "cycling", 5)]:
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "date": "2026-09-10",
                "start_local": f"2026-09-10 12:00:0{aid}",
                "gtype": gtype,
                "avg_speed_mps": speed,
            },
        )
    payload = fixture("activity_laps_structured_outdoor")
    laps.replace(conn, 1, payload["get_activity_splits"], payload["get_activity_typed_splits"])
    result = fitness_history.read(
        conn,
        from_date="2026-09-10",
        to_date="2026-09-14",
        data_start_date="2026-06-08",
        today=dt.date(2026, 9, 14),
    )
    runs = result["periods"]["selected"]["runs"]
    assert [r["activity_id"] for r in runs] == [1, 2]
    assert all(r["activity_average_pace_s_per_km"] is None for r in runs)
    assert runs[0]["lap_evidence"]["laps"]


def test_before_after_runs_are_not_claimed_matched_and_status_keeps_provenance(conn):
    for aid, day, pace in [(1, "2026-08-10", 3), (2, "2026-09-10", 4)]:
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "date": day,
                "start_local": day + " 12:00:00",
                "gtype": "running",
                "distance_m": 5000,
                "dur_s": 1500,
                "avg_speed_mps": pace,
                "avg_hr": 150,
                "max_hr": 180,
            },
        )
    db.upsert_daily(
        conn,
        "training_status_daily",
        {
            "date": "2026-09-10",
            "status_phrase": "UNKNOWN_CODE",
            "fitness_trend": 7,
            "status_source_date": "2026-09-09",
        },
    )
    result = fitness_history.read(
        conn,
        today=dt.date(2026, 9, 14),
        from_date="2026-09-01",
        to_date="2026-09-14",
        baseline_from="2026-08-01",
        baseline_to="2026-08-31",
        data_start_date="2026-06-08",
        reported_context=[{"activity_id": 2, "note": "Chest strap reported by athlete"}],
    )
    run = result["periods"]["selected"]["runs"][0]
    assert run["activity_average_pace_s_per_km"] == 250
    assert run["measurement_context"]["hr_sensor"] is None
    assert run["measurement_context"]["configured_max_hr"] is None
    assert run["athlete_reported_context"] == ["Chest strap reported by athlete"]
    assert result["matched_runs"] is None
    assert result["periods"]["selected"]["status"][0]["status_phrase"] == "UNKNOWN_CODE"
    assert result["periods"]["baseline"]["runs"][0]["activity_id"] == 1


def test_read_preserves_caller_transaction_and_rejects_pre_history_or_future(conn):

    conn.execute("BEGIN")
    fitness_history.read(
        conn,
        today=dt.date(2026, 9, 14),
        from_date="2026-08-01",
        to_date="2026-08-02",
        data_start_date="2026-06-08",
    )
    assert conn.in_transaction
    conn.rollback()
    with pytest.raises(ValueError):
        fitness_history.read(
            conn,
            today=dt.date(2026, 9, 14),
            from_date="2026-06-01",
            to_date="2026-08-02",
            data_start_date="2026-06-08",
        )
    with pytest.raises(ValueError):
        fitness_history.read(
            conn,
            today=dt.date(2026, 9, 14),
            from_date="2026-09-15",
            to_date="2026-09-16",
            data_start_date="2026-06-08",
        )


def test_cli_and_mcp_share_saved_period_evidence(athlete_home, capsys):

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
        db.upsert_daily(
            connection,
            "training_status_daily",
            {"date": "2026-08-01", "status_phrase": "MAINTAINING"},
        )
        db.upsert_activity(
            connection,
            {
                "activity_id": 1,
                "date": "2026-08-01",
                "start_local": "2026-08-01 12:00:00",
                "gtype": "running",
                "avg_speed_mps": 4,
            },
        )
        connection.commit()
    reported = [{"activity_id": 1, "note": "Chest strap reported by athlete"}]
    with context.use():
        mcp = server.get_fitness_history("2026-08-01", "2026-08-02", reported_context=reported)[
            "data"
        ]
    assert (
        cli.main(
            [
                "--home",
                str(athlete_home),
                "fitness-history",
                "--from",
                "2026-08-01",
                "--to",
                "2026-08-02",
                "--reported-context-json",
                json.dumps(reported),
            ]
        )
        == 0
    )
    command = json.loads(capsys.readouterr().out)
    assert command == mcp
    assert mcp["periods"]["selected"]["status"][0]["status_phrase"] == "MAINTAINING"

    assert mcp["periods"]["selected"]["runs"][0]["athlete_reported_context"] == [
        reported[0]["note"]
    ]
