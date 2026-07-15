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


def login_api(
    settings: Settings | None = None,
    prompt_mfa: Callable[[], str] | None = None,
) -> Garmin:
    """Log in and return the authenticated garminconnect ``Garmin`` client.

    First run caches OAuth tokens to GARMINTOKENS (mode 0600 by garth); later
    runs resume from them and only re-login when the refresh token expires.
    """
    settings = settings or get_settings()
    tokenstore = _tokenstore(settings)
    prompt_mfa = prompt_mfa or (lambda: input("Garmin MFA code: ").strip())

    api = Garmin()
    try:
        api.login(tokenstore)
        return api
    except Exception:
        pass  # no/expired tokens -> full login below

    # First login needs credentials. Both are optional in config (cached tokens
    # cover later runs), so prompt interactively for whatever isn't set.
    email = settings.garmin_email or input("Garmin email: ").strip()
    password = settings.garmin_password or getpass.getpass(f"Garmin password for {email}: ")
    api = Garmin(
        email=email,
        password=password,
        prompt_mfa=prompt_mfa,
    )
    # Passing the tokenstore makes login() persist OAuth tokens itself, so later
    # runs resume from them (and never hit the rate-limited login endpoint).
    os.makedirs(os.path.expanduser(tokenstore), exist_ok=True)
    api.login(tokenstore)
    return api


def login(
    settings: Settings | None = None,
    prompt_mfa: Callable[[], str] | None = None,
) -> "GarminTransport":
    """Log in (preferring cached tokens) and adapt the client to the read transport."""
    return GarminTransport(login_api(settings, prompt_mfa))


class GarminTransport:
    """Adapts garminconnect's Garmin to the GarminClient protocol used by sync."""

    def __init__(self, api: Garmin):
        self._api = api

    def get_activities(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        """Fetch activity summaries for an inclusive date range."""
        return self._api.get_activities_by_date(start_date, end_date)

    def get_sleep(self, date: str) -> dict[str, Any] | None:
        """Fetch sleep data for one date."""
        return self._api.get_sleep_data(date)

    def get_hrv(self, date: str) -> dict[str, Any] | None:
        """Fetch nightly HRV data for one date."""
        return self._api.get_hrv_data(date)

    def get_wellness(self, date: str) -> dict[str, Any] | None:
        """Fetch daily wellness summary data for one date."""
        return self._api.get_user_summary(date)

    def get_readiness(self, date: str) -> Any:
        """Fetch training readiness data for one date."""
        return self._api.get_training_readiness(date)

    def get_status(self, date: str) -> dict[str, Any] | None:
        """Fetch training status data for one date."""
        return self._api.get_training_status(date)

    def get_activity_weather(self, activity_id: int) -> dict[str, Any] | None:
        """Fetch per-activity weather (temperature is Fahrenheit in the payload)."""
        return self._api.get_activity_weather(str(activity_id))

    def get_activity_exercise_sets(self, activity_id: int) -> dict[str, Any] | None:
        """Fetch per-activity exercise sets (strength/Hyrox work only)."""
        return self._api.get_activity_exercise_sets(str(activity_id))

    def get_lactate_threshold(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch the Lactate Threshold anchor.

        Without a range, the latest detection (the library merges Garmin's raw
        two-entry list into one ``speed_and_heart_rate`` dict). With a range, the
        ranged form (parallel ``speed``/``heart_rate`` series) used by backfill to
        ingest the detection history.
        """
        if start_date is None:
            return self._api.get_lactate_threshold(latest=True)
        return self._api.get_lactate_threshold(
            latest=False, start_date=start_date, end_date=end_date
        )
