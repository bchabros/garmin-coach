"""Login behaviour, offline: the prompt gate and the saved-login resume (issue #71)."""

from __future__ import annotations

import logging
import secrets

import pytest

from garmin_coach.core.config import Settings
from garmin_coach.etl import client


# Generated per run, never a literal: no credential-shaped string lives in this file.
_FAKE_PASSWORD = secrets.token_urlsafe(12)
_TYPED_PASSWORD = secrets.token_urlsafe(12)


class _Stdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class _ResumeFails:
    """A Garmin stand-in whose saved login never resumes; a credential login works."""

    created: list["_ResumeFails"] = []

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        type(self).created.append(self)

    def login(self, tokenstore=None):
        if self.email is None:
            raise RuntimeError("Username and password are required")
        return None, None


@pytest.fixture
def no_prompts(monkeypatch):
    """Fail the test if any interactive prompt is reached."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("an interactive prompt was reached")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr(client.getpass, "getpass", _boom)


@pytest.fixture
def garmin(monkeypatch):
    _ResumeFails.created = []
    monkeypatch.setattr(client, "Garmin", _ResumeFails)
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    return _ResumeFails


def _settings(tmp_path, **overrides) -> Settings:
    store = tmp_path / "tokens"
    store.mkdir(exist_ok=True)
    (store / "oauth2_token.json").write_text("{}")
    return Settings(_env_file=None, garmintokens=str(store), **overrides)


def test_without_a_terminal_a_failed_resume_raises_instead_of_prompting(
    tmp_path, monkeypatch, garmin, no_prompts
):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))

    with pytest.raises(client.LoginUnavailableError) as excinfo:
        client.login_api(_settings(tmp_path))

    assert "no terminal" in str(excinfo.value)
    assert "from a terminal" in str(excinfo.value)


def test_at_a_terminal_the_prompts_still_work(tmp_path, monkeypatch, garmin):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda _prompt: " athlete@example.com ")
    monkeypatch.setattr(client.getpass, "getpass", lambda _prompt: _TYPED_PASSWORD)

    api = client.login_api(_settings(tmp_path))

    assert (api.email, api.password) == ("athlete@example.com", _TYPED_PASSWORD)


def test_credentials_in_the_environment_log_in_without_a_terminal(
    tmp_path, monkeypatch, garmin, no_prompts
):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))
    settings = _settings(
        tmp_path, garmin_email="athlete@example.com", garmin_password=_FAKE_PASSWORD
    )

    api = client.login_api(settings)

    assert (api.email, api.password) == ("athlete@example.com", _FAKE_PASSWORD)


def test_a_two_step_code_is_never_asked_for_without_a_terminal(
    tmp_path, monkeypatch, garmin, no_prompts
):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))
    settings = _settings(
        tmp_path, garmin_email="athlete@example.com", garmin_password=_FAKE_PASSWORD
    )

    api = client.login_api(settings)

    with pytest.raises(client.LoginUnavailableError, match="two-step code"):
        api.prompt_mfa()


# --- the saved login is resumed three times, and every failure says why ----------


class _ResumesOnThirdTry:
    """A Garmin stand-in whose saved login fails twice, the way a cold network does."""

    attempts = 0

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email

    def login(self, tokenstore=None):
        cls = type(self)
        cls.attempts += 1
        if cls.attempts < 3:
            logging.getLogger("garminconnect").debug(
                "Failed to cleanly load tokens from %s: read timed out", tokenstore
            )
            raise RuntimeError("Username and password are required")
        return None, None


@pytest.fixture
def flaky_garmin(monkeypatch):
    _ResumesOnThirdTry.attempts = 0
    waits: list[float] = []
    monkeypatch.setattr(client, "Garmin", _ResumesOnThirdTry)
    monkeypatch.setattr(client.time, "sleep", waits.append)
    return waits


def test_a_resume_that_fails_twice_then_succeeds_logs_in_without_prompting(
    tmp_path, monkeypatch, flaky_garmin, no_prompts
):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))

    api = client.login_api(_settings(tmp_path))

    assert api.email is None  # resumed from the saved login, not a credential login
    assert _ResumesOnThirdTry.attempts == 3
    assert len(flaky_garmin) == 2 and all(wait > 0 for wait in flaky_garmin)


def test_every_failed_resume_logs_its_reason_including_the_librarys_own(
    tmp_path, monkeypatch, flaky_garmin, no_prompts, caplog
):
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))

    with caplog.at_level(logging.WARNING, logger="garmin_coach.etl.client"):
        client.login_api(_settings(tmp_path))

    warnings = [r.getMessage() for r in caplog.records if r.name == "garmin_coach.etl.client"]
    assert len(warnings) == 2
    for line in warnings:
        assert line.startswith("client: saved login did not resume")
        assert "Username and password are required" in line
        assert "read timed out" in line


def test_a_first_ever_login_skips_the_resume_and_its_waits(tmp_path, monkeypatch):
    waits: list[float] = []
    _ResumeFails.created = []
    monkeypatch.setattr(client, "Garmin", _ResumeFails)
    monkeypatch.setattr(client.time, "sleep", waits.append)
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "athlete@example.com")
    monkeypatch.setattr(client.getpass, "getpass", lambda _prompt: _TYPED_PASSWORD)
    settings = Settings(_env_file=None, garmintokens=str(tmp_path / "never-logged-in"))

    api = client.login_api(settings)

    assert api.email == "athlete@example.com"
    assert waits == []
    assert len(_ResumeFails.created) == 1  # the credential login only, no resume attempt


# --- review fixes: behave like the real library, which wraps and logs on a child -----


class _WrapsLikeTheLibrary:
    """Stand-in for garminconnect's credential login, which wraps any error it catches.

    ``Garmin.login`` catches every exception from the two-step prompt and re-raises it
    as ``GarminConnectConnectionError("Login failed: ...") from e``.
    """

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email
        self.prompt_mfa = prompt_mfa

    def login(self, tokenstore=None):
        if self.email is None:
            raise RuntimeError("Username and password are required")
        try:
            self.prompt_mfa()
        except Exception as exc:
            raise ConnectionError(f"Login failed: {exc}") from exc
        return None, None


def test_a_two_step_code_needed_without_a_terminal_surfaces_as_the_typed_error(
    tmp_path, monkeypatch, no_prompts
):
    monkeypatch.setattr(client, "Garmin", _WrapsLikeTheLibrary)
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))
    settings = _settings(
        tmp_path, garmin_email="athlete@example.com", garmin_password=_FAKE_PASSWORD
    )

    with pytest.raises(client.LoginUnavailableError, match="two-step code"):
        client.login_api(settings)


class _RefreshFailsOnTheHttpClient:
    """The library reports a failed token refresh on its HTTP client's child logger."""

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email

    def login(self, tokenstore=None):
        if self.email is None:
            http = logging.getLogger("garminconnect.client")
            http.debug("GET https://connectapi.garmin.com/profile headers={...}")
            http.debug("DI token refresh failed: read timed out")
            logging.getLogger("garminconnect").warning("Cached tokens were rejected by the API")
            raise RuntimeError("Username and password are required")
        return None, None


def test_the_refresh_reason_from_the_http_client_reaches_the_warning_and_nothing_else_leaks(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(client, "Garmin", _RefreshFailsOnTheHttpClient)
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(client.sys, "stdin", _Stdin(tty=False))

    with pytest.raises(client.LoginUnavailableError):
        client.login_api(_settings(tmp_path))

    ours = [r.getMessage() for r in caplog.records if r.name == "garmin_coach.etl.client"]
    assert len(ours) == 3 and all("DI token refresh failed: read timed out" in m for m in ours)
    library = [r for r in caplog.records if r.name.startswith("garminconnect")]
    assert all(r.levelno >= logging.WARNING for r in library)  # no debug detail leaks
    assert any("Cached tokens were rejected" in r.getMessage() for r in library)
