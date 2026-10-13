"""The selected athlete installation: which folder a run acts on, and its paths (ADR 0031).

An athlete installation is one folder holding one athlete's personal state. A run selects
exactly one, before any configuration or data is read: ``--home`` or
``GARMIN_COACH_HOME`` names it, and with neither the starting folder qualifies only when
it holds a ``.env``. Settings are loaded from that ``.env`` and every relative path in
them resolves against the installation, never against the starting folder.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any

from pydantic_settings import DotEnvSettingsSource

from . import db
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
    variable = os.environ.get(HOME_VARIABLE) or None
    if home is not None and variable is not None and _resolve(home) != _resolve(variable):
        raise InstallationError(
            f"Installation conflict: --home and {HOME_VARIABLE} name different folders; "
            f"pass one of them, or make them agree."
        )
    named = home if home is not None else variable
    if named is None:
        root = pathlib.Path.cwd().resolve()
        if not (root / CONFIG_FILE).is_file():
            raise InstallationError(
                f"No athlete installation selected: pass --home <folder>, set {HOME_VARIABLE}, "
                f"or start from an installation folder (one with a {CONFIG_FILE})."
            )
        return root
    root = _resolve(named)
    if not (root / CONFIG_FILE).is_file():
        raise InstallationError(
            f"Not an athlete installation: {root} has no {CONFIG_FILE}; "
            f"select the intended installation folder."
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

    Only a manual import may create a missing database: everything else refuses, so a
    run started in the wrong place says so instead of coaching from an empty history.

    Args:
        settings: The selected installation's settings.
        create: Whether a missing database may be created (``backfill`` and ``sync``).

    Raises:
        InstallationError: The database is missing and ``create`` is False.
    """
    path = pathlib.Path(settings.db_path)
    if not path.is_file():
        if not create:
            raise InstallationError(
                f"No database at {path}; import the athlete's history first with "
                f"`garmin-coach backfill --from YYYY-MM-DD` (or `garmin-coach sync`)."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(path))
    db.bootstrap(conn)
    return conn


def load(root: pathlib.Path) -> Settings:
    """Load an installation's settings, refusing conflicting environment values.

    Args:
        root: The installation folder (already selected).

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
        update={name: str(_in(root, _configured(configured, name))) for name in PATH_SETTINGS}
    )


def _refuse_conflicts(root: pathlib.Path, configured: dict[str, Any]) -> None:
    for name in PATH_SETTINGS + IDENTITY_SETTINGS:
        value = _environment_value(name)
        if value is None:
            continue
        expected = _configured(configured, name)
        if name in PATH_SETTINGS:
            agrees = _resolve(value) == _in(root, expected)
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


def _environment_value(name: str) -> str | None:
    for key, value in os.environ.items():
        if key.lower() == name:
            return value
    return None


def _in(root: pathlib.Path, value: str) -> pathlib.Path:
    path = pathlib.Path(value).expanduser()
    return (path if path.is_absolute() else root / path).resolve()


def _resolve(value: str | os.PathLike[str]) -> pathlib.Path:
    return pathlib.Path(value).expanduser().resolve()
