"""Synthetic observations belong to an explicit temporary installation."""

import pytest

from garmin_coach.core import installation


def test_synthetic_installation_has_its_own_readable_data(tmp_path):
    from garmin_coach.synthetic import create_installation

    root = tmp_path / "synthetic athlete"
    create_installation(root)
    evidence = installation.inspect_database(installation.load(root))
    assert evidence.schema == "compatible"
    assert evidence.data_through == "2026-07-03"
    assert evidence.snapshot_computed_at == "2026-07-03"


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_synthetic_builder_preserves_existing_destinations(tmp_path, kind):
    from garmin_coach.synthetic import create_installation

    root = tmp_path / "existing"
    if kind == "directory":
        root.mkdir()
        (root / "retained.txt").write_text("keep")
    elif kind == "file":
        root.write_text("keep")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="new or empty"):
        create_installation(root)
    if kind == "directory":
        assert list(root.iterdir()) == [root / "retained.txt"]
        assert (root / "retained.txt").read_text() == "keep"
    elif kind == "file":
        assert root.read_text() == "keep"
    else:
        assert root.is_symlink() and list(target.iterdir()) == []
