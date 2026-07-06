"""Phase 4 automation: the nightly orchestrator (sync -> features -> alerts).

Wraps the deterministic pipeline in one testable seam, ``run_daily``, plus a
logging setup helper. ``garmin-coach daily`` and ``scripts/daily.sh`` are the
thin, out-of-seam wrappers that schedule it. The nightly path never renders
charts and never calls Garmin outside the injected sync client (the golden
rule); charts and ``report.md`` stay in the weekly ``report`` run.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import digest, features, report, sync

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
    """

    sync: sync.SyncResult | None = None
    features_ok: bool = False
    alerts: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    fatal: bool = False

    @property
    def status(self) -> str:
        """Return ``ok`` | ``degraded`` | ``failed`` for the run."""
        if self.fatal:
            return "failed"
        s = self.sync
        # Total sync outage: attempts were made, none progressed, all warned.
        if s is not None and s.warnings and s.attempted_streams and not s.had_progress:
            return "failed"
        # A single stream failed but the run went on, or a non-fatal error occurred.
        if (s is not None and s.warnings and s.had_progress) or self.errors:
            return "degraded"
        return "ok"

    @property
    def exit_code(self) -> int:
        """Return the process exit code for the run's status."""
        return _STATUS_EXIT[self.status]


def run_daily(
    client: sync.GarminClient,
    conn: sqlite3.Connection,
    *,
    data_start_date: str,
    to_date: str | None = None,
    thresholds: dict[str, float] | None = None,
    max_attempts: int = 3,
    retry_base_seconds: float = 1.0,
) -> DailyResult:
    """Run the nightly pipeline: sync -> features -> alert extraction.

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

    Returns:
        A :class:`DailyResult` with the sync outcome, alerts, and derived status.
    """
    result = DailyResult()

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
    logger.info(
        "daily: run complete (status=%s alerts=%d)", result.status, len(result.alerts)
    )
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
    OS-agnostic and deterministic, satisfying the Phase 4 "log rotated"
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
