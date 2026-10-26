"""Packaged connection evidence through real isolated stdio on synthetic data."""

import asyncio
import pathlib
import sys

import pytest

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


def _legacy_server(tmp_path):
    """An isolated pre-context server contract with real stdio and SQLite reads."""
    import shutil

    package = tmp_path / "legacy/garmin_coach"
    shutil.copytree(
        pathlib.Path(probe.__file__).parents[1],
        package,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (package / "runtime.py").unlink()
    (package / "mcp/tools.py").write_text(
        "from ..core.config import get_settings\n"
        "def snapshot(conn):\n"
        "    assert get_settings().sync_recheck_days == 7\n"
        "    day = conn.execute('SELECT computed_at FROM athlete_status WHERE id=1').fetchone()[0]\n"
        "    return {'freshness': {'data_through': day}, 'data': {'computed_at': day}}\n"
    )
    (package / "mcp/server.py").write_text("""
import argparse
from contextlib import closing
from typing import Any
from mcp.server.fastmcp import FastMCP
from ..core import installation
from ..core.config import get_settings
from . import tools
server = FastMCP("legacy-coach")
@server.tool()
def get_digest() -> dict[str, Any]:
    settings = get_settings()
    assert settings.garmin_email == '' and settings.garmin_password is None
    with closing(installation.open_existing_database(settings, writable=True)) as conn:
        day = conn.execute('SELECT MAX(date) FROM daily_metrics').fetchone()[0]
        return {'freshness': {'data_through': day}}
@server.tool()
def get_snapshot() -> dict[str, Any]:
    with closing(installation.open_existing_database(get_settings(), writable=True)) as conn:
        return tools.snapshot(conn)
def main(existing_db_only=False, argv=None):
    parser = argparse.ArgumentParser()
    installation.add_home_argument(parser)
    args = parser.parse_args(argv)
    installation.require_database(installation.activate(args.home))
    assert existing_db_only
    server.run()
""")
    python = tmp_path / "legacy-python"
    python.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "assert sys.argv[1:3] == ['-I', '-c']\n"
        f"sys.path.insert(0, {str(package.parent)!r})\n"
        "script = sys.argv[3]\nsys.argv = ['-c', *sys.argv[4:]]\nexec(script)\n"
    )
    python.chmod(0o755)
    return python, package


def test_legacy_probe_keeps_captured_settings_without_credentials(tmp_path):
    from garmin_coach.runtime import InstallationContext

    home = tmp_path / "athlete"
    create_installation(home)
    with (home / ".env").open("a") as config:
        config.write("\nSYNC_RECHECK_DAYS=7\nGARMIN_EMAIL=private@test\nGARMIN_PASSWORD=secret\n")
    context = InstallationContext.select(home)
    python, _ = _legacy_server(tmp_path)
    before = (home / "data/garmin.db").read_bytes()
    (home / ".env").write_text("DB_PATH=./missing.sqlite\nSYNC_RECHECK_DAYS=1\n")

    reading = asyncio.run(probe.check(python, home, settings=context.settings))

    assert reading.data_through == "2026-07-03"
    assert reading.snapshot_data_through == "2026-07-03"
    assert reading.snapshot_computed_at == "2026-07-03"
    assert (home / "data/garmin.db").read_bytes() == before
    assert not (home / "missing.sqlite").exists()


def test_broken_context_module_never_falls_back_to_legacy_readers(tmp_path):
    from garmin_coach.runtime import InstallationContext

    home = tmp_path / "athlete"
    create_installation(home)
    with (home / ".env").open("a") as config:
        config.write("\nSYNC_RECHECK_DAYS=7\n")
    context = InstallationContext.select(home)
    python, package = _legacy_server(tmp_path)
    # Establish that this server would pass if an import error triggered the bridge.
    asyncio.run(probe.check(python, home, settings=context.settings))
    (package / "runtime.py").write_text("raise RuntimeError('broken context module')\n")

    with pytest.raises(ExceptionGroup):
        asyncio.run(probe.check(python, home, settings=context.settings))
