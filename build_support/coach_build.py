"""Validate public coach resources before delegating distribution builds to Poetry."""

from __future__ import annotations

import json
import pathlib

try:
    import tomllib
except ModuleNotFoundError:  # Build frontends may still run under Python 3.10.
    import tomli as tomllib

from poetry.core.masonry import api as _poetry


def _validate_resources() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    public = json.loads((root / "src/garmin_coach/coach_resources.json").read_text())
    config = tomllib.loads((root / "pyproject.toml").read_text())
    expected = {"skills/coach/" + name for name in public}
    for format in ("wheel", "sdist"):
        included = {
            item["path"]
            for item in config["tool"]["poetry"]["include"]
            if item["path"].startswith("skills/") and format in item.get("format", ["sdist"])
        }
        if included != expected:
            raise ValueError("Distribution includes must match the public coach resource manifest.")
    source = root / "skills/coach"
    for name in public:
        relative = pathlib.PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid public coach resource path.")
        path = source / relative
        components = [path, *path.parents[: len(path.parts) - len(source.parts) + 1]]
        if any(part.is_symlink() for part in components) or not path.is_file():
            raise ValueError(f"Missing or symlinked public coach resource: {name}")


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    """Build a validated pure Python wheel through Poetry's standard backend."""
    _validate_resources()
    return _poetry.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    """Build a source distribution after validating its public coach resources."""
    _validate_resources()
    return _poetry.build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    """Apply the same resource validation to editable builds."""
    _validate_resources()
    return _poetry.build_editable(wheel_directory, config_settings, metadata_directory)


get_requires_for_build_wheel = _poetry.get_requires_for_build_wheel
get_requires_for_build_sdist = _poetry.get_requires_for_build_sdist
get_requires_for_build_editable = _poetry.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _poetry.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _poetry.prepare_metadata_for_build_editable
