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
