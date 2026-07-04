"""Guard: the runtime schema (package resource) must not drift from the docs snapshot."""

from __future__ import annotations

import importlib.resources
import pathlib


def test_package_schema_matches_docs_snapshot():
    pkg = importlib.resources.files("garmin_coach").joinpath("schema.sql").read_text()
    docs = (pathlib.Path(__file__).parent.parent / "docs" / "schema.sql").read_text()
    assert pkg == docs, (
        "src/garmin_coach/schema.sql and docs/schema.sql have diverged; "
        "edit the package copy and re-sync docs."
    )
