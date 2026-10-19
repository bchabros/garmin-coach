"""The one public coach resource selection shared by every package and packager."""

from __future__ import annotations

import pathlib
import shutil

import pytest

from garmin_coach import coach_resources

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def skill(tmp_path):
    root = tmp_path / "skills/coach"
    for name in coach_resources.public_names():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "skills/coach" / name).read_bytes())
    return root


def test_reads_exactly_the_public_resources_in_manifest_order(skill):
    (skill / "references/context.md").write_text("Synthetic private athlete context")
    resources = coach_resources.read_public(skill)
    assert tuple(resources) == coach_resources.public_names()
    assert b"Synthetic private athlete context" not in b"".join(resources.values())


@pytest.mark.parametrize("link", ["SKILL.md", "references", "."])
def test_refuses_a_resource_reached_through_a_symlink(skill, tmp_path, link):
    target = skill / link
    outside = tmp_path / "outside"
    shutil.move(target, outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    with pytest.raises(ValueError, match="Symlinked public coach resource"):
        coach_resources.read_public(skill)


def test_missing_resource_refuses_unless_partial_copies_are_allowed(skill):
    (skill / "references/saved-context.md").unlink()
    with pytest.raises(ValueError, match="Missing public coach resource"):
        coach_resources.read_public(skill)
    assert "references/saved-context.md" not in coach_resources.read_public(skill, missing_ok=True)
