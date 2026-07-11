"""CLI seam: parser wiring and the transport-free `log-rpe` writers."""

from __future__ import annotations

import pytest

from garmin_coach import cli, db
from garmin_coach.cli import build_parser

DATA_START = "2026-06-08"


def test_parser_accepts_sync_command_with_optional_to_date():
    args = build_parser().parse_args(["sync", "--to", "2026-06-11"])

    assert args.command == "sync"
    assert args.to_date == "2026-06-11"


def test_parser_accepts_log_rpe_activity_mode():
    args = build_parser().parse_args(["log-rpe", "--activity", "1", "--rpe", "8"])

    assert args.command == "log-rpe"
    assert args.activity_id == 1
    assert args.rpe == 8


def _sila(conn, aid=1, date="2026-06-08"):
    db.upsert_activity(conn, {
        "activity_id": aid, "start_local": f"{date} 18:00:00", "date": date,
        "gtype": "strength_training", "discipline": "Siła",
        "aero_te": 1.4, "anaero_te": 0.3, "training_load": 22.0, "dur_s": 4200,
    })


def test_log_session_rpe_rejects_unknown_activity(conn):
    with pytest.raises(ValueError, match="not found"):
        cli.log_session_rpe(conn, activity_id=999, rpe=8, data_start_date=DATA_START)


def test_log_session_rpe_writes_and_recomputes_load(conn):
    _sila(conn)

    date = cli.log_session_rpe(conn, activity_id=1, rpe=9, data_start_date=DATA_START)

    assert date == "2026-06-08"
    assert conn.execute("SELECT rpe FROM session_rpe WHERE activity_id=1").fetchone()[0] == 9
    load_strength = conn.execute(
        "SELECT load_strength FROM daily_metrics WHERE date='2026-06-08'"
    ).fetchone()[0]
    assert abs(load_strength - 189.0) < 1e-6  # 0.3 * 9 * 70, recomputed by features


def test_log_session_rpe_validates_rpe_range(conn):
    _sila(conn)
    with pytest.raises(ValueError, match="rpe"):
        cli.log_session_rpe(conn, activity_id=1, rpe=11, data_start_date=DATA_START)
