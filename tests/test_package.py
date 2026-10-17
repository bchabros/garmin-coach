"""The built package runs against an athlete installation (issue #83 T5).

The package is built once, installed into a fresh environment, and both commands run
from an unrelated folder. Dependencies are borrowed from the development environment so
the test stays offline; a fully clean install with downloaded dependencies is a manual
check recorded with the change.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
import sysconfig
import zipfile

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tests.test_installation_server import SCRUBBED, make_installation

ROOT = pathlib.Path(__file__).resolve().parents[1]
POETRY = shutil.which("poetry")

pytestmark = pytest.mark.skipif(POETRY is None, reason="building the package needs Poetry")


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> pathlib.Path:
    out = tmp_path_factory.mktemp("dist")
    # Inside the pre-commit hook GIT_DIR/GIT_INDEX_FILE are exported; the build must not
    # inherit them (docs/DEVELOPMENT.md, "Gotchas").
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(
        [POETRY, "build", "--format", "wheel", "--output", str(out)],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    (built,) = out.glob("garmin_coach-*.whl")
    return built


@pytest.fixture(scope="module")
def environment(wheel, tmp_path_factory) -> pathlib.Path:
    """A fresh environment with only the built package installed."""
    venv = tmp_path_factory.mktemp("env") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120
    )
    python = venv / "bin" / "python"
    subprocess.run(
        [sys.executable, "-m", "pip", "--python", str(python), "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    purelib = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Borrow the dependencies (never the development copy of the program itself).
    (pathlib.Path(purelib) / "borrowed.pth").write_text(sysconfig.get_paths()["purelib"] + "\n")
    return venv


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in SCRUBBED and key not in ("PYTHONPATH", "VIRTUAL_ENV")
    }


def _program_files(venv: pathlib.Path) -> set[pathlib.Path]:
    (package,) = venv.glob("lib/python*/site-packages/garmin_coach")
    return {path for path in package.rglob("*") if "__pycache__" not in path.parts}


def test_the_package_carries_the_schema_and_both_commands_and_nothing_personal(wheel):
    names = zipfile.ZipFile(wheel).namelist()
    entry_points = zipfile.ZipFile(wheel).read(
        next(name for name in names if name.endswith("entry_points.txt"))
    )

    assert "garmin_coach/core/schema.sql" in names
    assert b"garmin-coach=" in entry_points.replace(b" ", b"")
    assert b"garmin-coach-mcp=" in entry_points.replace(b" ", b"")
    personal = (".env", "garmin.db", "athlete-profile", "plans/", "reports/")
    assert not [name for name in names if any(part in name for part in personal)]
    assert {name for name in names if name.startswith("skills/")} == {
        "skills/coach/SKILL.md",
        "skills/coach/references/report.md",
        "skills/coach/references/planning.md",
        "skills/coach/references/authoring.md",
    }


def test_packaged_setup_templates_are_empty_and_match_the_coach_profile_contract(wheel):
    with zipfile.ZipFile(wheel) as archive:
        profile = archive.read("garmin_coach/templates/profile.md").decode("utf-8")
        conventions = archive.read("garmin_coach/templates/README.md").decode("utf-8")
    assert "Ostatnia aktualizacja: {created_on}" in profile
    assert "## Otwarte wątki" in profile
    assert "## Decyzje" in profile
    assert all(not line.strip() or line.startswith(("#", "_")) for line in profile.splitlines())
    assert "Every edit" in conventions


def test_installed_client_setup_carries_all_references_without_a_source_checkout(
    environment,
    tmp_path,
):
    home = make_installation(tmp_path / "athlete")
    runtime = tmp_path / "program"
    env = _clean_environment()
    env["PATH"] = ""
    result = subprocess.run(
        [
            str(environment / "bin/python"),
            "-m",
            "garmin_coach.clients",
            "--home",
            str(home),
            "--runtime-root",
            str(runtime),
            "configure",
            "--clients",
            "codex",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    package = pathlib.Path(output["integration_root"]) / "plugin"
    assert (package / "skills/coach/references/report.md").is_file()
    assert output["clients"]["codex"]["state"] == "client_missing"


def test_the_installed_program_is_the_built_package(environment):
    location = subprocess.run(
        [
            str(environment / "bin" / "python"),
            "-c",
            "import garmin_coach; print(garmin_coach.__file__)",
        ],
        env=_clean_environment(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert location.startswith(str(environment))


def test_both_installed_commands_run_against_an_installation_from_an_unrelated_folder(
    environment, tmp_path
):
    home = make_installation(tmp_path / "athlete")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = _program_files(environment)

    cli = subprocess.run(
        [str(environment / "bin" / "garmin-coach"), "--home", str(home), "report"],
        cwd=elsewhere,
        env=_clean_environment(),
        capture_output=True,
        text=True,
        timeout=120,
    )

    async def read_digest():
        params = StdioServerParameters(
            command=str(environment / "bin" / "garmin-coach-mcp"),
            args=["--home", str(home)],
            env=_clean_environment(),
            cwd=str(elsewhere),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("get_digest", {})
                return result.structuredContent

    digest = asyncio.run(asyncio.wait_for(read_digest(), timeout=60))

    assert cli.returncode == 0, cli.stderr
    assert list((home / "reports").glob("*/digest.json"))
    assert digest["freshness"]["data_through"] == "2026-07-03"
    assert list(elsewhere.iterdir()) == []
    assert _program_files(environment) == before
