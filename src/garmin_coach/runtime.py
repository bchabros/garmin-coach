"""One selected installation and captured configuration per entry-point run."""

from __future__ import annotations

import pathlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .core import installation
from .core.config import Settings, use_settings

if TYPE_CHECKING:
    from .etl.sync import GarminClient
    from .workouts.publish import WorkoutPublisher


def _connect_import(settings: Settings) -> GarminClient:
    from .etl import client

    return client.login(settings)


def _connect_publisher(settings: Settings) -> WorkoutPublisher:
    from .workouts import publish

    return publish.connect_publisher(settings)


_ACTIVE: ContextVar[InstallationContext | None] = ContextVar("installation_context", default=None)


@dataclass(frozen=True)
class InstallationContext:
    """Captured installation choices, without cached records or an open connection."""

    home: pathlib.Path
    settings: Settings = field(repr=False)
    import_factory: Callable[[Settings], GarminClient] = field(default=_connect_import, repr=False)
    publisher_factory: Callable[[Settings], WorkoutPublisher] = field(
        default=_connect_publisher, repr=False
    )
    existing_db_only: bool = False

    @classmethod
    def select(
        cls,
        home: str | pathlib.Path | None = None,
        *,
        import_factory: Callable[[Settings], GarminClient] = _connect_import,
        publisher_factory: Callable[[Settings], WorkoutPublisher] = _connect_publisher,
        existing_db_only: bool = False,
    ) -> InstallationContext:
        """Select and validate an installation, capturing its configuration once."""
        settings = installation.activate(home)
        return cls(
            home=installation.select(),
            settings=settings,
            import_factory=import_factory,
            publisher_factory=publisher_factory,
            existing_db_only=existing_db_only,
        )

    @property
    def plans_dir(self) -> pathlib.Path:
        """The captured configured plans folder."""
        return pathlib.Path(self.settings.plans_dir)

    @property
    def reports_dir(self) -> pathlib.Path:
        """The captured installation's live dated-artifact folder."""
        return self.home / installation.REPORTS_FOLDER

    def open_database(self, *, create: bool = False) -> sqlite3.Connection:
        """Open a fresh connection using captured settings, never cache records."""
        if self.existing_db_only:
            if create:
                raise installation.InstallationError("Guarded context cannot create storage.")
            return installation.open_existing_database(self.settings, writable=True)
        return installation.open_database(self.settings, create=create)

    def connect_import(self) -> GarminClient:
        """Create import transport only for an explicit authorized operation."""
        return self.import_factory(self.settings)

    def connect_publisher(self) -> WorkoutPublisher:
        """Create workout transport without retaining an account session."""
        return self.publisher_factory(self.settings)

    @contextmanager
    def use(self) -> Iterator[InstallationContext]:
        """Use this context for a command/server run, including child async tasks."""
        token = _ACTIVE.set(self)
        try:
            with use_settings(self.settings):
                yield self
        finally:
            _ACTIVE.reset(token)


def current() -> InstallationContext:
    """Return the context established by the command or server entry point."""
    context = _ACTIVE.get()
    if context is None:
        raise installation.InstallationError("No run context; start the command or coach server.")
    return context


def settings_for(home: pathlib.Path) -> Settings:
    """Reuse a run's settings for an explicit home, or select a standalone operation.

    Raises:
        InstallationError: An operation targets another installation within this run.
    """
    context = _ACTIVE.get()
    if context is None:
        return installation.activate(home)
    resolved = home.expanduser().resolve()
    if resolved != context.home and not resolved.samefile(context.home):
        raise installation.InstallationError("Operation differs from the run's installation.")
    return context.settings
