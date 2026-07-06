"""Phase 4 orchestration seam.

Seam: `run_daily(client, conn, ...)`. Each test injects a fake Garmin client and
an open temp SQLite (optionally pre-seeded with core rows), runs the nightly
orchestrator, and asserts on the returned ``DailyResult`` - never on private
helpers or log formatting. Mirrors test_sync.py (injected client) and
test_features.py (seed core -> run -> observe).
"""

from __future__ import annotations

from typing import Any

from garmin_coach import daily, db


class _AllStreamsFailClient:
    """Every endpoint raises: simulates a total outage (auth/network down)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

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


class _SleepFailsClient:
    """Sleep always fails; HRV returns data so at least one stream progresses."""

    def __init__(self, hrv_by_day: dict[str, Any]) -> None:
        self.hrv_by_day = hrv_by_day
        self.calls: list[tuple[str, str]] = []

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


def test_daily_run_is_ok_when_pipeline_completes_cleanly(conn, fake_client):
    """A run where every stream progresses without warnings is ok/exit 0."""
    client = fake_client()  # returns [] / None everywhere: no data, no warnings

    result = daily.run_daily(
        client, conn, data_start_date="2026-06-08", to_date="2026-06-10"
    )

    assert result.features_ok is True
    assert result.status == "ok"
    assert result.exit_code == 0


def test_daily_run_surfaces_hrv_low_alert_from_fresh_mart(conn, fake_client):
    """Given a low latest HRV night, the run extracts an HRV_LOW_MORNING alert."""
    # Descending series [76,72,68,64,60]: median 68, SD 6.3245, threshold 61.67,
    # so only the last night (60, latest day) flags low.
    for i, hrv in enumerate([76, 72, 68, 64, 60]):
        db.upsert_daily(conn, "hrv_nightly", {"date": f"2026-06-{8 + i:02d}", "avg_hrv": hrv})
    client = fake_client()

    result = daily.run_daily(
        client, conn, data_start_date="2026-06-08", to_date="2026-06-12"
    )

    assert result.status == "ok"
    assert "HRV_LOW_MORNING" in [a["code"] for a in result.alerts]


def test_daily_run_is_degraded_when_one_stream_fails(conn, fixture):
    """One failing stream degrades the run (exit 1) but does not abort it."""
    client = _SleepFailsClient(hrv_by_day={"2026-06-09": fixture("hrv_day")})

    result = daily.run_daily(
        client,
        conn,
        data_start_date="2026-06-08",
        to_date="2026-06-09",
        max_attempts=1,
        retry_base_seconds=0,
    )

    assert result.features_ok is True
    assert result.status == "degraded"
    assert result.exit_code == 1


def test_daily_run_fails_when_no_stream_progresses(conn):
    """A total outage (every stream fails) is failed/exit 2."""
    client = _AllStreamsFailClient()

    result = daily.run_daily(
        client,
        conn,
        data_start_date="2026-06-08",
        to_date="2026-06-09",
        max_attempts=1,
        retry_base_seconds=0,
    )

    assert result.status == "failed"
    assert result.exit_code == 2


def test_daily_parser_accepts_to_date():
    """The CLI exposes `daily` with an optional --to, mirroring `sync`."""
    from garmin_coach.cli import build_parser

    args = build_parser().parse_args(["daily", "--to", "2026-06-11"])
    assert args.command == "daily"
    assert args.to_date == "2026-06-11"
