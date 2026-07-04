"""Shared test infrastructure: fixture loading, in-memory DB, fake Garmin client."""
from __future__ import annotations

import json
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    """Load a JSON fixture by filename (with or without .json suffix)."""
    if not name.endswith(".json"):
        name += ".json"
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fixture():
    return load_fixture


@pytest.fixture
def conn():
    """A fresh in-memory SQLite connection with the package schema applied."""
    from garmin_coach import db

    c = db.connect(":memory:")
    db.bootstrap(c)
    yield c
    c.close()


class FakeGarminClient:
    """Injectable stand-in for the real transport client (see sync seam).

    Returns fixtures keyed by (endpoint, ref_date). Unknown dates return an
    empty payload so per-day streams can be exercised over a range.
    """

    def __init__(self, activities=None, by_day=None):
        # activities: list returned for the whole range
        # by_day: {endpoint: {date: payload}}
        self.activities = activities or []
        self.by_day = by_day or {}
        self.calls: list[tuple[str, str]] = []

    def get_activities(self, start_date: str, end_date: str):
        self.calls.append(("activities", f"{start_date}..{end_date}"))
        return self.activities

    def _day(self, endpoint: str, date: str):
        self.calls.append((endpoint, date))
        return self.by_day.get(endpoint, {}).get(date)

    def get_sleep(self, date: str):
        return self._day("sleep", date)

    def get_hrv(self, date: str):
        return self._day("hrv", date)

    def get_wellness(self, date: str):
        return self._day("wellness", date)

    def get_readiness(self, date: str):
        return self._day("readiness", date)

    def get_status(self, date: str):
        return self._day("status", date)


@pytest.fixture
def fake_client():
    return FakeGarminClient
