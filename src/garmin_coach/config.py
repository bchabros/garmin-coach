"""Runtime configuration via pydantic-settings (.env + environment)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from `.env` and environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Credentials — both optional: the first login prompts interactively for
    # whatever is unset (cached tokens cover later runs). Set them only for a
    # non-interactive first run.
    garmin_email: str = ""
    garmin_password: str | None = None

    # Backfill / storage.
    data_start_date: str = "2026-06-08"
    db_path: str = "./data/garmin.db"

    # Where garminconnect caches OAuth tokens.
    garmintokens: str = "~/.garminconnect"

    # Nightly-run logging (Phase 4). Rotation is size-based, in-process.
    log_path: str = "./logs/daily.log"
    log_max_bytes: int = 1_000_000
    log_backup_count: int = 5


def get_settings() -> Settings:
    """Load runtime settings."""
    return Settings()
