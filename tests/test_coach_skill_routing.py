"""Guard: the coach skill's router and its reference files must agree.

The router (SKILL.md) is the only file always in context; each flow's detail is a
reference file it gates behind a MUST-read. That contract has two failure modes,
both silent at authoring time and both discovered only through a broken
conversation: a gate that names a file which is not there, and a reference file no
gate ever points at. Neither reaches the athlete from here -- they fail `task check`.
"""

from __future__ import annotations

import pathlib
import re

SKILL_DIR = pathlib.Path(__file__).parent.parent / "skills" / "coach"

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


def _gated_paths() -> set[str]:
    return set(GATED_PATH.findall((SKILL_DIR / "SKILL.md").read_text()))


def _reference_files() -> set[str]:
    root = SKILL_DIR / "references"
    return {str(p.relative_to(SKILL_DIR)) for p in root.rglob("*.md")}


def test_every_gated_reference_file_exists():
    """A gate naming a missing file sends the model looking for detail that is not there."""
    missing = sorted(name for name in _gated_paths() if not (SKILL_DIR / name).is_file())
    assert not missing, (
        f"SKILL.md points at reference files that do not exist: {missing}. "
        "Fix the path in the router, or add the file."
    )


def test_every_reference_file_is_gated():
    """An ungated reference file is dead weight: no flow ever reads it."""
    orphans = sorted(_reference_files() - _gated_paths())
    assert not orphans, (
        f"reference files no routing gate in SKILL.md names: {orphans}. "
        "Add a MUST-read gate for them, or delete them."
    )


def test_the_description_keeps_every_report_trigger_phrase():
    """The description is the only text that decides whether the skill loads at all."""
    description = (SKILL_DIR / "SKILL.md").read_text().split("---")[1]
    dropped = [phrase for phrase in REPORT_TRIGGERS if phrase not in description]
    assert not dropped, (
        f"the frontmatter description dropped trigger phrases that used to work: {dropped}. "
        "Widen the description, never narrow it."
    )
