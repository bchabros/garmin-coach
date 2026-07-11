"""Command-line entry point. Phase 0 exposes only `backfill`."""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import pathlib
import sqlite3
from typing import TYPE_CHECKING

from . import client, daily, db, features, report, snapshot, sync
from .config import get_settings

if TYPE_CHECKING:
    from .config import Settings


def _bootstrap_db(settings: Settings) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)
    return conn


def _init_env() -> tuple[Settings, sqlite3.Connection, sync.GarminClient]:
    settings = get_settings()
    conn = _bootstrap_db(settings)
    transport = client.login(settings)
    return settings, conn, transport


def _check_range(name: str, value: int | None, lo: int, hi: int) -> None:
    """Raise ValueError when an optional integer input falls outside [lo, hi]."""
    if value is not None and not (lo <= value <= hi):
        raise ValueError(f"{name} must be between {lo} and {hi} (got {value})")


def log_session_rpe(
    conn: sqlite3.Connection,
    *,
    activity_id: int,
    rpe: int,
    soreness: int | None = None,
    mood: int | None = None,
    note: str | None = None,
    source: str = "manual",
    data_start_date: str,
) -> str:
    """Write a session-RPE to core and recompute the affected day's blended load.

    Validates the input ranges and that the activity exists, upserts ``session_rpe``,
    then recomputes ``features`` from the activity's date so the sRPE blend is
    immediately reflected in ``load_day`` / ACWR. Transport-free (never calls Garmin).

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        activity_id: The rated activity; must already exist in ``activities``.
        rpe: Borg CR10 session RPE (1-10).
        soreness: Optional post-session soreness (1-10).
        mood: Optional post-session mood (1-10).
        note: Optional free-text note.
        source: How the row was entered (default ``manual``).
        data_start_date: First real-data date, passed to the recompute.

    Returns:
        The activity's calendar date (the recompute start).

    Raises:
        ValueError: If a value is out of range or the activity does not exist.
    """
    _check_range("rpe", rpe, 1, 10)
    _check_range("soreness", soreness, 1, 10)
    _check_range("mood", mood, 1, 10)
    row = conn.execute(
        "SELECT date(start_local) FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"activity {activity_id} not found; run `garmin-coach sync` first")
    activity_date = row[0]
    db.upsert_session_rpe(conn, {
        "activity_id": activity_id, "rpe": rpe, "soreness": soreness,
        "mood": mood, "source": source, "notes": note,
    })
    conn.commit()
    features.features(conn, data_start_date=data_start_date, from_date=activity_date)
    return activity_date


def _cmd_backfill(args: argparse.Namespace) -> int:
    settings, conn, transport = _init_env()
    from_date = args.from_date or settings.data_start_date

    sync.backfill(transport, conn, from_date, args.to_date)
    conn.close()
    print(f"backfill complete: {from_date} .. {args.to_date or 'yesterday'}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    settings, conn, transport = _init_env()

    result = sync.sync_incremental(
        transport, conn, data_start_date=settings.data_start_date, to_date=args.to_date
    )
    conn.close()

    for warning in result.warnings:
        print(f"warning: {warning}")
    print(
        "sync complete: "
        f"progressed={','.join(sorted(result.progressed_streams)) or 'none'}; "
        f"warnings={len(result.warnings)}"
    )
    if result.total_outage:
        return 1
    return 0


def _cmd_features(args: argparse.Namespace) -> int:
    settings = get_settings()
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    features.features(
        conn,
        data_start_date=settings.data_start_date,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    conn.close()
    print(f"features complete: {args.from_date or settings.data_start_date} .. {args.to_date or 'latest'}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    settings = get_settings()
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    out = report.generate_report(conn, from_date=args.from_date, to_date=args.to_date)
    conn.close()
    print(f"report complete: {out} (digest.json + charts; run the coach skill for report.md)")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    settings = get_settings()
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    status = snapshot.read(conn)
    conn.close()
    if status is None:
        print("snapshot: no athlete_status row yet; run `garmin-coach features` first")
        return 1

    out = pathlib.Path(args.reports_dir) / _dt.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    path = snapshot.write_json(status, out)
    print(f"snapshot complete: {path} (as of {status['computed_at']})")
    return 0


def _cmd_log_rpe(args: argparse.Namespace) -> int:
    settings = get_settings()
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    try:
        if args.rpe is None:
            raise ValueError("--rpe is required with --activity")
        activity_date = log_session_rpe(
            conn,
            activity_id=args.activity_id,
            rpe=args.rpe,
            soreness=args.soreness,
            mood=args.mood,
            note=args.note,
            data_start_date=settings.data_start_date,
        )
    except ValueError as exc:
        conn.close()
        print(f"log-rpe failed: {exc}")
        return 2

    conn.close()
    print(
        f"log-rpe complete: activity {args.activity_id} rpe={args.rpe}; "
        f"load recomputed from {activity_date}"
    )
    return 0


def _cmd_daily(args: argparse.Namespace) -> int:
    settings = get_settings()
    daily.configure_logging(
        settings.log_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    conn = _bootstrap_db(settings)

    try:
        transport = client.login(settings)
    except Exception as exc:  # noqa: BLE001 - surface login failures as a failed run
        daily.logger.exception("daily: login failed")
        conn.close()
        print(f"daily failed: login error: {exc}")
        return 2

    result = daily.run_daily(
        transport, conn, data_start_date=settings.data_start_date, to_date=args.to_date
    )
    conn.close()
    warnings = len(result.sync.warnings) if result.sync else 0
    print(
        f"daily complete: status={result.status} "
        f"alerts={len(result.alerts)} warnings={warnings}"
    )
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="garmin-coach")
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="Pull a date range into the DB (raw + core).")
    bf.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="Start date YYYY-MM-DD (default: DATA_START_DATE).",
    )
    bf.add_argument(
        "--to", dest="to_date", default=None, help="End date YYYY-MM-DD (default: yesterday)."
    )
    bf.set_defaults(func=_cmd_backfill)

    sc = sub.add_parser("sync", help="Pull missing Garmin data since per-stream watermarks.")
    sc.add_argument(
        "--to", dest="to_date", default=None, help="End date YYYY-MM-DD (default: yesterday)."
    )
    sc.set_defaults(func=_cmd_sync)

    ft = sub.add_parser("features", help="Recompute the daily_metrics mart from core data.")
    ft.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="First changed day YYYY-MM-DD (weekly rollup may expand to Monday).",
    )
    ft.add_argument(
        "--to", dest="to_date", default=None, help="Last day to emit YYYY-MM-DD (default: latest core date)."
    )
    ft.set_defaults(func=_cmd_features)

    rp = sub.add_parser("report", help="Build the coach digest + charts into reports/{date}/.")
    rp.add_argument(
        "--from", dest="from_date", default=None,
        help="Window start YYYY-MM-DD (default: trailing 28 days).",
    )
    rp.add_argument(
        "--to", dest="to_date", default=None,
        help="Window end YYYY-MM-DD (default: latest mart day).",
    )
    rp.set_defaults(func=_cmd_report)

    sp = sub.add_parser(
        "snapshot", help="Write the current standing to reports/{date}/snapshot.json."
    )
    sp.add_argument(
        "--reports-dir", dest="reports_dir", default="./reports",
        help="Root directory for dated report folders.",
    )
    sp.set_defaults(func=_cmd_snapshot)

    lr = sub.add_parser(
        "log-rpe", help="Log a session RPE to core (transport-free) and refresh load."
    )
    lr.add_argument(
        "--activity", dest="activity_id", type=int, required=True,
        help="Activity ID to rate.",
    )
    lr.add_argument("--rpe", type=int, default=None, help="Borg CR10 session RPE (1-10).")
    lr.add_argument("--soreness", type=int, default=None, help="Optional soreness (1-10).")
    lr.add_argument("--mood", type=int, default=None, help="Optional mood (1-10).")
    lr.add_argument("--note", dest="note", default=None, help="Optional free-text note.")
    lr.set_defaults(func=_cmd_log_rpe)

    dl = sub.add_parser(
        "daily", help="Nightly run: sync -> features -> alerts (for cron/launchd)."
    )
    dl.add_argument(
        "--to", dest="to_date", default=None, help="End date YYYY-MM-DD (default: yesterday)."
    )
    dl.set_defaults(func=_cmd_daily)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
