"""Phase 6b report-integration test.

Seam: ``report.generate_report`` at the DB boundary. Asserts the report bundle now
carries ``snapshot.json`` (the current standing) beside the digest and charts.
"""

from __future__ import annotations

import json

from garmin_coach import db, report


def _seed_daily(conn, date):
    db.upsert_daily(conn, "daily_metrics", {
        "date": date, "load_day": 100, "load_low": 60, "load_high": 30,
        "load_anaerobic": 10, "hrv": 70, "hrv_baseline": 68, "hrv_sd": 11,
        "hrv_low_flag": 0, "acwr": 1.1, "n_chronic": 30,
    })


def test_generate_report_emits_snapshot_json(conn, tmp_path):
    for date in ["2026-07-06", "2026-07-07", "2026-07-08"]:
        _seed_daily(conn, date)
    db.upsert_status(conn, {"id": 1, "computed_at": "2026-07-08", "vo2max": 52.0,
                            "acwr": 1.1, "planned_intent_today": "easy"})

    out = report.generate_report(conn, reports_dir=str(tmp_path))

    snapshot_path = out / "snapshot.json"
    assert snapshot_path.exists()
    status = json.loads(snapshot_path.read_text())
    assert status["computed_at"] == "2026-07-08"
    assert status["vo2max"] == 52.0
    assert "id" not in status  # the singleton key is stripped on the way out


def test_generate_report_without_status_row_skips_snapshot(conn, tmp_path):
    _seed_daily(conn, "2026-07-08")
    out = report.generate_report(conn, reports_dir=str(tmp_path))
    assert (out / "digest.json").exists()
    assert not (out / "snapshot.json").exists()  # nothing to emit, no crash
