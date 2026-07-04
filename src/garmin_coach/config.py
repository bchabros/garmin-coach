"""Runtime configuration via pydantic-settings (.env + environment)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from `.env` and environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Credentials — email required for the first login; password optional
    # (rely on cached tokens after the first successful login).
    garmin_email: str = ""
    garmin_password: str | None = None

    # Backfill / storage.
    data_start_date: str = "2026-06-08"
    db_path: str = "./data/garmin.db"

    # Where garminconnect caches OAuth tokens.
    garmintokens: str = "~/.garminconnect"


def get_settings() -> Settings:
    """Load runtime settings."""
    return Settings()
