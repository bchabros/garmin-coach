"""Imported observations through sync, temporary SQLite and offline standing reads."""

import datetime as dt
import json

import pytest

from garmin_coach.etl import sync
from garmin_coach.core import observations
from garmin_coach.mcp import tools
from garmin_coach.runtime import InstallationContext
from garmin_coach import cli
from garmin_coach.mcp import server
from garmin_coach.marts import snapshot


def test_backfill_retains_both_weighings_and_shows_latest_in_kg(conn, fake_client, fixture):
    client = fake_client(by_day={"weight": {"2025-06-14": fixture("weight_daily_multiple")}})
    sync.backfill(client, conn, "2025-06-14", "2025-06-14")

    weight = snapshot.build(conn, through_date="2025-06-14")["weight"]
    assert weight["value"] == 59.5
    assert weight["unit"] == "kg"
    assert weight["date"] == "2025-06-14"
    assert weight["measurement_time_utc_ms"] == 1749948725175
    assert weight["measurement_source"] == "MANUAL"
    assert weight["delta"] is None
    assert conn.execute("SELECT count(*) FROM weight_observations").fetchone()[0] == 2
    assert (
        conn.execute(
            "SELECT count(*) FROM raw_payloads WHERE endpoint='get_daily_weigh_ins'"
        ).fetchone()[0]
        == 1
    )


def _weight_day(day, values):
    """Synthetic weighings for independently worked calendar comparison cases."""
    return {
        "startDate": day,
        "endDate": day,
        "dateWeightList": [
            {
                "samplePk": ident,
                "calendarDate": day,
                "weight": kg * 1000,
                "timestampGMT": int(
                    dt.datetime.fromisoformat(day)
                    .replace(hour=hour, tzinfo=dt.timezone.utc)
                    .timestamp()
                    * 1000
                ),
                "sourceType": "MANUAL",
            }
            for ident, kg, hour in values
        ],
    }


def test_weight_standing_persists_actual_28_day_span_without_future_values(conn, fake_client):
    days = {
        "2026-06-01": _weight_day("2026-06-01", [(1, 75, 12)]),
        "2026-06-03": _weight_day("2026-06-03", [(2, 60.4, 12)]),
        "2026-06-30": _weight_day("2026-06-30", [(3, 59, 10), (4, 59.7, 20)]),
        "2026-07-01": _weight_day("2026-07-01", [(5, 85, 12)]),
    }
    sync.backfill(fake_client(by_day={"weight": days}), conn, "2026-06-01", "2026-07-01")
    snapshot.rollup(conn, through_date="2026-06-30")
    weight = snapshot.read(conn)["weight"]
    assert weight["value"] == 59.7
    assert weight["delta"] == pytest.approx(-0.7)
    assert weight["span_days"] == 27
    assert weight["baseline_date"] == "2026-06-03"
    assert weight["points"] == 2


def test_incremental_weight_import_does_not_bootstrap_from_legacy_observations(
    conn, fake_client, fixture
):
    conn.execute("CREATE TABLE weight_log(date TEXT PRIMARY KEY, weight_g REAL)")
    conn.execute("INSERT INTO weight_log VALUES ('2050-01-01', 90000)")
    conn.commit()
    client = fake_client(by_day={"weight": {"2025-06-14": fixture("weight_daily_multiple")}})
    result = sync.sync_incremental(client, conn, "2025-06-14", "2025-06-14", retry_base_seconds=0)
    assert snapshot.build(conn, through_date="2025-06-14")["weight"]["value"] == 59.5
    assert "weight" in result.progressed_streams
    assert conn.execute("SELECT * FROM weight_log").fetchall() == [("2050-01-01", 90000)]


def test_refresh_preserves_weight_on_malformed_read_and_accepts_verified_empty(
    conn, fake_client, fixture
):
    day = "2025-06-14"
    client = fake_client(by_day={"weight": {day: fixture("weight_daily_multiple")}})
    sync.backfill(client, conn, day, day)
    client.by_day["weight"][day] = _weight_day(day, [(910004, 61.5, 20)])
    assert "weight" in sync.refresh_day(client, conn, day).progressed_streams
    assert snapshot.build(conn, through_date=day)["weight"]["value"] == 61.5
    # Deliberately malformed synthetic response cannot clear prior good evidence.
    client.by_day["weight"][day] = {"startDate": day, "endDate": day, "dateWeightList": "bad"}
    result = sync.refresh_day(client, conn, day)
    assert any("weight" in warning for warning in result.warnings)
    assert snapshot.build(conn, through_date=day)["weight"]["value"] == 61.5
    assert conn.execute("SELECT count(*) FROM weight_observations").fetchone()[0] == 1
    client.by_day["weight"][day] = _weight_day(day, [])
    assert not sync.refresh_day(client, conn, day).warnings
    assert snapshot.build(conn, through_date=day)["weight"] is None


def test_race_history_reaches_offline_standing_with_dates_and_seconds(conn, fake_client, fixture):
    recorded = fixture("race_daily_recent")
    client = fake_client(
        by_day={"race_forecasts": {row["calendarDate"]: [row] for row in recorded}}
    )
    sync.backfill(client, conn, "2026-10-28", "2026-10-30")
    past = snapshot.build(conn, through_date="2026-10-29")["race_forecasts"]
    assert past["5k"]["value"] == 1091
    assert past["10k"]["value"] == 2339
    assert past["5k"]["date"] == "2026-10-29"
    snapshot.rollup(conn, through_date="2026-10-30")
    current = snapshot.read(conn)["race_forecasts"]
    assert current["half_marathon"]["value"] == 5340
    assert current["marathon"]["value"] == 11964
    assert current["marathon"]["unit"] == "s"
    assert current["marathon"]["source"] == "garmin"
    assert current["marathon"]["delta"] is None


def test_forecast_changes_use_each_distances_actual_supported_history(conn, fake_client, fixture):
    # Synthetic baseline and partial-distance variants of the recorded daily schema.
    old = {
        "calendarDate": "2026-10-02",
        "time5K": 1200,
        "time10K": 2500,
        "timeHalfMarathon": None,
        "timeMarathon": None,
    }
    latest = fixture("race_daily_recent")[-1]
    client = fake_client(by_day={"race_forecasts": {"2026-10-02": [old], "2026-10-30": [latest]}})
    sync.backfill(client, conn, "2026-10-02", "2026-10-30")
    forecasts = snapshot.build(conn, through_date="2026-10-30")["race_forecasts"]
    assert forecasts["5k"]["delta"] == -108
    assert forecasts["5k"]["span_days"] == 28
    assert forecasts["10k"]["delta"] == -159
    assert forecasts["half_marathon"]["delta"] is None
    assert forecasts["half_marathon"]["points"] == 1


def test_backfill_weight_outage_does_not_stop_dated_forecasts(conn, fake_client, fixture):
    day = "2026-10-30"
    client = fake_client(
        by_day={
            "weight": {day: TimeoutError("synthetic outage")},
            "race_forecasts": {day: [fixture("race_daily_recent")[-1]]},
        }
    )
    sync.backfill(client, conn, day, day)
    standing = snapshot.build(conn, through_date=day)
    assert standing["weight"] is None
    assert standing["race_forecasts"]["5k"]["value"] == 1092


@pytest.mark.parametrize("mart_failure", [False, True])
def test_interrupted_period_refresh_reports_and_rebuilds_committed_progress(
    conn, fake_client, fixture, athlete_home, mart_failure
):
    from garmin_coach.marts import features

    day = "2026-10-29"
    original = fake_client(by_day={"weight": {day: _weight_day(day, [(1, 79, 12)])}})
    sync.backfill(original, conn, day, day)
    features.features(conn, data_start_date=day)
    if mart_failure:
        conn.execute(
            "CREATE TRIGGER refuse_partial_mart BEFORE INSERT ON daily_metrics BEGIN SELECT RAISE(FAIL, 'synthetic mart failure'); END"
        )
        conn.commit()

    class Interrupted(fake_client):
        def get_sleep(self, date):
            if date == "2026-10-30":
                raise TimeoutError("synthetic interruption")
            return super().get_sleep(date)

    transport = Interrupted(
        by_day={
            "weight": {day: _weight_day(day, [(1, 80, 12)])},
            "wellness": {day: fixture("wellness_day")},
        }
    )
    with InstallationContext.select(athlete_home).use():
        preview = tools.repair_preview(
            conn, from_date=day, to_date="2026-10-30", data_start_date=day
        )["data"]
        result = tools.repair_confirm(
            conn,
            transport,
            from_date=day,
            to_date="2026-10-30",
            confirm_token=preview["confirm_token"],
            data_start_date=day,
        )["data"]
        source = tools.get_observation_history(conn, from_date=day, to_date=day)["data"]
    assert result["applied"] and result["saved"]
    assert result["complete"] is False
    assert "weight" in result["progressed_streams"]
    assert result["committed_days"] == [day]
    assert result["features_ok"] is not mart_failure
    assert source["weight"][0]["weight_kg"] == 80
    assert snapshot.read(conn)["weight"]["value"] == (79 if mart_failure else 80)
    if mart_failure:
        assert result["recovery_command"] == "garmin-coach features"


def test_seven_nightly_runs_find_old_app_edits_without_duplicate_day_reads(conn, fake_client):
    old = "2026-06-04"
    # Synthetic dated forecast change exercises the same history cycle as weight.
    client = fake_client(
        by_day={
            "weight": {old: _weight_day(old, [(1, 80, 12)])},
            "race_forecasts": {old: [{"calendarDate": old, "time5K": 1500}]},
        }
    )
    sync.sync_incremental(client, conn, "2026-06-01", "2026-06-30", retry_base_seconds=0)
    client.by_day["weight"][old] = _weight_day(old, [(1, 79, 12)])
    client.by_day["race_forecasts"][old] = [{"calendarDate": old, "time5K": 1400}]
    for day in range(1, 8):
        client.calls.clear()
        sync.sync_incremental(client, conn, "2026-06-01", f"2026-07-{day:02}", retry_base_seconds=0)
        fetched = [date for endpoint, date in client.calls if endpoint == "weight"]
        assert len(fetched) == len(set(fetched))
    assert snapshot.build(conn, through_date="2026-07-07")["weight"]["value"] == 79
    assert snapshot.build(conn, through_date="2026-07-07")["race_forecasts"]["5k"]["value"] == 1400


def test_failed_history_bucket_stays_due_and_same_night_cannot_advance_twice(conn, fake_client):
    client = fake_client()
    sync.sync_incremental(client, conn, "2026-06-01", "2026-06-30", retry_base_seconds=0)
    before = observations.scan_state(conn, "weight")
    client.by_day["weight"] = {
        f"2026-06-{day:02}": TimeoutError("synthetic") for day in range(1, 29)
    }
    failed = sync.sync_incremental(client, conn, "2026-06-01", "2026-07-01", retry_base_seconds=0)
    assert any("weight history failed" in warning for warning in failed.warnings)
    assert observations.scan_state(conn, "weight") == before
    client.by_day["weight"].clear()
    sync.sync_incremental(client, conn, "2026-06-01", "2026-07-02", retry_base_seconds=0)
    after = observations.scan_state(conn, "weight")
    assert after["successful_runs"] == before["successful_runs"] + 1
    sync.sync_incremental(client, conn, "2026-06-01", "2026-07-02", retry_base_seconds=0)
    assert observations.scan_state(conn, "weight") == after


def test_offline_history_exposes_failed_and_unchecked_days(conn, fake_client, athlete_home):
    (athlete_home / ".env").write_text("DATA_START_DATE=2026-06-01\n")
    client = fake_client(by_day={"weight": {"2026-06-10": TimeoutError("synthetic")}})
    sync.sync_incremental(client, conn, "2026-06-01", "2026-06-30", retry_base_seconds=0)
    with InstallationContext.select(athlete_home).use():
        data = tools.get_observation_history(conn, from_date="2026-06-01", to_date="2026-06-30")[
            "data"
        ]
    assert data["checks"]["weight"]["successful_days"] == 9
    assert data["checks"]["weight"]["failed_dates"] == ["2026-06-10"]
    assert data["checks"]["weight"]["never_checked_days"] == 21
    assert data["checks"]["weight"]["incomplete"] is True
    assert data["checks"]["race_forecasts"]["successful_days"] == 30


def test_terminal_and_mcp_read_identical_saved_history_without_login(
    athlete_home, fake_client, fixture, capsys
):
    def reject(settings):
        raise AssertionError("cached reads must not connect")

    context = InstallationContext.select(
        athlete_home, import_factory=reject, publisher_factory=reject
    )
    conn = context.open_database(create=True)
    sync.backfill(
        fake_client(by_day={"race_forecasts": {"2026-10-30": [fixture("race_daily_recent")[-1]]}}),
        conn,
        "2026-10-30",
        "2026-10-30",
    )
    conn.close()
    assert (
        cli.main(
            ["observations", "--from", "2026-10-30", "--to", "2026-10-30"],
            context_factory=lambda _: context,
        )
        == 0
    )
    terminal = json.loads(capsys.readouterr().out)
    with context.use():
        conversational = server.get_observation_history(
            from_date="2026-10-30", to_date="2026-10-30"
        )
    assert terminal == conversational
    assert terminal["data"]["race_forecasts"][0]["time_5k_s"] == 1092


def test_requested_period_refresh_saves_observations_without_changing_watermarks(
    conn, fake_client, athlete_home, fixture
):
    day = "2026-06-08"
    client = fake_client(by_day={"wellness": {day: fixture("wellness_day")}})
    sync.sync_incremental(client, conn, day, "2026-06-15", retry_base_seconds=0)
    watermarks = conn.execute("SELECT * FROM sync_state ORDER BY stream").fetchall()
    client.by_day["weight"] = {day: _weight_day(day, [(1, 80, 12)])}
    with InstallationContext.select(athlete_home).use():
        preview = tools.repair_preview(conn, from_date=day, to_date=day, data_start_date=day)[
            "data"
        ]
        result = tools.repair_confirm(
            conn,
            client,
            from_date=day,
            to_date=day,
            confirm_token=preview["confirm_token"],
            data_start_date=day,
        )["data"]
        assert result["applied"] and result["features_ok"]
        assert snapshot.read(conn)["weight"]["value"] == 80
        assert conn.execute("SELECT * FROM sync_state ORDER BY stream").fetchall() == watermarks
        client.by_day["weight"][day] = TimeoutError("synthetic")
        preview = tools.repair_preview(conn, from_date=day, to_date=day, data_start_date=day)[
            "data"
        ]
        partial = tools.repair_confirm(
            conn,
            client,
            from_date=day,
            to_date=day,
            confirm_token=preview["confirm_token"],
            data_start_date=day,
        )["data"]
        assert partial["observation_warnings"]
        assert partial["complete"] is False
        assert snapshot.read(conn)["weight"]["value"] == 80


def test_observation_change_makes_period_preview_stale_even_in_same_second(
    conn, fake_client, athlete_home
):
    day = "2026-06-08"
    client = fake_client(by_day={"weight": {day: _weight_day(day, [(1, 80, 12)])}})
    sync.backfill(client, conn, day, day)
    with InstallationContext.select(athlete_home).use():
        preview = tools.repair_preview(conn, from_date=day, to_date=day, data_start_date=day)[
            "data"
        ]
        client.by_day["weight"][day] = _weight_day(day, [(1, 79, 12)])
        sync.refresh_day(client, conn, day)
        assert tools.repair_refusal(
            conn,
            from_date=day,
            to_date=day,
            confirm_token=preview["confirm_token"],
            data_start_date=day,
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"weight": 0},
        {"weight": True},
        {"weight": "80000"},
        {"weight": float("nan")},
        {"samplePk": None},
        {"timestampGMT": None},
        {"calendarDate": "2026-06-09"},
        {"sourceType": {}},
    ],
)
def test_malformed_weight_keeps_good_source_and_raw_evidence(conn, fake_client, patch):
    day = "2026-06-08"
    client = fake_client(by_day={"weight": {day: _weight_day(day, [(1, 80, 12)])}})
    sync.backfill(client, conn, day, day)
    bad = _weight_day(day, [(1, 79, 12)])
    bad["dateWeightList"][0].update(patch)
    client.by_day["weight"][day] = bad
    result = sync.refresh_day(client, conn, day)
    assert any("weight failed" in error for error in result.warnings)
    assert snapshot.build(conn, through_date=day)["weight"]["value"] == 80
    assert (
        conn.execute(
            "SELECT count(*) FROM raw_payloads WHERE endpoint='get_daily_weigh_ins'"
        ).fetchone()[0]
        == 2
    )


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"calendarDate": "2026-10-30", "time5K": 1000},
        [{"calendarDate": "2026-10-29", "time5K": 1000}],
        [{"calendarDate": "2026-10-30", "time5K": "bad"}],
        [{"calendarDate": "2026-10-30", "time5K": 0}],
        [{"calendarDate": "2026-10-30", "unsupported": 1000}],
    ],
)
def test_malformed_forecast_or_latest_object_cannot_replace_daily_history(
    conn, fake_client, fixture, bad
):
    day = "2026-10-30"
    client = fake_client(by_day={"race_forecasts": {day: [fixture("race_daily_recent")[-1]]}})
    sync.backfill(client, conn, day, day)
    client.by_day["race_forecasts"][day] = bad
    result = sync.refresh_day(client, conn, day)
    assert any("race_forecasts failed" in error for error in result.warnings)
    assert snapshot.build(conn, through_date=day)["race_forecasts"]["5k"]["value"] == 1092


def test_sql_failure_during_day_replacement_preserves_previous_weighing(conn, fake_client):
    day = "2026-06-08"
    client = fake_client(by_day={"weight": {day: _weight_day(day, [(1, 80, 12)])}})
    sync.backfill(client, conn, day, day)
    conn.execute(
        "CREATE TRIGGER refuse_weight BEFORE INSERT ON weight_observations BEGIN SELECT RAISE(FAIL, 'synthetic storage failure'); END"
    )
    conn.commit()
    client.by_day["weight"][day] = _weight_day(day, [(1, 79, 12)])
    result = sync.refresh_day(client, conn, day)
    assert result.warnings
    assert snapshot.build(conn, through_date=day)["weight"]["value"] == 80
    assert (
        conn.execute(
            "SELECT count(*) FROM raw_payloads WHERE endpoint='get_daily_weigh_ins'"
        ).fetchone()[0]
        == 2
    )


def test_period_refresh_preserves_new_source_when_late_mart_rebuild_fails(
    conn, fake_client, fixture, athlete_home
):
    from garmin_coach.marts import features

    day = "2026-06-08"
    client = fake_client(
        by_day={
            "wellness": {day: fixture("wellness_day")},
            "weight": {day: _weight_day(day, [(1, 80, 12)])},
        }
    )
    sync.backfill(client, conn, day, day)
    features.features(conn, data_start_date=day)
    conn.execute(
        "CREATE TRIGGER refuse_daily BEFORE INSERT ON daily_metrics BEGIN SELECT RAISE(FAIL, 'synthetic mart failure'); END"
    )
    conn.commit()
    client.by_day["weight"][day] = _weight_day(day, [(1, 79, 12)])
    with InstallationContext.select(athlete_home).use():
        preview = tools.repair_preview(conn, from_date=day, to_date=day, data_start_date=day)[
            "data"
        ]
        result = tools.repair_confirm(
            conn,
            client,
            from_date=day,
            to_date=day,
            confirm_token=preview["confirm_token"],
            data_start_date=day,
        )["data"]
        saved = tools.get_observation_history(conn, from_date=day, to_date=day)["data"]
    assert result["applied"] and not result["features_ok"]
    assert result["recovery_command"] == "garmin-coach features"
    assert saved["weight"][0]["weight_kg"] == 79
    assert snapshot.read(conn)["weight"]["value"] == 80


def test_historical_source_outage_stops_after_one_failed_day(conn, fake_client):
    client = fake_client()
    sync.sync_incremental(client, conn, "2026-06-01", "2026-06-30", retry_base_seconds=0)
    client.calls.clear()
    client.by_day["weight"] = {
        f"2026-06-{day:02}": TimeoutError("synthetic") for day in range(1, 29)
    }
    sync.sync_incremental(
        client, conn, "2026-06-01", "2026-07-01", max_attempts=1, retry_base_seconds=0
    )
    old_reads = [
        date for source, date in client.calls if source == "weight" and date < "2026-06-29"
    ]
    assert len(old_reads) == 1


def test_overlapping_nightly_runs_cannot_claim_two_history_buckets(athlete_home, fake_client):
    context = InstallationContext.select(athlete_home)
    first = context.open_database(create=True)
    second = context.open_database()
    base = fake_client()
    sync.sync_incremental(base, first, "2026-06-01", "2026-06-30", retry_base_seconds=0)
    before = observations.scan_state(first, "weight")["successful_runs"]

    class InterleavedClient(fake_client):
        triggered = False

        def get_weight(self, date):
            if date < "2026-06-29" and not self.triggered:
                self.triggered = True
                sync.sync_incremental(
                    base, second, "2026-06-01", "2026-07-01", retry_base_seconds=0
                )
            return super().get_weight(date)

    try:
        sync.sync_incremental(
            InterleavedClient(), first, "2026-06-01", "2026-07-01", retry_base_seconds=0
        )
        assert observations.scan_state(first, "weight")["successful_runs"] == before + 1
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("name, count", [("weight_daily_single", 1), ("weight_daily_empty", 0)])
def test_recorded_single_and_empty_weight_days(conn, fake_client, fixture, name, count):
    payload = fixture(name)
    day = payload["startDate"]
    result = sync.backfill(fake_client(by_day={"weight": {day: payload}}), conn, day, day)
    assert not result.warnings
    assert conn.execute("SELECT count(*) FROM weight_observations").fetchone()[0] == count
    if count == 0:
        assert snapshot.build(conn, through_date=day)["weight"] is None


def test_latest_forecast_is_never_used_as_first_history_day(conn, fake_client, fixture):
    day = "2026-06-08"
    client = fake_client(by_day={"race_forecasts": {day: fixture("race_daily_data_start")}})
    assert not sync.backfill(client, conn, day, day).warnings
    client.by_day["race_forecasts"][day] = fixture("race_latest")
    assert sync.refresh_day(client, conn, day).warnings
    forecast = snapshot.build(conn, through_date=day)["race_forecasts"]["5k"]
    assert forecast["date"] == day


def test_old_check_timestamp_is_overdue_even_while_source_fact_remains_available(
    conn, fake_client, athlete_home
):
    day = "2026-06-08"
    sync.backfill(
        fake_client(by_day={"weight": {day: _weight_day(day, [(1, 80, 12)])}}), conn, day, day
    )
    conn.execute(
        "UPDATE observation_checks SET checked_at='2000-01-01T00:00:00+00:00' WHERE stream='weight'"
    )
    conn.commit()
    with InstallationContext.select(athlete_home).use():
        data = tools.get_observation_history(conn, from_date=day, to_date=day)["data"]
    assert data["checks"]["weight"]["overdue_days"] == 1
    assert data["checks"]["weight"]["due_days"] == 1
    assert data["checks"]["weight"]["incomplete"]
    assert data["weight"][0]["weight_kg"] == 80


@pytest.mark.parametrize(
    "start,end",
    [
        ("20260608", "2026-06-09"),
        ("2026-06-07", "2026-06-08"),
        ("2026-06-09", "2026-06-08"),
        ("2050-01-01", "2050-01-02"),
    ],
)
def test_history_read_refuses_invalid_or_out_of_scope_dates(conn, athlete_home, start, end):
    with InstallationContext.select(athlete_home).use():
        data = tools.get_observation_history(conn, from_date=start, to_date=end)["data"]
    assert data["error"]


def test_pre_cutoff_observations_are_retained_but_excluded_from_standing(
    conn, fake_client, fixture
):
    from garmin_coach.marts import features

    # Explicit synthetic older source facts must stay outside the configured history.
    old = "2026-06-01"
    first = "2026-06-08"
    client = fake_client(
        by_day={
            "weight": {
                old: {
                    "startDate": old,
                    "endDate": old,
                    "dateWeightList": [
                        {
                            "samplePk": 1,
                            "calendarDate": old,
                            "weight": 80000,
                            "timestampGMT": 1780300800000,
                        }
                    ],
                }
            },
            "race_forecasts": {old: [{"calendarDate": old, "time5K": 1200}]},
            "wellness": {first: fixture("wellness_day")},
        }
    )
    sync.backfill(client, conn, old, first)
    features.features(conn, data_start_date=first)
    status = snapshot.read(conn)
    assert status["weight"] is None
    assert status["race_forecasts"]["5k"] is None
    assert conn.execute("SELECT count(*) FROM weight_observations").fetchone()[0] == 1


@pytest.mark.parametrize("stale_wellness", [False, True])
def test_default_features_horizon_includes_new_observation_sources(
    conn, fake_client, fixture, stale_wellness
):
    from garmin_coach.marts import features

    day = "2026-10-30"
    client = fake_client(
        by_day={
            "weight": {day: _weight_day(day, [(1, 80, 12)])},
            "race_forecasts": {day: [fixture("race_daily_recent")[-1]]},
            "wellness": {"2026-10-29": fixture("wellness_day")} if stale_wellness else {},
        }
    )
    sync.backfill(client, conn, "2026-10-29", day)
    features.features(conn, data_start_date="2026-10-29")
    standing = snapshot.read(conn)
    assert standing is not None
    assert standing["computed_at"] == day
    assert standing["weight"]["value"] == 80
    assert standing["race_forecasts"]["5k"]["value"] == 1092
