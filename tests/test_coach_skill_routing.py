"""Guard: the coach skill's router and its reference files must agree.

The router (SKILL.md) is the only file always in context; each flow's detail is a
reference file it gates behind a MUST-read. That contract has two failure modes,
both silent at authoring time and both discovered only through a broken
conversation: a gate that names a file which is not there, and a reference file no
gate ever points at. Neither reaches the athlete from here -- they fail `task check`.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "coach"
PUBLIC_RESOURCES = tuple(
    json.loads((REPO_ROOT / "src/garmin_coach/coach_resources.json").read_text())
)

# A gate is the categorical MUST-read line, not any mention of the path: the router also
# cross-references a reference file in ordinary prose, and counting those as gates would
# let the real gate be deleted with both tests still green.
GATED_PATH = re.compile(r"\*\*MUST\*\*\s+read\s+`(references/[\w-]+\.md)`")

# The phrases the report flow triggered on before the skill grew the planning and
# authoring clauses (issue #51). Widening the description must never cost a phrase
# that already worked -- the report flow is the one that reliably loads today.
REPORT_TRIGGERS = (
    "training report",
    "coach read",
    "weekly review",
    "how am I doing",
    "where do I stand",
    "what are my numbers",
    "gdzie stoje",
    "jakie mam staty",
    "jaka mam forme",
)

# The planning and authoring phrases the description carried before issue #99 shortened
# it. They are how the athlete asks to plan a week or change what is on the watch.
PLANNING_AND_AUTHORING_TRIGGERS = (
    "zaplanuj tydzien",
    "co planujemy na przyszly tydzien",
    "wrzuc na zegarek",
    "tempo w czwartek: 8x1km po 3:40",
    "dodaj trening silowy na piatek",
    "zdejmij trening z czwartku",
    "odwolaj piatkowy trening",
    "jestem chory, usun to z zegarka",
)


def _gated_paths() -> set[str]:
    return set(GATED_PATH.findall((SKILL_DIR / "SKILL.md").read_text()))


def _reference_files() -> set[str]:
    return {name for name in PUBLIC_RESOURCES if name.startswith("references/")}


def test_every_gated_reference_file_exists():
    """A gate naming a missing file sends the model looking for detail that is not there."""
    missing = sorted(name for name in _gated_paths() if not (SKILL_DIR / name).is_file())
    assert not missing, (
        f"SKILL.md points at reference files that do not exist: {missing}. "
        "Fix the path in the router, or add the file."
    )


def test_every_reference_file_is_gated():
    """An ungated public reference is unreachable; private files are not skill resources."""
    orphans = sorted(_reference_files() - _gated_paths())
    assert not orphans, (
        f"reference files no routing gate in SKILL.md names: {orphans}. "
        "Add a MUST-read gate for them, or delete them."
    )


@pytest.mark.parametrize("triggers", [REPORT_TRIGGERS, PLANNING_AND_AUTHORING_TRIGGERS])
def test_the_description_keeps_every_trigger_phrase(triggers):
    """The description is the only text that decides whether the skill loads at all."""
    description = (SKILL_DIR / "SKILL.md").read_text().split("---")[1]
    dropped = [phrase for phrase in triggers if phrase not in description]
    assert not dropped, (
        f"the frontmatter description dropped trigger phrases that used to work: {dropped}. "
        "Widen the description, never narrow it."
    )


def test_the_frontmatter_is_kept_in_the_form_claude_ai_saves():
    """An uploaded skill comes back with its description on one single-quoted line.

    Seen on 2026-10-19: after uploading a description folded over several lines,
    claude.ai synced it down as `description: '...'` with the same text, and
    `task claude:check` reported the account STALE although the upload was current.
    """
    frontmatter = (SKILL_DIR / "SKILL.md").read_text().split("---")[1]
    lines = frontmatter.strip("\n").splitlines()
    assert lines[0] == "name: coach"
    assert len(lines) == 2, "keep the description on one line, the way claude.ai saves it"
    assert re.fullmatch(r"description: '(?:[^']|'')*'", lines[1])


@pytest.mark.parametrize("private_extra", [False, True])
def test_every_registered_coach_tool_is_named_in_the_skill(tmp_path, monkeypatch, private_extra):
    """A tool the uploaded skill never mentions is a tool the coach never reaches for."""
    import asyncio

    from garmin_coach.mcp import server

    if private_extra:
        public_skill = tmp_path / "coach"
        for name in PUBLIC_RESOURCES:
            target = public_skill / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SKILL_DIR / name, target)
        (public_skill / "references/context.md").write_bytes(b"\xff private synthetic note")
        monkeypatch.setitem(globals(), "SKILL_DIR", public_skill)
        assert "references/context.md" not in _reference_files()

    text = "\n".join((SKILL_DIR / name).read_text() for name in PUBLIC_RESOURCES)
    registered = {t.name for t in asyncio.run(server.server.list_tools())}

    missing = sorted(name for name in registered if name not in text)
    assert not missing, f"registered but absent from the coach skill: {', '.join(missing)}"


def test_saved_context_questions_reach_the_archive_reference():
    """A cached skill must discover race evidence without bundling athlete records."""
    router = (SKILL_DIR / "SKILL.md").read_text()
    description = router.split("---")[1]
    assert "race results" in description
    assert "saved coaching context" in description
    assert "references/saved-context.md" in _gated_paths()
    context = (SKILL_DIR / "references/saved-context.md").read_text()
    assert "data/races/index.md" in context
    assert "gitignored" in context
    assert "MCP calendar/digest access does not itself" in context


def test_reporting_separates_read_only_questions_from_artifact_generation():
    """A quick MCP answer must not inherit a procedure that writes report files."""
    report = (SKILL_DIR / "references/report.md").read_text()
    heading = "## Read-only questions"
    assert heading in report
    read_only = report.split(heading, 1)[1].split("\n## ", 1)[0]
    assert "get_digest" in read_only and "get_snapshot" in read_only
    assert "existing artifacts" in read_only
    assert "Do not generate report files" in read_only
    assert "## Requested report artifacts" in report


def test_recommendation_guidance_checks_unknown_intent_before_keep_plan_advice():
    """No-template onboarding must not be interpreted as an existing agreed plan."""
    report = (SKILL_DIR / "references/report.md").read_text()
    recommendation = report.split("**Rekomendacja na dziś**", 1)[1].split("**Wykresy**", 1)[0]
    unknown = "If `planned_intent` or `intended_type` is null"
    assert unknown in recommendation
    assert recommendation.index(unknown) < recommendation.index("keep the planned session")
    assert "Only with a known planned intent" in recommendation
