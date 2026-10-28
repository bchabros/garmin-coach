"""Reported console settings through saved execution and athlete-change seams."""

from __future__ import annotations

import pytest

from garmin_coach.coach import activity
from garmin_coach.etl import sync
from tests.test_activity_laps import LapClient

DAY = "2026-07-10"
AID = 900104


def _import(conn, fixture):
    client = LapClient(fixture("activity_laps_treadmill"))
    sync.backfill(client, conn, DAY, DAY)
    return client


def test_saved_read_exposes_reviewed_work_binding_without_a_mart(conn, fixture):
    _import(conn, fixture)
    out = activity.read(conn, AID)
    work = out["work_execution"]
    assert work["binding"].startswith("v1:")
    assert work["unavailable_reason"] is None
    assert work["work_count"] == 6
    assert [r["repetition"] for r in work["work_repetitions"]] == [1, 2, 3, 4, 5, 6]
    assert work["work_repetitions"][0]["lap_indices"] == [5, 6]
    assert work["work_repetitions"][0]["dur_s"] == 225
    assert out["summary"] is None


def test_binding_survives_measurement_metadata_and_equivalent_time_edits(conn, fixture):
    import datetime as dt

    client = _import(conn, fixture)
    original = activity.read(conn, AID)["work_execution"]["binding"]
    evidence = client.evidence
    for endpoint, key in (
        ("get_activity_splits", "lapDTOs"),
        ("get_activity_typed_splits", "splits"),
    ):
        for row in evidence[endpoint][key]:
            row.update(averageHR=120, maxHR=150, averageSpeed=2.2, distance=99)
            row["messageIndex"] = 999
    splits = evidence["get_activity_typed_splits"]["splits"]
    splits.insert(
        0, splits.pop(next(i for i, g in enumerate(splits) if g["type"].startswith("RWD_")))
    )
    for lap in evidence["get_activity_splits"]["lapDTOs"]:
        lap["lapIndex"] += 100
    for group in splits:
        if "lapIndexes" in group:
            group["lapIndexes"] = [i + 100 for i in group["lapIndexes"]]
    stamp = evidence["get_activity_splits"]["lapDTOs"][4]["startTimeGMT"]
    instant = dt.datetime.fromisoformat(stamp).replace(tzinfo=dt.timezone.utc)
    evidence["get_activity_splits"]["lapDTOs"][4]["startTimeGMT"] = instant.astimezone(
        dt.timezone(dt.timedelta(hours=2))
    ).isoformat()
    sync.backfill(client, conn, DAY, DAY)
    current = activity.read(conn, AID)["work_execution"]
    assert current["binding"] == original
    assert current["work_count"] == 6
    assert current["work_repetitions"][0]["lap_indices"] == [105, 106]


def test_binding_changes_when_work_boundaries_change_and_refuses_unknown_roles(conn, fixture):
    client = _import(conn, fixture)
    original = activity.read(conn, AID)["work_execution"]["binding"]
    first = next(
        g
        for g in client.evidence["get_activity_typed_splits"]["splits"]
        if g["type"] == "INTERVAL_ACTIVE"
    )
    first["duration"] = 226
    sync.backfill(client, conn, DAY, DAY)
    assert activity.read(conn, AID)["work_execution"]["binding"] != original
    first["type"] = "INTERVAL_UNKNOWN"
    sync.backfill(client, conn, DAY, DAY)
    current = activity.read(conn, AID)["work_execution"]
    assert current["binding"] is None
    assert current["unavailable_reason"] == "execution_roles_or_grouping_unavailable"


def test_whole_work_speed_is_durable_source_aware_and_keeps_capture_unchanged(conn, fixture):
    import pytest
    from garmin_coach import athlete_changes
    from garmin_coach.marts import features

    client = _import(conn, fixture)
    before = activity.read(conn, AID)
    binding = before["work_execution"]["binding"]
    result = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    assert result.saved is True and result.recomputed is True
    out = activity.read(conn, AID)
    report = out["reported_console"]
    assert report["state"] == "matched"
    assert report["source"] == "athlete_reported_treadmill_console"
    assert report["reported_count"] == report["current_work_count"] == 6
    assert report["missing_repetitions"] == []
    assert report["applied_reports"][0]["speed_kmh"] == 16.4
    assert report["applied_reports"][0]["pace_s_per_km"] == pytest.approx(219.5121951)
    assert out["laps"] == before["laps"]
    assert out["summary"]["outdoor_pace"] is None
    sync.backfill(client, conn, DAY, DAY)
    features.features(conn, data_start_date=DAY)
    assert activity.read(conn, AID)["reported_console"] == report


def test_per_repetition_input_replaces_whole_set_and_shows_missing_coverage(conn, fixture):
    from garmin_coach import athlete_changes

    _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    result = athlete_changes.log_treadmill_speed(
        conn,
        activity_id=AID,
        binding=binding,
        repetitions=[{"repetition": 2, "speed_kmh": 16.4}, {"repetition": 6, "speed_kmh": 16}],
        data_start_date=DAY,
    )
    assert result.saved and result.recomputed
    report = activity.read(conn, AID)["reported_console"]
    assert report["reported_count"] == 2
    assert report["missing_repetitions"] == [1, 3, 4, 5]
    assert [r["repetition"] for r in report["applied_reports"]] == [2, 6]
    result = athlete_changes.log_treadmill_speed(
        conn,
        activity_id=AID,
        binding=binding,
        repetitions=[{"repetition": 1, "speed_kmh": 15}],
        data_start_date=DAY,
    )
    assert result.saved
    report = activity.read(conn, AID)["reported_console"]
    assert report["reported_count"] == 1
    assert report["missing_repetitions"] == [2, 3, 4, 5, 6]


@pytest.mark.parametrize(
    "arguments",
    [
        {"speed_kmh": 0},
        {"speed_kmh": -1},
        {"speed_kmh": True},
        {"speed_kmh": float("nan")},
        {"speed_kmh": float("inf")},
        {"speed_kmh": 1e-320},
        {"speed_kmh": 10**400},
        {},
        {"repetitions": []},
        {"speed_kmh": 16, "repetitions": [{"repetition": 1, "speed_kmh": 15}]},
        {"repetitions": [{"repetition": 1, "speed_kmh": 16}, {"repetition": 1, "speed_kmh": 15}]},
        {"repetitions": [{"repetition": 0, "speed_kmh": 16}]},
        {"repetitions": [{"repetition": 7, "speed_kmh": 16}]},
        {"repetitions": [{"repetition": True, "speed_kmh": 16}]},
        {"repetitions": [{"repetition": 2.0, "speed_kmh": 16}]},
        {"repetitions": [{"repetition": 1, "speed_kmh": 16}, {"repetition": 2, "speed_kmh": 0}]},
        {"repetitions": [{"repetition": 1, "speed_kmh": 16, "extra": 1}]},
    ],
)
def test_invalid_input_preserves_prior_complete_report_without_partial_write(
    conn, fixture, arguments
):
    from garmin_coach import athlete_changes

    _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    before = activity.read(conn, AID)
    result = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, data_start_date=DAY, **arguments
    )
    assert result.saved is False
    assert result.error
    assert activity.read(conn, AID) == before
    assert conn.in_transaction is False


def test_changed_layout_rejects_old_read_and_clear_works_with_unavailable_execution(conn, fixture):
    from garmin_coach import athlete_changes

    client = _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    group = next(
        g
        for g in client.evidence["get_activity_typed_splits"]["splits"]
        if g["type"] == "INTERVAL_ACTIVE"
    )
    group["duration"] = 226
    sync.backfill(client, conn, DAY, DAY)
    result = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=15, data_start_date=DAY
    )
    assert result.saved is False
    report = activity.read(conn, AID)["reported_console"]
    assert report["state"] == "stale"
    assert report["reported_count"] == 0 and report["applied_reports"] == []
    assert len(report["reports"]) == 6
    assert report["reports"][0]["speed_kmh"] == 16.4
    group["type"] = "INTERVAL_UNKNOWN"
    sync.backfill(client, conn, DAY, DAY)
    result = athlete_changes.clear_treadmill_speed(conn, activity_id=AID, data_start_date=DAY)
    assert result.saved and result.recomputed
    assert activity.read(conn, AID)["reported_console"]["state"] == "none"
    assert athlete_changes.clear_treadmill_speed(conn, activity_id=AID, data_start_date=DAY).saved


def test_whole_work_report_does_not_extend_to_a_newly_observed_repetition(conn, fixture):
    from garmin_coach import athlete_changes

    evidence = fixture("activity_laps_treadmill")
    removed = {17, 18, 19}
    evidence["get_activity_splits"]["lapDTOs"] = [
        r for r in evidence["get_activity_splits"]["lapDTOs"] if r["lapIndex"] not in removed
    ]
    evidence["get_activity_typed_splits"]["splits"] = [
        g
        for g in evidence["get_activity_typed_splits"]["splits"]
        if not removed.intersection(g.get("lapIndexes", []))
    ]
    client = LapClient(evidence)
    sync.backfill(client, conn, DAY, DAY)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    result = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    assert result.data["reported_count"] == 5
    _import(conn, fixture)
    report = activity.read(conn, AID)["reported_console"]
    assert report["state"] == "stale"
    assert report["saved_work_count"] == 5 and report["current_work_count"] == 6
    assert report["applied_reports"] == []
    assert report["missing_repetitions"] == [1, 2, 3, 4, 5, 6]


def test_saved_speed_and_clear_survive_failed_rebuild_and_reopen(tmp_path, fixture):
    from garmin_coach import athlete_changes
    from garmin_coach.core import db

    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    previous = activity.read(conn, AID)["summary"]
    conn.execute(
        "CREATE TEMP TRIGGER late_failure BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
    )
    conn.commit()
    changed = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16, data_start_date=DAY
    )
    assert changed.saved is True and changed.recomputed is False
    assert changed.recovery_command == "garmin-coach features"
    assert activity.read(conn, AID)["summary"] == previous
    assert activity.read(conn, AID)["reported_console"]["applied_reports"][0]["speed_kmh"] == 16
    conn.close()
    with db.connect(str(path)) as reopened:
        assert (
            activity.read(reopened, AID)["reported_console"]["applied_reports"][0]["speed_kmh"]
            == 16
        )
        reopened.execute(
            "CREATE TEMP TRIGGER late_failure BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late mart failure'); END"
        )
        reopened.commit()
        cleared = athlete_changes.clear_treadmill_speed(
            reopened, activity_id=AID, data_start_date=DAY
        )
        assert cleared.saved is True and cleared.recomputed is False
    with db.connect(str(path)) as reopened:
        assert activity.read(reopened, AID)["reported_console"]["state"] == "none"
        assert activity.read(reopened, AID)["summary"] == previous


def test_invalid_or_wrong_activity_cannot_create_a_console_report(conn, fixture):
    from garmin_coach import athlete_changes

    client = _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    assert (
        athlete_changes.log_treadmill_speed(
            conn, activity_id=9, binding=binding, speed_kmh=16, data_start_date=DAY
        ).saved
        is False
    )
    client.activities[0]["activityType"]["typeKey"] = "running"
    sync.backfill(client, conn, DAY, DAY)
    assert (
        athlete_changes.log_treadmill_speed(
            conn, activity_id=AID, binding=binding, speed_kmh=16, data_start_date=DAY
        ).saved
        is False
    )
    assert activity.read(conn, AID)["reported_console"] is None


def test_cli_mcp_and_server_share_write_clear_and_freshness(athlete_home, fixture, capsys):
    import asyncio
    import json
    from garmin_coach import cli
    from garmin_coach.core import db
    from garmin_coach.mcp import server, tools
    from garmin_coach.runtime import InstallationContext

    path = athlete_home / "data" / "garmin.db"
    path.parent.mkdir()
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    conn.close()
    args = [
        "--home",
        str(athlete_home),
        "log-treadmill-speed",
        str(AID),
        "--binding",
        binding,
        "--speed-kmh",
        "16.4",
    ]
    assert cli.main(args) == 0
    cli_result = json.loads(capsys.readouterr().out)
    context = InstallationContext.select(str(athlete_home))
    with context.use():
        conn = context.open_database()
        mcp_result = tools.log_treadmill_speed(
            conn,
            activity_id=AID,
            binding=binding,
            speed_kmh=16.4,
            data_start_date=context.settings.data_start_date,
        )
        assert cli_result == mcp_result
        assert server.get_activity_laps(AID)["data"]["reported_console"]["reported_count"] == 6
        asyncio.run(
            server.server.call_tool(
                "log_treadmill_speed",
                {
                    "activity_id": AID,
                    "binding": binding,
                    "speed_kmh": 16.0,
                },
            )
        )
        assert (
            server.get_activity_laps(AID)["data"]["reported_console"]["applied_reports"][0][
                "speed_kmh"
            ]
            == 16
        )
        with pytest.raises(Exception):
            asyncio.run(
                server.server.call_tool(
                    "log_treadmill_speed",
                    {
                        "activity_id": AID,
                        "binding": binding,
                        "speed_kmh": True,
                    },
                )
            )
        assert (
            server.get_activity_laps(AID)["data"]["reported_console"]["applied_reports"][0][
                "speed_kmh"
            ]
            == 16
        )
        conn.close()
        server_result = server.clear_treadmill_speed(AID)
    assert server_result["data"]["cleared"] is True
    assert cli.main(args) == 0
    capsys.readouterr()
    assert cli.main(["--home", str(athlete_home), "clear-treadmill-speed", str(AID)]) == 0
    assert json.loads(capsys.readouterr().out) == server_result


def test_weekly_report_keeps_console_sources_and_historical_window(conn, fixture):
    from garmin_coach import athlete_changes
    from garmin_coach.coach import digest
    from garmin_coach.marts import features

    client = _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date="2026-07-06"
    )
    client.activities[0].update(activityId=AID + 1, startTimeLocal="2026-07-13 08:00:00")
    for endpoint in ("get_activity_splits", "get_activity_typed_splits"):
        client.evidence[endpoint]["activityId"] = AID + 1
    sync.backfill(client, conn, "2026-07-13", "2026-07-13")
    later_binding = activity.read(conn, AID + 1)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID + 1, binding=later_binding, speed_kmh=15, data_start_date="2026-07-06"
    )
    features.features(conn, data_start_date="2026-07-06", to_date="2026-07-12")
    out = digest.build_digest(conn, to_date="2026-07-12")
    assert [s["activity_id"] for s in out["weekly"]["interval_sessions"]] == [AID]
    entry = out["weekly"]["interval_sessions"][0]
    assert entry["reported_console"]["state"] == "matched"
    assert entry["reported_console"]["source"] == "athlete_reported_treadmill_console"
    assert entry["reported_console"]["applied_reports"][0]["speed_kmh"] == 16.4
    assert entry["outdoor_pace"] is None
    assert activity.read(conn, AID + 1)["reported_console"]["state"] == "matched"


def test_changed_activity_type_retains_stale_report_and_allows_explicit_clear(conn, fixture):
    from garmin_coach import athlete_changes

    client = _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    client.activities[0]["activityType"]["typeKey"] = "strength_training"
    sync.backfill(client, conn, DAY, DAY)
    report = activity.read(conn, AID)["reported_console"]
    assert report["state"] == "stale" and report["applied_reports"] == []
    assert report["reports"][0]["speed_kmh"] == 16.4
    assert athlete_changes.clear_treadmill_speed(conn, activity_id=AID, data_start_date=DAY).saved
    assert activity.read(conn, AID)["reported_console"] is None


def test_database_write_failure_preserves_report_and_captured_data(tmp_path, fixture):
    from garmin_coach import athlete_changes
    from garmin_coach.core import db

    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    before = activity.read(conn, AID)
    conn.execute("PRAGMA busy_timeout=1")
    other = db.connect(str(path))
    other.execute("BEGIN IMMEDIATE")
    result = athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=15, data_start_date=DAY
    )
    assert result.saved is False
    assert conn.in_transaction is False
    other.rollback()
    other.close()
    assert activity.read(conn, AID) == before
    conn.close()


def test_schema_upgrade_keeps_existing_capture_and_repeated_reports(conn, fixture):
    from garmin_coach import athlete_changes
    from garmin_coach.core import db

    _import(conn, fixture)
    captured = activity.read(conn, AID)["laps"]
    conn.execute("DROP TABLE treadmill_speed_reports")
    conn.execute("DROP TABLE treadmill_speed_sets")
    conn.commit()
    db.bootstrap(conn)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    before = activity.read(conn, AID)["reported_console"]
    db.bootstrap(conn)
    db.validate_schema(conn)
    assert activity.read(conn, AID)["reported_console"] == before
    assert activity.read(conn, AID)["laps"] == captured


@pytest.mark.parametrize("view", ["activity", "week"])
def test_read_keeps_one_generation_during_concurrent_capture_and_report_change(
    tmp_path, fixture, view
):
    from garmin_coach import athlete_changes
    from garmin_coach.core import db

    path = tmp_path / "athlete.db"
    conn = db.connect(str(path))
    db.bootstrap(conn)
    client = _import(conn, fixture)
    binding = activity.read(conn, AID)["work_execution"]["binding"]
    athlete_changes.log_treadmill_speed(
        conn, activity_id=AID, binding=binding, speed_kmh=16.4, data_start_date=DAY
    )
    writer = db.connect(str(path))
    outcomes = []

    def change_during_read(statement):
        if outcomes or "FROM treadmill_speed_sets" not in statement:
            return
        outcomes.append(None)  # Run the interleaving exactly once.
        first = next(
            g
            for g in client.evidence["get_activity_typed_splits"]["splits"]
            if g["type"] == "INTERVAL_ACTIVE"
        )
        first["duration"] = 226
        sync.backfill(client, writer, DAY, DAY)
        changed = activity.read(writer, AID)["work_execution"]["binding"]
        outcomes[0] = athlete_changes.log_treadmill_speed(
            writer, activity_id=AID, binding=changed, speed_kmh=15, data_start_date=DAY
        )

    conn.set_trace_callback(change_during_read)
    try:
        out = (
            activity.read(conn, AID)
            if view == "activity"
            else activity.weekly(conn, "2026-07-06")[0]
        )
    finally:
        conn.set_trace_callback(None)
    assert outcomes and outcomes[0].saved
    assert out["reported_console"]["state"] == "matched"
    assert out["reported_console"]["applied_reports"][0]["speed_kmh"] == 16.4
    assert conn.in_transaction is False
    latest = activity.read(conn, AID)
    assert latest["reported_console"]["state"] == "matched"
    assert latest["reported_console"]["applied_reports"][0]["speed_kmh"] == 15
    assert latest["work_execution"]["binding"] != binding
    writer.close()
    conn.close()


def test_saved_reads_preserve_caller_owned_transaction(conn, fixture):
    _import(conn, fixture)
    conn.execute("UPDATE activities SET name='uncommitted title' WHERE activity_id=?", (AID,))
    assert activity.read(conn, AID)["activity"]["name"] == "uncommitted title"
    assert conn.in_transaction is True
    assert activity.weekly(conn, "2026-07-06")[0]["activity_id"] == AID
    assert conn.in_transaction is True
    conn.rollback()
    assert activity.read(conn, AID)["activity"]["name"] != "uncommitted title"
