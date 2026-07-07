"""Command-line entry point. Phase 0 exposes only `backfill`."""

from __future__ import annotations

import argparse
import os
import sqlite3
from typing import TYPE_CHECKING

from . import client, daily, db, features, report, sync
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
