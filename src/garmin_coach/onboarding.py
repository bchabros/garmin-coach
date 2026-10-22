"""Guided, resumable setup of one explicitly selected athlete installation."""

from __future__ import annotations

import datetime as dt
import importlib.resources
import json
import logging
import os
import pathlib
import shlex
import sqlite3
import sys
import tempfile
from contextlib import closing
from collections.abc import Callable
from typing import Any

from dotenv import dotenv_values

from . import distribution, plan_changes
from .coach import digest, thresholds
from .core import db, installation, plan
from .core.config import Settings
from .etl import client, sync
from .marts import features, snapshot

logger = logging.getLogger(__name__)


def run(home: str | None, *, configure_only: bool = False, to_date: str | None = None) -> int:
    """Configure a new installation or adopt existing state without replacing it.

    Args:
        home: Explicit destination, or the installation selection variable.
        configure_only: Stop before authentication and history import.
        to_date: Inclusive import end date; defaults to yesterday.

    Returns:
        Zero on success, two on refusal or failure.
    """
    try:
        return _run(home, configure_only=configure_only, to_date=to_date)
    except (ValueError, OSError, EOFError) as exc:
        _record_failure("configuration", exc)
        print(f"setup: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        _record_failure("database", exc)
        print(
            "setup: database unavailable; review the selected installation and retry.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt as exc:
        _record_failure("setup", exc)
        print("setup: interrupted; re-run setup for the same installation.", file=sys.stderr)
        return 2


def _run(home: str | None, *, configure_only: bool, to_date: str | None) -> int:
    if not sys.stdin.isatty():
        raise ValueError("Setup needs an interactive terminal; run it in Terminal.")
    _refuse_first_run_overrides()
    root, end = _destination(home), _end_date(to_date)
    settings = _configure_installation(root, end)
    print(f"Configured: {root}; real data starts {settings.data_start_date}.")
    if configure_only:
        return 0
    start = _history_start(settings.data_start_date, end).isoformat()
    adopted = _adopt_existing(root, settings, start, end.isoformat())
    if adopted is not None:
        return adopted
    print(f"Import your Garmin history from {start} through {end} (inclusive).")
    print("Login and MFA stay in this terminal; the saved login belongs to this installation.")
    if input("Start or repeat this import? Type yes: ").strip().lower() != "yes":
        print("setup: configured; history import not started.")
        return 0
    return _import(root, settings, start, end.isoformat())


def _configure_installation(root: pathlib.Path, end: dt.date) -> Settings:
    existing = (root / installation.CONFIG_FILE).is_file()
    print(f"{'Existing' if existing else 'New'} athlete installation: {root}")
    if not existing:
        if root.exists() and any(root.iterdir()):
            raise ValueError("A new installation needs an empty folder; select another folder.")
        _configure(root, end)
    settings = installation.activate(str(root))
    if not pathlib.Path(settings.garmintokens).resolve().is_relative_to(root):
        raise ValueError(
            "Setup requires GARMINTOKENS inside this athlete installation; "
            "select an installation-owned token folder before importing history."
        )
    if pathlib.Path(settings.db_path).exists():
        configured = {
            key.lower(): value
            for key, value in dotenv_values(root / installation.CONFIG_FILE).items()
        }
        if not configured.get("data_start_date"):
            raise ValueError(
                "Set DATA_START_DATE to this athlete's first real history date "
                "in the installation configuration before adopting existing history."
            )
    else:
        settings = _complete_first_configuration(root, settings, end)
        _seed_folders(root)
    return settings


def _destination(home: str | None) -> pathlib.Path:
    selected = os.environ.get(installation.HOME_VARIABLE) or None
    if home is None and selected is None:
        raise ValueError("Setup needs --home <folder> or GARMIN_COACH_HOME.")
    root = pathlib.Path(home or selected or "").expanduser().resolve()
    if selected and root != pathlib.Path(selected).expanduser().resolve():
        raise ValueError("Installation conflict: --home and GARMIN_COACH_HOME differ.")
    return root


def _adopt_existing(root: pathlib.Path, settings: Settings, start: str, end: str) -> int | None:
    if (
        not pathlib.Path(settings.db_path).is_file()
        or _checkpoint_path(root).exists()
        or any((root / installation.REPORTS_FOLDER).glob("*/setup-*/status.json"))
    ):
        return None
    with closing(installation.read_database(settings)) as conn:
        observations = _observation_dates(conn, start, end)
        coverage = {name: len(dates) for name, dates in observations.items()}
        if not any(coverage.values()):
            return None
        through = max(date for dates in observations.values() for date in dates)
    print(
        "Stored observations: " + ", ".join(f"{name}={count}" for name, count in coverage.items())
    )
    print(f"Existing history is readable through {through}; coverage may be incomplete.")
    choice = (
        input("Use existing local history? Type yes, or 'import' to fetch history from Garmin: ")
        .strip()
        .lower()
    )
    if choice == "import":
        return None
    if choice != "yes":
        print("setup: existing history preserved; no import or coach read requested.")
        return 0
    with closing(installation.open_database(settings)) as conn:
        _first_read(root, settings, conn, start, through, complete=True, adopted=True)
    return 0


def _checkpoint_path(root: pathlib.Path) -> pathlib.Path:
    return root / "data/setup-import.json"


def _configure(root: pathlib.Path, end: dt.date) -> None:
    # Refuse inherited path/account overrides before creating personal state.
    installation.load(root)
    _refuse_first_run_overrides()
    start = _history_start(input("First date with your real Garmin history (YYYY-MM-DD): "), end)
    weekly = _weekly_choice()
    print(f"Create {root}; data start {start}; weekly template {weekly}.")
    if input("Continue? Type yes: ").strip().lower() != "yes":
        raise ValueError("Setup cancelled; nothing created.")
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".coach-setup-", dir=root.parent) as temporary:
        staged = pathlib.Path(temporary) / "installation"
        staged.mkdir(mode=0o700)
        _write_template(staged, start, weekly)
        staged.rename(root)


def _write_template(root: pathlib.Path, start: dt.date, weekly: str) -> None:
    _seed_folders(root)
    with (root / installation.CONFIG_FILE).open("x", encoding="utf-8") as env:
        env.write(f"DATA_START_DATE={start}\nWEEKLY_TEMPLATE={weekly}\n")
    (root / installation.CONFIG_FILE).chmod(0o600)


def _seed_folders(root: pathlib.Path) -> None:
    for folder in ("data", "plans", "reports", "logs", "memory"):
        (root / folder).mkdir(exist_ok=True)
    resources = importlib.resources.files("garmin_coach").joinpath("templates")
    for name, source in (("README.md", "README.md"), ("athlete-profile.md", "profile.md")):
        if (root / "memory" / name).exists():
            continue
        text = resources.joinpath(source).read_text(encoding="utf-8")
        with (root / "memory" / name).open("x", encoding="utf-8") as target:
            target.write(text.replace("{created_on}", dt.date.today().isoformat()))


def _weekly_choice() -> str:
    print(f"Weekly intents (Monday to Sunday): {', '.join(plan.INTENTS)}.")
    choice = input(
        "Enter seven comma-separated intents, or 'none' for no agreed template: "
    ).strip()
    return ",".join(plan.parse_template(choice)) or "none"


def _complete_first_configuration(root: pathlib.Path, settings: Settings, end: dt.date) -> Settings:
    _refuse_first_run_overrides()
    path = root / installation.CONFIG_FILE
    configured = {key.lower(): value for key, value in dotenv_values(path).items()}
    if configured.get("data_start_date"):
        _history_start(str(configured["data_start_date"]), end)
    if configured.get("weekly_template"):
        plan.parse_template(str(configured["weekly_template"]))
    additions = []
    if not configured.get("data_start_date"):
        start = _history_start(
            input("First date with your real Garmin history (YYYY-MM-DD): "), end
        )
        additions.append(f"DATA_START_DATE={start}")
    if not configured.get("weekly_template"):
        additions.append(f"WEEKLY_TEMPLATE={_weekly_choice()}")
    if not additions:
        return settings
    print("Add first-run choices to the existing configuration:\n" + "\n".join(additions))
    if input("Continue? Type yes: ").strip().lower() != "yes":
        raise ValueError("Setup cancelled; existing configuration is unchanged.")
    _append_configuration(path, additions)
    return installation.activate(str(root))


def _refuse_first_run_overrides() -> None:
    for key in os.environ:
        if key.upper() in ("DATA_START_DATE", "WEEKLY_TEMPLATE", "GARMIN_PASSWORD"):
            raise ValueError(f"Unset {key.upper()} before running athlete setup.")


def _append_configuration(path: pathlib.Path, additions: list[str]) -> None:
    original = path.read_bytes()
    separator = b"" if not original or original.endswith(b"\n") else b"\n"
    content = original + separator + ("\n".join(additions) + "\n").encode("utf-8")
    distribution.atomic_write(path, content, mode=path.stat().st_mode & 0o777)


def _end_date(value: str | None) -> dt.date:
    try:
        end = dt.date.fromisoformat(value) if value else dt.date.today() - dt.timedelta(days=1)
    except ValueError:
        raise ValueError("Import end must be a date in YYYY-MM-DD format.") from None
    if end >= dt.date.today():
        raise ValueError("Import through yesterday or earlier; today is incomplete.")
    return end


def _history_start(value: str, end: dt.date) -> dt.date:
    try:
        start = dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("History start must be a date in YYYY-MM-DD format.") from None
    if start > end:
        raise ValueError("History start must be on or before the import end.")
    return start


def _import(root: pathlib.Path, settings: Settings, start: str, end: str) -> int:
    retry = shlex.join(["garmin-coach", "--home", str(root), "setup", "--to", end])
    first = _resume_from(root, settings, start)
    if first > end:
        with closing(installation.open_database(settings)) as conn:
            _first_read(root, settings, conn, start, end, complete=True)
        return 0
    print("setup: logging in to Garmin.")
    try:
        with client.redacted_logs():
            transport = client.login(settings)
    except Exception as exc:
        kind = _record_failure("login", exc)
        print(f"setup: Garmin login failed ({kind}); retry from Terminal: {retry}", file=sys.stderr)
        return 2
    with closing(installation.open_database(settings, create=True)) as conn:
        if not _checkpoint_path(root).exists():
            _checkpoint(root, settings, start, end, None)
        print("setup: importing history; completed days are retained if interrupted.")
        failure = _backfill(
            transport, conn, first, end, lambda day: _checkpoint(root, settings, start, end, day)
        )
        complete = failure is None
        if failure:
            completed = _completed_through(root, settings, start) or "none"
            print(
                f"setup: import incomplete ({failure}); last completed day: {completed}. "
                f"Saved history is retained. Retry later: {retry}",
                file=sys.stderr,
            )
        try:
            _first_read(root, settings, conn, start, end, complete=complete, failure=failure)
        except Exception as exc:
            _record_failure("local_read", exc)
            print(f"setup: local read failed; history is retained. Retry: {retry}", file=sys.stderr)
            return 2
    return 0 if complete else 2


def _record_failure(stage: str, exc: BaseException) -> str:
    logger.warning("onboarding: %s failed (%s)", stage, type(exc).__name__)
    return client.failure_kind(exc)


def _backfill(
    transport: sync.GarminClient,
    conn: sqlite3.Connection,
    first: str,
    end: str,
    checkpoint: Callable[[str], None],
) -> str | None:
    try:
        with client.redacted_logs():
            sync.backfill(
                transport,
                conn,
                first,
                end,
                on_day_complete=checkpoint,
                stop_on_enrichment_error=lambda exc: client.failure_kind(exc) == "rate_limited",
            )
    except (Exception, KeyboardInterrupt) as exc:
        conn.rollback()
        return _record_failure("history_import", exc)
    return None


def _database_identity(settings: Settings) -> list[str | int]:
    path = pathlib.Path(settings.db_path)
    stat = path.stat()
    return [str(path), stat.st_dev, stat.st_ino]


def _resume_from(root: pathlib.Path, settings: Settings, start: str) -> str:
    last = _completed_through(root, settings, start)
    if last is None:
        return start
    following = (dt.date.fromisoformat(last) + dt.timedelta(days=1)).isoformat()
    print(f"setup: history already imported through {last}; resuming from {following}.")
    return following


def _completed_through(root: pathlib.Path, settings: Settings, start: str) -> str | None:
    try:
        saved = json.loads(_checkpoint_path(root).read_text(encoding="utf-8"))
        if (saved["from"], saved["database"]) != (start, _database_identity(settings)):
            return None
        last = dt.date.fromisoformat(saved["completed_through"])
        saved_end = dt.date.fromisoformat(saved["to"])
        if not dt.date.fromisoformat(start) <= last <= saved_end:
            return None
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return last.isoformat()


def _checkpoint(
    root: pathlib.Path, settings: Settings, start: str, end: str, day: str | None
) -> None:
    folder = root / "data"
    folder.mkdir(exist_ok=True)
    state = {
        "from": start,
        "to": end,
        "completed_through": day,
        "database": _database_identity(settings),
    }
    distribution.atomic_write(_checkpoint_path(root), json.dumps(state).encode())


def _first_read(
    root: pathlib.Path,
    settings: Settings,
    conn: sqlite3.Connection,
    start: str,
    end: str,
    complete: bool,
    *,
    adopted: bool = False,
    failure: str | None = None,
) -> None:
    coverage = _coverage(conn, start, end)
    missing = [name for name, count in coverage.items() if count == 0]
    reading: dict[str, Any] = {}
    if complete:
        _prepare_read(root, settings, conn, end, has_observations=any(coverage.values()))
        reading = digest.build_digest(
            conn,
            thresholds=thresholds.read(conn),
            recheck_days=settings.sync_recheck_days,
            from_date=start,
            to_date=end,
        )
    reading["onboarding"] = {
        "from": start,
        "to": end,
        "import_complete": None if adopted else complete,
        "read_ready": complete,
        "history_source": "existing" if adopted else "initial_import",
        "failure_kind": failure,
        "completed_through": _completed_through(root, settings, start) if not adopted else None,
        "missing_streams": missing,
        "days_with_observations": coverage,
    }
    output = _save_read(root, conn, end, reading)
    _describe_read(output, reading, coverage, db.count_activities(conn, start, end))


def _prepare_read(
    root: pathlib.Path,
    settings: Settings,
    conn: sqlite3.Connection,
    end: str,
    *,
    has_observations: bool,
) -> None:
    imported = plan_changes.import_plans(
        conn,
        plans_dir=settings.plans_dir,
        reports_dir=root / installation.REPORTS_FOLDER,
        data_start_date=settings.data_start_date,
        rebuild=False,
    )
    if has_observations:
        latest = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0]
        horizon = max(end, latest) if latest is not None else end
        features.features(conn, data_start_date=settings.data_start_date, to_date=horizon)
    if imported.error:
        print(
            "setup: authored plan import failed; fix the plan files and repeat setup.",
            file=sys.stderr,
        )
        raise ValueError("Authored plans need repair before the first coach read.")
    if imported.invalidated_pushes:
        print(
            "setup: imported plans conflict with pushed workouts; review them with `plan import`."
        )


def _save_read(
    root: pathlib.Path,
    conn: sqlite3.Connection,
    end: str,
    reading: dict[str, Any],
) -> pathlib.Path:
    dated = root / installation.REPORTS_FOLDER / end
    dated.mkdir(parents=True, exist_ok=True)
    output = pathlib.Path(tempfile.mkdtemp(prefix="setup-", dir=dated))
    complete = reading["onboarding"]["read_ready"]
    name = "digest.json" if complete else "status.json"
    (output / name).write_text(json.dumps(reading, indent=2), encoding="utf-8")
    standing = snapshot.read(conn) if complete else None
    if complete and standing is not None and standing["computed_at"] == end:
        snapshot.write_json(standing, output)
    return output


def _describe_read(
    output: pathlib.Path,
    reading: dict[str, Any],
    coverage: dict[str, int],
    activities: int,
) -> None:
    missing = reading["onboarding"]["missing_streams"]
    complete = reading["onboarding"]["read_ready"]
    label, name = (
        ("First coach read", "digest.json")
        if complete
        else ("Partial import status", "status.json")
    )
    print(f"{label}: {output / name}")
    if not any(coverage.values()):
        print("No recorded training or daily measurements in this range; evidence is missing.")
    elif complete:
        print(f"Recorded activities: {activities}.")
        headline = reading["headline"]
        print(
            f"HRV baseline: {headline['hrv_baseline']}; "
            f"HRV SD: {headline['hrv_sd']} (None = unknown)."
        )
    if missing:
        print(f"No observations for: {', '.join(missing)}; these are not zero measurements.")
    print(
        "Days with observations: "
        + ", ".join(f"{name}={count}" for name, count in coverage.items())
    )
    if not complete:
        print("No coach read yet: finish the import before interpreting training or recovery.")
    print(
        "Algorithm thresholds are policy defaults; personal measurements come only from your data."
    )


def _coverage(conn: sqlite3.Connection, start: str, end: str) -> dict[str, int]:
    return {name: len(dates) for name, dates in _observation_dates(conn, start, end).items()}


def _observation_dates(conn: sqlite3.Connection, start: str, end: str) -> dict[str, list[str]]:
    days = conn.execute(
        "SELECT DISTINCT date(start_local) FROM activities WHERE date(start_local) BETWEEN ? AND ?",
        (start, end),
    )
    observations = {"activities": [row[0] for row in days]}
    for name, table in db.DAILY_STREAM_TABLES.items():
        # Date-only normalizer rows describe a response, not an athlete measurement.
        rows = conn.execute(f"SELECT * FROM {table} WHERE date BETWEEN ? AND ?", (start, end))
        observed = [
            i
            for i, column in enumerate(rows.description)
            if column[0] not in ("date", "has_data", "valid_sleep")
        ]
        date_index = next(i for i, column in enumerate(rows.description) if column[0] == "date")
        observations[name] = [
            row[date_index] for row in rows if any(row[i] is not None for i in observed)
        ]
    return observations
