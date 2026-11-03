"""Observed recorded sets, correction conflicts and retained declaration history."""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from collections.abc import Callable
from typing import Any

from . import set_corrections
from .core import db, models
from .marts import features


def persist(conn: sqlite3.Connection, activity_id: int, payload: Any, *, checked_on: str) -> None:
    """Stage validated raw/core source evidence atomically; caller owns the commit."""
    data = set_corrections.account_payload(activity_id, payload)
    dt.date.fromisoformat(checked_on)
    for entry in data["exerciseSets"]:
        for key in ("duration", "weight", "repetitionCount"):
            value = entry.get(key)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"invalid captured {key}")
        if entry.get("exercises") is not None and (
            not isinstance(entry["exercises"], list)
            or any(not isinstance(e, dict) for e in entry["exercises"])
        ):
            raise ValueError("malformed captured exercise identities")
    rows = models.normalize_exercise_sets(activity_id, data)
    day = set_corrections.saved(conn, activity_id)["date"]
    if not conn.in_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT observed_sets")
    try:
        encoded = json.dumps(data, allow_nan=False)
        db.insert_raw(conn, "get_activity_exercise_sets", day, encoded)
        db.replace_activity_sets(conn, activity_id, rows)
        conn.execute(
            "INSERT INTO exercise_set_observations(activity_id,payload_json,checked_on) VALUES(?,?,?) ON CONFLICT(activity_id) DO UPDATE SET payload_json=excluded.payload_json,checked_on=excluded.checked_on,observed_at=CURRENT_TIMESTAMP",
            (activity_id, encoded, checked_on),
        )
    except BaseException:
        conn.execute("ROLLBACK TO observed_sets")
        raise
    finally:
        conn.execute("RELEASE observed_sets")


def archive(conn: sqlite3.Connection, activity_id: int, reason: str) -> None:
    """Retain the selected manual declaration before replacement or retirement."""
    local = set_corrections.saved(conn, activity_id)
    if local["manual"]:
        state = conn.execute(
            "SELECT captured_sha FROM exercise_correction_state WHERE activity_id=?", (activity_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO exercise_correction_history(activity_id,declaration_json,baseline_sha,reason) VALUES(?,?,?,?)",
            (activity_id, json.dumps(local["manual"]), state[0] if state else None, reason),
        )


def baseline(conn: sqlite3.Connection, activity_id: int) -> None:
    """Bind a newly recorded correction to current captured and manual evidence."""
    local = set_corrections.saved(conn, activity_id)
    conn.execute(
        "INSERT INTO exercise_correction_state VALUES(?,?,?) ON CONFLICT(activity_id) DO UPDATE SET captured_sha=excluded.captured_sha,manual_sha=excluded.manual_sha",
        (
            activity_id,
            set_corrections.fingerprint(local["captured"]),
            set_corrections.fingerprint(local["manual"]),
        ),
    )


def read(conn: sqlite3.Connection, activity_id: int) -> dict[str, Any]:
    """Read coherent baseline, conflict, retained history and source-check evidence."""
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        local = set_corrections.saved(conn, activity_id)
        state = conn.execute(
            "SELECT captured_sha,manual_sha FROM exercise_correction_state WHERE activity_id=?",
            (activity_id,),
        ).fetchone()
        source = conn.execute(
            "SELECT checked_on,observed_at FROM exercise_set_observations WHERE activity_id=?",
            (activity_id,),
        ).fetchone()
        known = bool(state and state[1] == set_corrections.fingerprint(local["manual"]))
        history = [
            {
                "declaration": json.loads(row[0]),
                "baseline": row[1],
                "reason": row[2],
                "retired_at": row[3],
            }
            for row in conn.execute(
                "SELECT declaration_json,baseline_sha,reason,retired_at FROM exercise_correction_history WHERE activity_id=? ORDER BY id",
                (activity_id,),
            )
        ]
        return {
            **local,
            "baseline_state": "known" if known else "unknown",
            "conflict": state[0] != set_corrections.fingerprint(local["captured"])
            if known and local["manual"]
            else None,
            "history": history,
            "checked_on": source[0] if source else None,
            "observed_at": source[1] if source else None,
            "binding": set_corrections.fingerprint({"local": local, "state": state}),
        }
    finally:
        if started:
            conn.rollback()


def rebuild(conn: sqlite3.Connection, day: str, data_start_date: str) -> dict[str, Any]:
    """Report saved facts independently of a failed derived refresh."""
    try:
        features.features(conn, from_date=day, data_start_date=data_start_date)
        return {"saved": True, "recomputed": True}
    except Exception:
        conn.rollback()
        return {
            "saved": True,
            "recomputed": False,
            "recovery": "Run features offline in the same installation.",
        }


def choose(
    conn: sqlite3.Connection, activity_id: int, choice: str, binding: str, *, data_start_date: str
) -> dict[str, Any]:
    """Apply the explicitly reviewed captured/manual conflict choice, retaining history."""
    if choice not in {"captured", "manual"}:
        raise ValueError("choose captured or manual")
    if conn.in_transaction:
        raise ValueError("conflict resolution requires its own transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        state = read(conn, activity_id)
        if state["binding"] != binding:
            raise ValueError("stale correction choice; read again")
        if not state["manual"]:
            raise ValueError("no active correction to resolve")
        archive(conn, activity_id, "choose_" + choice)
        if choice == "captured":
            conn.execute("DELETE FROM manual_activity_sets WHERE activity_id=?", (activity_id,))
            conn.execute(
                "DELETE FROM exercise_correction_state WHERE activity_id=?", (activity_id,)
            )
        else:
            baseline(conn, activity_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return rebuild(conn, state["date"], data_start_date)


def refresh(
    conn: sqlite3.Connection,
    activity_id: int,
    fetch: Callable[[], Any],
    *,
    data_start_date: str,
    checked_on: str | None = None,
) -> dict[str, Any]:
    """Explicitly refresh one source activity, preserving prior facts after failed reads."""
    local = set_corrections.saved(conn, activity_id)
    payload = fetch()
    try:
        persist(conn, activity_id, payload, checked_on=checked_on or dt.date.today().isoformat())
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return {**rebuild(conn, local["date"], data_start_date), "evidence": read(conn, activity_id)}


def recheck(
    conn: sqlite3.Connection,
    fetch: Callable[[int], Any],
    *,
    through_date: str,
    data_start_date: str,
    recent_days: int = 3,
) -> dict[str, Any]:
    """Stage one oldest-first seventh of eligible history per successful night.

    Reads share the import transport callback. Failed observations remain due;
    this function commits source facts but never recomputes marts.
    """
    end = dt.date.fromisoformat(through_date)
    cutoff = (end - dt.timedelta(days=max(1, recent_days) - 1)).isoformat()
    candidates = conn.execute(
        "SELECT a.activity_id,a.date,o.checked_on FROM activities a LEFT JOIN exercise_set_observations o USING(activity_id) WHERE a.gtype IN ('strength_training','hiit') AND a.date>=? AND a.date<? ORDER BY COALESCE(o.checked_on,''),a.date,a.activity_id",
        (data_start_date, cutoff),
    ).fetchall()
    due = [
        row
        for row in candidates
        if row[2] is None or row[2] <= (end - dt.timedelta(days=7)).isoformat()
    ]
    if conn.execute(
        "SELECT 1 FROM exercise_set_recheck_runs WHERE date=?", (through_date,)
    ).fetchone():
        return {"checked": [], "due": len(due), "errors": [], "already_run": True}
    budget = math.ceil(len(candidates) / 7)
    checked, errors = [], []
    for aid, _, _ in due[:budget]:
        try:
            persist(conn, aid, fetch(aid), checked_on=through_date)
            conn.execute(
                "INSERT INTO exercise_set_attempts VALUES(?,?,NULL) ON CONFLICT(activity_id) DO UPDATE SET attempted_on=excluded.attempted_on,error=NULL",
                (aid, through_date),
            )
            conn.commit()
            checked.append(aid)
        except Exception:
            conn.rollback()
            errors.append(f"set history read failed for activity {aid}; prior evidence retained")
            conn.execute(
                "INSERT INTO exercise_set_attempts VALUES(?,?,?) ON CONFLICT(activity_id) DO UPDATE SET attempted_on=excluded.attempted_on,error=excluded.error",
                (aid, through_date, "source_read_failed"),
            )
            conn.commit()
    conn.execute("INSERT INTO exercise_set_recheck_runs VALUES(?)", (through_date,))
    conn.commit()
    return {
        "checked": checked,
        "due": len(due) - len(checked),
        "errors": errors,
        "already_run": False,
    }


def coverage(
    conn: sqlite3.Connection, *, through_date: str, data_start_date: str, recent_days: int = 3
) -> dict[str, Any]:
    """Expose conditional seven-night progress, including failed and missed checks."""
    end = dt.date.fromisoformat(through_date)
    cutoff = (end - dt.timedelta(days=max(1, recent_days) - 1)).isoformat()
    overdue = (end - dt.timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT a.activity_id,a.date,o.checked_on,t.attempted_on,t.error FROM activities a LEFT JOIN exercise_set_observations o USING(activity_id) LEFT JOIN exercise_set_attempts t USING(activity_id) WHERE a.gtype IN ('strength_training','hiit') AND a.date>=? AND a.date<? ORDER BY a.date,a.activity_id",
        (data_start_date, cutoff),
    ).fetchall()
    items = [
        {
            "activity_id": aid,
            "date": day,
            "checked_on": checked,
            "attempted_on": attempted,
            "error": error,
            "due": checked is None or checked <= overdue,
        }
        for aid, day, checked, attempted, error in rows
    ]
    return {
        "through_date": through_date,
        "recent_window_start": cutoff,
        "eligible": len(items),
        "due": sum(i["due"] for i in items),
        "never_checked": sum(i["checked_on"] is None for i in items),
        "overdue": sum(i["checked_on"] is not None and i["due"] for i in items),
        "failed": sum(i["error"] is not None for i in items),
        "activities": items,
        "condition": "Coverage requires successful nights and source reads; observation dates are not edit times.",
    }
