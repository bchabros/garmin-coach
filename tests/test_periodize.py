"""Periodization seam: pure anchor selection over goal events (Phase 9, ticket 01).

The anchor is the single event the block calendar counts back from. Everything here
is a pure function of (events, today) - no DB, no wall clock.
"""

from __future__ import annotations

from garmin_coach import periodize, thresholds

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


def test_annotate_marks_exactly_one_anchor_and_its_countdown():
    half = _event("2026-09-05", id=2, type="run_race", priority="B", status="tentative")
    hyrox = _event("2026-10-17", id=1)

    rows = periodize.annotate([half, hyrox], TODAY)

    assert [row["is_anchor"] for row in rows] == [False, True]
    assert rows[1]["weeks_to_event"] == 13


def test_annotate_marks_no_anchor_when_only_tentative():
    rows = periodize.annotate([_event("2026-10-17", status="tentative")], TODAY)

    assert not any(row["is_anchor"] for row in rows)
    assert rows[0]["weeks_to_event"] == 13


def test_annotate_orders_events_soonest_first():
    half = _event("2026-09-05", id=2, type="run_race", priority="B")
    hyrox = _event("2026-10-17", id=1)

    assert [row["date"] for row in periodize.annotate([hyrox, half], TODAY)] == [
        "2026-09-05", "2026-10-17",
    ]


def test_weeks_to_event_counts_whole_weeks_between_mondays():
    # 2026-07-14 (Tue) -> Monday 07-13; 2026-10-17 (Sat) -> Monday 10-12. 13 weeks.
    assert periodize.weeks_to_event(TODAY, "2026-10-17") == 13


def test_weeks_to_event_is_zero_in_the_race_week():
    assert periodize.weeks_to_event("2026-10-12", "2026-10-17") == 0
    assert periodize.weeks_to_event("2026-10-16", "2026-10-17") == 0


# --- periodize(): the block calendar counted back from the anchor ---

DATA_START = "2026-06-08"
TH = thresholds.merge()


def _plan(event_date: str = "2026-10-17", data_start: str = DATA_START) -> dict[str, dict]:
    weeks = periodize.periodize(_event(event_date), data_start, TH)
    return {week["week_start"]: week for week in weeks}


def test_the_plan_spans_data_start_through_the_race_week():
    plan = _plan()

    assert min(plan) == "2026-06-08"
    assert max(plan) == "2026-10-12"  # the Monday of race week


def test_golden_block_layout_for_the_real_calendar():
    """taper 2 / peak 3 / build 5, base absorbing the rest back to data_start."""
    plan = _plan()
    blocks = {week: row["block"] for week, row in plan.items()}

    assert blocks["2026-06-08"] == "base"
    assert blocks["2026-08-03"] == "base"  # last base week
    assert blocks["2026-08-10"] == "build"  # first build week
    assert blocks["2026-09-07"] == "build"  # last build week
    assert blocks["2026-09-14"] == "peak"
    assert blocks["2026-09-28"] == "peak"  # last peak week
    assert blocks["2026-10-05"] == "taper"
    assert blocks["2026-10-12"] == "taper"  # race week


def test_weeks_to_event_is_zero_in_the_race_week_of_the_plan():
    assert _plan()["2026-10-12"]["weeks_to_event"] == 0
    assert _plan()["2026-10-05"]["weeks_to_event"] == 1


def test_base_absorbs_the_remainder_however_distant_the_race():
    """A far race must not leave the athlete in an unlabeled limbo."""
    plan = _plan(event_date="2027-06-05")

    assert plan["2026-06-08"]["block"] == "base"
    assert sum(1 for row in plan.values() if row["block"] == "base") > 30


def test_golden_deload_placement_for_the_real_calendar():
    """Every 4th week counted back from a block's end, in base and build only."""
    plan = _plan()
    deloads = sorted(week for week, row in plan.items() if row["is_deload"])

    assert deloads == ["2026-07-06", "2026-08-03", "2026-09-07"]


def test_no_deload_is_planned_in_peak_or_taper():
    plan = _plan()

    assert not any(row["is_deload"] for row in plan.values() if row["block"] in ("peak", "taper"))


def test_a_block_never_opens_with_a_deload():
    """A block starts by ramping up; its first week is never a planned recovery week."""
    plan = _plan()
    first_of_block = {}
    for week in sorted(plan):
        first_of_block.setdefault(plan[week]["block"], week)

    assert not any(plan[week]["is_deload"] for week in first_of_block.values())


def test_a_deload_always_lands_on_the_last_week_of_base_and_build():
    plan = _plan()

    assert plan["2026-08-03"]["is_deload"] == 1  # last base week
    assert plan["2026-09-07"]["is_deload"] == 1  # last build week


def test_every_week_carries_the_anchor_event_id():
    assert {row["anchor_event_id"] for row in _plan().values()} == {1}


def test_a_race_closer_than_the_block_structure_truncates_at_data_start():
    """No base at all when the race is nearer than taper+peak+build weeks of history."""
    plan = _plan(event_date="2026-07-25", data_start="2026-06-08")

    assert min(plan) == "2026-06-08"
    assert "base" not in {row["block"] for row in plan.values()}


# --- rollup(): materializing the plan_block mart at the DB boundary ---


def _record(conn, date="2026-10-17", type="hyrox", priority="A", status="confirmed"):
    from garmin_coach import db

    db.upsert_goal_event(conn, {
        "date": date, "type": type, "priority": priority, "status": status,
        "date_precision": "approx", "target_s": 3600, "note": None,
    })


def _blocks(conn) -> dict[str, str]:
    return {
        row[0]: row[1]
        for row in conn.execute("SELECT week_start, block FROM plan_block")
    }


def test_rollup_materializes_the_plan_including_future_weeks(conn):
    _record(conn)

    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    blocks = _blocks(conn)
    assert blocks["2026-10-12"] == "taper"  # a week that has not happened yet
    assert blocks["2026-06-08"] == "base"


def test_rollup_writes_nothing_without_an_anchor(conn):
    _record(conn, status="tentative")

    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    assert _blocks(conn) == {}


def test_rollup_is_idempotent_and_rebuilds_from_scratch(conn):
    _record(conn)
    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)
    before = _blocks(conn)

    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    assert _blocks(conn) == before


def test_rollup_clears_the_plan_when_the_anchor_is_withdrawn(conn):
    _record(conn)
    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)
    conn.execute("DELETE FROM goal_event")

    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    assert _blocks(conn) == {}


def test_rollup_anchors_as_of_through_date_not_the_wall_clock(conn):
    """A backfill to a past date must reproduce that day's plan, not today's."""
    _record(conn)

    periodize.rollup(conn, data_start_date=DATA_START, through_date="2026-10-20")

    assert _blocks(conn) == {}  # the race is behind through_date: no anchor


# --- current_plan(): the plan row for the week a given day falls in ---


def test_current_plan_reads_the_week_the_day_falls_in(conn):
    _record(conn)
    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    plan = periodize.current_plan(conn, TODAY)  # Tue 2026-07-14 -> week of 07-13

    assert plan["week_start"] == "2026-07-13"
    assert plan["block"] == "base"
    assert plan["weeks_to_event"] == 13
    assert plan["race_date"] == "2026-10-17"
    assert plan["race_type"] == "hyrox"


def test_current_plan_carries_the_taper_week(conn):
    _record(conn)
    periodize.rollup(conn, data_start_date=DATA_START, through_date=TODAY)

    assert periodize.current_plan(conn, "2026-10-07")["block"] == "taper"


def test_current_plan_is_none_without_a_plan(conn):
    assert periodize.current_plan(conn, TODAY) is None
