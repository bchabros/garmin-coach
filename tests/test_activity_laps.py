"""Captured-lap import and saved-activity reads through the agreed public seams."""

from __future__ import annotations

import json

from garmin_coach.etl import sync
from tests.conftest import FakeGarminClient


class LapClient(FakeGarminClient):
    def __init__(self, evidence):
        super().__init__(
            activities=[
                {
                    "activityId": 900104,
                    "startTimeLocal": "2026-07-10 08:00:00",
                    "activityType": {"typeKey": evidence["activity_type"]},
                    "workoutId": 900,
                }
            ]
        )
        self.evidence = evidence

    def get_activity_splits(self, activity_id):
        value = self.evidence["get_activity_splits"]
        if isinstance(value, Exception):
            raise value
        return value

    def get_activity_typed_splits(self, activity_id):
        value = self.evidence["get_activity_typed_splits"]
        if isinstance(value, Exception):
            raise value
        return value


def test_backfill_captures_laps_and_typed_memberships_raw_first(conn, fixture):
    evidence = fixture("activity_laps_treadmill")
    sync.backfill(LapClient(evidence), conn, "2026-07-09", "2026-07-10")
    assert conn.execute("SELECT COUNT(*) FROM activity_laps").fetchone()[0] == 25
    assert conn.execute("SELECT COUNT(*) FROM activity_split_laps").fetchone()[0] == 25
    raw = conn.execute(
        "SELECT ref_date,payload FROM raw_payloads WHERE endpoint='get_activity_splits'"
    ).fetchone()
    assert raw[0] == "2026-07-10"
    assert json.loads(raw[1]) == evidence["get_activity_splits"]
    assert (
        conn.execute("SELECT dur_s FROM activity_laps WHERE lap_index=5").fetchone()[0] == 213.022
    )


def test_stored_activity_read_preserves_laps_without_requiring_a_summary(conn, fixture):
    from garmin_coach.coach import activity

    sync.backfill(LapClient(fixture("activity_laps_treadmill")), conn, "2026-07-10", "2026-07-10")
    out = activity.read(conn, 900104)
    assert len(out["laps"]) == 25
    assert out["summary"] is None
    assert out["unavailable_reason"] == "not_recomputed"
    assert out["captured_source"] == "Garmin get_activity_splits"
    assert out["treadmill_pace_caution"]
    assert activity.read(conn, 100)["unavailable_reason"] == "activity_not_found"


def test_treadmill_summary_counts_executed_groups_instead_of_laps(conn, fixture):
    from garmin_coach.coach import activity
    from garmin_coach.marts import features

    sync.backfill(LapClient(fixture("activity_laps_treadmill")), conn, "2026-07-10", "2026-07-10")
    features.features(conn, data_start_date="2026-07-10", to_date="2026-07-10")
    summary = activity.read(conn, 900104)["summary"]
    assert summary["work_count"] == 6
    assert summary["work_repetitions"][0]["lap_indices"] == [5, 6]
    assert summary["work_repetitions"][0]["dur_s"] == 225
    assert summary["hr_change_first_to_last_bpm"] == {"average": 10, "maximum": 11}
    assert summary["work_recovery_difference"]["valid_pair_count"] == 6
    assert summary["work_recovery_difference"]["mean_bpm"] == 19.0
    assert summary["work_recovery_difference"]["pairs"][0]["difference_bpm"] == 19
    assert summary["work_recovery_difference"]["pairs"][0]["recovery_dur_s"] == 120
    assert summary["outdoor_pace"] is None


def test_activity_adapters_share_saved_read_and_freshness(conn, fixture, athlete_home, capsys):
    from garmin_coach import cli
    from garmin_coach.core import db
    from garmin_coach.mcp import tools
    from garmin_coach.runtime import InstallationContext

    path = athlete_home / "data" / "garmin.db"
    path.parent.mkdir()
    disk = db.connect(str(path))
    db.bootstrap(disk)
    sync.backfill(LapClient(fixture("activity_laps_treadmill")), disk, "2026-07-10", "2026-07-10")
    disk.close()
    context = InstallationContext.select(str(athlete_home))
    with context.use():
        check = context.open_database()
        expected = tools.get_activity_laps(check, 900104)
        assert tools.get_recent_activities(check)["data"][0]["has_laps"] is True
        check.close()
    assert cli.main(["--home", str(athlete_home), "activity-laps", "900104"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert expected["freshness"]["today_included"] is False


def test_digest_interval_entries_stay_in_the_latest_complete_week(conn, fixture):
    from garmin_coach.coach import digest
    from garmin_coach.core import db
    from garmin_coach.marts import features

    client = LapClient(fixture("activity_laps_treadmill"))
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    client.activities[0] = {
        **client.activities[0],
        "activityId": 900105,
        "startTimeLocal": "2026-07-13 08:00:00",
    }
    for endpoint in ("get_activity_splits", "get_activity_typed_splits"):
        client.evidence[endpoint]["activityId"] = 900105
    sync.backfill(client, conn, "2026-07-13", "2026-07-13")
    features.features(conn, data_start_date="2026-07-06", to_date="2026-07-13")
    out = digest.build_digest(conn, to_date="2026-07-13")
    assert out["weekly"]["week_start"] == "2026-07-06"
    assert [s["activity_id"] for s in out["weekly"]["interval_sessions"]] == [900104]
    assert out["weekly"]["interval_sessions"][0]["work_count"] == 6
    assert "work_repetitions" not in out["weekly"]["interval_sessions"][0]
    db.upsert_weekly(conn, {"week_start": "2026-07-13"})
    assert digest.build_digest(conn, to_date="2026-07-12")["weekly"]["week_start"] == "2026-07-06"


def _rebuild(conn):
    from garmin_coach.marts import features

    features.features(conn, data_start_date="2026-07-10", to_date="2026-07-10")


def test_outdoor_groups_preserve_three_multilap_repetitions(conn, fixture):
    import pytest
    from garmin_coach.coach import activity

    sync.backfill(
        LapClient(fixture("activity_laps_structured_outdoor")), conn, "2026-07-10", "2026-07-10"
    )
    _rebuild(conn)
    summary = activity.read(conn, 900104)["summary"]
    assert summary["work_count"] == 3
    assert summary["work_repetitions"][0]["lap_indices"] == [6, 7, 8]
    assert summary["outdoor_pace"]["fastest_s_per_km"] == pytest.approx(237.5636667)
    assert summary["outdoor_pace"]["spread_s_per_km"] == pytest.approx(1.6916667)
    assert summary["hr_change_first_to_last_bpm"] == {"average": 8, "maximum": 3}


def test_unstructured_and_station_laps_do_not_become_running_repetitions(conn, fixture):
    from garmin_coach.coach import activity

    for sample in ("unstructured_run", "stations_lap_ended"):
        sync.backfill(
            LapClient(fixture("activity_laps_" + sample)), conn, "2026-07-10", "2026-07-10"
        )
        _rebuild(conn)
        out = activity.read(conn, 900104)
        assert out["laps"]
        assert out["summary"] is None
        assert out["unavailable_reason"] == "execution_roles_or_grouping_unavailable"


def test_reimport_invalidates_summary_and_replaces_shrunk_then_empty_capture(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    client = LapClient(evidence)
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    assert activity.read(conn, 900104)["summary"]["work_count"] == 6
    evidence["get_activity_splits"]["lapDTOs"] = evidence["get_activity_splits"]["lapDTOs"][:2]
    evidence["get_activity_typed_splits"]["splits"] = []
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    assert len(activity.read(conn, 900104)["laps"]) == 2
    assert activity.read(conn, 900104)["unavailable_reason"] == "not_recomputed"
    _rebuild(conn)
    assert activity.read(conn, 900104)["summary"] is None
    evidence["get_activity_splits"]["lapDTOs"] = []
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    assert activity.read(conn, 900104)["laps"] == []
    assert activity.read(conn, 900104)["unavailable_reason"] == "no_captured_laps"


def test_failed_or_absent_second_endpoint_preserves_previous_complete_capture(conn, fixture):
    from garmin_coach.coach import activity

    for failure in (None, RuntimeError("unavailable"), {"unexpected": []}):
        evidence = fixture("activity_laps_treadmill")
        client = LapClient(evidence)
        sync.backfill(client, conn, "2026-07-10", "2026-07-10")
        _rebuild(conn)
        before = activity.read(conn, 900104)
        evidence["get_activity_splits"]["lapDTOs"] = []
        evidence["get_activity_typed_splits"] = failure
        result = sync.refresh_day(client, conn, "2026-07-10")
        assert activity.read(conn, 900104) == before
        assert bool(result.enrichment_misses) is (failure is not None)
        assert conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0] == 0


def test_onboarding_stops_before_checkpoint_when_lap_enrichment_is_rate_limited(conn, fixture):
    import pytest

    evidence = fixture("activity_laps_treadmill")
    evidence["get_activity_splits"] = TimeoutError("rate limit stand-in")
    checkpoints = []
    with pytest.raises(TimeoutError):
        sync.backfill(
            LapClient(evidence),
            conn,
            "2026-07-10",
            "2026-07-10",
            on_day_complete=checkpoints.append,
            stop_on_enrichment_error=lambda exc: isinstance(exc, TimeoutError),
        )
    assert checkpoints == []


def test_mart_failure_rolls_back_interval_generation_with_other_marts(conn, fixture):
    import pytest
    import sqlite3
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    before = activity.read(conn, 900104)
    conn.execute(
        "CREATE TRIGGER late_failure BEFORE INSERT ON athlete_status BEGIN SELECT RAISE(ABORT, 'late failure'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="late failure"):
        _rebuild(conn)
    assert activity.read(conn, 900104) == before


def test_response_for_another_activity_cannot_replace_saved_laps(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    client = LapClient(evidence)
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    evidence["get_activity_splits"] = {"activityId": 999, "lapDTOs": []}
    result = sync.refresh_day(client, conn, "2026-07-10")
    assert len(activity.read(conn, 900104)["laps"]) == 25
    assert result.enrichment_misses


def test_hr_comparisons_keep_missing_inputs_and_negative_differences(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    groups = evidence["get_activity_typed_splits"]["splits"]
    work = [g for g in groups if g["type"] == "INTERVAL_ACTIVE"]
    recovery = [g for g in groups if g["type"] == "INTERVAL_RECOVERY"]
    work[0]["averageHR"] = None
    work[0]["maxHR"] = 150
    recovery[0]["averageHR"] = 160
    recovery[1]["averageHR"] = None
    sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    summary = activity.read(conn, 900104)["summary"]
    assert summary["hr_change_first_to_last_bpm"] == {"average": None, "maximum": 38}
    differences = summary["work_recovery_difference"]
    assert differences["pairs"][0]["difference_bpm"] == -10
    assert differences["pairs"][1]["difference_bpm"] is None
    assert differences["valid_pair_count"] == 5
    assert differences["total_work_count"] == 6
    assert differences["mean_bpm"] == 13.4


def test_final_work_without_recovery_never_pairs_with_cooldown(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    evidence["get_activity_splits"]["lapDTOs"] = [
        r for r in evidence["get_activity_splits"]["lapDTOs"] if r["lapIndex"] != 22
    ]
    evidence["get_activity_typed_splits"]["splits"] = [
        g for g in evidence["get_activity_typed_splits"]["splits"] if g.get("lapIndexes") != [22]
    ]
    sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    summary = activity.read(conn, 900104)["summary"]
    pairs = summary["work_recovery_difference"]
    assert summary["work_count"] == 6
    assert pairs["valid_pair_count"] == 5
    assert pairs["pairs"][-1]["difference_bpm"] is None
    assert pairs["pairs"][-1]["recovery_split_idx"] is None


def test_one_observed_work_does_not_borrow_planned_repetition_count(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    wanted = [1, 2, 3, 4, 5, 6, 23, 24, 25]
    evidence["get_activity_splits"]["lapDTOs"] = [
        r for r in evidence["get_activity_splits"]["lapDTOs"] if r["lapIndex"] in wanted
    ]
    evidence["get_activity_typed_splits"]["splits"] = [
        g
        for g in evidence["get_activity_typed_splits"]["splits"]
        if g.get("lapIndexes") in ([1, 2, 3, 4], [5, 6], [23, 24, 25])
    ]
    sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    summary = activity.read(conn, 900104)["summary"]
    assert summary["work_count"] == 1
    assert summary["hr_change_first_to_last_bpm"] == {"average": None, "maximum": None}
    assert summary["work_recovery_difference"]["valid_pair_count"] == 0
    assert summary["work_recovery_difference"]["mean_bpm"] is None


def test_ambiguous_execution_keeps_laps_readable_without_summary(conn, fixture):
    from garmin_coach.coach import activity

    mutations = (
        "duplicate_lap",
        "missing_member",
        "overlapping_groups",
        "unknown_role",
        "missing_step",
        "mixed_steps",
        "missing_time",
    )
    for mutation in mutations:
        evidence = fixture("activity_laps_treadmill")
        captured = evidence["get_activity_splits"]["lapDTOs"]
        work = next(
            g
            for g in evidence["get_activity_typed_splits"]["splits"]
            if g["type"] == "INTERVAL_ACTIVE"
        )
        if mutation == "duplicate_lap":
            captured[5]["lapIndex"] = 5
        elif mutation == "missing_member":
            work["lapIndexes"] = [999]
        elif mutation == "overlapping_groups":
            work["lapIndexes"] = [5, 6, 7]
        elif mutation == "unknown_role":
            work["type"] = "INTERVAL_UNKNOWN"
        elif mutation == "missing_step":
            captured[4]["wktStepIndex"] = None
        elif mutation == "mixed_steps":
            captured[5]["wktStepIndex"] = 2
        else:
            captured[4]["startTimeGMT"] = None
        sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
        _rebuild(conn)
        out = activity.read(conn, 900104)
        assert len(out["laps"]) == 25, mutation
        assert out["summary"] is None, mutation
        assert out["unavailable_reason"] == "execution_roles_or_grouping_unavailable", mutation


def test_incremental_recheck_and_same_day_capture_preserve_watermark_policy(conn, fixture):
    import datetime as dt
    from garmin_coach.coach import activity
    from garmin_coach.core import db

    evidence = fixture("activity_laps_treadmill")
    client = LapClient(evidence)
    result = sync.sync_incremental(
        client, conn, data_start_date="2026-07-10", to_date="2026-07-12", retry_base_seconds=0
    )
    assert not result.enrichment_misses
    assert len(activity.read(conn, 900104)["laps"]) == 25
    before = conn.execute("SELECT * FROM sync_state ORDER BY stream").fetchall()
    date = dt.date.today().isoformat()
    client.activities[0]["startTimeLocal"] = date + " 08:00:00"
    sync.refresh_day(client, conn, date)
    assert activity.read(conn, 900104)["partial_today"] is True
    assert conn.execute("SELECT * FROM sync_state ORDER BY stream").fetchall() == before
    assert db.latest_mart_date(conn) is None


def test_existing_schema_upgrade_preserves_saved_activity_and_is_idempotent(conn):
    from garmin_coach.core import db

    db.upsert_activity(
        conn,
        {
            "activity_id": 123,
            "start_local": "2026-07-10 08:00:00",
            "date": "2026-07-10",
            "name": "preserved",
            "gtype": "running",
        },
    )
    for table in (
        "activity_split_laps",
        "activity_splits",
        "activity_laps",
        "activity_interval_summaries",
    ):
        conn.execute("DROP TABLE " + table)
    conn.commit()
    db.bootstrap(conn)
    db.bootstrap(conn)
    db.validate_schema(conn)
    assert (
        conn.execute("SELECT name FROM activities WHERE activity_id=123").fetchone()[0]
        == "preserved"
    )
    assert conn.execute("SELECT COUNT(*) FROM activity_laps").fetchone()[0] == 0


def test_malformed_individual_fields_remain_null_scalar_observations(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_treadmill")
    evidence["get_activity_splits"]["lapDTOs"] = [
        None,
        {"lapIndex": [], "duration": {}, "intensityType": ["ACTIVE"]},
    ]
    evidence["get_activity_typed_splits"]["splits"] = [None]
    sync.backfill(LapClient(evidence), conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    out = activity.read(conn, 900104)
    assert len(out["laps"]) == 2
    assert all(row["lap_index"] is None and row["dur_s"] is None for row in out["laps"])
    assert out["summary"] is None


def test_changed_activity_type_cannot_reuse_old_summary_after_fetch_failure(conn, fixture):
    from garmin_coach.coach import activity

    evidence = fixture("activity_laps_structured_outdoor")
    client = LapClient(evidence)
    sync.backfill(client, conn, "2026-07-10", "2026-07-10")
    _rebuild(conn)
    assert activity.read(conn, 900104)["summary"]["outdoor_pace"]
    client.activities[0]["activityType"]["typeKey"] = "treadmill_running"
    evidence["get_activity_splits"] = None
    sync.refresh_day(client, conn, "2026-07-10")
    assert activity.read(conn, 900104)["summary"] is None
    assert len(activity.read(conn, 900104)["laps"]) == 22
