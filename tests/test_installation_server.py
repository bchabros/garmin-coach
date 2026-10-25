"""The coach server on temporary athlete installations, over real stdio (issue #83 T4).

Every server here is a separate process started from an unrelated folder, the way an
assistant client starts it, with every installation and path variable scrubbed.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from garmin_coach.core import db
from garmin_coach.marts import snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
SCRUBBED = {
    "GARMIN_COACH_HOME",
    "DB_PATH",
    "PLANS_DIR",
    "LOG_PATH",
    "GARMINTOKENS",
    "GARMIN_EMAIL",
    "GARMIN_PASSWORD",
}

_today = _dt.date.today()
WEEK = (_today + _dt.timedelta(days=7 - _today.weekday())).isoformat()  # next Monday
WORKOUT_DAY = (_dt.date.fromisoformat(WEEK) + _dt.timedelta(days=2)).isoformat()
CROSSFIT_WEEK = [{"planned": "WOD", "intent": "crossfit"} for _ in range(7)]


def make_installation(path: pathlib.Path, *, horizon: str = "2026-07-03", env: str = ""):
    """A temporary installation with a populated digest and snapshot."""
    path.mkdir(parents=True)
    (path / ".env").write_text(env)
    (path / "data").mkdir()
    conn = db.connect(str(path / "data" / "garmin.db"))
    db.bootstrap(conn)
    db.upsert_daily(conn, "daily_metrics", {"date": horizon, "hrv": 61, "load_day": 80})
    snapshot.rollup(conn)
    conn.commit()
    conn.close()
    return path


def _environment() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in SCRUBBED}
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _server_args(home: pathlib.Path | None) -> list[str]:
    args = ["-m", "garmin_coach.mcp.server"]
    return args if home is None else [*args, "--home", str(home)]


@contextlib.asynccontextmanager
async def coach_server(
    cwd: pathlib.Path, home: pathlib.Path | None = None
) -> AsyncIterator[ClientSession]:
    """A real coach server over stdio, started from ``cwd``."""
    params = StdioServerParameters(
        command=sys.executable, args=_server_args(home), env=_environment(), cwd=str(cwd)
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def call(session: ClientSession, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await session.call_tool(tool, arguments)
    assert not result.isError, result.content
    assert result.structuredContent is not None
    return result.structuredContent


def run(coroutine) -> Any:
    return asyncio.run(asyncio.wait_for(coroutine, timeout=60))


@pytest.fixture
def elsewhere(tmp_path):
    start = tmp_path / "elsewhere"
    start.mkdir()
    return start


def test_a_named_installation_is_served_from_an_unrelated_folder(tmp_path, elsewhere):
    home = make_installation(tmp_path / "athlete")
    request = json.loads((FIXTURES / "hiit_request.json").read_text())

    async def scenario():
        async with coach_server(elsewhere, home) as session:
            digest = await call(session, "get_digest")
            await call(session, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)
            authored = await call(session, "author_workout", date=WORKOUT_DAY, request=request)
            return digest, authored

    digest, authored = run(scenario())

    assert digest["freshness"]["data_through"] == "2026-07-03"
    assert authored["data"]["error"] is None
    assert (home / "plans" / f"{WEEK}_week.md").is_file()
    assert (home / "reports" / WORKOUT_DAY / "workout.json").is_file()
    assert list(elsewhere.iterdir()) == []


def test_the_server_honours_the_installations_configured_plans_folder(tmp_path, elsewhere):
    home = make_installation(tmp_path / "athlete", env="PLANS_DIR=./my plans\n")

    async def scenario():
        async with coach_server(elsewhere, home) as session:
            await call(session, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)

    run(scenario())

    assert (home / "my plans" / f"{WEEK}_week.md").is_file()
    assert not (home / "plans").exists()


def test_running_server_keeps_configuration_but_reads_live_data_until_restart(tmp_path, elsewhere):
    home = make_installation(
        tmp_path / "athlete", env="PLANS_DIR=./agreed-plans\nSYNC_RECHECK_DAYS=5\n"
    )
    other = make_installation(tmp_path / "other", horizon="2026-08-14")
    database = home / "data/garmin.db"
    with db.connect(str(database)) as conn:
        conn.executemany(
            "INSERT INTO plan_template(dow,planned,intent) VALUES (?,?,?)",
            [(day, "tempo", "tempo") for day in range(7)],
        )

    async def scenario():
        async with coach_server(elsewhere, home) as session:
            first = await call(session, "get_digest")
            (home / ".env").write_text(
                f"DB_PATH={other / 'data/garmin.db'}\nPLANS_DIR=./new-plans\nSYNC_RECHECK_DAYS=7\n"
            )
            stable = await call(session, "get_digest")
            assert stable == first
            assert len(stable["freshness"]["unconfirmed_days"]) == 5
            await call(session, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)
            assert (home / "agreed-plans" / f"{WEEK}_week.md").is_file()
            with db.connect(str(database)) as conn:
                db.upsert_daily(conn, "daily_metrics", {"date": "2026-07-04", "load_day": 100})
                snapshot.rollup(conn)
            live = await call(session, "get_snapshot")
            assert live["data"]["computed_at"] == "2026-07-04"
        async with coach_server(elsewhere, home) as restarted:
            assert (await call(restarted, "get_digest"))["freshness"][
                "data_through"
            ] == "2026-08-14"
            await call(restarted, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)
            assert (home / "new-plans" / f"{WEEK}_week.md").is_file()

    run(scenario())


def test_two_installations_served_at_once_stay_apart(tmp_path, elsewhere):
    first = make_installation(tmp_path / "first", horizon="2026-07-03")
    second = make_installation(tmp_path / "second", horizon="2026-08-14")
    request = json.loads((FIXTURES / "hiit_request.json").read_text())

    async def scenario():
        async with coach_server(elsewhere, first) as a, coach_server(elsewhere, second) as b:
            await call(a, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)
            await call(a, "author_workout", date=WORKOUT_DAY, request=request)
            return (
                (await call(a, "get_digest"))["freshness"]["data_through"],
                (await call(b, "get_digest"))["freshness"]["data_through"],
                (await call(b, "get_plan", week_start=WEEK))["data"],
            )

    first_horizon, second_horizon, second_plan = run(scenario())

    assert (first_horizon, second_horizon) == ("2026-07-03", "2026-08-14")
    assert all(day["source"] != "plan_week" for day in second_plan["days"])
    assert (first / "plans" / f"{WEEK}_week.md").is_file()
    assert (first / "reports" / WORKOUT_DAY / "workout.json").is_file()
    assert not (second / "plans").exists()
    assert not (second / "reports").exists()


def test_two_servers_on_one_installation_see_each_others_writes(tmp_path, elsewhere):
    home = make_installation(tmp_path / "athlete")
    other_start = tmp_path / "another client"
    other_start.mkdir()

    async def scenario():
        async with coach_server(elsewhere, home) as work, coach_server(other_start, home) as code:
            await call(work, "plan_confirm", week_start=WEEK, days=CROSSFIT_WEEK)
            return (await call(code, "get_plan", week_start=WEEK))["data"]

    plan = run(scenario())

    assert all(day["source"] == "plan_week" for day in plan["days"])


def test_the_owners_layout_is_served_unchanged_from_inside_the_installation(tmp_path, monkeypatch):
    """The checkout doubles as the installation: bare server, relative DB, home-folder login."""
    home = make_installation(
        tmp_path / "garmin-coach", env="DB_PATH=./data/garmin.db\nGARMINTOKENS=~/.garminconnect\n"
    )

    async def scenario():
        async with coach_server(home) as session:
            return await call(session, "get_digest")

    assert run(scenario())["freshness"]["data_through"] == "2026-07-03"


def test_the_server_refuses_to_start_on_an_installation_without_a_database(tmp_path, elsewhere):
    home = tmp_path / "athlete"
    home.mkdir()
    (home / ".env").write_text("")
    result = subprocess.run(
        [sys.executable, *_server_args(home)],
        cwd=elsewhere,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "No database" in result.stderr
    assert result.stdout == ""
    assert [path.name for path in home.iterdir()] == [".env"]


@pytest.mark.parametrize("named", [True, False])
def test_the_server_refuses_to_start_without_an_installation(tmp_path, elsewhere, named):
    folder = tmp_path / "plugin cache"
    folder.mkdir()
    start = elsewhere if named else folder
    result = subprocess.run(
        [sys.executable, *_server_args(folder if named else None)],
        cwd=start,
        env=_environment(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 2
    assert "athlete installation" in result.stderr
    assert result.stdout == ""
    assert list(folder.iterdir()) == []
    assert list(elsewhere.iterdir()) == []
