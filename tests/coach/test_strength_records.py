"""Comparable observed strength records retract corrected leaders."""

from garmin_coach import set_history, strength_changes
from garmin_coach.coach import strength_records
from garmin_coach.core import db


def captured(conn, aid, day, weight, reps):
    db.upsert_activity(
        conn,
        {
            "activity_id": aid,
            "date": day,
            "start_local": day + " 12:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    set_history.persist(
        conn,
        aid,
        {
            "activityId": aid,
            "exerciseSets": [
                {
                    "setType": "ACTIVE",
                    "repetitionCount": reps,
                    "weight": weight * 1000,
                    "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}],
                }
            ],
        },
        checked_on=day,
    )
    conn.commit()


def declare_all(conn):
    evidence = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    for row in evidence["unranked"]:
        strength_changes.declare(
            conn,
            row["activity_id"],
            row["set_idx"],
            row["binding"],
            equipment="barbell",
            weight_basis="total",
            resistance_mode="external_load",
        )


def test_known_records_and_per_rep_bests_retract_after_correction(conn):
    captured(conn, 1, "2026-08-01", 60, 10)
    captured(conn, 2, "2026-08-02", 70, 5)
    captured(conn, 3, "2026-08-03", 65, 10)
    declare_all(conn)
    records = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")[
        "records"
    ]
    assert records[0]["heaviest"]["weight_kg"] == 70
    assert records[0]["by_repetitions"]["10"]["weight_kg"] == 65
    captured(conn, 2, "2026-08-02", 55, 5)
    result = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    assert result["records"][0]["heaviest"]["weight_kg"] == 65
    assert result["unranked"][0]["reason"] == "comparison_conditions_stale"


def test_ties_and_load_basis_stay_separate_and_manual_circuit_hides_captured(conn):
    from garmin_coach import athlete_changes

    for aid, day in [(1, "2026-08-01"), (2, "2026-08-02"), (3, "2026-08-03")]:
        captured(conn, aid, day, 70, 5)
    declare_all(conn)
    state = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    assert len(state["records"][0]["heaviest"]["ties"]) == 3
    row = state["records"][0]["history"][-1]
    strength_changes.declare(
        conn,
        3,
        0,
        row["binding"],
        equipment="dumbbell",
        weight_basis="per_hand",
        resistance_mode="external_load",
    )
    state = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    assert len(state["records"]) == 2
    athlete_changes.log_activity_sets(
        conn,
        activity_id=1,
        stations=[{"subcategory": "BENCH_PRESS", "reps": 5, "max_weight": 999000, "sets": 3}],
        data_start_date="2026-06-08",
    )
    state = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    assert state["unranked"][0]["source"] == "manual"
    assert state["unranked"][0]["reason"] == "individual_working_set_identity_unverified"
    assert all(
        tie["activity_id"] != 1 for record in state["records"] for tie in record["heaviest"]["ties"]
    )


def test_variants_and_historical_horizon_are_independent(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    captured(conn, 2, "2026-08-02", 80, 5)
    payload = {
        "activityId": 2,
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "repetitionCount": 5,
                "weight": 80000,
                "exercises": [{"category": "BENCH_PRESS", "name": "DUMBBELL_BENCH_PRESS"}],
            }
        ],
    }
    set_history.persist(conn, 2, payload, checked_on="2026-08-02")
    conn.commit()
    declare_all(conn)
    state = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")
    assert len(state["records"]) == 2
    early = strength_records.read(conn, through_date="2026-08-01", data_start_date="2026-06-08")
    assert early["coverage"]["ranked_sets"] == 1
    after_onboarding = strength_records.read(
        conn, through_date="2026-09-01", data_start_date="2026-08-02"
    )
    assert after_onboarding["coverage"]["ranked_sets"] == 1


def test_changed_source_refuses_stale_condition_declaration(conn):
    import pytest

    captured(conn, 1, "2026-08-01", 60, 5)
    row = strength_records.read(conn, through_date="2026-09-01", data_start_date="2026-06-08")[
        "unranked"
    ][0]
    captured(conn, 1, "2026-08-01", 65, 5)
    with pytest.raises(ValueError, match="stale"):
        strength_changes.declare(
            conn,
            1,
            0,
            row["binding"],
            equipment="barbell",
            weight_basis="total",
            resistance_mode="external_load",
        )


def test_cli_and_mcp_records_are_offline_and_equal(athlete_home, capsys):
    import json
    from garmin_coach import cli
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
        captured(connection, 1, "2026-08-01", 60, 10)
        declare_all(connection)
    with context.use():
        expected = server.get_strength_records("2026-09-01")["data"]
    assert (
        cli.main(["--home", str(athlete_home), "strength-records", "--through", "2026-09-01"]) == 0
    )
    assert json.loads(capsys.readouterr().out) == expected
    assert expected["records"][0]["heaviest"]["weight_kg"] == 60
