"""Runtime configuration via pydantic-settings (an installation's .env + environment)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Width of the re-check window (ADR 0026): a run uploaded from the watch a day late was
# the observed case; three days leaves margin for a weekend without the phone.
DEFAULT_RECHECK_DAYS = 3


class Settings(BaseSettings):
    """Runtime settings loaded from `.env` and environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Credentials — both optional: the first login prompts interactively for
    # whatever is unset (cached tokens cover later runs). Set them only for a
    # non-interactive first run.
    garmin_email: str = ""
    garmin_password: str | None = None

    # Every relative path setting below resolves against the selected installation, never
    # against the folder a run starts in (ADR 0031).

    # Backfill / storage.
    data_start_date: str = "2026-06-08"
    db_path: str = "./data/garmin.db"
    # Explicit first-run choice; None is an adopted installation's existing DB policy.
    weekly_template: str | None = None

    # Re-check window (ADR 0026): the trailing days, ending yesterday, that every
    # incremental sync pulls again. A day is recorded as final on the last of them.
    sync_recheck_days: int = Field(default=DEFAULT_RECHECK_DAYS, ge=1)

    # Authored weekly plans of record (issue #21); gitignored personal data.
    plans_dir: str = "./plans"

    # Where garminconnect caches OAuth tokens: inside the installation unless configured,
    # so two installations never share one Garmin login (ADR 0031).
    garmintokens: str = ".garminconnect"

    # Nightly-run logging. Rotation is size-based, in-process.
    log_path: str = "./logs/daily.log"
    log_max_bytes: int = 1_000_000
    log_backup_count: int = 5


_RUN_SETTINGS: ContextVar[Settings | None] = ContextVar("installation_settings", default=None)


@contextmanager
def use_settings(settings: Settings) -> Iterator[None]:
    """Bind captured settings to one entry-point run and its async tasks."""
    token = _RUN_SETTINGS.set(settings)
    try:
        yield
    finally:
        _RUN_SETTINGS.reset(token)


def get_settings() -> Settings:
    """Return captured run settings, or load an explicitly selected standalone caller.

    Raises:
        InstallationError: No usable installation is selected, or the selection conflicts.
    """
    captured = _RUN_SETTINGS.get()
    if captured is not None:
        return captured
    from . import installation

    return installation.load(installation.select())
