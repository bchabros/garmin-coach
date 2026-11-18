"""Single-attempt recorded-set account writes bound to an exact reviewed proposal."""

from __future__ import annotations

import copy
import datetime as dt
import hmac
import logging
import sqlite3
from collections.abc import Callable
from typing import Any, Protocol

from . import set_corrections, set_history
from .etl.client import AccountWriteRejectedError

logger = logging.getLogger(__name__)


def _timestamp(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid captured startTime")
    return dt.datetime.fromisoformat(value)


def _same_set(expected: dict[str, Any], received: dict[str, Any]) -> bool:
    keys = ["setType", "duration", "repetitionCount", "weight"]
    keys.extend(k for k in ("setId", "setIndex", "messageIndex") if expected.get(k) is not None)
    if any(expected.get(k) != received.get(k) for k in keys):
        return False
    if _timestamp(expected.get("startTime")) != _timestamp(received.get("startTime")):
        return False
    return [(e.get("category"), e.get("name")) for e in expected.get("exercises") or []] == [
        (e.get("category"), e.get("name")) for e in received.get("exercises") or []
    ]


def _matches_proposal(expected: dict[str, Any], received: dict[str, Any]) -> bool:
    before, after = expected["exerciseSets"], received["exerciseSets"]
    return (
        expected["activityId"] == received["activityId"]
        and len(before) == len(after)
        and all(_same_set(left, right) for left, right in zip(before, after, strict=True))
    )


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
        except AccountWriteRejectedError:
            raise
        except Exception:
            logger.exception("set_confirm: account write outcome uncertain")
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
        result["verified"] = _matches_proposal(proposal["proposed"], received)
    except Exception:
        logger.exception("set_confirm: account read-back unavailable or invalid")
        return {
            **result,
            "error": "Account changed; read-back unavailable or invalid. Active correction retained.",
        }
    try:
        result["overlay_retired"], unchanged = _save(conn, proposal, received, result["verified"])
        result["saved"] = True
    except Exception:
        logger.exception("set_confirm: local source save failed after account write")
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


def _send(conn: sqlite3.Connection, proposal: dict[str, Any], editor: SetEditor) -> dict[str, Any]:
    result: dict[str, Any] = {
        "account_write": "not_sent",
        "verified": False,
        "saved": False,
        "overlay_retired": False,
        "recomputed": None,
    }
    try:
        sent = _write_once(conn, proposal, editor)
    except AccountWriteRejectedError as exc:
        logger.warning("set_confirm: account rejected correction (HTTP %s)", exc.status_code)
        return {
            **result,
            "account_write": "rejected",
            "http_status": exc.status_code,
            "error": "Account rejected the correction. Active correction retained.",
            "recovery": "Resolve the HTTP rejection before previewing and confirming again.",
        }
    if not sent:
        return {
            **result,
            "account_write": "unknown",
            "error": "Account write uncertain; do not retry blindly. Read fresh source evidence.",
        }
    return {**result, "account_write": "sent"}


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

    Added/deleted sets share the preview's observed nullable timing contract.
    Failed or uncertain account writes are never retried automatically.
    """
    if conn.in_transaction:
        raise ValueError("confirmation requires its own transaction")
    set_corrections.saved(conn, activity_id)
    set_corrections.validate(rows)
    if (
        not isinstance(approval, str)
        or len(approval) != 64
        or any(char not in "0123456789abcdef" for char in approval)
    ):
        raise ValueError("a reviewed approval fingerprint is required")
    editor = connect()
    proposal = set_corrections.preview(
        conn, activity_id, rows, lambda: editor.get_activity_exercise_sets(activity_id)
    )
    if not hmac.compare_digest(approval, proposal["approval"]):
        raise ValueError("stale proposal; preview and approve again")
    result = _send(conn, proposal, editor)
    if result["account_write"] != "sent":
        return result
    return _verify_and_save(conn, proposal, editor, result, data_start_date)
