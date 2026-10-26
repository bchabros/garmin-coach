"""Program-state operations retain compatibility and recovery evidence."""

import json
import stat

import pytest


def test_build_checkpoint_preserves_legacy_recovery_and_extension_fields(tmp_path):
    from garmin_coach.program_receipt import ProgramReceipt

    path = tmp_path / "install-state.json"
    legacy = {"kind": "update", "phase": "preparing", "backup": "/old/backup"}
    path.write_text(json.dumps({"schema_version": 1, "operation": legacy, "extension": [1, 2]}))
    release = tmp_path / "releases" / "0.2.0-arm64"
    receipt = ProgramReceipt(tmp_path)
    receipt.record_build(release, {"version": "0.2.0"}, "creating", {})

    state = receipt.read()
    assert state["operation"] == legacy
    assert state["extension"] == [1, 2]
    assert state["builds"][str(release)]["stage"] == "creating"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "fields",
    [
        {"builds": []},
        {"builds": None},
        {"operation": []},
        {"selected_home": []},
        {"selected_clients": {}},
        {"selected_clients": [1]},
        {"scheduler": {"path": []}},
        {"pending_activation": {"launchers": []}},
        {"pending_activation": {"launchers": {"/launcher": []}}},
        {"pending_activation": {"launchers": {"/launcher": {"sha256": []}}}},
        {"owned_launchers": {"/launcher": []}},
        {"builds": {"/release": []}},
        {"release": {"private": "private marker"}},
        {"source_commit": {"private": "private marker"}},
    ],
)
def test_malformed_nested_state_is_refused_without_replacing_it(tmp_path, fields):
    from garmin_coach.program_receipt import ProgramReceipt

    path = tmp_path / "install-state.json"
    content = json.dumps({"schema_version": 1, "extension": "private marker", **fields})
    path.write_text(content)
    with pytest.raises(ValueError, match="Unsupported install receipt"):
        ProgramReceipt(tmp_path).read()
    assert path.read_text() == content


def test_scheduler_aliases_are_read_without_rewriting_saved_observations(tmp_path):
    from garmin_coach.program_receipt import ProgramReceipt

    path = tmp_path / "install-state.json"
    content = json.dumps(
        {
            "schema_version": 1,
            "scheduler": {
                "plist_path": "/temporary/job.plist",
                "status": "prepared",
                "choice": "enabled",
            },
        }
    )
    path.write_text(content)
    schedule = ProgramReceipt(tmp_path).read()["scheduler"]
    assert schedule["path"] == "/temporary/job.plist"
    assert schedule["activation_status"] == "prepared"
    assert schedule["choice"] == "enabled"
    assert path.read_text() == content


def test_versioned_journal_with_unknown_phase_requires_client_recovery(tmp_path):
    from garmin_coach.program_receipt import ProgramReceipt

    (tmp_path / "install-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": {
                    "schema_version": 1,
                    "kind": "update",
                    "backup": str(tmp_path / "backup"),
                },
            }
        )
    )
    operation = ProgramReceipt(tmp_path).interrupted_update()
    assert operation.may_have_changed_clients is True


@pytest.mark.parametrize("fields", [{"home": None}, {"previous": None}, {"previous": ""}])
def test_explicit_invalid_recovery_identity_is_not_treated_as_legacy_omission(tmp_path, fields):
    from garmin_coach.program_receipt import ProgramReceipt

    (tmp_path / "install-state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "operation": {
                    "schema_version": 1,
                    "kind": "update",
                    "phase": "preparing",
                    "backup": str(tmp_path / "backup"),
                    **fields,
                },
            }
        )
    )
    with pytest.raises(ValueError):
        ProgramReceipt(tmp_path).interrupted_update()
