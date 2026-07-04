"""Command-line entry point. Phase 0 exposes only `backfill`."""
from __future__ import annotations

import argparse
import os

from . import client, db, sync
from .config import get_settings


def _cmd_backfill(args: argparse.Namespace) -> int:
    settings = get_settings()
    from_date = args.from_date or settings.data_start_date

    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    transport = client.login(settings)
    sync.backfill(transport, conn, from_date, args.to_date)
    conn.close()
    print(f"backfill complete: {from_date} .. {args.to_date or 'yesterday'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-coach")
    sub = parser.add_subparsers(dest="command", required=True)

    bf = sub.add_parser("backfill", help="Pull a date range into the DB (raw + core).")
    bf.add_argument("--from", dest="from_date", default=None,
                    help="Start date YYYY-MM-DD (default: DATA_START_DATE).")
    bf.add_argument("--to", dest="to_date", default=None,
                    help="End date YYYY-MM-DD (default: yesterday).")
    bf.set_defaults(func=_cmd_backfill)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
