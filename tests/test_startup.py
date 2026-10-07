"""Non-report entry points can start without initializing the chart library."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module", ["garmin_coach.cli", "garmin_coach.daily", "garmin_coach.mcp.server"]
)
def test_non_report_startup_does_not_import_matplotlib(module):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {module}; import sys; assert 'matplotlib' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
