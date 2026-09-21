"""Thin transport over garminconnect: login (+ MFA) and the endpoint->method map.

This is the only module that imports garminconnect. It holds no ETL logic — it
just fetches and returns raw payloads, so sync.py can depend on the GarminClient
protocol and be tested with a fake. The endpoint map is not unit-tested
(network); login's prompt gate and saved-login resume are, offline.
"""

from __future__ import annotations

import contextlib
import getpass
import logging
import os
import sys
import time
from collections.abc import Iterator
from typing import Any, Callable

from garminconnect import Garmin

from ..core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Waits between the three attempts to resume the saved login. A network that is not
# up yet just after the machine wakes needs several seconds, not milliseconds.
_RESUME_DELAYS_S = (5.0, 15.0)
_LIBRARY_LOGGER = "garminconnect"


class LoginUnavailableError(RuntimeError):
    """Login needs an answer typed at a terminal, and no terminal is attached."""


def _tokenstore(settings: Settings) -> str:
    return os.path.expanduser(settings.garmintokens)


def _ask(prompt: Callable[[], str], what: str) -> str:
    """Run an interactive prompt, or refuse when nobody can answer it."""
    if not sys.stdin.isatty():
        raise LoginUnavailableError(
            f"the saved Garmin login did not resume and no terminal is attached to ask for "
            f"the {what}; run any Garmin command (e.g. `garmin-coach sync`) once from a "
            "terminal to renew the saved login, or set GARMIN_EMAIL and GARMIN_PASSWORD"
        )
    return prompt()


class _FailureReasons(logging.Handler):
    """Collect the failure lines garminconnect itself reports only at debug level."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.reasons: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        # Only the library's top-level logger, and only its failure lines: its HTTP
        # client logs request detail under a child logger that must not reach our log.
        message = record.getMessage()
        if record.name == _LIBRARY_LOGGER and "ailed" in message:
            self.reasons.append(message)


@contextlib.contextmanager
def _library_failure_reasons() -> Iterator[list[str]]:
    """Listen to garminconnect's debug log for the duration of one login attempt."""
    library = logging.getLogger(_LIBRARY_LOGGER)
    handler = _FailureReasons()
    level = library.level
    library.addHandler(handler)
    library.setLevel(logging.DEBUG)
    try:
        yield handler.reasons
    finally:
        library.setLevel(level)
        library.removeHandler(handler)


def _has_saved_login(tokenstore: str) -> bool:
    """Whether the token store holds anything to resume from."""
    return os.path.isdir(tokenstore) and any(os.scandir(tokenstore))


def _resume(tokenstore: str) -> Garmin | None:
    """Resume the saved login, retrying passing failures; None when it will not resume."""
    if not _has_saved_login(tokenstore):
        return None  # first-ever login: nothing to resume, so nothing worth waiting for
    for delay in (*_RESUME_DELAYS_S, None):
        api = Garmin()
        with _library_failure_reasons() as reasons:
            try:
                api.login(tokenstore)
                return api
            except Exception as exc:  # noqa: BLE001 - any failure falls through to a full login
                detail = f" ({'; '.join(reasons)})" if reasons else ""
                logger.warning("client: saved login did not resume: %s%s", exc, detail)
        if delay is not None:
            time.sleep(delay)
    return None


def login_api(
    settings: Settings | None = None,
    prompt_mfa: Callable[[], str] | None = None,
) -> Garmin:
    """Log in and return the authenticated garminconnect ``Garmin`` client.

    First run caches OAuth tokens to GARMINTOKENS (mode 0600 by garth); later
    runs resume from them and only re-login when the refresh token expires.

    Raises:
        LoginUnavailableError: If the saved login does not resume and an email,
            password or two-step code would have to be typed with no terminal
            attached (a scheduled run, the MCP server).
    """
    settings = settings or get_settings()
    tokenstore = _tokenstore(settings)
    ask_mfa = prompt_mfa or (lambda: input("Garmin MFA code: ").strip())

    def gated_mfa() -> str:
        return _ask(ask_mfa, "two-step code")

    resumed = _resume(tokenstore)
    if resumed is not None:
        return resumed

    # First login needs credentials. Both are optional in config (cached tokens
    # cover later runs), so prompt interactively for whatever isn't set.
    email = settings.garmin_email or _ask(lambda: input("Garmin email: ").strip(), "email")
    password = settings.garmin_password or _ask(
        lambda: getpass.getpass(f"Garmin password for {email}: "), "password"
    )
    api = Garmin(
        email=email,
        password=password,
        prompt_mfa=gated_mfa,
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
