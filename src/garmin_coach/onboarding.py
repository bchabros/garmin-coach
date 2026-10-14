"""Guided, resumable setup of one explicitly selected athlete installation."""

from __future__ import annotations

import datetime as dt
import importlib.resources
import json
import os
import pathlib
import shlex
import sqlite3
import sys
import tempfile
from contextlib import closing
from typing import Any

from . import plan_changes
from .coach import digest, thresholds
from .core import db, installation, plan
from .core.config import Settings
from .etl import client, sync
from .marts import features, snapshot


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
        if not sys.stdin.isatty():
            raise ValueError("Setup needs an interactive terminal; run it in Terminal.")
        root = _destination(home)
        end = _end_date(to_date)
        existing = (root / ".env").is_file()
        print(f"{'Existing' if existing else 'New'} athlete installation: {root}")
        if not existing:
            if root.exists() and any(root.iterdir()):
                raise ValueError("A new installation needs an empty folder; select another folder.")
            _configure(root, end)
        settings = installation.activate(str(root))
        print(f"Configured: {root}; real data starts {settings.data_start_date}.")
        if configure_only:
            return 0
        start = _history_start(settings.data_start_date, end)
        print(f"Import your Garmin history from {start} through {end} (inclusive).")
        print("Login and MFA stay in this terminal; the saved login belongs to this installation.")
        if input("Start or repeat this import? Type yes: ").strip().lower() != "yes":
            print("setup: configured; history import not started.")
            return 0
        return _import(root, settings, start.isoformat(), end.isoformat())
    except (ValueError, OSError, EOFError) as exc:
        print(f"setup: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error:
        print(
            "setup: database unavailable; review the selected installation and retry.",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print("setup: interrupted; re-run setup for the same installation.", file=sys.stderr)
        return 2


def _destination(home: str | None) -> pathlib.Path:
    selected = os.environ.get(installation.HOME_VARIABLE) or None
    if home is None and selected is None:
        raise ValueError("Setup needs --home <folder> or GARMIN_COACH_HOME.")
    root = pathlib.Path(home or selected or "").expanduser().resolve()
    if selected and root != pathlib.Path(selected).expanduser().resolve():
        raise ValueError("Installation conflict: --home and GARMIN_COACH_HOME differ.")
    return root


def _configure(root: pathlib.Path, end: dt.date) -> None:
    # Refuse inherited path/account overrides before creating personal state.
    installation.load(root)
    for key in os.environ:
        if key.upper() in ("DATA_START_DATE", "WEEKLY_TEMPLATE", "GARMIN_PASSWORD"):
            raise ValueError(f"Unset {key.upper()} before configuring a new athlete.")
    start = _history_start(input("First date with your real Garmin history (YYYY-MM-DD): "), end)
    print(f"Weekly intents (Monday to Sunday): {', '.join(plan.INTENTS)}.")
    weekly = input(
        "Enter seven comma-separated intents, or 'none' for no agreed template: "
    ).strip()
    weekly = ",".join(plan.parse_template(weekly)) or "none"
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
    for folder in ("data", "plans", "reports", "logs", "memory"):
        (root / folder).mkdir()
    resources = importlib.resources.files("garmin_coach").joinpath("templates")
    for name, source in (("README.md", "README.md"), ("athlete-profile.md", "profile.md")):
        text = resources.joinpath(source).read_text(encoding="utf-8")
        with (root / "memory" / name).open("x", encoding="utf-8") as target:
            target.write(text.replace("{created_on}", dt.date.today().isoformat()))
    with (root / ".env").open("x", encoding="utf-8") as env:
        env.write(f"DATA_START_DATE={start}\nWEEKLY_TEMPLATE={weekly}\n")
    (root / ".env").chmod(0o600)


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
    first = _resume_from(root, settings, start, end)
    if first > end:
        with closing(installation.open_database(settings)) as conn:
            _first_read(root, settings, conn, start, end, True)
        return 0
    print("setup: logging in to Garmin.")
    try:
        transport = client.login(settings)
    except Exception:
        print(f"setup: Garmin login failed; retry from Terminal: {retry}", file=sys.stderr)
        return 2
    with closing(installation.open_database(settings, create=True)) as conn:
        complete = False
        try:
            print("setup: importing history; completed days are retained if interrupted.")
            sync.backfill(
                transport,
                conn,
                first,
                end,
                on_day_complete=lambda day: _checkpoint(root, settings, start, end, day),
            )
            complete = True
        except (Exception, KeyboardInterrupt):
            conn.rollback()
            print(
                f"setup: import incomplete (interrupted, unavailable or rate-limited). "
                f"Saved history is retained. Retry later: {retry}",
                file=sys.stderr,
            )
        try:
            _first_read(root, settings, conn, start, end, complete)
        except Exception:
            print(f"setup: local read failed; history is retained. Retry: {retry}", file=sys.stderr)
            return 2
    return 0 if complete else 2


def _database_identity(settings: Settings) -> list[str | int]:
    path = pathlib.Path(settings.db_path)
    stat = path.stat()
    return [str(path), stat.st_dev, stat.st_ino]


def _resume_from(root: pathlib.Path, settings: Settings, start: str, end: str) -> str:
    try:
        saved = json.loads((root / "data/setup-import.json").read_text(encoding="utf-8"))
        if (saved["from"], saved["to"], saved["database"]) != (
            start,
            end,
            _database_identity(settings),
        ):
            return start
        last = dt.date.fromisoformat(saved["completed_through"])
        if not start <= last.isoformat() <= end:
            return start
    except (OSError, ValueError, KeyError, TypeError):
        return start
    following = (last + dt.timedelta(days=1)).isoformat()
    print(f"setup: history already imported through {last}; resuming from {following}.")
    return following


def _checkpoint(root: pathlib.Path, settings: Settings, start: str, end: str, day: str) -> None:
    folder = root / "data"
    folder.mkdir(exist_ok=True)
    state = {
        "from": start,
        "to": end,
        "completed_through": day,
        "database": _database_identity(settings),
    }
    with tempfile.NamedTemporaryFile(
        mode="w", dir=folder, delete=False, encoding="utf-8"
    ) as target:
        temporary = pathlib.Path(target.name)
        try:
            json.dump(state, target)
            target.close()
            temporary.replace(folder / "setup-import.json")
        finally:
            temporary.unlink(missing_ok=True)


def _first_read(
    root: pathlib.Path,
    settings: Settings,
    conn: sqlite3.Connection,
    start: str,
    end: str,
    complete: bool,
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
        "import_complete": complete,
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
        reports_dir=root / "reports",
        data_start_date=settings.data_start_date,
        rebuild=False,
    )
    if has_observations:
        features.features(conn, data_start_date=settings.data_start_date, to_date=end)
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
    dated = root / "reports" / end
    dated.mkdir(parents=True, exist_ok=True)
    output = pathlib.Path(tempfile.mkdtemp(prefix="setup-", dir=dated))
    complete = reading["onboarding"]["import_complete"]
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
    complete = reading["onboarding"]["import_complete"]
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
            f"HRV baseline: {headline['hrv_baseline']}; HRV SD: {headline['hrv_sd']} (None = unknown)."
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
    days = conn.execute(
        "SELECT count(DISTINCT date(start_local)) FROM activities "
        "WHERE date(start_local) BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]
    counts = {"activities": days}
    for name, table in db.DAILY_STREAM_TABLES.items():
        # Date-only normalizer rows describe a response, not an athlete measurement.
        rows = conn.execute(f"SELECT * FROM {table} WHERE date BETWEEN ? AND ?", (start, end))
        observed = [
            i
            for i, column in enumerate(rows.description)
            if column[0] not in ("date", "has_data", "valid_sleep")
        ]
        counts[name] = sum(any(row[i] is not None for i in observed) for row in rows)
    return counts
