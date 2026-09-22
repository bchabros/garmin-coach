"""Goal-event seam: the writers both the CLI and the coach MCP call (issue #72)."""

from __future__ import annotations

import pytest

from garmin_coach.core import db, events


def test_parse_target_s_reads_hours_minutes_seconds():
    assert events.parse_target_s("1:00:00") == 3600
    assert events.parse_target_s("1:30:00") == 5400


def test_parse_target_s_reads_minutes_seconds_and_bare_seconds():
    assert events.parse_target_s("61:46") == 3706
    assert events.parse_target_s("3600") == 3600


def test_parse_target_s_rejects_nonsense():
    with pytest.raises(ValueError, match="target"):
        events.parse_target_s("under an hour")


def test_parse_target_s_rejects_out_of_range_minutes_and_seconds():
    with pytest.raises(ValueError, match="target"):
        events.parse_target_s("1:99")
    with pytest.raises(ValueError, match="target"):
        events.parse_target_s("0:0:75")


def test_add_goal_event_records_the_race(conn):
    events.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
        target="1:00:00",
    )

    recorded = db.list_goal_events(conn)
    assert len(recorded) == 1
    assert recorded[0]["target_s"] == 3600


def test_add_goal_event_rejects_a_malformed_date(conn):
    """A date typo must be refused at entry: it would otherwise poison every later read."""
    with pytest.raises(ValueError, match="date"):
        events.add_goal_event(
            conn,
            date="17/10/2026",
            type="run_race",
            priority="B",
            status="tentative",
            date_precision="exact",
        )

    assert db.list_goal_events(conn) == []


def test_add_goal_event_rejects_an_unknown_enum(conn):
    with pytest.raises(ValueError, match="priority"):
        events.add_goal_event(
            conn,
            date="2026-10-17",
            type="hyrox",
            priority="S",
            status="confirmed",
            date_precision="exact",
        )


def test_add_goal_event_refuses_a_race_already_recorded(conn):
    for _ in range(1):
        events.add_goal_event(
            conn,
            date="2026-10-17",
            type="hyrox",
            priority="A",
            status="confirmed",
            date_precision="approx",
        )

    with pytest.raises(ValueError, match="already recorded"):
        events.add_goal_event(
            conn,
            date="2026-10-17",
            type="hyrox",
            priority="B",
            status="tentative",
            date_precision="exact",
        )


def test_update_goal_event_rejects_a_malformed_date(conn):
    events.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    with pytest.raises(ValueError, match="date"):
        events.update_goal_event(conn, event_id, date="24.10.2026")

    assert db.list_goal_events(conn)[0]["date"] == "2026-10-17"


def test_update_goal_event_rejects_an_unknown_id(conn):
    with pytest.raises(ValueError, match="999"):
        events.update_goal_event(conn, 999, status="confirmed")


def test_update_goal_event_rejects_an_empty_update(conn):
    events.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    with pytest.raises(ValueError, match="nothing to update"):
        events.update_goal_event(conn, event_id)


def test_update_goal_event_pins_the_date_and_the_precision(conn):
    events.add_goal_event(
        conn,
        date="2026-10-17",
        type="hyrox",
        priority="A",
        status="confirmed",
        date_precision="approx",
    )
    event_id = db.list_goal_events(conn)[0]["id"]

    events.update_goal_event(conn, event_id, date="2026-10-10", date_precision="exact")

    row = db.list_goal_events(conn)[0]
    assert (row["date"], row["date_precision"]) == ("2026-10-10", "exact")
