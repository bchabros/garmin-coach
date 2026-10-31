"""Command-line entry point: pipeline runs, marts, reports, and workout push."""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sqlite3
import sys
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from . import athlete_changes, clients, daily, lifecycle, onboarding, plan_changes, retention
from .coach import activity, freshness, report
from .core import db, events as _events, exercise_mappings, installation
from .etl import client, sync
from .marts import features, periodize, snapshot
from .runtime import InstallationContext, current
from .workouts import push

if TYPE_CHECKING:
    from .core.config import Settings


def _reports_dir(args: argparse.Namespace) -> str:
    """The command's ``--reports-dir`` override, else the installation's reports folder."""
    return args.reports_dir or str(current().reports_dir)


def _init_env(*, create: bool = False) -> tuple[Settings, sqlite3.Connection, sync.GarminClient]:
    settings = current().settings
    conn = current().open_database(create=create)
    try:
        transport = current().connect_import()
    except BaseException:
        conn.close()
        raise
    return settings, conn, transport


def _cmd_backfill(args: argparse.Namespace) -> int:
    settings, conn, transport = _init_env(create=True)
    from_date = args.from_date or settings.data_start_date

    sync.backfill(transport, conn, from_date, args.to_date)
    conn.close()
    print(f"backfill complete: {from_date} .. {args.to_date or 'yesterday'}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    settings, conn, transport = _init_env(create=True)

    result = sync.sync_incremental(
        transport,
        conn,
        data_start_date=settings.data_start_date,
        to_date=args.to_date,
        recheck_days=settings.sync_recheck_days,
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


def _cmd_activity_laps(args: argparse.Namespace) -> int:
    context = current()
    conn = context.open_database()
    try:
        data = activity.read(conn, args.activity_id)
        print(
            json.dumps(
                freshness.wrap(conn, data, recheck_days=context.settings.sync_recheck_days),
                indent=2,
            )
        )
        return 0 if data["activity"] is not None else 1
    finally:
        conn.close()


def _cmd_treadmill_speed(args: argparse.Namespace) -> int:
    context = current()
    conn = context.open_database()
    try:
        if args.command == "clear-treadmill-speed":
            result = athlete_changes.clear_treadmill_speed(
                conn,
                activity_id=args.activity_id,
                data_start_date=context.settings.data_start_date,
            )
        else:
            result = athlete_changes.log_treadmill_speed(
                conn,
                activity_id=args.activity_id,
                binding=args.binding,
                speed_kmh=args.speed_kmh,
                repetitions=args.repetitions_json,
                data_start_date=context.settings.data_start_date,
            )
        print(
            json.dumps(
                freshness.wrap(
                    conn, result.as_data(), recheck_days=context.settings.sync_recheck_days
                ),
                indent=2,
            )
        )
        return 0 if result.error is None else 2
    finally:
        conn.close()


def _cmd_features(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()

    features.features(
        conn,
        data_start_date=settings.data_start_date,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    conn.close()
    print(
        f"features complete: {args.from_date or settings.data_start_date} .. {args.to_date or 'latest'}"
    )
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()

    out = report.generate_report(
        conn,
        reports_dir=str(current().reports_dir),
        from_date=args.from_date,
        to_date=args.to_date,
        recheck_days=settings.sync_recheck_days,
    )
    conn.close()
    print(f"report complete: {out} (digest.json + charts; run the coach skill for report.md)")
    return 0


def _cmd_author(args: argparse.Namespace) -> int:
    request = None
    if args.request:
        try:
            request = json.loads(pathlib.Path(args.request).read_text())
        except (OSError, ValueError) as exc:
            print(f"author failed: cannot read request {args.request}: {exc}")
            return 1
    settings = current().settings
    conn = current().open_database()
    try:
        outcome = push.author_workout(
            conn,
            args.date,
            request=request,
            sport=args.sport,
            reuse_from=args.reuse_from,
            reports_dir=_reports_dir(args),
            recheck_days=settings.sync_recheck_days,
        )
    finally:
        conn.close()
    if outcome["error"] is not None:
        print(f"author failed: {outcome['error']}")
        return 1
    spec = outcome["spec"]
    if spec is None:
        print(f"author: nothing to author for {args.date} (rest)")
        return 0
    print(f"author complete: {outcome['path']} ({spec['name']}, {len(spec['steps'])} steps)")
    for warning in spec["warnings"]:
        print(f"  warning: {warning}")
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    conn = current().open_database()
    try:
        outcome = push.push_for_date(
            conn,
            date=args.date,
            connect=current().connect_publisher,
            reports_dir=_reports_dir(args),
            confirm=args.confirm,
            replace=args.replace,
        )
    except push.PushRefused as exc:
        print(f"push: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - a connection or receipt failure is observable
        print(f"push failed: {exc}")
        return 2
    finally:
        conn.close()
    result = outcome.result
    for warning in result.warnings:
        print(f"  warning: {warning}")
    print(f"push [{result.action}]: {result.message}")

    if result.error is not None:
        print(f"push failed: {result.error}")
        return 2
    if result.applied:
        if args.confirm:
            print(f"push complete: {outcome.receipt_path}")
        return 0
    if args.confirm and result.action == "refuse":
        return 1
    if not args.confirm:
        print("push: dry-run (re-run with --confirm to write to your Garmin account)")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    conn = current().open_database()

    status = snapshot.read(conn)
    conn.close()
    if status is None:
        print("snapshot: no athlete_status row yet; run `garmin-coach features` first")
        return 1

    out = pathlib.Path(_reports_dir(args)) / _dt.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    path = snapshot.write_json(status, out)
    print(f"snapshot complete: {path} (as of {status['computed_at']})")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()
    plans_dir = args.plans_dir or settings.plans_dir

    try:
        result = plan_changes.import_plans(
            conn,
            plans_dir=plans_dir,
            reports_dir=_reports_dir(args),
            week=args.week,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()
    if result.error is not None:
        print(f"plan import failed: {result.error}")
    if not result.weeks:
        if result.error is not None:
            return 1
        target = args.week or "any week"
        print(f"plan import: no plan file for {target} in {plans_dir}")
        return 1
    status = "partial" if result.error else "complete"
    print(
        f"plan import {status}: {', '.join(result.weeks)} "
        f"({len(result.weeks)} week(s) from {plans_dir})"
    )
    for warning in result.warnings:
        print(f"  warning: {warning}")
    for conflict in result.invalidated_pushes:
        print(
            f"  conflict: {conflict['date']} has a pushed {conflict['pushed_type']} workout, "
            f"but the plan now says {conflict['planned_intent']}; re-author and re-push it"
        )
    return 1 if result.error else 0


def _report_change_error(label: str, result: athlete_changes.ChangeResult, identity: str) -> bool:
    """Print an unsuccessful or partial result without denying a committed fact."""
    if result.error is None:
        return False
    state = "partial" if result.saved else "failed"
    print(f"{label} {state}: {result.error}")
    if result.saved:
        print(f"saved: {identity}")
    if result.recovery_command is not None:
        print(f"recovery (same athlete installation): {result.recovery_command}")
    return True


def _cmd_log_rpe(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()
    try:
        if args.activity_id is not None:
            if args.rpe is None:
                raise ValueError("--rpe is required with --activity")
            result = athlete_changes.log_session_rpe(
                conn,
                activity_id=args.activity_id,
                rpe=args.rpe,
                soreness=args.soreness,
                mood=args.mood,
                note=args.note,
                data_start_date=settings.data_start_date,
            )
            identity = f"activity {args.activity_id} rpe={args.rpe} on {result.data.get('date')}"
            message = f"{identity}; load recomputed from {result.data.get('date')}"
        else:
            if args.severity is None:
                raise ValueError("--severity is required with --niggle")
            result = athlete_changes.log_niggle(
                conn,
                body_part=args.body_part,
                severity=args.severity,
                date=args.date,
                note=args.note,
            )
            identity = (
                f"niggle {args.body_part} severity={args.severity} on {result.data.get('date')}"
            )
            message = identity
    except ValueError as exc:
        print(f"log-rpe failed: {exc}")
        return 2
    finally:
        conn.close()
    if _report_change_error("log-rpe", result, identity):
        return 2
    print(f"log-rpe complete: {message}")
    return 0


def _cmd_exercise_map(args: argparse.Namespace) -> int:
    context = current()
    try:
        conn = context.open_existing_database(writable=args.map_command == "set")
    except installation.InstallationError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    try:
        if args.map_command == "list":
            data = exercise_mappings.read(conn, name=args.name, local_only=args.local_only)
        else:
            result = athlete_changes.map_exercise(
                conn,
                name=args.name,
                pattern=None if args.pattern == "none" else args.pattern,
                muscle_group=None if args.muscle_group == "none" else args.muscle_group,
                expected_current=args.expected_current,
                data_start_date=context.settings.data_start_date,
            )
            data = result.as_data()
        print(json.dumps(data, indent=2))
        return 2 if data.get("error") else 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    finally:
        conn.close()


def _add_exercise_map_commands(sub: Any) -> None:
    parser = sub.add_parser("exercise-map", help="Read or change local exercise classifications.")
    commands = parser.add_subparsers(dest="map_command", required=True)
    listing = commands.add_parser(
        "list", help="Show assignments and packaged reference differences."
    )
    listing.add_argument("--name", default=None, help="Filter by normalized exercise name.")
    listing.add_argument(
        "--local-only", action="store_true", help="Show local additions/overrides."
    )
    setting = commands.add_parser(
        "set", help="Save an approved assignment and refresh its history."
    )
    setting.add_argument("name", help="Exercise name, as recorded or manually logged.")
    setting.add_argument("--pattern", required=True, choices=[*exercise_mappings.PATTERNS, "none"])
    setting.add_argument(
        "--muscle-group", required=True, choices=[*exercise_mappings.MUSCLE_GROUPS, "none"]
    )
    setting.add_argument(
        "--expected-current", default=None, help="Reviewed binding from list, required to replace."
    )
    parser.set_defaults(func=_cmd_exercise_map)


def _cmd_log_sets(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()
    try:
        result = athlete_changes.log_activity_sets(
            conn,
            activity_id=args.activity_id,
            stations=args.stations,
            data_start_date=settings.data_start_date,
        )
    finally:
        conn.close()
    if _report_change_error(
        "log-sets", result, f"activity {args.activity_id} on {result.data.get('date')}"
    ):
        return 2
    out = result.data
    drift = f" (not in the movement map: {', '.join(out['unmapped'])})" if out["unmapped"] else ""
    print(
        f"log-sets complete: activity {args.activity_id} stations={out['n_sets']}{drift}; "
        f"load split recomputed from {out['date']}"
    )
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


def _format_block(row: dict[str, Any] | None) -> str:
    """Render a computed current block and its anchor without inventing a missing one."""
    return f"{row['block']} (anchor {row['race_date']})" if row is not None else "none"


def _cmd_event(args: argparse.Namespace) -> int:
    settings = current().settings
    conn = current().open_database()
    try:
        if args.event_command not in {"add", "update"}:
            return _list_events(conn)
        fields = {
            name: getattr(args, name)
            for name in ("date", "type", "priority", "status", "date_precision", "target", "note")
        }
        if args.event_command == "add":
            result = athlete_changes.event_add(
                conn, **fields, data_start_date=settings.data_start_date
            )
        else:
            result = athlete_changes.event_update(
                conn, event_id=args.event_id, **fields, data_start_date=settings.data_start_date
            )
    finally:
        conn.close()
    row = result.data.get("event") or {}
    identity = f"event {result.data.get('event_id')}"
    if row.get("date") is not None:
        identity += f" on {row['date']}"
    if _report_change_error("event", result, identity):
        return 2
    print(f"event {args.event_command} complete: {_format_event(row)}")
    print(
        f"current block: {_format_block(result.data['block_before'])} -> "
        f"{_format_block(result.data['block_after'])}"
    )
    return 0


def _cmd_daily(args: argparse.Namespace) -> int:
    settings = current().settings
    # Refuse before the log exists: a run that cannot happen leaves nothing behind.
    installation.require_database(settings)
    daily.configure_logging(
        settings.log_path,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    conn = current().open_database()

    try:
        transport = current().connect_import()
    except Exception as exc:  # noqa: BLE001 - surface login failures as a failed run
        # An unanswerable login is a configuration message, not a crash: no traceback.
        readable = isinstance(exc, client.LoginUnavailableError)
        daily.logger.error("daily: login failed: %s", exc, exc_info=not readable)
        conn.close()
        print(f"daily failed: login error: {exc}")
        return 2

    result = daily.run_daily(
        transport,
        conn,
        data_start_date=settings.data_start_date,
        to_date=args.to_date,
        plans_dir=settings.plans_dir,
        reports_dir=_reports_dir(args),
        recheck_days=settings.sync_recheck_days,
    )
    conn.close()
    warnings = len(result.sync.warnings) if result.sync else 0
    print(f"daily complete: status={result.status} alerts={len(result.alerts)} warnings={warnings}")
    return result.exit_code


def _cmd_refresh_today(args: argparse.Namespace) -> int:
    settings, conn, transport = _init_env()

    result = daily.run_refresh_today(transport, conn, data_start_date=settings.data_start_date)
    conn.close()

    for warning in result.sync.warnings if result.sync else []:
        print(f"warning: {warning}")
    print(
        f"refresh-today complete: status={result.status} (today is partial until the nightly run)"
    )
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(prog="garmin-coach")
    installation.add_home_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    clients.add_parser(sub)
    lifecycle.add_commands(sub)

    setup = sub.add_parser("setup", help="Guide first login and history import for an athlete.")
    setup.add_argument(
        "--configure-only", action="store_true", help="Configure without logging in."
    )
    setup.add_argument(
        "--to", dest="to_date", help="Import through YYYY-MM-DD (default: yesterday)."
    )

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

    rf = sub.add_parser(
        "refresh-today",
        help="Pull today's (partial) data and rebuild the mart through today (issue #8).",
    )
    rf.set_defaults(func=_cmd_refresh_today)

    al = sub.add_parser(
        "activity-laps", help="Read saved laps and descriptive repetition facts (no network)."
    )
    al.add_argument("activity_id", type=int, help="Stored Garmin activity ID.")
    al.set_defaults(func=_cmd_activity_laps)

    ts = sub.add_parser(
        "log-treadmill-speed", help="Replace athlete-reported console speeds (local only)."
    )
    ts.add_argument("activity_id", type=int)
    ts.add_argument(
        "--binding", required=True, help="Reviewed work_execution.binding from activity-laps."
    )
    ts.add_argument("--speed-kmh", type=float, help="One setting for all current work repetitions.")
    ts.add_argument(
        "--repetitions-json",
        type=json.loads,
        help='Complete report list: [{"repetition":1,"speed_kmh":16.4}].',
    )
    ts.set_defaults(func=_cmd_treadmill_speed)
    tc = sub.add_parser(
        "clear-treadmill-speed", help="Explicitly remove console reports, including stale ones."
    )
    tc.add_argument("activity_id", type=int)
    tc.set_defaults(func=_cmd_treadmill_speed)

    ft = sub.add_parser("features", help="Recompute the daily_metrics mart from core data.")
    ft.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="First changed day YYYY-MM-DD (weekly rollup may expand to Monday).",
    )
    ft.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="Last day to emit YYYY-MM-DD (default: latest core date).",
    )
    ft.set_defaults(func=_cmd_features)

    au = sub.add_parser(
        "author", help="Turn a recommendation into a workout spec in reports/{date}/ (no network)."
    )
    au.add_argument("--date", dest="date", required=True, help="Target date YYYY-MM-DD.")
    au_src = au.add_mutually_exclusive_group(required=True)
    au_src.add_argument(
        "--from-recommendation",
        dest="from_recommendation",
        action="store_true",
        help="Author from the stored recommendation for the target date.",
    )
    au_src.add_argument(
        "--request",
        dest="request",
        default=None,
        help="Author from an athlete-authored workout_request JSON file.",
    )
    au_src.add_argument(
        "--reuse-from", default=None, help="Repeat a saved workout from this YYYY-MM-DD."
    )
    au.add_argument(
        "--sport",
        dest="sport",
        default=None,
        choices=["run", "hiit", "strength"],
        help=(
            "Authoring family override; by default the recommendation's intent picks it "
            "(strength -> strength, crossfit -> hiit, run types -> run; hyrox asks)."
        ),
    )
    au.add_argument(
        "--reports-dir",
        dest="reports_dir",
        default=None,
        help="Root directory for dated report folders (default: the installation's reports/).",
    )
    au.set_defaults(func=_cmd_author)

    pu = sub.add_parser("push", help="Push a workout spec to Garmin (dry-run unless --confirm).")
    pu.add_argument("--date", dest="date", required=True, help="Target date YYYY-MM-DD.")
    pu.add_argument(
        "--confirm",
        dest="confirm",
        action="store_true",
        help="Actually write to the Garmin account (default: dry-run, shows the payload).",
    )
    pu.add_argument(
        "--replace",
        dest="replace",
        action="store_true",
        help="Overwrite a different workout of the same name (unschedule + delete + re-push).",
    )
    pu.add_argument(
        "--reports-dir",
        dest="reports_dir",
        default=None,
        help="Root directory for dated report folders (default: the installation's reports/).",
    )
    pu.set_defaults(func=_cmd_push)

    rp = sub.add_parser("report", help="Build the coach digest + charts into reports/{date}/.")
    rp.add_argument(
        "--from",
        dest="from_date",
        default=None,
        help="Window start YYYY-MM-DD (default: trailing 28 days).",
    )
    rp.add_argument(
        "--to",
        dest="to_date",
        default=None,
        help="Window end YYYY-MM-DD (default: latest mart day).",
    )
    rp.set_defaults(func=_cmd_report)

    sp = sub.add_parser(
        "snapshot", help="Write the current standing to reports/{date}/snapshot.json."
    )
    sp.add_argument(
        "--reports-dir",
        dest="reports_dir",
        default=None,
        help="Root directory for dated report folders (default: the installation's reports/).",
    )
    sp.set_defaults(func=_cmd_snapshot)

    lr = sub.add_parser("log-rpe", help="Log a session RPE or a niggle to core (transport-free).")
    mode = lr.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--activity",
        dest="activity_id",
        type=int,
        default=None,
        help="Activity ID to rate (RPE mode); recomputes load.",
    )
    mode.add_argument(
        "--niggle",
        dest="body_part",
        default=None,
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
        "--date",
        dest="date",
        default=None,
        help="Niggle date YYYY-MM-DD (default: today).",
    )
    lr.add_argument("--note", dest="note", default=None, help="Optional free-text note.")
    lr.set_defaults(func=_cmd_log_rpe)

    _add_exercise_map_commands(sub)

    ls = sub.add_parser(
        "log-sets",
        help="Log the stations of a circuit the watch recorded as one set (transport-free).",
    )
    ls.add_argument(
        "--activity",
        dest="activity_id",
        type=int,
        required=True,
        help="Activity ID of the circuit; its manual stations are replaced wholesale.",
    )
    ls.add_argument(
        "stations",
        nargs="+",
        help="Station names in order, Garmin vocabulary (SLED_PULL SANDBAG_CARRY ...).",
    )
    ls.set_defaults(func=_cmd_log_sets)

    pl = sub.add_parser("plan", help="Ingest the authored weekly plans of record (transport-free).")
    pl_sub = pl.add_subparsers(dest="plan_command", required=True)

    pl_imp = pl_sub.add_parser("import", help="Cache plans/<monday>_week.md into plan_week.")
    pl_imp.add_argument(
        "--week",
        default=None,
        help="Monday YYYY-MM-DD of a single week (default: every plan file).",
    )
    pl_imp.add_argument(
        "--plans-dir",
        dest="plans_dir",
        default=None,
        help="Directory of plan files (default: the configured plans_dir).",
    )
    pl_imp.add_argument(
        "--reports-dir",
        dest="reports_dir",
        default=None,
        help=(
            "Root directory for dated report folders, checked for invalidated pushes "
            "(default: the installation's reports/)."
        ),
    )
    pl_imp.set_defaults(func=_cmd_plan)

    ev = sub.add_parser("event", help="Record the races you are training for (transport-free).")
    ev_sub = ev.add_subparsers(dest="event_command", required=True)

    ev_add = ev_sub.add_parser("add", help="Record a goal race.")
    ev_add.add_argument("--date", required=True, help="Race day YYYY-MM-DD (best estimate).")
    ev_add.add_argument("--type", required=True, choices=_events.EVENT_TYPES, help="Race type.")
    ev_add.add_argument(
        "--priority",
        required=True,
        choices=_events.EVENT_PRIORITIES,
        help="A anchors the plan; B/C never do.",
    )
    ev_add.add_argument(
        "--status",
        required=True,
        choices=_events.EVENT_STATUSES,
        help="Will you start? Only 'confirmed' anchors the plan or fires the taper.",
    )
    ev_add.add_argument(
        "--date-precision",
        dest="date_precision",
        required=True,
        choices=_events.EVENT_DATE_PRECISIONS,
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
    ev_up.add_argument("--type", default=None, choices=_events.EVENT_TYPES, help="New race type.")
    ev_up.add_argument(
        "--priority", default=None, choices=_events.EVENT_PRIORITIES, help="New race priority."
    )
    ev_up.add_argument(
        "--status", default=None, choices=_events.EVENT_STATUSES, help="New start status."
    )
    ev_up.add_argument(
        "--date-precision",
        dest="date_precision",
        default=None,
        choices=_events.EVENT_DATE_PRECISIONS,
        help="New date precision.",
    )
    ev_up.add_argument("--target", default=None, help="New goal time H:MM:SS, MM:SS, or seconds.")
    ev_up.add_argument("--note", default=None, help="New free-text note.")
    ev_up.set_defaults(func=_cmd_event)

    dl = sub.add_parser("daily", help="Nightly run: sync -> features -> alerts (for cron/launchd).")
    dl.add_argument(
        "--to", dest="to_date", default=None, help="End date YYYY-MM-DD (default: yesterday)."
    )
    dl.add_argument(
        "--reports-dir",
        default=None,
        help="Root of push receipts for plan conflicts (default: the installation's reports/).",
    )
    dl.set_defaults(func=_cmd_daily)

    reports = sub.add_parser("reports", help="Maintain local report artifacts (no network).")
    reports_sub = reports.add_subparsers(dest="reports_command", required=True)
    prune = reports_sub.add_parser("prune", help="Preview old report files eligible for deletion.")
    prune.add_argument("--confirm", action="store_true", help="Delete the selected report files.")
    prune.add_argument(
        "--older-than", type=_nonnegative_days, default=90, help="Age in days (default: 90)."
    )
    prune.add_argument(
        "--no-keep-digest", action="store_true", help="Also select digest.json and snapshot.json."
    )
    prune.set_defaults(func=_cmd_prune_reports)
    return parser


def _nonnegative_days(value: str) -> int:
    days = int(value)
    if days < 0:
        raise argparse.ArgumentTypeError("age must be a nonnegative number of days")
    return days


def _cmd_prune_reports(args: argparse.Namespace) -> int:
    installation.require_database(current().settings)
    try:
        result = retention.prune_reports(
            current().reports_dir,
            today=_dt.date.today(),
            older_than=args.older_than,
            confirm=args.confirm,
            keep_digest=not args.no_keep_digest,
        )
    except (OSError, ValueError) as exc:
        print(f"reports prune failed: {exc}")
        return 2
    print(
        "reports prune: confirmed" if args.confirm else "reports prune: dry-run (no files deleted)"
    )
    for folder in result.folders:
        print(
            f"{folder.path.name}: age={folder.age_days} days "
            f"files={len(folder.files)} bytes={sum(folder.files.values())}"
        )
    print(f"reports prune: folders={len(result.folders)} deleted_files={result.deleted_files}")
    for error in result.errors:
        print(f"reports prune failed: {error}")
    return result.exit_code


def main(
    argv: list[str] | None = None,
    *,
    context_factory: Callable[[str | None], InstallationContext] = InstallationContext.select,
) -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args(argv)
    if args.command == "setup":
        return onboarding.run(args.home, configure_only=args.configure_only, to_date=args.to_date)
    try:
        context = context_factory(args.home)
    except installation.InstallationError as exc:
        print(f"garmin-coach: {exc}", file=sys.stderr)
        return 2
    try:
        with context.use():
            return args.func(args)
    except installation.InstallationError as exc:
        print(f"garmin-coach: {exc}", file=sys.stderr)
        return 2
    except client.LoginUnavailableError as exc:
        print(f"login failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
