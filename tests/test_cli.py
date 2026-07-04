"""CLI seam: parser exposes the Phase 1 sync command."""

from __future__ import annotations

from garmin_coach.cli import build_parser


def test_parser_accepts_sync_command_with_optional_to_date():
    args = build_parser().parse_args(["sync", "--to", "2026-06-11"])

    assert args.command == "sync"
    assert args.to_date == "2026-06-11"
