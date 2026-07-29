"""Guard: the coach skill's profile rail and the athlete's profile must keep agreeing.

`memory/athlete-profile.md` is the only qualitative memory the coach flows have, and
issue #52 was exactly its absence from the router: nothing read it, so nothing noticed it
going stale for 19 days. Two failure modes bring that back, both silent inside a
conversation -- the router losing the rail that names the file, and the router forgetting
the date line the freshness check reads. Neither reaches the athlete from here: they fail
`task check`.

The profile itself is gitignored personal data, so the check on its own date line binds
only on the athlete's machine and is skipped wherever the file is absent (CI, a fresh
clone).
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent
SKILL_MD = REPO_ROOT / "skills" / "coach" / "SKILL.md"

PROFILE_PATH = "memory/athlete-profile.md"
PROFILE = REPO_ROOT / PROFILE_PATH

# The athlete writes the profile by hand, in Polish, and the freshness check reads one
# line of it. Like the plan file's table headers, the literal is the athlete's own format:
# the router and the file have to spell it the same way or the check reads nothing.
DATE_LINE_TOKEN = "_Ostatnia aktualizacja:"
DATE_LINE = re.compile(re.escape(DATE_LINE_TOKEN) + r"\s*(\d{4}-\d{2}-\d{2})")


def test_the_router_names_the_profile():
    """A router that never names the profile is the bug issue #52 was filed for."""
    assert PROFILE_PATH in SKILL_MD.read_text(), (
        f"SKILL.md never names {PROFILE_PATH}, so no flow reads the athlete's long-term "
        "context. Restore the profile rail in the router."
    )


def test_the_router_carries_the_date_line_contract():
    """The freshness check can only read a line the router spells out."""
    assert DATE_LINE_TOKEN in SKILL_MD.read_text(), (
        f"SKILL.md dropped the profile's date line ({DATE_LINE_TOKEN} YYYY-MM-DD), so "
        "nothing tells the model which line carries the profile's age. Put it back."
    )


def test_the_profile_date_line_matches_the_contract():
    """A hand-edit that drops the date line is a known breakage, never a silent one."""
    if not PROFILE.is_file():
        pytest.skip(f"{PROFILE_PATH} is gitignored personal data and is absent here")
    assert DATE_LINE.search(PROFILE.read_text()), (
        f"{PROFILE_PATH} has no `{DATE_LINE_TOKEN} YYYY-MM-DD` line, so the coach skill "
        "cannot tell how old it is. Restore the line at the top of the profile."
    )
