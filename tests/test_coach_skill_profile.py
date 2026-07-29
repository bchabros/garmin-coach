"""Guard: the coach skill's profile rail and the athlete's profile must keep agreeing.

`memory/athlete-profile.md` is the only qualitative memory the coach flows have, and
issue #52 was exactly its absence from the router: nothing read it, so nothing noticed it
going stale for 19 days. Two failure modes bring that back, both silent inside a
conversation -- the router losing the rail that names the file, and the router forgetting
the date line the freshness check reads. Neither reaches the athlete from here: they fail
`task check`.

The profile itself is gitignored personal data, so the check on its own date line binds
only on the athlete's machine and is skipped wherever the file is absent (CI, a fresh
clone). `memory/README.md` carries the human-facing copy of the date-line contract; it is
tracked, so its check binds everywhere.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
SKILL_MD = REPO_ROOT / "skills" / "coach" / "SKILL.md"
MEMORY_README = REPO_ROOT / "memory" / "README.md"

PROFILE_PATH = "memory/athlete-profile.md"
PROFILE = REPO_ROOT / PROFILE_PATH

# The rail is anchored to its own section, never to a mention anywhere in the router:
# a bare substring search over the whole file stays green when the section is deleted and
# any passing reference to the path survives. `docs/OPERATIONS.md` cites the heading by
# name twice, so the heading is part of the contract too.
SECTION_HEADING = "## The athlete profile"

# The athlete writes the profile by hand, in Polish, and the freshness check reads one
# line of it. Like the plan file's table headers, the literal is the athlete's own format:
# the router and the file have to spell it the same way or the check reads nothing.
DATE_LINE_TOKEN = "_Ostatnia aktualizacja:"
DATE_LINE = re.compile(re.escape(DATE_LINE_TOKEN) + r"\s*\d{4}-\d{2}-\d{2}")


def _profile_section() -> str:
    """Return the router's profile section, from its heading to the next one."""
    body = SKILL_MD.read_text()
    start = body.find(SECTION_HEADING)
    if start == -1:
        return ""
    rest = body[start + len(SECTION_HEADING) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_the_router_keeps_the_profile_section():
    """Two pointers in docs/OPERATIONS.md resolve by this heading; a rename breaks both."""
    assert SECTION_HEADING in SKILL_MD.read_text(), (
        f"SKILL.md has no '{SECTION_HEADING}' section, so nothing carries the profile "
        "rail and the pointers in docs/OPERATIONS.md name a section that is gone."
    )


def test_the_profile_section_names_the_profile():
    """A rail that never names the profile is the bug issue #52 was filed for."""
    assert PROFILE_PATH in _profile_section(), (
        f"the '{SECTION_HEADING}' section never names {PROFILE_PATH}, so no flow reads "
        "the athlete's long-term context. Restore the path in the rail."
    )


def test_the_profile_section_carries_the_date_line_contract():
    """The freshness check can only read a line the router spells out."""
    assert DATE_LINE_TOKEN in _profile_section(), (
        f"the '{SECTION_HEADING}' section dropped the profile's date line "
        f"({DATE_LINE_TOKEN} YYYY-MM-DD), so nothing tells the model which line carries "
        "the profile's age. Put it back."
    )


def test_the_memory_readme_keeps_the_date_line_contract():
    """The human-facing copy of the contract must keep naming the same line."""
    assert DATE_LINE_TOKEN in MEMORY_README.read_text(), (
        f"memory/README.md no longer names the `{DATE_LINE_TOKEN} YYYY-MM-DD` line, so "
        "the human-facing copy of the contract has drifted from the router's (the source "
        f"of truth is the '{SECTION_HEADING}' section in SKILL.md). Restore it there."
    )


def test_the_profile_date_line_matches_the_contract():
    """A hand-edit that drops the date line is a known breakage, never a silent one."""
    if not PROFILE.is_file():
        pytest.skip(f"{PROFILE_PATH} is gitignored personal data and is absent here")
    assert DATE_LINE.search(PROFILE.read_text()), (
        f"{PROFILE_PATH} has no `{DATE_LINE_TOKEN} YYYY-MM-DD` line, so the coach skill "
        "cannot tell how old it is. Restore the line at the top of the profile."
    )
