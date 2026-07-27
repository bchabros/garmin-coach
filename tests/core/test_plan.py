"""Plan-of-record seam: parser, resolver, import, and the confirm-path file writer.

All plan files here are synthetic - plans/ is gitignored personal data (issue #21).
"""

from __future__ import annotations

import pytest

from garmin_coach.core import plan

WEEK = "2026-07-13"  # a Monday

DAYS = [
    ("Pon", "13.07", "bieg easy 10 km, Zone 2", "easy"),
    ("Wt", "14.07", "FBB + Hyrox (HIT)", "quality"),
    ("Śr", "15.07", "rest", "rest"),
    ("Czw", "16.07", "tempo 8x1 km", "quality"),
    ("Pt", "17.07", "bieg easy 10 km, HR <145", "easy"),
    ("Sob", "18.07", "rest", "rest"),
    ("Nd", "19.07", "Crossfit + Hyrox (HIT)", "quality"),
]


def week_file(days=DAYS, prose="## Zamiar tygodnia\n\nSynthetic.\n") -> str:
    rows = "\n".join(
        f"| {d} | {date} | {label} | {intent} | plan |" for d, date, label, intent in days
    )
    return (
        "# Plan tygodnia — 13–19.07.2026\n\n"
        "| Dzień | Data | Plan | Zamiar (dla silnika) | Status |\n"
        "|------|------|------|----------------------|--------|\n"
        f"{rows}\n\n{prose}"
    )


# --- parser ------------------------------------------------------------------


def test_parse_week_maps_all_seven_days():
    rows = plan.parse_week(week_file(), WEEK)
    assert len(rows) == 7
    assert rows[3] == {
        "week_start": WEEK,
        "dow": 3,
        "planned": "tempo 8x1 km",
        "intent": "quality",
    }
    assert [r["intent"] for r in rows] == [
        "easy",
        "quality",
        "rest",
        "quality",
        "easy",
        "rest",
        "quality",
    ]


def test_parse_week_strips_markdown_emphasis_and_status():
    days = list(DAYS)
    days[0] = ("Pon", "13.07", "**bieg easy 10 km** — zmienione z rest", "easy")
    rows = plan.parse_week(week_file(days), WEEK)
    assert rows[0]["planned"] == "bieg easy 10 km — zmienione z rest"


def test_parse_week_rejects_unknown_intent():
    days = list(DAYS)
    days[2] = ("Śr", "15.07", "rest", "chill")
    with pytest.raises(plan.PlanParseError, match="chill"):
        plan.parse_week(week_file(days), WEEK)


def test_parse_week_rejects_date_not_matching_week():
    days = list(DAYS)
    days[4] = ("Pt", "18.07", "bieg easy", "easy")  # Friday of WEEK is 17.07
    with pytest.raises(plan.PlanParseError, match="17.07"):
        plan.parse_week(week_file(days), WEEK)


def test_parse_week_rejects_wrong_row_count():
    with pytest.raises(plan.PlanParseError, match="7"):
        plan.parse_week(week_file(DAYS[:5]), WEEK)


def test_parse_week_rejects_non_monday_week_start():
    with pytest.raises(plan.PlanParseError, match="Monday"):
        plan.parse_week(week_file(), "2026-07-14")


def test_parse_week_rejects_missing_table():
    with pytest.raises(plan.PlanParseError, match="table"):
        plan.parse_week("# Plan tygodnia\n\nNo table here.\n", WEEK)


# --- resolver ----------------------------------------------------------------


def test_resolve_day_falls_back_to_template(conn):
    resolved = plan.resolve_day(conn, "2026-07-16")  # Thu; template says rest
    assert resolved == {
        "date": "2026-07-16",
        "dow": 3,
        "planned": "rest",
        "intent": "rest",
        "source": "plan_template",
    }


def test_resolve_day_prefers_plan_week_override(conn):
    plan.upsert_week(conn, plan.parse_week(week_file(), WEEK))
    resolved = plan.resolve_day(conn, "2026-07-16")
    assert resolved["intent"] == "quality"
    assert resolved["planned"] == "tempo 8x1 km"
    assert resolved["source"] == "plan_week"


def test_resolve_week_mixes_sources_per_week_not_per_day(conn):
    plan.upsert_week(conn, plan.parse_week(week_file(), WEEK))
    override = plan.resolve_week(conn, WEEK)
    fallback = plan.resolve_week(conn, "2026-07-06")
    assert [d["source"] for d in override] == ["plan_week"] * 7
    assert [d["source"] for d in fallback] == ["plan_template"] * 7
    assert [d["date"] for d in override][:2] == ["2026-07-13", "2026-07-14"]


def test_has_override(conn):
    assert not plan.has_override(conn, WEEK)
    plan.upsert_week(conn, plan.parse_week(week_file(), WEEK))
    assert plan.has_override(conn, WEEK)


# --- import ------------------------------------------------------------------


def test_import_dir_upserts_and_is_idempotent(conn, tmp_path):
    (tmp_path / f"{WEEK}_week.md").write_text(week_file(), encoding="utf-8")
    assert plan.import_dir(conn, tmp_path) == [WEEK]
    assert plan.import_dir(conn, tmp_path) == [WEEK]
    n = conn.execute("SELECT COUNT(*) FROM plan_week").fetchone()[0]
    assert n == 7


def test_import_dir_revision_overwrites(conn, tmp_path):
    path = tmp_path / f"{WEEK}_week.md"
    path.write_text(week_file(), encoding="utf-8")
    plan.import_dir(conn, tmp_path)
    days = list(DAYS)
    days[4] = ("Pt", "17.07", "rest — zmienione", "rest")  # mid-week downgrade
    path.write_text(week_file(days), encoding="utf-8")
    plan.import_dir(conn, tmp_path)
    assert plan.resolve_day(conn, "2026-07-17")["intent"] == "rest"


def test_import_dir_single_week_filter(conn, tmp_path):
    (tmp_path / f"{WEEK}_week.md").write_text(week_file(), encoding="utf-8")
    assert plan.import_dir(conn, tmp_path, week=WEEK) == [WEEK]
    assert plan.import_dir(conn, tmp_path, week="2026-07-06") == []


def test_import_dir_fails_loudly_on_bad_file(conn, tmp_path):
    days = list(DAYS)
    days[0] = ("Pon", "13.07", "luz", "luz")
    (tmp_path / f"{WEEK}_week.md").write_text(week_file(days), encoding="utf-8")
    with pytest.raises(plan.PlanParseError, match="luz"):
        plan.import_dir(conn, tmp_path)


def test_import_dir_rejects_non_monday_filename(conn, tmp_path):
    (tmp_path / "2026-07-14_week.md").write_text(week_file(), encoding="utf-8")
    with pytest.raises(plan.PlanParseError, match="Monday"):
        plan.import_dir(conn, tmp_path)


def test_import_dir_missing_dir_is_empty(conn, tmp_path):
    assert plan.import_dir(conn, tmp_path / "absent") == []


# --- confirm-path writer -------------------------------------------------------


def test_write_week_file_round_trips_through_parser(tmp_path):
    days = [
        {"planned": "bieg easy 10 km", "intent": "easy"},
        {"planned": "FBB + Hyrox", "intent": "quality"},
        {"planned": "rest", "intent": "rest"},
        {"planned": "tempo 6x1 km", "intent": "tempo"},
        {"planned": "rest", "intent": "rest"},
        {"planned": "Hyrox sim", "intent": "hyrox"},
        {"planned": "bieg easy 12 km", "intent": "easy"},
    ]
    path = plan.write_week_file(tmp_path, WEEK, days)
    assert path.name == f"{WEEK}_week.md"
    rows = plan.parse_week(path.read_text(encoding="utf-8"), WEEK)
    assert [r["intent"] for r in rows] == [d["intent"] for d in days]
    assert "coach" in path.read_text(encoding="utf-8")  # provenance note


def test_write_week_file_refuses_existing(tmp_path):
    (tmp_path / f"{WEEK}_week.md").write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        plan.write_week_file(tmp_path, WEEK, [{"planned": "rest", "intent": "rest"}] * 7)
    assert (tmp_path / f"{WEEK}_week.md").read_text(encoding="utf-8") == "existing"


# --- vocabulary --------------------------------------------------------------


@pytest.mark.parametrize(
    "intent", ["rest", "easy", "tempo", "strength", "hyrox", "crossfit", "quality"]
)
def test_parse_week_accepts_every_vocabulary_intent(intent):
    days = [(abbr, date, "sesja", intent) for abbr, date, _, _ in DAYS]
    rows = plan.parse_week(week_file(days), WEEK)
    assert {r["intent"] for r in rows} == {intent}


def test_intent_class_collapses_the_vocabulary_to_measurable_classes():
    """Adherence compares what the load classifier can actually observe: a planned
    crossfit day executed as a hard session is a match, not a divergence."""
    assert plan.intent_class("crossfit") == "quality"
    assert plan.intent_class("hyrox") == "quality"
    assert plan.intent_class("tempo") == "quality"
    assert plan.intent_class("quality") == "quality"
    assert plan.intent_class("strength") == "strength"
    assert plan.intent_class("easy") == "easy"
    assert plan.intent_class("rest") == "rest"


def test_intent_class_covers_the_whole_vocabulary():
    assert all(plan.intent_class(i) is not None for i in plan.INTENTS)


# --- hardness ladder and the plan guard (issue #22) ---------------------------


def test_intent_rank_covers_the_whole_vocabulary():
    """The guard indexes the ladder directly, so an intent a plan file may carry
    without a rank would crash instead of being judged."""
    assert set(plan.INTENT_RANK) == set(plan.INTENTS)


def test_is_harder_ranks_quality_above_easy_above_rest():
    assert plan.is_harder("quality", "easy")
    assert plan.is_harder("easy", "rest")
    assert not plan.is_harder("easy", "quality")
    assert not plan.is_harder("easy", "easy")


def test_is_harder_treats_the_top_types_as_one_rank():
    """hyrox / crossfit / quality are equally hard: swapping between them is a
    change of session, not an escalation."""
    assert not plan.is_harder("hyrox", "quality")
    assert not plan.is_harder("quality", "crossfit")


def test_is_harder_is_false_when_either_side_is_unknown():
    assert not plan.is_harder("quality", None)
    assert not plan.is_harder(None, "rest")


def test_planned_intent_resolves_the_plan_of_record(conn):
    plan.upsert_week(conn, plan.parse_week(week_file(), WEEK))
    assert plan.planned_intent(conn, "2026-07-16") == "quality"
    assert plan.planned_intent(conn, "2026-07-17") == "easy"


def test_planned_intent_falls_back_to_the_template(conn):
    assert plan.planned_intent(conn, "2026-07-16") == "rest"  # template dow=3


def test_guard_error_refuses_a_session_harder_than_the_plan():
    """The 2026-07-17 case: the plan was downgraded to easy hours before a
    quality session was authored and pushed."""
    error = plan.guard_error("2026-07-17", "quality", "easy")

    assert error is not None
    assert "quality" in error and "easy" in error and "2026-07-17" in error


def test_guard_error_allows_a_softer_session_than_planned():
    """The downgrade contract: the recommender only ever softens."""
    assert plan.guard_error("2026-07-17", "easy", "quality") is None


def test_guard_error_allows_the_planned_session_itself():
    assert plan.guard_error("2026-07-17", "quality", "quality") is None


def test_guard_error_is_silent_when_nothing_is_planned():
    assert plan.guard_error("2026-07-17", "quality", None) is None


# --- proposal validation -----------------------------------------------------


def _proposal(planned="sesja", intent="easy"):
    return [{"planned": planned, "intent": intent} for _ in range(7)]


def test_validate_days_accepts_a_clean_proposal(tmp_path):
    assert plan.validate_days(WEEK, _proposal(), tmp_path) is None


def test_validate_days_rejects_a_pipe_that_would_break_the_table(tmp_path):
    """A realistic session note ('8x1 km @ 4:00 | HR <165') must not silently
    corrupt the row it is written into."""
    days = _proposal(planned="8x1 km @ 4:00 | HR <165", intent="quality")

    error = plan.validate_days(WEEK, days, tmp_path)

    assert error is not None
    assert "|" in error


def test_validate_days_rejects_a_newline_in_the_session_text(tmp_path):
    error = plan.validate_days(WEEK, _proposal(planned="8x1 km\nHR <165"), tmp_path)

    assert error is not None


def test_validate_days_rejects_bad_vocabulary_short_week_and_non_monday(tmp_path):
    assert "chill" in plan.validate_days(WEEK, _proposal(intent="chill"), tmp_path)
    assert "7" in plan.validate_days(WEEK, _proposal()[:5], tmp_path)
    assert "Monday" in plan.validate_days("2026-07-14", _proposal(), tmp_path)


def test_validate_days_reports_an_already_authored_week(tmp_path):
    (tmp_path / f"{WEEK}_week.md").write_text("existing", encoding="utf-8")

    assert "exists" in plan.validate_days(WEEK, _proposal(), tmp_path)


def test_validate_days_skips_the_file_check_without_a_dir():
    assert plan.validate_days(WEEK, _proposal()) is None


def test_write_week_file_never_emits_a_file_its_own_parser_would_reject(tmp_path):
    """The confirm path's core guarantee: no unparseable file reaches plans/."""
    days = _proposal(planned="8x1 km @ 4:00 | HR <165", intent="quality")

    with pytest.raises(plan.PlanParseError):
        plan.write_week_file(tmp_path, WEEK, days)

    assert list(tmp_path.iterdir()) == []
