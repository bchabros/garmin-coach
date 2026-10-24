"""Packaged connection evidence through real isolated stdio on synthetic data."""

import asyncio
import pathlib
import sys

from garmin_coach.mcp import probe
from garmin_coach.synthetic import create_installation


def test_probe_reads_both_tools_with_independent_freshness(tmp_path):
    home = tmp_path / "athlete"
    create_installation(home)
    before = (home / "data/garmin.db").read_bytes()
    reading = asyncio.run(probe.check(pathlib.Path(sys.executable), home))
    assert reading.data_through == "2026-07-03"
    assert reading.snapshot_data_through == "2026-07-03"
    assert reading.snapshot_computed_at == "2026-07-03"
    assert {"get_digest", "get_snapshot"} <= set(reading.tools)
    assert (home / "data/garmin.db").read_bytes() == before
