"""Validate public coach resources before delegating distribution builds to Poetry."""

from __future__ import annotations

import importlib.util
import pathlib

try:
    import tomllib
except ModuleNotFoundError:  # Build frontends may still run under Python 3.10.
    import tomli as tomllib

from poetry.core.masonry import api as _poetry

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_coach_resources():
    """Load the package's public resource selection by path, without its dependencies."""
    path = _ROOT / "src/garmin_coach/coach_resources.py"
    spec = importlib.util.spec_from_file_location("coach_resources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_includes(public: tuple[str, ...]) -> None:
    """Refuse a build whose explicit skill includes differ from the public manifest."""
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text())
    expected = {"skills/coach/" + name for name in public}
    for distribution_format in ("wheel", "sdist"):
        included = {
            item["path"]
            for item in config["tool"]["poetry"]["include"]
            if item["path"].startswith("skills/")
            and distribution_format in item.get("format", ["sdist"])
        }
        if included != expected:
            raise ValueError("Distribution includes must match the public coach resource manifest.")


def _validate_resources() -> None:
    coach_resources = _load_coach_resources()
    _check_includes(coach_resources.public_names())
    coach_resources.read_public(_ROOT / "skills/coach")


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
