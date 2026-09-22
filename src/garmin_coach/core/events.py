"""Goal-event writers: the seam the CLI and the coach MCP server both call.

Transport-free. The metrics rebuild that a changed race implies belongs to the
caller (`core` never imports the marts), so both surfaces run it themselves.
"""

from __future__ import annotations

import datetime as _dt
import sqlite3
from typing import Any

from . import db

EVENT_TYPES = ("hyrox", "run_race")
EVENT_PRIORITIES = ("A", "B", "C")
EVENT_STATUSES = ("confirmed", "tentative")
EVENT_DATE_PRECISIONS = ("exact", "approx")

# The single source of truth for the goal-event enums: argparse `choices=`, the MCP
# tool docstrings, and the add/update validators all read it, so no two can drift.
EVENT_CHOICES: dict[str, tuple[str, ...]] = {
    "type": EVENT_TYPES,
    "priority": EVENT_PRIORITIES,
    "status": EVENT_STATUSES,
    "date_precision": EVENT_DATE_PRECISIONS,
}


def parse_target_s(target: str) -> int:
    """Parse a goal time into seconds.

    Accepts ``H:MM:SS``, ``MM:SS``, or bare seconds, so the athlete can type a race
    goal the way they say it ("1:00:00") and the DB still stores a number a later
    race plan can split across segments.

    Args:
        target: The goal time as ``H:MM:SS``, ``MM:SS``, or plain seconds.

    Returns:
        The goal time in whole seconds.

    Raises:
        ValueError: If the text is not a colon-separated time or a plain integer, or
            if a minutes/seconds field is out of range (a mistyped goal must not be
            silently accepted as a plausible number).
    """
    parts = target.split(":")
    if len(parts) > 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"target must be H:MM:SS, MM:SS or seconds (got {target!r})")
    if any(int(part) >= 60 for part in parts[1:]):
        raise ValueError(f"target has a minutes/seconds field above 59 (got {target!r})")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _check_date(value: str) -> str:
    """Return an ISO date, refusing anything a later read could not parse."""
    try:
        return _dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD (got {value!r})") from None


def _check_choices(fields: dict[str, Any]) -> None:
    for name, allowed in EVENT_CHOICES.items():
        value = fields.get(name)
        if value is not None and value not in allowed:
            raise ValueError(f"{name} must be one of {'|'.join(allowed)} (got {value!r})")


def add_goal_event(
    conn: sqlite3.Connection,
    *,
    date: str,
    type: str,  # noqa: A002 - mirrors the goal_event column and the --type flag
    priority: str,
    status: str,
    date_precision: str,
    target: str | None = None,
    note: str | None = None,
) -> int:
    """Record a goal race in core (transport-free).

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        date: Race day YYYY-MM-DD (the best known estimate).
        type: Race type (``hyrox`` or ``run_race``).
        priority: Race priority (``A``, ``B``, or ``C``).
        status: Whether the athlete will start (``confirmed`` or ``tentative``).
        date_precision: Whether the exact day is known (``exact`` or ``approx``).
        target: Optional goal time as ``H:MM:SS``, ``MM:SS``, or seconds.
        note: Optional free-text note.

    Returns:
        The new event's `id`, as `event list` shows it.

    Raises:
        ValueError: If the date is malformed, an enum value is unknown, the target time
            is unparseable, or the race is already recorded (correct it with `update`
            rather than re-adding it, which would erase the fields left unset here).
    """
    fields = {
        "type": type,
        "priority": priority,
        "status": status,
        "date_precision": date_precision,
    }
    _check_choices(fields)
    row = {
        "date": _check_date(date),
        **fields,
        "target_s": parse_target_s(target) if target else None,
        "note": note,
    }
    try:
        event_id = db.insert_goal_event(conn, row)
    except sqlite3.IntegrityError:
        existing = next(
            e
            for e in db.list_goal_events(conn)
            if e["date"] == row["date"] and e["type"] == row["type"]
        )
        raise ValueError(
            f"{type} on {row['date']} is already recorded (id {existing['id']}); "
            f"use `garmin-coach event update {existing['id']}` to change it"
        ) from None
    conn.commit()
    return event_id


def update_goal_event(conn: sqlite3.Connection, event_id: int, **fields: str | None) -> None:
    """Update a recorded goal event (pin an approx date, commit a tentative start).

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        event_id: The event's `id`, as shown by `event list`.
        **fields: Any of `date`, `type`, `priority`, `status`, `date_precision`,
            `target` (parsed to seconds), or `note`. Fields left None keep their value.

    Raises:
        ValueError: If no field is given, the event does not exist, the date is
            malformed, an enum value is unknown, or the target time is unparseable.
    """
    changes: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        raise ValueError(f"nothing to update on event {event_id}; pass at least one field")
    _check_choices(changes)
    if "date" in changes:
        changes["date"] = _check_date(str(changes["date"]))
    if "target" in changes:
        changes["target_s"] = parse_target_s(str(changes.pop("target")))
    db.update_goal_event(conn, event_id, **changes)
    conn.commit()
