"""Comparable observed strength records retract corrected leaders."""

from garmin_coach import set_history, strength_changes
from garmin_coach.coach import strength_records
from garmin_coach.core import db

import datetime as dt
import pytest


def test_undeclared_resistance_mode_remains_unknown(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert state["unranked"][0]["resistance_mode"] is None


def test_declared_assistance_survives_reopen_and_does_not_rank(tmp_path):
    path = str(tmp_path / "records.sqlite")
    with db.connect(path) as connection:
        db.bootstrap(connection)
        captured(connection, 1, "2026-08-01", 60, 5)
        row = strength_records.read(
            connection,
            today=dt.date(2026, 9, 1),
            through_date="2026-09-01",
            data_start_date="2026-06-08",
        )["unranked"][0]
        strength_changes.declare(
            connection,
            1,
            0,
            row["binding"],
            equipment="machine",
            weight_basis="total",
            resistance_mode="assisted_load",
        )
    with db.connect(path) as connection:
        result = strength_records.read(
            connection,
            today=dt.date(2026, 9, 1),
            through_date="2026-09-01",
            data_start_date="2026-06-08",
        )
        assert result["records"] == []
        assert result["unranked"][0]["resistance_mode"] == "assisted_load"
        assert result["unranked"][0]["reason"] == "assisted_load_unsupported"


def test_legacy_condition_migration_does_not_invent_a_resistance_mode(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    declare_all(conn)
    conn.execute("ALTER TABLE strength_set_conditions DROP COLUMN resistance_mode")
    conn.commit()
    db.bootstrap(conn)
    db.validate_schema(conn)
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert state["records"] == []
    assert state["unranked"][0]["resistance_mode"] is None
    assert state["unranked"][0]["reason"] == "comparison_conditions_unknown"


def test_unranked_reasons_distinguish_missing_source_and_declared_bodyweight(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    conn.execute("DELETE FROM exercise_set_observations WHERE activity_id=1")
    conn.commit()
    captured(conn, 2, "2026-08-02", 0, 5)
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    missing, bodyweight = state["unranked"]
    assert missing["reason"] == "source_observation_missing"
    strength_changes.declare(
        conn,
        2,
        0,
        bodyweight["binding"],
        equipment="pullup_bar",
        weight_basis="total",
        resistance_mode="bodyweight_only",
    )
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert state["unranked"][1]["reason"] == "bodyweight_only_unsupported"
    assert state["unranked"][1]["resistance_mode"] == "bodyweight_only"


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
    evidence = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
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
    records = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )["records"]
    assert records[0]["heaviest"]["weight_kg"] == 70
    assert records[0]["by_repetitions"]["10"]["weight_kg"] == 65
    assert [r["weight_kg"] for r in records[0]["by_repetitions"]["10"]["record_progression"]] == [
        60,
        65,
    ]
    captured(conn, 2, "2026-08-02", 55, 5)
    result = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert result["records"][0]["heaviest"]["weight_kg"] == 65
    assert result["unranked"][0]["reason"] == "comparison_conditions_stale"


def test_equivalent_gram_measurements_tie_without_float_kg_comparison(conn):
    captured(conn, 1, "2026-08-01", 32.20000000000001, 5)
    captured(conn, 2, "2026-08-02", 32.2, 5)
    payload = {
        "activityId": 2,
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "repetitionCount": 5,
                "weight": 32200,
                "exercises": [{"category": "BENCH_PRESS", "name": "BARBELL_BENCH_PRESS"}],
            }
        ],
    }
    set_history.persist(conn, 2, payload, checked_on="2026-08-02")
    conn.commit()
    declare_all(conn)
    record = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )["records"][0]
    assert record["heaviest"]["weight_kg"] == 32.2
    assert [r["activity_id"] for r in record["heaviest"]["ties"]] == [1, 2]
    assert len(record["record_progression"]) == 1


def test_coverage_names_unobserved_activities_and_distinguishes_observed_empty(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    for aid, day in [(2, "2026-08-02"), (3, "2026-08-03"), (4, "2026-10-01"), (5, "2026-06-01")]:
        db.upsert_activity(
            conn,
            {"activity_id": aid, "date": day, "start_local": day + " 12:00:00", "gtype": "hiit"},
        )
    set_history.persist(conn, 3, {"activityId": 3, "exerciseSets": []}, checked_on="2026-08-03")
    conn.commit()
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    coverage = state["coverage"]
    assert coverage["eligible_activities"] == 3
    assert coverage["source_missing_activity_ids"] == [2]
    assert coverage["observed_empty_activity_ids"] == [3]
    assert [a["activity_id"] for a in coverage["activities"]] == [1, 2, 3]
    assert coverage["history_rechecks"]["never_checked"] == 1


def test_record_horizon_uses_an_explicit_clock(conn):
    with pytest.raises(ValueError, match="horizon"):
        strength_records.read(
            conn, through_date="2026-09-02", data_start_date="2026-06-08", today=dt.date(2026, 9, 1)
        )
    with pytest.raises(ValueError, match="horizon"):
        strength_records.read(
            conn, through_date="2026-06-07", data_start_date="2026-06-08", today=dt.date(2026, 9, 1)
        )


def test_observed_source_with_missing_normalized_sets_is_not_reported_as_empty(conn):
    captured(conn, 1, "2026-08-01", 60, 5)
    conn.execute("DELETE FROM activity_sets WHERE activity_id=1")
    conn.commit()
    coverage = strength_records.read(
        conn, through_date="2026-09-01", data_start_date="2026-06-08", today=dt.date(2026, 9, 1)
    )["coverage"]
    assert coverage["observed_empty_activity_ids"] == []
    assert coverage["source_rows_missing_activity_ids"] == [1]


def test_coverage_detects_partially_missing_captured_sets(conn):
    import json

    captured(conn, 1, "2026-08-01", 60, 5)
    payload = json.loads(
        conn.execute(
            "SELECT payload_json FROM exercise_set_observations WHERE activity_id=1"
        ).fetchone()[0]
    )
    payload["exerciseSets"].append(dict(payload["exerciseSets"][0]))
    set_history.persist(conn, 1, payload, checked_on="2026-08-01")
    conn.execute("DELETE FROM activity_sets WHERE activity_id=1 AND set_idx=1")
    conn.commit()
    coverage = strength_records.read(
        conn, through_date="2026-09-01", data_start_date="2026-06-08", today=dt.date(2026, 9, 1)
    )["coverage"]
    assert coverage["selected_sets"] == 1
    assert coverage["source_rows_missing_activity_ids"] == [1]


def test_ties_and_load_basis_stay_separate_and_manual_circuit_hides_captured(conn):
    from garmin_coach import athlete_changes

    for aid, day in [(1, "2026-08-01"), (2, "2026-08-02"), (3, "2026-08-03")]:
        captured(conn, aid, day, 70, 5)
    declare_all(conn)
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
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
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert len(state["records"]) == 2
    athlete_changes.log_activity_sets(
        conn,
        activity_id=1,
        stations=[{"subcategory": "BENCH_PRESS", "reps": 5, "max_weight": 999000, "sets": 3}],
        data_start_date="2026-06-08",
    )
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert state["unranked"][0]["source"] == "manual"
    assert state["unranked"][0]["reason"] == "legacy_circuit_aggregate"
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
    state = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )
    assert len(state["records"]) == 2
    early = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-08-01", data_start_date="2026-06-08"
    )
    assert early["coverage"]["ranked_sets"] == 1
    after_onboarding = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-08-02"
    )
    assert after_onboarding["coverage"]["ranked_sets"] == 1


def test_changed_source_refuses_stale_condition_declaration(conn):
    import pytest

    captured(conn, 1, "2026-08-01", 60, 5)
    row = strength_records.read(
        conn, today=dt.date(2026, 9, 1), through_date="2026-09-01", data_start_date="2026-06-08"
    )["unranked"][0]
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


def test_cli_and_mcp_declare_the_same_durable_conditions_and_refuse_stale_input(
    athlete_home, capsys
):
    import json
    from garmin_coach import cli
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    def forbidden(_):
        pytest.fail("strength operations must not connect Garmin")

    context = InstallationContext.select(athlete_home, import_factory=forbidden)
    with context.open_database(create=True) as connection:
        db.bootstrap(connection)
        captured(connection, 1, "2026-08-01", 60, 5)
        row = strength_records.read(
            connection,
            through_date="2026-09-01",
            data_start_date="2026-06-08",
            today=dt.date(2026, 9, 1),
        )["unranked"][0]
    argv = [
        "--home",
        str(athlete_home),
        "declare-strength-conditions",
        "1",
        "0",
        "--binding",
        row["binding"],
        "--equipment",
        "machine",
        "--weight-basis",
        "total",
        "--resistance-mode",
        "assisted_load",
    ]
    assert cli.main(argv, context_factory=lambda _: context) == 0
    result = json.loads(capsys.readouterr().out)
    with context.use():
        expected = server.declare_strength_conditions(
            1, 0, row["binding"], "machine", "total", "assisted_load"
        )["data"]
        records = server.get_strength_records("2026-09-01")["data"]
    assert result == expected
    assert records["unranked"][0]["resistance_mode"] == "assisted_load"
    assert records["unranked"][0]["reason"] == "assisted_load_unsupported"
    argv[argv.index(row["binding"])] = "stale"
    assert cli.main(argv, context_factory=lambda _: context) == 2
    assert "stale" in capsys.readouterr().err
    future = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    assert cli.main(["--home", str(athlete_home), "strength-records", "--through", future]) == 2
    assert "horizon" in capsys.readouterr().err


@pytest.mark.parametrize(
    "name,expected_reason",
    [
        ("ASSISTED_BARBELL_BENCH_PRESS", "assistance_indicated_by_exercise_name"),
        ("UNASSISTED_BARBELL_BENCH_PRESS", "comparison_conditions_unknown"),
    ],
)
def test_assistance_name_hint_is_conservative_and_does_not_invent_declared_mode(
    conn, name, expected_reason
):
    captured(conn, 1, "2026-08-01", 60, 5)
    payload = {
        "activityId": 1,
        "exerciseSets": [
            {
                "setType": "ACTIVE",
                "weight": 60000,
                "repetitionCount": 5,
                "exercises": [{"category": "BENCH_PRESS", "name": name}],
            }
        ],
    }
    set_history.persist(conn, 1, payload, checked_on="2026-08-01")
    conn.commit()
    row = strength_records.read(
        conn, through_date="2026-09-01", data_start_date="2026-06-08", today=dt.date(2026, 9, 1)
    )["unranked"][0]
    assert row["reason"] == expected_reason
    assert row["resistance_mode"] is None
