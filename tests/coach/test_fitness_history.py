"""Offline before/after evidence keeps unknown measurement context explicit."""

from garmin_coach.coach import fitness_history
from garmin_coach.core import db


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
    import pytest

    conn.execute("BEGIN")
    fitness_history.read(
        conn, from_date="2026-08-01", to_date="2026-08-02", data_start_date="2026-06-08"
    )
    assert conn.in_transaction
    conn.rollback()
    with pytest.raises(ValueError):
        fitness_history.read(
            conn, from_date="2026-06-01", to_date="2026-08-02", data_start_date="2026-06-08"
        )
    with pytest.raises(ValueError):
        fitness_history.read(
            conn, from_date="2099-08-01", to_date="2099-08-02", data_start_date="2026-06-08"
        )


def test_cli_and_mcp_share_saved_period_evidence(athlete_home, capsys):
    import json
    from garmin_coach import cli
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
        db.upsert_daily(
            connection,
            "training_status_daily",
            {"date": "2026-08-01", "status_phrase": "MAINTAINING"},
        )
    with context.use():
        mcp = server.get_fitness_history("2026-08-01", "2026-08-02")["data"]
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
            ]
        )
        == 0
    )
    command = json.loads(capsys.readouterr().out)
    assert command == mcp
    assert mcp["periods"]["selected"]["status"][0]["status_phrase"] == "MAINTAINING"
