"""Periodization seam: pure anchor selection over goal events (Phase 9, ticket 01).

The anchor is the single event the block calendar counts back from. Everything here
is a pure function of (events, today) - no DB, no wall clock.
"""

from __future__ import annotations

from garmin_coach import periodize

TODAY = "2026-07-14"


def _event(
    date: str,
    *,
    id: int = 1,
    type: str = "hyrox",
    priority: str = "A",
    status: str = "confirmed",
    date_precision: str = "approx",
    target_s: int | None = 3600,
) -> dict:
    return {
        "id": id,
        "date": date,
        "type": type,
        "priority": priority,
        "status": status,
        "date_precision": date_precision,
        "target_s": target_s,
        "note": None,
    }


def test_anchor_is_the_nearest_upcoming_confirmed_a_event():
    near = _event("2026-10-17", id=1)
    far = _event("2027-03-06", id=2)

    assert periodize.anchor_event([far, near], TODAY)["id"] == 1


def test_a_nearer_tentative_event_does_not_anchor():
    half = _event("2026-09-05", id=2, type="run_race", priority="B", status="tentative")
    hyrox = _event("2026-10-17", id=1)

    assert periodize.anchor_event([half, hyrox], TODAY)["id"] == 1


def test_a_nearer_confirmed_b_event_does_not_anchor():
    half = _event("2026-09-05", id=2, type="run_race", priority="B", status="confirmed")
    hyrox = _event("2026-10-17", id=1)

    assert periodize.anchor_event([half, hyrox], TODAY)["id"] == 1


def test_a_tentative_a_event_does_not_anchor():
    assert periodize.anchor_event([_event("2026-10-17", status="tentative")], TODAY) is None


def test_a_past_a_event_does_not_anchor():
    assert periodize.anchor_event([_event("2026-06-20")], TODAY) is None


def test_the_race_day_itself_still_anchors():
    assert periodize.anchor_event([_event(TODAY)], TODAY)["date"] == TODAY


def test_no_events_means_no_anchor():
    assert periodize.anchor_event([], TODAY) is None


def test_an_approx_date_still_anchors():
    """date_precision is orthogonal to status: it suppresses nothing."""
    anchor = periodize.anchor_event([_event("2026-10-17", date_precision="approx")], TODAY)

    assert anchor is not None


def test_weeks_to_event_counts_whole_weeks_between_mondays():
    # 2026-07-14 (Tue) -> Monday 07-13; 2026-10-17 (Sat) -> Monday 10-12. 13 weeks.
    assert periodize.weeks_to_event(TODAY, "2026-10-17") == 13


def test_weeks_to_event_is_zero_in_the_race_week():
    assert periodize.weeks_to_event("2026-10-12", "2026-10-17") == 0
    assert periodize.weeks_to_event("2026-10-16", "2026-10-17") == 0
