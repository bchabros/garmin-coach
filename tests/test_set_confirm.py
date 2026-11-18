"""Approval-bound account correction and independent partial outcomes."""

import copy

import pytest

from garmin_coach import set_confirm, set_corrections
from garmin_coach.core import db


class Account:
    def __init__(self, payload):
        self.payload = copy.deepcopy(payload)
        self.writes = []

    def get_activity_exercise_sets(self, aid):
        return copy.deepcopy(self.payload)

    def set_activity_exercise_sets(self, aid, payload):
        self.writes.append(copy.deepcopy(payload))
        self.payload = copy.deepcopy(payload)


def prepare(conn, fixture):
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
    account = Account(fixture("exercise_sets_strength"))
    rows = [{"name": "bench_press", "weight_kg": 65}, {"name": "deadlift"}, {"name": "bench_press"}]
    proposal = set_corrections.preview(
        conn, 900001, rows, lambda: account.get_activity_exercise_sets(900001)
    )
    return account, rows, proposal


def test_confirm_verifies_and_saves_the_reviewed_account_sets(conn, fixture):
    account, rows, proposal = prepare(conn, fixture)
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    assert result["account_write"] == "sent"
    assert result["verified"] is True
    assert result["saved"] is True
    assert set_corrections.saved(conn, 900001)["captured"][0]["max_weight"] == 65000


def test_repetition_only_confirm_retains_candidates_and_subsecond_source_timing(conn, fixture):
    account, _, _ = prepare(conn, fixture)
    account.payload = fixture("exercise_sets_candidates")
    rows = [{"captured_index": 0, "reps": 9}, {"captured_index": 1}, {"captured_index": 2}]
    proposal = set_corrections.preview(
        conn, 900001, rows, lambda: account.get_activity_exercise_sets(900001)
    )
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    expected = fixture("exercise_sets_candidates")
    expected["exerciseSets"][0]["repetitionCount"] = 9
    assert account.writes == [expected]
    assert result["verified"] is True
    assert result["saved"] is True
    saved = set_corrections.saved(conn, 900001)["captured"]
    assert len(saved) == 3
    assert saved[0]["duration_s"] == 60.125
    assert saved[0]["subcategory"] == "LUNGE"


def test_add_and_remove_verify_observed_server_enrichment_and_saved_counts(conn, fixture):
    account, _, _ = prepare(conn, fixture)
    account.payload = fixture("exercise_sets_candidates")
    rows = [
        {"captured_index": 0},
        {"captured_index": 1},
        {"captured_index": 2},
        {"captured_index": None, "name": "lunge", "reps": 1, "weight_kg": 0},
    ]
    received = iter([fixture("exercise_sets_added"), fixture("exercise_sets_candidates")])

    def accepted(aid, payload):
        account.writes.append(copy.deepcopy(payload))
        account.payload = next(received)

    account.set_activity_exercise_sets = accepted
    for intended, expected_count in [(rows, 4), (rows[:3], 3)]:
        preview = set_corrections.preview(
            conn, 900001, intended, lambda: account.get_activity_exercise_sets(900001)
        )
        result = set_confirm.confirm(
            conn,
            900001,
            intended,
            preview["approval"],
            lambda: account,
            data_start_date="2026-06-08",
        )
        assert result["verified"] is True
        assert result["saved"] is True
        assert result["recomputed"] is True
        assert len(set_corrections.saved(conn, 900001)["captured"]) == expected_count
    assert account.writes == [
        fixture("exercise_sets_added_request"),
        fixture("exercise_sets_candidates"),
    ]


def test_a_changed_rest_refuses_previously_approved_deletion_before_put(conn, fixture):
    account, _, _ = prepare(conn, fixture)
    account.payload = fixture("exercise_sets_added")
    rows = [{"captured_index": 0}, {"captured_index": 1}, {"captured_index": 2}]
    preview = set_corrections.preview(conn, 900001, rows, lambda: account.payload)
    account.payload["exerciseSets"][-1]["duration"] = 20
    with pytest.raises(ValueError, match="stale"):
        set_confirm.confirm(
            conn,
            900001,
            rows,
            preview["approval"],
            lambda: account,
            data_start_date="2026-06-08",
        )
    assert account.writes == []


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_sanctioned_adapters_preview_and_confirm_added_work(athlete_home, fixture, capsys, surface):
    import json
    from garmin_coach import cli
    from garmin_coach.mcp import server
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home, import_factory=lambda _: account)
    with context.open_database(create=True) as conn:
        db.bootstrap(conn)
        account, _, _ = prepare(conn, fixture)
    account.payload = fixture("exercise_sets_candidates")
    rows = [
        {"captured_index": 0},
        {"captured_index": 1},
        {"captured_index": 2},
        {"captured_index": None, "name": "lunge", "reps": 1, "weight_kg": 0},
    ]
    if surface == "mcp":
        with context.use():
            proposal = server.preview_set_correction(900001, rows)["data"]
            result = server.confirm_set_correction(900001, rows, proposal["approval"])["data"]
    else:
        base = ["--home", str(athlete_home)]
        assert (
            cli.main(
                base + ["activity-sets", "900001", "--rows-json", json.dumps(rows)],
                context_factory=lambda _: context,
            )
            == 0
        )
        proposal = json.loads(capsys.readouterr().out)
        assert (
            cli.main(
                base
                + [
                    "confirm-set-correction",
                    "900001",
                    "--rows-json",
                    json.dumps(rows),
                    "--approval",
                    proposal["approval"],
                    "--confirm",
                ],
                context_factory=lambda _: context,
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
    assert result["verified"] is True
    assert result["saved"] is True
    assert account.writes == [fixture("exercise_sets_added_request")]
    with context.open_existing_database() as conn:
        assert len(set_corrections.saved(conn, 900001)["captured"]) == 4


def _count_proposal_with_active_overlay(conn, fixture):
    from garmin_coach import athlete_changes, set_history

    account, _, _ = prepare(conn, fixture)
    account.payload = fixture("exercise_sets_candidates")
    set_history.persist(conn, 900001, account.payload, checked_on="2026-06-13")
    conn.commit()
    athlete_changes.log_activity_sets(
        conn, activity_id=900001, stations=["LUNGE"], data_start_date="2026-06-08"
    )
    rows = [
        {"captured_index": 0},
        {"captured_index": 1},
        {"captured_index": 2},
        {"captured_index": None, "name": "lunge", "reps": 1, "weight_kg": 0},
    ]
    proposal = set_corrections.preview(conn, 900001, rows, lambda: account.payload)
    return account, rows, proposal


def test_unavailable_readback_keeps_active_correction_and_prior_source(conn, fixture):
    from garmin_coach import set_history

    account, rows, proposal = _count_proposal_with_active_overlay(conn, fixture)
    before = set_history.read(conn, 900001)

    def read(aid):
        if account.writes:
            raise TimeoutError("synthetic read-back unavailable")
        return copy.deepcopy(account.payload)

    account.get_activity_exercise_sets = read
    result = set_confirm.confirm(
        conn,
        900001,
        rows,
        proposal["approval"],
        lambda: account,
        data_start_date="2026-06-08",
    )
    assert result["account_write"] == "sent"
    assert result["verified"] is False
    assert result["saved"] is False
    assert result["overlay_retired"] is False
    assert result["recomputed"] is None
    assert "read-back unavailable" in result["error"]
    assert len(account.writes) == 1
    assert set_history.read(conn, 900001) == before


@pytest.mark.parametrize("failure", ["source_save", "after_overlay_retirement"])
def test_failed_capture_or_archive_rolls_back_all_local_retirement(conn, fixture, failure):
    from garmin_coach import set_history

    account, rows, proposal = _count_proposal_with_active_overlay(conn, fixture)
    before = set_history.read(conn, 900001)
    raw_before = conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0]
    table = (
        "exercise_set_observations" if failure == "source_save" else "exercise_correction_history"
    )
    condition = (
        "" if failure == "source_save" else "WHEN NEW.reason='verified_working_set_correction'"
    )
    conn.execute(
        f"CREATE TRIGGER fail_operator_save BEFORE INSERT ON {table} {condition} BEGIN SELECT RAISE(ABORT,'synthetic local persistence failure'); END"
    )
    conn.commit()
    result = set_confirm.confirm(
        conn,
        900001,
        rows,
        proposal["approval"],
        lambda: account,
        data_start_date="2026-06-08",
    )
    assert result["account_write"] == "sent"
    assert result["verified"] is True
    assert result["saved"] is False
    assert result["overlay_retired"] is False
    assert result["recomputed"] is None
    assert "local save failed" in result["error"]
    assert len(account.writes) == 1
    assert set_history.read(conn, 900001) == before
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == raw_before


def test_failed_derived_refresh_keeps_verified_source_and_retirement_history(conn, fixture):
    from garmin_coach import set_history

    account, rows, proposal = _count_proposal_with_active_overlay(conn, fixture)
    mart_before = conn.execute("SELECT * FROM daily_metrics ORDER BY date").fetchall()
    assert mart_before
    conn.execute(
        "CREATE TRIGGER fail_mart_rebuild BEFORE INSERT ON daily_metrics BEGIN SELECT RAISE(ABORT,'synthetic mart failure'); END"
    )
    conn.commit()
    result = set_confirm.confirm(
        conn,
        900001,
        rows,
        proposal["approval"],
        lambda: account,
        data_start_date="2026-06-08",
    )
    assert result["account_write"] == "sent"
    assert result["verified"] is True
    assert result["saved"] is True
    assert result["overlay_retired"] is True
    assert result["recomputed"] is False
    assert "features offline" in result["recovery"]
    state = set_history.read(conn, 900001)
    assert state["manual"] == []
    assert len(state["captured"]) == 4
    assert [row["reason"] for row in state["history"]] == [
        "verified_account_correction",
        "verified_working_set_correction",
    ]
    assert conn.execute("SELECT * FROM daily_metrics ORDER BY date").fetchall() == mart_before
    assert len(account.writes) == 1


def test_stale_account_refuses_before_any_write(conn, fixture):
    account, rows, proposal = prepare(conn, fixture)
    account.payload["exerciseSets"][0]["weight"] = 80000
    with pytest.raises(ValueError, match="stale"):
        set_confirm.confirm(
            conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
        )
    assert account.writes == []


def test_uncertain_write_is_reported_without_retry(conn, fixture):
    account, rows, proposal = prepare(conn, fixture)

    def uncertain(aid, payload):
        account.writes.append(payload)
        raise TimeoutError("response lost")

    account.set_activity_exercise_sets = uncertain
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    assert result["account_write"] == "unknown"
    assert result["saved"] is False
    assert len(account.writes) == 1


def test_mismatching_readback_keeps_manual_overlay(conn, fixture):
    from garmin_coach import athlete_changes

    account, rows, _ = prepare(conn, fixture)
    athlete_changes.log_activity_sets(
        conn, activity_id=900001, stations=["BENCH_PRESS"], data_start_date="2026-06-08"
    )
    proposal = set_corrections.preview(
        conn, 900001, rows, lambda: account.get_activity_exercise_sets(900001)
    )

    def mismatch(aid, payload):
        account.payload = copy.deepcopy(payload)
        account.payload["exerciseSets"][0]["weight"] = 12000

    account.set_activity_exercise_sets = mismatch
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    assert result["verified"] is False
    assert result["saved"] is True
    assert result["overlay_retired"] is False
    assert set_corrections.saved(conn, 900001)["manual"]


def test_local_edit_during_account_read_refuses_before_put(conn, fixture):
    account, rows, proposal = prepare(conn, fixture)

    def edit_during_get(aid):
        db.replace_manual_activity_sets(
            conn, aid, [{"activity_id": aid, "set_idx": 0, "subcategory": "NEW_CORRECTION"}]
        )
        conn.commit()
        return copy.deepcopy(account.payload)

    account.get_activity_exercise_sets = edit_during_get
    with pytest.raises(ValueError, match="stale"):
        set_confirm.confirm(
            conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
        )
    assert account.writes == []


def test_direct_confirm_retains_reviewed_working_set_history_without_overlay(conn, fixture):
    from garmin_coach import set_history

    account, rows, proposal = prepare(conn, fixture)
    set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    history = set_history.read(conn, 900001)["history"]
    assert history[-1]["reason"] == "verified_working_set_correction"
    assert history[-1]["declaration"] == rows


def test_server_enrichment_and_numeric_representation_do_not_block_overlay_retirement(
    conn, fixture
):
    from garmin_coach import athlete_changes

    account, rows, _ = prepare(conn, fixture)
    athlete_changes.log_activity_sets(
        conn, activity_id=900001, stations=["BENCH_PRESS"], data_start_date="2026-06-08"
    )
    account.payload["exerciseSets"][0]["setId"] = None
    account.payload["exerciseSets"][0]["messageIndex"] = 0
    rows[0]["weight_kg"] = 32.2
    proposal = set_corrections.preview(
        conn, 900001, rows, lambda: account.get_activity_exercise_sets(900001)
    )

    def enriched(aid, payload):
        account.writes.append(copy.deepcopy(payload))
        account.payload = copy.deepcopy(payload)
        account.payload["serverMetadata"] = {"revision": 2}
        first = account.payload["exerciseSets"][0]
        first["exercises"][0]["probability"] = 81
        first["setId"] = "new-server-id"
        first["duration"] = 48
        first["weight"] = 32200.0
        first["startTime"] = "2026-06-12T17:00:00.000"

    account.set_activity_exercise_sets = enriched
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    assert result["verified"] is True
    assert result["overlay_retired"] is True
    assert result["saved"] is True
    assert len(account.writes) == 1
    saved = set_corrections.saved(conn, 900001)
    assert saved["manual"] == []
    assert saved["captured"][0]["max_weight"] == 32200


@pytest.mark.parametrize(
    "changed",
    [
        "identity",
        "category",
        "reps",
        "weight",
        "duration",
        "start",
        "rest",
        "count",
        "order",
        "existing_id",
    ],
)
def test_meaningful_readback_difference_retains_overlay(conn, fixture, changed):
    from garmin_coach import athlete_changes

    account, rows, _ = prepare(conn, fixture)
    account.payload["exerciseSets"][0]["setId"] = "captured-id"
    athlete_changes.log_activity_sets(
        conn, activity_id=900001, stations=["BENCH_PRESS"], data_start_date="2026-06-08"
    )
    proposal = set_corrections.preview(
        conn, 900001, rows, lambda: account.get_activity_exercise_sets(900001)
    )

    def mismatch(aid, payload):
        account.payload = copy.deepcopy(payload)
        entries = account.payload["exerciseSets"]
        first = entries[0]
        if changed == "identity":
            first["exercises"][0]["name"] = "BARBELL_DEADLIFT"
        elif changed == "category":
            first["exercises"][0]["category"] = "DEADLIFT"
        elif changed == "reps":
            first["repetitionCount"] = 9
        elif changed == "weight":
            first["weight"] = 64000
        elif changed == "duration":
            first["duration"] = 49
        elif changed == "start":
            first["startTime"] = "2026-06-12T17:00:01.0"
        elif changed == "rest":
            entries[1]["duration"] = 91
        elif changed == "count":
            entries.pop()
        elif changed == "order":
            entries[0], entries[2] = entries[2], entries[0]
        elif changed == "existing_id":
            first["setId"] = "other-id"

    account.set_activity_exercise_sets = mismatch
    result = set_confirm.confirm(
        conn, 900001, rows, proposal["approval"], lambda: account, data_start_date="2026-06-08"
    )
    assert result["verified"] is False
    assert result["saved"] is True
    assert result["overlay_retired"] is False
    assert set_corrections.saved(conn, 900001)["manual"]


def test_explicit_account_rejection_is_distinct_from_uncertain_write(conn, fixture):
    from garmin_coach.etl.client import GarminTransport, GarminConnectConnectionError

    account, rows, proposal = prepare(conn, fixture)

    def rejected(aid, payload):
        account.writes.append(copy.deepcopy(payload))
        raise GarminConnectConnectionError("API Error 400 - Invalid Sub-Category Passed")

    account.set_activity_exercise_sets = rejected
    result = set_confirm.confirm(
        conn,
        900001,
        rows,
        proposal["approval"],
        lambda: GarminTransport(account),
        data_start_date="2026-06-08",
    )
    assert result["account_write"] == "rejected"
    assert result["http_status"] == 400
    assert result["verified"] is False
    assert result["saved"] is False
    assert len(account.writes) == 1
    assert "uncertain" not in result["error"].lower()


@pytest.mark.parametrize("rows", ["{", "{}", '[{"name":"made_up"}]'])
def test_cli_invalid_confirmation_is_a_clean_refusal(athlete_home, capsys, rows):
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
                "confirm-set-correction",
                "900001",
                "--rows-json",
                rows,
                "--approval",
                "a" * 64,
                "--confirm",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert "garmin-coach:" in output.err
    assert "Traceback" not in output.err


def test_cli_stale_confirmation_is_a_clean_refusal_without_put(athlete_home, fixture, capsys):
    import json
    from garmin_coach import cli
    from garmin_coach.runtime import InstallationContext

    context = InstallationContext.select(athlete_home, import_factory=lambda _: account)
    with context.open_database(create=True) as conn:
        db.bootstrap(conn)
        account, rows, proposal = prepare(conn, fixture)
    account.payload["exerciseSets"][0]["weight"] = 80000
    assert (
        cli.main(
            [
                "--home",
                str(athlete_home),
                "confirm-set-correction",
                "900001",
                "--rows-json",
                json.dumps(rows),
                "--approval",
                proposal["approval"],
                "--confirm",
            ],
            context_factory=lambda _: context,
        )
        == 2
    )
    assert account.writes == []
    output = capsys.readouterr()
    assert "stale" in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("approval", ["é" * 64, "g" * 64])
def test_malformed_approval_refuses_before_account_connection(conn, fixture, approval):
    _, rows, _ = prepare(conn, fixture)

    def forbidden():
        pytest.fail("malformed approval must refuse before account connection")

    with pytest.raises(ValueError, match="fingerprint"):
        set_confirm.confirm(conn, 900001, rows, approval, forbidden, data_start_date="2026-06-08")
