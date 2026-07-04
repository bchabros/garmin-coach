"""Thin transport over garminconnect: login (+ MFA) and the endpoint->method map.

This is the only module that imports garminconnect. It holds no ETL logic — it
just fetches and returns raw payloads, so sync.py can depend on the GarminClient
protocol and be tested with a fake. Not unit-tested (network/auth); validated by
the live backfill.
"""
from __future__ import annotations

import getpass
import os
from typing import Any, Callable

from garminconnect import Garmin

from .config import Settings, get_settings


def _tokenstore(settings: Settings) -> str:
    return os.path.expanduser(settings.garmintokens)


def login(
    settings: Settings | None = None,
    prompt_mfa: Callable[[], str] | None = None,
) -> "GarminTransport":
    """Log in, preferring cached tokens; fall back to a full login with MFA.

    First run caches OAuth tokens to GARMINTOKENS (mode 0600 by garth); later
    runs resume from them and only re-login when the refresh token expires.
    """
    settings = settings or get_settings()
    tokenstore = _tokenstore(settings)
    prompt_mfa = prompt_mfa or (lambda: input("Garmin MFA code: ").strip())

    api = Garmin()
    try:
        api.login(tokenstore)
        return GarminTransport(api)
    except Exception:
        pass  # no/expired tokens -> full login below

    # First login needs a password. It's optional in config (cached tokens cover
    # later runs), so prompt interactively when it isn't set.
    password = settings.garmin_password or getpass.getpass(
        f"Garmin password for {settings.garmin_email}: "
    )
    api = Garmin(
        email=settings.garmin_email,
        password=password,
        prompt_mfa=prompt_mfa,
    )
    # Passing the tokenstore makes login() persist OAuth tokens itself, so later
    # runs resume from them (and never hit the rate-limited login endpoint).
    os.makedirs(os.path.expanduser(tokenstore), exist_ok=True)
    api.login(tokenstore)
    return GarminTransport(api)


class GarminTransport:
    """Adapts garminconnect's Garmin to the GarminClient protocol used by sync."""

    def __init__(self, api: Garmin):
        self._api = api

    def get_activities(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return self._api.get_activities_by_date(start_date, end_date)

    def get_sleep(self, date: str) -> dict[str, Any] | None:
        return self._api.get_sleep_data(date)

    def get_hrv(self, date: str) -> dict[str, Any] | None:
        return self._api.get_hrv_data(date)

    def get_wellness(self, date: str) -> dict[str, Any] | None:
        return self._api.get_user_summary(date)

    def get_readiness(self, date: str) -> Any:
        return self._api.get_training_readiness(date)

    def get_status(self, date: str) -> dict[str, Any] | None:
        return self._api.get_training_status(date)
