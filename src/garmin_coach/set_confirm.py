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
    try:
        editor.set_activity_exercise_sets(activity_id, copy.deepcopy(proposal["proposed"]))
    except Exception:
        return {
            **result,
            "account_write": "unknown",
            "error": "Account write uncertain; do not retry blindly. Read fresh source evidence.",
        }
    result["account_write"] = "sent"
    try:
        received = set_corrections.account_payload(
            activity_id, editor.get_activity_exercise_sets(activity_id)
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
        conn.execute("BEGIN IMMEDIATE")
        current = set_corrections.saved(conn, activity_id)
        unchanged = set_corrections.fingerprint(current) == set_corrections.fingerprint(
            proposal["local"]
        )
        set_history.persist(conn, activity_id, received, checked_on=dt.date.today().isoformat())
        if result["verified"] and unchanged and current["manual"]:
            set_history.archive(conn, activity_id, "verified_account_correction")
            conn.execute("DELETE FROM manual_activity_sets WHERE activity_id=?", (activity_id,))
            conn.execute(
                "DELETE FROM exercise_correction_state WHERE activity_id=?", (activity_id,)
            )
            result["overlay_retired"] = True
        conn.commit()
        result["saved"] = True
    except Exception:
        conn.rollback()
        return {
            **result,
            "overlay_retired": False,
            "error": "Account changed; local save failed. Read fresh source before recovery.",
        }
    result.update(set_history.rebuild(conn, proposal["local"]["date"], data_start_date))
    if not result["verified"] or not unchanged:
        result["error"] = (
            "Read-back mismatch or concurrent local correction; active overlay retained. Preview again."
        )
    return result
