"""Historical recorded sets, correction baselines and explicit conflict choices."""

from garmin_coach import athlete_changes, set_corrections, set_history
from garmin_coach.core import db

import pytest

from garmin_coach.etl import client as transport


def test_selected_refresh_retains_unrecorded_timing_and_garmin_rest_sentinel(conn, fixture):
    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Strength",
        },
    )
    conn.commit()
    result = set_history.refresh(
        conn,
        900001,
        lambda: fixture("exercise_sets_added"),
        data_start_date="2026-06-08",
        checked_on="2026-06-13",
    )
    assert result["saved"] is True
    captured = set_corrections.saved(conn, 900001)["captured"]
    assert len(captured) == 4
    assert captured[0]["duration_s"] == 60.125
    assert captured[3]["duration_s"] is None
    assert captured[3]["reps"] == 1
    assert captured[3]["subcategory"] == "LUNGE"


def test_source_change_exposes_conflict_and_retirement_retains_history(conn, fixture):
    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    payload = fixture("exercise_sets_strength")
    set_history.persist(conn, 900001, payload, checked_on="2026-06-13")
    conn.commit()
    athlete_changes.log_activity_sets(
        conn, activity_id=900001, stations=["BENCH_PRESS"], data_start_date="2026-06-08"
    )
    assert set_history.read(conn, 900001)["conflict"] is False
    assert set_corrections.saved(conn, 900001)["baseline_state"] == "known"
    assert set_corrections.saved(conn, 900001)["conflict"] is False
    payload["exerciseSets"][0]["weight"] = 70000
    set_history.persist(conn, 900001, payload, checked_on="2026-06-14")
    conn.commit()
    state = set_history.read(conn, 900001)
    assert state["conflict"] is True
    assert set_corrections.saved(conn, 900001)["conflict"] is True
    result = set_history.choose(
        conn, 900001, "captured", state["binding"], data_start_date="2026-06-08"
    )
    assert result["saved"] is True
    assert set_corrections.saved(conn, 900001)["manual"] == []
    assert set_history.read(conn, 900001)["history"][0]["reason"] == "choose_captured"


def test_failed_selected_refresh_keeps_prior_observations(conn, fixture):
    import pytest

    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    set_history.persist(conn, 900001, fixture("exercise_sets_strength"), checked_on="2026-06-13")
    conn.commit()
    before = set_history.read(conn, 900001)
    with pytest.raises(ValueError):
        set_history.refresh(
            conn,
            900001,
            lambda: {"activityId": 999, "exerciseSets": []},
            data_start_date="2026-06-08",
        )
    assert set_history.read(conn, 900001) == before


def test_seven_successful_nights_cover_history_without_repeating_recent_sets(conn):
    import datetime as dt

    for aid in range(1, 15):
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "date": "2026-06-12",
                "start_local": f"2026-06-12 17:{aid:02}:00",
                "gtype": "strength_training",
                "discipline": "Siła",
            },
        )
    conn.commit()
    observed = []

    def fetch(aid):
        observed.append(aid)
        return {"activityId": aid, "exerciseSets": []}

    for offset in range(7):
        day = (dt.date(2026, 7, 1) + dt.timedelta(days=offset)).isoformat()
        outcome = set_history.recheck(conn, fetch, through_date=day, data_start_date="2026-06-08")
        assert len(outcome["checked"]) == 2
        repeated = set_history.recheck(conn, fetch, through_date=day, data_start_date="2026-06-08")
        assert repeated["already_run"] is True
    assert sorted(observed) == list(range(1, 15))
    assert outcome["due"] == 0


def test_persist_is_rolled_back_by_its_caller(conn, fixture):
    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    conn.commit()
    set_history.persist(conn, 900001, fixture("exercise_sets_strength"), checked_on="2026-06-13")
    assert conn.in_transaction
    conn.rollback()
    assert set_history.read(conn, 900001)["captured"] == []
    assert set_history.read(conn, 900001)["checked_on"] is None


def test_failed_scan_is_observable_and_remains_due_until_recovered(conn):
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    conn.commit()
    set_history.recheck(
        conn, lambda aid: None, through_date="2026-07-01", data_start_date="2026-06-08"
    )
    state = set_history.coverage(conn, through_date="2026-07-01", data_start_date="2026-06-08")
    assert state["due"] == state["failed"] == state["never_checked"] == 1
    set_history.recheck(
        conn,
        lambda aid: {"activityId": aid, "exerciseSets": []},
        through_date="2026-07-02",
        data_start_date="2026-06-08",
    )
    state = set_history.coverage(conn, through_date="2026-07-02", data_start_date="2026-06-08")
    assert state["due"] == state["failed"] == state["never_checked"] == 0


def test_selected_refresh_clears_an_earlier_historical_failure(conn):
    db.upsert_activity(
        conn,
        {
            "activity_id": 1,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
            "discipline": "Siła",
        },
    )
    conn.commit()
    set_history.recheck(
        conn, lambda aid: None, through_date="2026-07-01", data_start_date="2026-06-08"
    )
    set_history.refresh(
        conn,
        1,
        lambda: {"activityId": 1, "exerciseSets": []},
        checked_on="2026-07-02",
        data_start_date="2026-06-08",
    )
    assert (
        set_history.coverage(conn, through_date="2026-07-02", data_start_date="2026-06-08")[
            "failed"
        ]
        == 0
    )


@pytest.mark.parametrize(
    "error,reason",
    [
        (transport.GarminConnectTooManyRequestsError("limited"), "rate_limited"),
        (transport.GarminConnectAuthenticationError("expired"), "authentication"),
        (transport.LoginUnavailableError("expired"), "authentication"),
        (TimeoutError("down"), "unavailable"),
    ],
)
def test_history_batch_stops_on_account_failure_and_keeps_remaining_due(conn, error, reason):
    for aid in range(1, 15):
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "date": "2026-06-12",
                "start_local": f"2026-06-12 17:{aid:02}:00",
                "gtype": "strength_training",
            },
        )
    conn.commit()
    calls = []

    def fail(aid):
        calls.append(aid)
        raise error

    result = set_history.recheck(
        conn, fail, through_date="2026-07-01", data_start_date="2026-06-08"
    )
    assert calls == [1]
    assert result["stopped_reason"] == reason
    assert result["checked"] == []
    assert result["due"] == 14
    coverage = set_history.coverage(conn, through_date="2026-07-01", data_start_date="2026-06-08")
    assert coverage["failed"] == 1
    assert coverage["never_checked"] == 14


def test_isolated_bad_history_payload_does_not_stop_the_batch(conn):
    for aid in range(1, 15):
        db.upsert_activity(
            conn,
            {
                "activity_id": aid,
                "date": "2026-06-12",
                "start_local": f"2026-06-12 17:{aid:02}:00",
                "gtype": "strength_training",
            },
        )
    conn.commit()
    calls = []

    def fetch(aid):
        calls.append(aid)
        return None if aid == 1 else {"activityId": aid, "exerciseSets": []}

    result = set_history.recheck(
        conn, fetch, through_date="2026-07-01", data_start_date="2026-06-08"
    )
    assert calls == [1, 2]
    assert result["checked"] == [2]
    assert result["stopped_reason"] is None


def test_saved_source_survives_failed_rebuild_with_operator_log(conn, fixture, caplog):
    db.upsert_activity(
        conn,
        {
            "activity_id": 900001,
            "date": "2026-06-12",
            "start_local": "2026-06-12 17:00:00",
            "gtype": "strength_training",
        },
    )
    conn.execute(
        "CREATE TRIGGER fail_derived BEFORE INSERT ON daily_metrics BEGIN SELECT RAISE(ABORT, 'derived storage unavailable'); END"
    )
    conn.commit()
    result = set_history.refresh(
        conn, 900001, lambda: fixture("exercise_sets_strength"), data_start_date="2026-06-08"
    )
    assert result["saved"] is True
    assert result["recomputed"] is False
    assert result["evidence"]["captured"]
    assert "set_history: derived refresh failed" in caplog.text


def test_cli_stale_conflict_choice_is_a_clean_refusal(athlete_home, capsys):
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home)
    with context.open_database(create=True) as conn:
        db.bootstrap(conn)
        db.upsert_activity(
            conn,
            {
                "activity_id": 900001,
                "date": "2026-06-12",
                "start_local": "2026-06-12 17:00:00",
                "gtype": "strength_training",
            },
        )
        conn.commit()
    assert (
        cli.main(
            [
                "--home",
                str(athlete_home),
                "resolve-set-conflict",
                "900001",
                "--choice",
                "captured",
                "--binding",
                "old",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "stale" in output.err
    assert "Traceback" not in output.err
