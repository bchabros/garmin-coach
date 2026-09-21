"""Config seam: env-backed settings with sane defaults."""

from __future__ import annotations

from garmin_coach.core.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATA_START_DATE", raising=False)
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.c")
    s = Settings(_env_file=None)
    assert s.garmin_email == "a@b.c"
    assert s.garmin_password is None
    assert s.data_start_date == "2026-06-08"
    assert s.db_path.endswith("garmin.db")


def test_env_override(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "x@y.z")
    monkeypatch.setenv("DATA_START_DATE", "2026-01-01")
    s = Settings(_env_file=None)
    assert s.data_start_date == "2026-01-01"


def test_the_re_check_window_is_at_least_one_day():
    import pydantic
    import pytest

    assert Settings(_env_file=None).sync_recheck_days == 3
    with pytest.raises(pydantic.ValidationError):
        Settings(_env_file=None, sync_recheck_days=0)
