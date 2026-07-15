"""Same-day refresh seam (issue #8).

Seam: ``daily.run_refresh_today(client, conn, ...)``. Each test injects a fake
Garmin client and a temp SQLite, runs the refresh, and asserts on the returned
``DailyResult`` and observable DB state - mirroring test_daily.py. The critical
invariant: refresh never advances sync watermarks, so the nightly run re-pulls
today complete.
"""

from __future__ import annotations

from typing import Any

from garmin_coach import daily, db

DATA_START = "2026-06-08"
TODAY = "2026-07-15"


class _SleepFailsClient:
    """Sleep always fails; HRV returns data so at least one stream progresses."""

    def __init__(self, hrv_by_day: dict[str, Any]) -> None:
        self.hrv_by_day = hrv_by_day

    def get_activities(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        return []

    def get_sleep(self, date: str) -> dict[str, Any] | None:
        raise TimeoutError("sleep down")

    def get_hrv(self, date: str) -> dict[str, Any] | None:
        return self.hrv_by_day.get(date)

    def get_wellness(self, date: str) -> dict[str, Any] | None:
        return None

    def get_readiness(self, date: str) -> Any:
        return None

    def get_status(self, date: str) -> dict[str, Any] | None:
        return None


class _AllStreamsFailClient:
    """Every endpoint raises: simulates a total outage (auth/network down)."""

    def get_activities(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        raise TimeoutError("activities down")

    def get_sleep(self, date: str) -> dict[str, Any] | None:
        raise TimeoutError("sleep down")

    def get_hrv(self, date: str) -> dict[str, Any] | None:
        raise TimeoutError("hrv down")

    def get_wellness(self, date: str) -> dict[str, Any] | None:
        raise TimeoutError("wellness down")

    def get_readiness(self, date: str) -> Any:
        raise TimeoutError("readiness down")

    def get_status(self, date: str) -> dict[str, Any] | None:
        raise TimeoutError("status down")


def test_refresh_today_pulls_today_and_rebuilds_the_mart(conn, fake_client, fixture):
    """A same-day refresh lands today's HRV in core and today's row in the mart."""
    client = fake_client(by_day={"hrv": {TODAY: fixture("hrv_day")}})

    result = daily.run_refresh_today(client, conn, data_start_date=DATA_START, today=TODAY)

    assert result.status == "ok"
    assert result.exit_code == 0
    assert result.features_ok is True
    hrv = conn.execute("SELECT date FROM hrv_nightly WHERE date=?", (TODAY,)).fetchone()
    assert hrv is not None
    mart = conn.execute("SELECT date FROM daily_metrics WHERE date=?", (TODAY,)).fetchone()
    assert mart is not None


def test_refresh_today_fetches_activities_for_today_only(conn, fake_client):
    """The activities range call covers exactly today - no historical re-pull."""
    client = fake_client()

    daily.run_refresh_today(client, conn, data_start_date=DATA_START, today=TODAY)

    assert ("activities", f"{TODAY}..{TODAY}") in client.calls


def test_refresh_today_never_advances_sync_watermarks(conn, fake_client, fixture):
    """Refresh leaves watermarks alone so the nightly sync re-pulls today complete."""
    db.set_sync_watermark(conn, "hrv", "2026-07-14")
    client = fake_client(by_day={"hrv": {TODAY: fixture("hrv_day")}})

    daily.run_refresh_today(client, conn, data_start_date=DATA_START, today=TODAY)

    assert db.get_sync_watermark(conn, "hrv") == "2026-07-14"
    assert db.get_sync_watermark(conn, "activities") is None


def test_refresh_today_is_degraded_when_one_stream_fails(conn, fixture):
    """One failing stream degrades the refresh (exit 1) but features still run."""
    client = _SleepFailsClient(hrv_by_day={TODAY: fixture("hrv_day")})

    result = daily.run_refresh_today(client, conn, data_start_date=DATA_START, today=TODAY)

    assert result.status == "degraded"
    assert result.exit_code == 1
    assert result.features_ok is True


def test_refresh_today_fails_on_total_outage(conn):
    """Every stream failing is a failed run (exit 2)."""
    client = _AllStreamsFailClient()

    result = daily.run_refresh_today(client, conn, data_start_date=DATA_START, today=TODAY)

    assert result.status == "failed"
    assert result.exit_code == 2


def test_refresh_today_defaults_to_the_current_date(conn, fake_client):
    """Omitting `today` refreshes the actual current date (not yesterday)."""
    import datetime as dt

    client = fake_client()

    daily.run_refresh_today(client, conn, data_start_date=DATA_START)

    today = dt.date.today().isoformat()
    assert ("activities", f"{today}..{today}") in client.calls


def test_parser_accepts_refresh_today_command():
    from garmin_coach.cli import build_parser

    args = build_parser().parse_args(["refresh-today"])

    assert args.command == "refresh-today"
