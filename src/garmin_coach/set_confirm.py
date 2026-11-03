"""Single-attempt recorded-set account writes bound to an exact reviewed proposal."""

from __future__ import annotations

import copy
import datetime as dt
import hmac
import sqlite3
from collections.abc import Callable
from typing import Any, Protocol

from . import set_corrections, set_history


class SetEditor(Protocol):
    """Explicit source read and single full-payload write transport."""

    def get_activity_exercise_sets(self, activity_id: int) -> Any:
        """Read complete recorded-set account evidence."""
        ...

    def set_activity_exercise_sets(self, activity_id: int, payload: dict[str, Any]) -> Any:
        """Send one reviewed complete replacement without automatic retry."""
        ...


def _write_once(conn: sqlite3.Connection, proposal: dict[str, Any], editor: SetEditor) -> bool:
    # Lock local facts across the final check and PUT. Never retry an uncertain PUT.
    conn.execute("BEGIN IMMEDIATE")
    try:
        local = set_corrections.saved(conn, proposal["activity_id"])
        if set_corrections.fingerprint(local) != set_corrections.fingerprint(proposal["local"]):
            raise ValueError("stale local correction; preview and approve again")
        try:
            editor.set_activity_exercise_sets(
                proposal["activity_id"], copy.deepcopy(proposal["proposed"])
            )
            return True
        except Exception:
            return False
    finally:
        conn.rollback()


def _save(
    conn: sqlite3.Connection, proposal: dict[str, Any], received: dict[str, Any], verified: bool
) -> tuple[bool, bool]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        aid = proposal["activity_id"]
        current = set_corrections.saved(conn, aid)
        unchanged = set_corrections.fingerprint(current) == set_corrections.fingerprint(
            proposal["local"]
        )
        set_history.persist(conn, aid, received, checked_on=dt.date.today().isoformat())
        retired = bool(verified and unchanged and current["manual"])
        if retired:
            set_history.archive(conn, aid, "verified_account_correction")
            conn.execute("DELETE FROM manual_activity_sets WHERE activity_id=?", (aid,))
            conn.execute("DELETE FROM exercise_correction_state WHERE activity_id=?", (aid,))
        if verified:
            import json

            conn.execute(
                "INSERT INTO exercise_correction_history(activity_id,declaration_json,baseline_sha,reason) VALUES(?,?,?,?)",
                (
                    aid,
                    json.dumps(proposal["rows"]),
                    set_corrections.fingerprint(proposal["local"]["captured"]),
                    "verified_working_set_correction",
                ),
            )
        conn.commit()
        return retired, unchanged
    except BaseException:
        conn.rollback()
        raise


def _verify_and_save(
    conn: sqlite3.Connection,
    proposal: dict[str, Any],
    editor: SetEditor,
    result: dict[str, Any],
    data_start_date: str,
) -> dict[str, Any]:
    try:
        received = set_corrections.account_payload(
            proposal["activity_id"], editor.get_activity_exercise_sets(proposal["activity_id"])
        )
        result["verified"] = set_corrections.fingerprint(received) == set_corrections.fingerprint(
            proposal["proposed"]
        )
    except Exception:
        return {
            **result,
            "error": "Account changed; read-back unavailable or invalid. Active correction retained.",
        }
    try:
        result["overlay_retired"], unchanged = _save(conn, proposal, received, result["verified"])
        result["saved"] = True
    except Exception:
        return {
            **result,
            "error": "Account changed; local save failed. Read fresh source before recovery.",
        }
    result.update(set_history.rebuild(conn, proposal["local"]["date"], data_start_date))
    if not result["verified"] or not unchanged:
        result["error"] = (
            "Read-back mismatch or concurrent local correction; active overlay retained. Preview again."
        )
    return result


def confirm(
    conn: sqlite3.Connection,
    activity_id: int,
    rows: list[dict[str, Any]],
    approval: str,
    connect: Callable[[], SetEditor],
    *,
    data_start_date: str,
) -> dict[str, Any]:
    """Recheck, write once, verify and save independently reportable outcomes.

    Unsupported add/delete timing remains refused by the shared preview contract.
    Failed or uncertain account writes are never retried automatically.
    """
    if conn.in_transaction:
        raise ValueError("confirmation requires its own transaction")
    set_corrections.saved(conn, activity_id)
    set_corrections.validate(rows)
    if not isinstance(approval, str) or len(approval) != 64:
        raise ValueError("a reviewed approval fingerprint is required")
    editor = connect()
    proposal = set_corrections.preview(
        conn, activity_id, rows, lambda: editor.get_activity_exercise_sets(activity_id)
    )
    if not hmac.compare_digest(approval, proposal["approval"]):
        raise ValueError("stale proposal; preview and approve again")
    result: dict[str, Any] = {
        "account_write": "not_sent",
        "verified": False,
        "saved": False,
        "overlay_retired": False,
        "recomputed": None,
    }
    if not _write_once(conn, proposal, editor):
        return {
            **result,
            "account_write": "unknown",
            "error": "Account write uncertain; do not retry blindly. Read fresh source evidence.",
        }
    return _verify_and_save(
        conn, proposal, editor, {**result, "account_write": "sent"}, data_start_date
    )
