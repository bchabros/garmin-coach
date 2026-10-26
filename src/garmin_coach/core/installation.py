"""The selected athlete installation: which folder a run acts on, and its paths (ADR 0031).

An athlete installation is one folder holding one athlete's personal state. A run selects
exactly one, before any configuration or data is read: ``--home`` or
``GARMIN_COACH_HOME`` names it, and with neither the starting folder qualifies only when
it holds a ``.env``. Settings are loaded from that ``.env`` and every relative path in
them resolves against the installation, never against the starting folder.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_settings import DotEnvSettingsSource

from . import db, plan
from .config import Settings

HOME_VARIABLE = "GARMIN_COACH_HOME"
CONFIG_FILE = ".env"
REPORTS_FOLDER = "reports"

# Settings that decide where a run reads and writes, or whose Garmin account it logs in
# to. An environment value for one of them must agree with the installation's own
# configuration; any other setting keeps the usual environment-over-.env precedence.
PATH_SETTINGS = ("db_path", "plans_dir", "log_path", "garmintokens")
IDENTITY_SETTINGS = ("garmin_email",)


class InstallationError(ValueError):
    """No usable installation was selected, or the selection contradicts itself."""


@dataclass(frozen=True)
class InstallationEvidence:
    """Schema and available dates, interpreted separately by each installation workflow."""

    schema: Literal["missing", "incompatible", "compatible"]
    data_through: str | None = None
    snapshot_computed_at: str | None = None


def inspect_database(settings: Settings) -> InstallationEvidence:
    """Observe schema and data availability without creating or migrating history."""
    if not pathlib.Path(settings.db_path).is_file():
        return InstallationEvidence("missing")
    try:
        with closing(read_database(settings)) as conn:
            horizon = conn.execute("SELECT MAX(date) FROM daily_metrics").fetchone()[0]
            snapshot = conn.execute("SELECT computed_at FROM athlete_status WHERE id=1").fetchone()
    except (sqlite3.Error, OSError, InstallationError):
        return InstallationEvidence("incompatible")
    return InstallationEvidence("compatible", horizon, snapshot[0] if snapshot else None)


def add_home_argument(parser: argparse.ArgumentParser) -> None:
    """Give an entry point the ``--home`` option that names the installation."""
    parser.add_argument(
        "--home",
        default=None,
        help=(
            f"Athlete installation to act on: a folder holding a {CONFIG_FILE} "
            f"(default: {HOME_VARIABLE}, else the current folder when it holds a {CONFIG_FILE})."
        ),
    )


def activate(home: str | os.PathLike[str] | None = None) -> Settings:
    """Select the run's installation and make it the process-wide selection.

    The selected folder is exported as ``GARMIN_COACH_HOME``, so every later settings
    read in this process - and every child process it starts - acts on the same one.

    Args:
        home: The folder named on the command line, or None when none was named.

    Returns:
        The selected installation's settings, with absolute paths.

    Raises:
        InstallationError: Nothing usable was selected, or the selection conflicts.
    """
    root = select(home)
    settings = load(root)
    os.environ[HOME_VARIABLE] = str(root)
    return settings


def select(home: str | os.PathLike[str] | None = None) -> pathlib.Path:
    """Return the selected installation's folder.

    Args:
        home: The folder named on the command line, or None when none was named.

    Raises:
        InstallationError: Nothing usable was selected, or the selection conflicts.
    """
    from_variable = os.environ.get(HOME_VARIABLE) or None
    if home is not None and from_variable is not None and _resolve(home) != _resolve(from_variable):
        raise InstallationError(
            f"Installation conflict: --home and {HOME_VARIABLE} name different folders; "
            f"pass one of them, or make them agree."
        )
    named = home if home is not None else from_variable
    if named is None:
        root = pathlib.Path.cwd().resolve()
        if not (root / CONFIG_FILE).is_file():
            raise InstallationError(
                f"No athlete installation selected: pass --home <folder>, set {HOME_VARIABLE}, "
                f"or start inside an athlete installation (a folder holding a {CONFIG_FILE})."
            )
        return root
    root = _resolve(named)
    if not (root / CONFIG_FILE).is_file():
        raise InstallationError(
            f"Not an athlete installation: {root} has no {CONFIG_FILE}; "
            f"select the intended athlete installation."
        )
    return root


def reports_dir() -> pathlib.Path:
    """The selected installation's folder of dated reports, workout specs and receipts.

    Raises:
        InstallationError: No usable installation is selected.
    """
    return select() / REPORTS_FOLDER


def open_database(settings: Settings, *, create: bool = False) -> sqlite3.Connection:
    """Open the selected installation's database, bootstrapping its schema.

    Only a manual import (including confirmed setup) may create a missing database.
    Other commands started in the wrong place refuse to coach from an empty history.

    Args:
        settings: The selected installation's settings.
        create: Whether a missing database may be created (manual import or setup).

    Raises:
        InstallationError: The database is missing and ``create`` is False.
    """
    path = pathlib.Path(settings.db_path)
    is_new = not path.exists()
    try:
        template = (
            plan.parse_template(settings.weekly_template)
            if is_new and settings.weekly_template
            else []
        )
    except ValueError:
        raise InstallationError(
            "WEEKLY_TEMPLATE must contain seven valid intents, or 'none'."
        ) from None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        require_database(settings)
    conn = db.connect(str(path))
    db.bootstrap(conn)
    if is_new and template:
        conn.executemany(
            "INSERT INTO plan_template(dow, planned, intent) VALUES (?, ?, ?)",
            [(dow, intent, intent) for dow, intent in enumerate(template)],
        )
        conn.commit()
    return conn


def require_database(settings: Settings) -> None:
    """Refuse an installation whose database has never been imported.

    Raises:
        InstallationError: The selected installation has no database yet.
    """
    path = pathlib.Path(settings.db_path)
    if not path.is_file():
        raise InstallationError(
            f"No database at {path}; import the athlete's history first with "
            f"`garmin-coach backfill --from YYYY-MM-DD` (or `garmin-coach sync`)."
        )


def read_database(settings: Settings) -> sqlite3.Connection:
    """Inspect an existing installation without creating, seeding or migrating it.

    Args:
        settings: The selected athlete installation's resolved settings.

    Raises:
        InstallationError: The selected database does not exist.
        sqlite3.DatabaseError: Its schema needs an explicit migration first.
    """
    return open_existing_database(settings)


def open_existing_database(settings: Settings, *, writable: bool = False) -> sqlite3.Connection:
    """Open validated existing storage without bootstrap, seeding or migration.

    Args:
        settings: Resolved selected-installation settings.
        writable: Permit explicit local operations, retaining foreign-key enforcement.

    Raises:
        InstallationError: The selected database is missing.
        sqlite3.Error: Storage is unavailable or its schema is incompatible.
    """
    require_database(settings)
    mode = "rw" if writable else "ro"
    uri = pathlib.Path(settings.db_path).expanduser().resolve().as_uri() + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True)
    try:
        if writable:
            conn.execute("PRAGMA foreign_keys = ON")
        db.validate_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def backup_database(settings: Settings, destination: pathlib.Path) -> None:
    """Save a private consistent SQLite snapshot without migrating its source.

    Older schemas are deliberately accepted: recovery must be possible before an
    upgrade. SQLite's backup API includes committed WAL records. The destination
    must not exist, and its parent directory must already have been prepared.

    Args:
        settings: Selected athlete installation's resolved settings.
        destination: New snapshot file, created with private permissions.

    Raises:
        InstallationError: The selected source database is missing.
        OSError: The destination already exists or cannot be created.
        sqlite3.Error: SQLite could not complete the snapshot.
    """
    require_database(settings)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    source = pathlib.Path(settings.db_path).as_uri() + "?mode=ro"
    target = destination.resolve().as_uri() + "?mode=rw"
    with closing(sqlite3.connect(source, uri=True)) as original:
        with closing(sqlite3.connect(target, uri=True)) as snapshot:
            original.backup(snapshot)


def load(root: pathlib.Path) -> Settings:
    """Load an installation's settings, refusing conflicting environment values.

    Args:
        root: The selected installation's root (already selected).

    Returns:
        Settings read from the installation's ``.env`` and the environment, with every
        path absolute and relative ones resolved against ``root``.

    Raises:
        InstallationError: The configuration is unreadable, or an environment value for a
            path or identity setting differs from the installation's configuration.
    """
    env_file = root / CONFIG_FILE
    try:
        configured = DotEnvSettingsSource(Settings, env_file=env_file)()
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    except Exception:
        raise InstallationError(
            f"Invalid runtime settings in {env_file}; review that installation's configuration."
        ) from None
    _refuse_conflicts(root, configured)
    return settings.model_copy(
        update={name: str(_within(root, _configured(configured, name))) for name in PATH_SETTINGS}
    )


def _refuse_conflicts(root: pathlib.Path, configured: dict[str, Any]) -> None:
    for name in PATH_SETTINGS + IDENTITY_SETTINGS:
        expected = _configured(configured, name)
        for value in _environment_values(name):
            if name in PATH_SETTINGS:
                agrees = _resolve(value) == _within(root, expected)
            else:
                agrees = value.strip().casefold() == expected.strip().casefold()
            if not agrees:
                variable = name.upper()
                raise InstallationError(
                    f"Installation conflict: {variable} differs from the selected installation's "
                    f"configuration ({root / CONFIG_FILE}); unset {variable} or intentionally "
                    f"update that installation's {CONFIG_FILE}."
                )


def _configured(configured: dict[str, Any], name: str) -> str:
    """The installation's own value for a setting: its `.env` entry, else the default."""
    return str(configured.get(name, Settings.model_fields[name].default) or "")


def _environment_values(name: str) -> list[str]:
    return [value for key, value in os.environ.items() if key.lower() == name]


def _within(root: pathlib.Path, value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _resolve(value: str | os.PathLike[str]) -> pathlib.Path:
    return pathlib.Path(value).expanduser().resolve()
