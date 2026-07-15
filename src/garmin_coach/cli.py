"""Command-line entry point. Phase 0 exposes only `backfill`."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import sqlite3
from typing import TYPE_CHECKING, Any

from . import author as _author
from . import client, daily, db, digest, features, periodize, report, snapshot, sync
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


def log_niggle(
    conn: sqlite3.Connection,
    *,
    body_part: str,
    severity: int,
    date: str | None = None,
    note: str | None = None,
) -> str:
    """Write a niggle entry to core (transport-free); no recompute needed.

    Reduced-mode is a digest-layer read, so it surfaces on the next report rather
    than requiring a mart recompute here. A re-log for the same body part on the
    same day updates it.

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        body_part: The affected body part (free text, e.g. ``kolano``).
        severity: Niggle severity (1-5).
        date: The niggle date (default: today).
        note: Optional free-text note.

    Returns:
        The date the niggle was recorded against.

    Raises:
        ValueError: If severity is out of range.
    """
    _check_range("severity", severity, 1, 5)
    day = date or _dt.date.today().isoformat()
    db.upsert_niggle(conn, {"date": day, "body_part": body_part, "severity": severity, "note": note})
    conn.commit()
    return day


EVENT_TYPES = ("hyrox", "run_race")
EVENT_PRIORITIES = ("A", "B", "C")
EVENT_STATUSES = ("confirmed", "tentative")
EVENT_DATE_PRECISIONS = ("exact", "approx")

# The single source of truth for the goal-event enums: argparse `choices=` and the
# add/update validators both read it, so the CLI and the writers cannot drift apart.
EVENT_CHOICES: dict[str, tuple[str, ...]] = {
    "type": EVENT_TYPES,
    "priority": EVENT_PRIORITIES,
    "status": EVENT_STATUSES,
    "date_precision": EVENT_DATE_PRECISIONS,
}


def parse_target_s(target: str) -> int:
    """Parse a goal time into seconds.

    Accepts ``H:MM:SS``, ``MM:SS``, or bare seconds, so the athlete can type a race
    goal the way they say it ("1:00:00") and the DB still stores a number a later
    race plan can split across segments.

    Args:
        target: The goal time as ``H:MM:SS``, ``MM:SS``, or plain seconds.

    Returns:
        The goal time in whole seconds.

    Raises:
        ValueError: If the text is not a colon-separated time or a plain integer, or
            if a minutes/seconds field is out of range (a mistyped goal must not be
            silently accepted as a plausible number).
    """
    parts = target.split(":")
    if len(parts) > 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"target must be H:MM:SS, MM:SS or seconds (got {target!r})")
    if any(int(part) >= 60 for part in parts[1:]):
        raise ValueError(f"target has a minutes/seconds field above 59 (got {target!r})")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def _check_date(value: str) -> str:
    """Return an ISO date, refusing anything a later read could not parse."""
    try:
        return _dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"date must be YYYY-MM-DD (got {value!r})") from None


def _check_choices(fields: dict[str, Any]) -> None:
    for name, allowed in EVENT_CHOICES.items():
        value = fields.get(name)
        if value is not None and value not in allowed:
            raise ValueError(f"{name} must be one of {'|'.join(allowed)} (got {value!r})")


def add_goal_event(
    conn: sqlite3.Connection,
    *,
    date: str,
    type: str,  # noqa: A002 - mirrors the goal_event column and the --type flag
    priority: str,
    status: str,
    date_precision: str,
    target: str | None = None,
    note: str | None = None,
) -> None:
    """Record a goal race in core (transport-free).

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        date: Race day YYYY-MM-DD (the best known estimate).
        type: Race type (``hyrox`` or ``run_race``).
        priority: Race priority (``A``, ``B``, or ``C``).
        status: Whether the athlete will start (``confirmed`` or ``tentative``).
        date_precision: Whether the exact day is known (``exact`` or ``approx``).
        target: Optional goal time as ``H:MM:SS``, ``MM:SS``, or seconds.
        note: Optional free-text note.

    Raises:
        ValueError: If the date is malformed, an enum value is unknown, the target time
            is unparseable, or the race is already recorded (correct it with `update`
            rather than re-adding it, which would erase the fields left unset here).
    """
    fields = {"type": type, "priority": priority, "status": status,
              "date_precision": date_precision}
    _check_choices(fields)
    row = {
        "date": _check_date(date), **fields,
        "target_s": parse_target_s(target) if target else None,
        "note": note,
    }
    try:
        db.insert_goal_event(conn, row)
    except sqlite3.IntegrityError:
        existing = next(
            e for e in db.list_goal_events(conn)
            if e["date"] == row["date"] and e["type"] == row["type"]
        )
        raise ValueError(
            f"{type} on {row['date']} is already recorded (id {existing['id']}); "
            f"use `garmin-coach event update {existing['id']}` to change it"
        ) from None
    conn.commit()


def update_goal_event(conn: sqlite3.Connection, event_id: int, **fields: str | None) -> None:
    """Update a recorded goal event (pin an approx date, commit a tentative start).

    Args:
        conn: Open SQLite connection with the schema bootstrapped.
        event_id: The event's `id`, as shown by `event list`.
        **fields: Any of `date`, `type`, `priority`, `status`, `date_precision`,
            `target` (parsed to seconds), or `note`. Fields left None keep their value.

    Raises:
        ValueError: If no field is given, the event does not exist, the date is
            malformed, an enum value is unknown, or the target time is unparseable.
    """
    changes: dict[str, Any] = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        raise ValueError(f"nothing to update on event {event_id}; pass at least one field")
    _check_choices(changes)
    if "date" in changes:
        changes["date"] = _check_date(str(changes["date"]))
    if "target" in changes:
        changes["target_s"] = parse_target_s(str(changes.pop("target")))
    db.update_goal_event(conn, event_id, **changes)
    conn.commit()


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


def _cmd_author(args: argparse.Namespace) -> int:
    settings = get_settings()
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = db.connect(settings.db_path)
    db.bootstrap(conn)

    to_date = (_dt.date.fromisoformat(args.date) - _dt.timedelta(days=1)).isoformat()
    thresholds = report.read_thresholds(conn)
    dg = digest.build_digest(conn, to_date=to_date, thresholds=thresholds)
    conn.close()

    recommendation = dg.get("recommendation")
    if recommendation is None:
        print(f"author: no recommendation for {args.date}; run `garmin-coach features` first")
        return 1

    request = _author.request_from_recommendation(recommendation, sport=args.sport)
    context = {"zones": dg.get("zones"), "today": _dt.date.today().isoformat()}
    try:
        spec = _author.author(request, context)
    except ValueError as exc:
        print(f"author failed: {exc}")
        return 1

    if spec is None:
        print(f"author: nothing to author for {args.date} (rest)")
        return 0

    out = pathlib.Path(args.reports_dir) / args.date
    out.mkdir(parents=True, exist_ok=True)
    (out / "workout.json").write_text(json.dumps(spec, indent=2))
    print(f"author complete: {out / 'workout.json'} ({spec['name']}, {len(spec['steps'])} steps)")
    for warning in spec["warnings"]:
        print(f"  warning: {warning}")
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
        if args.activity_id is not None:
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
            message = (
                f"log-rpe complete: activity {args.activity_id} rpe={args.rpe}; "
                f"load recomputed from {activity_date}"
            )
        else:
            if args.severity is None:
                raise ValueError("--severity is required with --niggle")
            day = log_niggle(
                conn,
                body_part=args.body_part,
                severity=args.severity,
                date=args.date,
                note=args.note,
            )
            message = (
                f"log-rpe complete: niggle {args.body_part} "
                f"severity={args.severity} on {day}"
            )
    except ValueError as exc:
        conn.close()
        print(f"log-rpe failed: {exc}")
        return 2

    conn.close()
    print(message)
    return 0


def _format_event(row: dict[str, Any]) -> str:
    """Render one `event list` line: the race, its two uncertainty axes, its countdown."""
    anchor = "*" if row["is_anchor"] else " "
    target = f" target={row['target_s']}s" if row["target_s"] else ""
    return (
        f"{anchor} [{row['id']}] {row['date']} ({row['date_precision']}) "
        f"{row['type']} prio={row['priority']} {row['status']}"
        f"{target} weeks_to_event={row['weeks_to_event']}"
    )


def _list_events(conn: sqlite3.Connection) -> int:
    rows = periodize.annotate(db.list_goal_events(conn), _dt.date.today().isoformat())
    if not rows:
        print("event list: no goal events recorded; the plan has no anchor")
        return 0
    for row in rows:
        print(_format_event(row))
    if not any(row["is_anchor"] for row in rows):
        print("note: no confirmed priority-A race upcoming, so the plan has no anchor")
    return 0


def _cmd_event(args: argparse.Namespace) -> int:
    settings = get_settings()
    conn = _bootstrap_db(settings)

    try:
        if args.event_command == "add":
            add_goal_event(
                conn, date=args.date, type=args.type, priority=args.priority,
                status=args.status, date_precision=args.date_precision,
                target=args.target, note=args.note,
            )
            message = f"event add complete: {args.type} on {args.date} ({args.status})"
        elif args.event_command == "update":
            update_goal_event(
                conn, args.event_id, date=args.date, type=args.type,
                priority=args.priority, status=args.status,
                date_precision=args.date_precision, target=args.target, note=args.note,
            )
            message = f"event update complete: id={args.event_id}"
        else:
            exit_code = _list_events(conn)
            conn.close()
            return exit_code
    except (ValueError, sqlite3.IntegrityError) as exc:
        conn.close()
        print(f"event failed: {exc}")
        return 2

    conn.close()
    print(message)
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

    au = sub.add_parser(
        "author", help="Turn a recommendation into a workout spec in reports/{date}/ (no network)."
    )
    au.add_argument("--date", dest="date", required=True, help="Target date YYYY-MM-DD.")
    au.add_argument(
        "--from-recommendation", dest="from_recommendation", action="store_true",
        help="Author from the Phase 10 recommendation for the target date.",
    )
    au.add_argument(
        "--sport", dest="sport", default="run", choices=["run"],
        help="Authoring family (only run is authored in this phase).",
    )
    au.add_argument(
        "--reports-dir", dest="reports_dir", default="./reports",
        help="Root directory for dated report folders.",
    )
    au.set_defaults(func=_cmd_author)

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
        "log-rpe", help="Log a session RPE or a niggle to core (transport-free)."
    )
    mode = lr.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--activity", dest="activity_id", type=int, default=None,
        help="Activity ID to rate (RPE mode); recomputes load.",
    )
    mode.add_argument(
        "--niggle", dest="body_part", default=None,
        help="Body part for a niggle entry (niggle mode).",
    )
    lr.add_argument(
        "--rpe", type=int, default=None, help="Borg CR10 session RPE (1-10), with --activity."
    )
    lr.add_argument("--soreness", type=int, default=None, help="Optional soreness (1-10).")
    lr.add_argument("--mood", type=int, default=None, help="Optional mood (1-10).")
    lr.add_argument(
        "--severity", type=int, default=None, help="Niggle severity (1-5), with --niggle."
    )
    lr.add_argument(
        "--date", dest="date", default=None,
        help="Niggle date YYYY-MM-DD (default: today).",
    )
    lr.add_argument("--note", dest="note", default=None, help="Optional free-text note.")
    lr.set_defaults(func=_cmd_log_rpe)

    ev = sub.add_parser("event", help="Record the races you are training for (transport-free).")
    ev_sub = ev.add_subparsers(dest="event_command", required=True)

    ev_add = ev_sub.add_parser("add", help="Record a goal race.")
    ev_add.add_argument("--date", required=True, help="Race day YYYY-MM-DD (best estimate).")
    ev_add.add_argument("--type", required=True, choices=EVENT_TYPES, help="Race type.")
    ev_add.add_argument(
        "--priority", required=True, choices=EVENT_PRIORITIES,
        help="A anchors the plan; B/C never do.",
    )
    ev_add.add_argument(
        "--status", required=True, choices=EVENT_STATUSES,
        help="Will you start? Only 'confirmed' anchors the plan or fires the taper.",
    )
    ev_add.add_argument(
        "--date-precision", dest="date_precision", required=True,
        choices=EVENT_DATE_PRECISIONS,
        help="Is the exact day known? 'approx' still drives every block.",
    )
    ev_add.add_argument("--target", default=None, help="Goal time H:MM:SS, MM:SS, or seconds.")
    ev_add.add_argument("--note", default=None, help="Optional free-text note.")
    ev_add.set_defaults(func=_cmd_event)

    ev_list = ev_sub.add_parser("list", help="Show the recorded races, the anchor, and countdowns.")
    ev_list.set_defaults(func=_cmd_event)

    ev_up = ev_sub.add_parser("update", help="Pin a date, or commit to a tentative race.")
    ev_up.add_argument("event_id", type=int, help="Event id (from `event list`).")
    ev_up.add_argument("--date", default=None, help="New race day YYYY-MM-DD.")
    ev_up.add_argument("--type", default=None, choices=EVENT_TYPES, help="New race type.")
    ev_up.add_argument(
        "--priority", default=None, choices=EVENT_PRIORITIES, help="New race priority."
    )
    ev_up.add_argument("--status", default=None, choices=EVENT_STATUSES, help="New start status.")
    ev_up.add_argument(
        "--date-precision", dest="date_precision", default=None,
        choices=EVENT_DATE_PRECISIONS, help="New date precision.",
    )
    ev_up.add_argument("--target", default=None, help="New goal time H:MM:SS, MM:SS, or seconds.")
    ev_up.add_argument("--note", default=None, help="New free-text note.")
    ev_up.set_defaults(func=_cmd_event)

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
