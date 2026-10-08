"""Launch the selected checkout with its Poetry runtime, including from GUI clients."""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys


def _runtime_works(python: pathlib.Path, env: dict[str, str]) -> bool:
    try:
        result = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; assert sys.version_info >= (3, 13); import mcp, pydantic_settings, garmin_coach",
            ],
            env=env,
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    python = root / ".venv" / "bin" / "python"
    if not python.is_file():
        poetry = shutil.which("poetry")
        if not poetry:
            print(
                "coach-project: Poetry runtime missing; run poetry install in the checkout.",
                file=sys.stderr,
            )
            return 1
        try:
            result = subprocess.run(
                [poetry, "env", "info", "--executable"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            print(
                "coach-project: Poetry unavailable; run poetry install in the checkout.",
                file=sys.stderr,
            )
            return 1
        python = pathlib.Path(result.stdout.strip())
        if result.returncode or not python.is_file():
            print(
                "coach-project: runtime missing; run poetry install in the checkout.",
                file=sys.stderr,
            )
            return 1
    env = dict(os.environ, PYTHONPATH=str(root / "src"))
    if not _runtime_works(python, env):
        print(
            "coach-project: runtime unusable; run poetry install with Python 3.13 or newer.",
            file=sys.stderr,
        )
        return 1
    os.execve(
        python,
        [str(python), "-m", "garmin_coach.project", *sys.argv[1:]],
        env,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
