"""Nightly orchestrator: plans -> sync -> features -> alerts.

Wraps the deterministic pipeline in one testable seam, ``run_daily``, plus a
logging setup helper. ``garmin-coach daily`` and ``scripts/daily.sh`` are the
thin, out-of-seam wrappers that schedule it. The nightly path never renders
charts and never calls Garmin outside the injected sync client (the golden
rule); charts and ``report.md`` stay in the weekly ``report`` run.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .coach import digest, report
from .core import plan
from .etl import sync
from .marts import features

logger = logging.getLogger("garmin_coach.daily")

# Digest signal severities that are surfaced as nightly alerts.
ALERT_SEVERITIES = frozenset({"warn", "alert"})

_STATUS_EXIT = {"ok": 0, "degraded": 1, "failed": 2}


@dataclass
class DailyResult:
    """Observable outcome of one nightly run.

    Attributes:
        sync: The sync stage outcome, or ``None`` if the stage crashed.
        features_ok: Whether the features stage completed.
        alerts: Digest signals with ``warn``/``alert`` severity that fired.
        errors: Human-readable messages for any stage that errored.
        fatal: Whether a core stage (sync or features) crashed.
        plans_imported: week_start of every plan file cached by the plans stage.
    """

    sync: sync.SyncResult | None = None
    features_ok: bool = False
    alerts: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fatal: bool = False
    plans_imported: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Return ``ok`` | ``degraded`` | ``failed`` for the run."""
        if self.fatal:
            return "failed"
        s = self.sync
        # Total sync outage: attempts were made, none progressed, all warned.
        if s is not None and s.total_outage:
            return "failed"
        # A single stream failed but the run went on, or a non-fatal error occurred.
        if (s is not None and s.degraded) or self.errors:
            return "degraded"
        return "ok"

    @property
    def exit_code(self) -> int:
        """Return the process exit code for the run's status."""
        return _STATUS_EXIT[self.status]


def _run_plans_stage(
    conn: sqlite3.Connection, result: DailyResult, plans_dir: str | Path | None
) -> None:
    """Cache every authored plan file into ``plan_week`` before the mart is rebuilt.

    Non-fatal by design: a malformed plan must not stop the night's data from
    landing, but it is never swallowed either - the run degrades and names the
    failure, so the athlete fixes the file rather than reading a template plan
    that looks authored.
    """
    if plans_dir is None:
        return
    logger.info("daily: plans stage starting (dir=%s)", plans_dir)
    try:
        result.plans_imported = plan.import_dir(conn, plans_dir)
    except plan.PlanParseError as exc:
        logger.error("daily: plan import failed: %s", exc)
        result.errors.append(f"plan import failed: {exc}")
        return
    logger.info("daily: plans stage done (imported=%d)", len(result.plans_imported))


def run_daily(
    client: sync.GarminClient,
    conn: sqlite3.Connection,
    *,
    data_start_date: str,
    to_date: str | None = None,
    thresholds: dict[str, float] | None = None,
    max_attempts: int = 3,
    retry_base_seconds: float = 1.0,
    plans_dir: str | Path | None = None,
) -> DailyResult:
    """Run the nightly pipeline: plans -> sync -> features -> alert extraction.

    Sequences the deterministic stages over an injected transport client and an
    open connection. A raised exception in the sync or features stage is fatal
    and skips later stages; an isolated stream failure (a sync warning) only
    degrades the run. The digest is read purely to extract ``warn``/``alert``
    signals as alerts - no charts are rendered and no report folder is written.

    Args:
        client: Transport client satisfying the sync ``GarminClient`` protocol.
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First real-data date; earlier days are explicit gaps.
        to_date: Last date to pull/emit (default: yesterday / latest mart day).
        thresholds: Coach thresholds; read from ``coach_thresholds`` when omitted.
        max_attempts: Retry attempts per Garmin call in the sync stage.
        retry_base_seconds: Base for the sync stage's exponential backoff.
        plans_dir: Directory of authored ``<monday>_week.md`` plans; skipped when
            omitted. A parse error degrades the run rather than falling back
            silently to the template (issue #21).

    Returns:
        A :class:`DailyResult` with the sync outcome, alerts, and derived status.
    """
    result = DailyResult()
    _run_plans_stage(conn, result, plans_dir)

    logger.info("daily: sync stage starting (to_date=%s)", to_date or "yesterday")
    try:
        result.sync = sync.sync_incremental(
            client,
            conn,
            data_start_date=data_start_date,
            to_date=to_date,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - orchestrator records, never re-raises
        logger.exception("daily: sync stage crashed")
        result.errors.append(f"sync crashed: {exc}")
        result.fatal = True
        return result
    for warning in result.sync.warnings:
        logger.warning("daily: sync warning: %s", warning)
    logger.info(
        "daily: sync stage done (progressed=%s warnings=%d)",
        ",".join(sorted(result.sync.progressed_streams)) or "none",
        len(result.sync.warnings),
    )

    logger.info("daily: features stage starting")
    try:
        features.features(conn, data_start_date=data_start_date, to_date=to_date)
        result.features_ok = True
    except Exception as exc:  # noqa: BLE001 - orchestrator records, never re-raises
        logger.exception("daily: features stage crashed")
        result.errors.append(f"features crashed: {exc}")
        result.fatal = True
        return result
    logger.info("daily: features stage done")

    logger.info("daily: alert stage starting")
    try:
        thr = thresholds if thresholds is not None else report.read_thresholds(conn)
        dg = digest.build_digest(conn, to_date=to_date, thresholds=thr)
        result.alerts = [s for s in dg["signals"] if s["severity"] in ALERT_SEVERITIES]
    except Exception as exc:  # noqa: BLE001 - non-fatal: data is already persisted
        logger.exception("daily: alert stage crashed")
        result.errors.append(f"alerts crashed: {exc}")

    for alert in result.alerts:
        level = logging.ERROR if alert["severity"] == "alert" else logging.WARNING
        logger.log(level, "daily: ALERT %s %s", alert["code"], alert.get("facts", {}))
    logger.info("daily: run complete (status=%s alerts=%d)", result.status, len(result.alerts))
    return result


def run_refresh_today(
    client: sync.GarminClient,
    conn: sqlite3.Connection,
    *,
    data_start_date: str,
    today: str | None = None,
) -> DailyResult:
    """Run the same-day refresh: pull today (partial), rebuild the mart through today.

    The opt-in counterpart to the nightly path (issue #8): the default pipeline
    stops at yesterday, this pulls the current day so a same-day coach read can
    see this morning's HRV/readiness. Sync watermarks are never advanced - the
    day is partial by definition and the nightly run must re-pull it complete.
    No alert stage: signals over a partial day would false-fire; alerts stay
    owned by ``run_daily``.

    Args:
        client: Transport client satisfying the sync ``GarminClient`` protocol.
        conn: Open SQLite connection with the schema bootstrapped.
        data_start_date: First real-data date; earlier days are explicit gaps.
        today: The day to refresh (default: the actual current date).

    Returns:
        A :class:`DailyResult` with the pull outcome and derived status.
    """
    date = today or dt.date.today().isoformat()
    result = DailyResult()

    logger.info("refresh: same-day pull starting (date=%s)", date)
    try:
        result.sync = sync.refresh_day(client, conn, date)
    except Exception as exc:  # noqa: BLE001 - orchestrator records, never re-raises
        logger.exception("refresh: same-day pull crashed")
        result.errors.append(f"refresh crashed: {exc}")
        result.fatal = True
        return result
    for warning in result.sync.warnings:
        logger.warning("refresh: warning: %s", warning)

    logger.info("refresh: features stage starting (to_date=%s)", date)
    try:
        features.features(conn, data_start_date=data_start_date, to_date=date)
        result.features_ok = True
    except Exception as exc:  # noqa: BLE001 - orchestrator records, never re-raises
        logger.exception("refresh: features stage crashed")
        result.errors.append(f"features crashed: {exc}")
        result.fatal = True
        return result

    logger.info("refresh: run complete (status=%s)", result.status)
    return result


def configure_logging(
    log_path: str,
    *,
    max_bytes: int = 1_000_000,
    backup_count: int = 5,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure the ``garmin_coach`` logger with a rotating file + console.

    Rotation lives in-process (size-based ``RotatingFileHandler``) so it is
    OS-agnostic and deterministic, satisfying the automation "log rotated"
    requirement without a system logrotate.

    Args:
        log_path: Destination log file; parent directories are created.
        max_bytes: Rotate once the active file reaches this size.
        backup_count: Number of rotated files to keep.
        level: Logging level for the package logger.

    Returns:
        The configured ``garmin_coach`` package logger.
    """
    path = Path(log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    package_logger = logging.getLogger("garmin_coach")
    package_logger.setLevel(level)
    package_logger.handlers.clear()
    package_logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)
    file_handler.setFormatter(fmt)
    package_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    package_logger.addHandler(console_handler)

    return package_logger
