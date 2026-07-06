# ADR 0004 - Phase 4 automation (nightly orchestrator + alerts)

## Status

Accepted

## Context

Phases 0-3 built the deterministic pipeline as separate commands: `sync`
(incremental ETL), `features` (mart), and `report` (digest + charts). Nothing
schedules them, and nobody is told when a signal fires. BUILD Phase 4 asks for a
`scripts/daily.sh` (sync -> features) under cron/launchd, threshold alerts
("morning HRV < threshold", "ACWR > 1.3", `AEROBIC_LOW_SHORTAGE`), and an
unattended nightly run whose failures are observable. DoD: the nightly run works
without interaction; the log is rotated; an error yields a non-zero exit plus a
log entry.

The tension: automation is inherently shell/OS-level (launchd, cron), which the
project's test discipline treats as out of seam (validated by a live run, not
unit tests). We still want the orchestration logic - stage sequencing, partial
failure handling, exit-code contract, alert extraction - under a testable seam.

## Decision

- **Single seam.** `run_daily(client, conn, *, data_start_date, to_date=None,
  thresholds=None) -> DailyResult` in a new `daily.py`. It runs sync -> features
  -> alert extraction over an injected client and an open connection, mirroring
  the `sync_incremental(client, conn, ...)` seam. Tests inject a fake client and
  assert on the returned `DailyResult`. `configure_logging`, `scripts/daily.sh`,
  the launchd plist, and `cli._cmd_daily` are out of seam (live run).

- **Pipeline shape: sync -> features -> alerts, no charts.** The nightly path
  runs `sync_incremental` then `features`, then calls `build_digest` (a pure DB
  read) purely to extract alerts. It does **not** render charts or write a report
  folder. Charts and `report.md` stay in the weekly `garmin-coach report` run
  driven by Cowork. This keeps matplotlib and any headless-rendering failure off
  the unattended path and matches BUILD ("daily.sh = sync -> features"; weekly
  review from Cowork).

- **Alerts are digest signals, reused.** An alert is any digest signal with
  `severity in {warn, alert}` (the Phase 3 signals: `HRV_LOW_MORNING`,
  `ACWR_OUT_OF_RANGE`, `AEROBIC_LOW_SHORTAGE`, `TWO_HARD_DAYS`). No new thresholds
  and no new rules: thresholds come from `coach_thresholds`, semantics from
  ADR-0003. Delivery is log lines (WARNING for `warn`, ERROR for `alert`), which
  satisfies "error = log entry" and needs no external service. Push / email /
  Notion delivery is out of scope for this phase.

- **Golden rule preserved.** `run_daily` is the only place the daily path touches
  transport, and only through the injected `sync` client. `features` and
  `build_digest` read the finished DB, never Garmin.

- **Status + exit-code contract.** `DailyResult` derives:
  - `failed` (exit 2): a core stage (`sync` or `features`) raised, **or** sync
    saw a total failure (`warnings and attempted_streams and not had_progress`) -
    the same condition `cli._cmd_sync` already uses.
  - `degraded` (exit 1): sync had warnings but still progressed (a single stream
    failed, isolated per Phase 1), **or** the non-fatal alert stage errored.
  - `ok` (exit 0): otherwise (including a clean no-op run where everything is
    already current: no warnings, no alerts).
  A failing stream must never abort the run (BUILD Phase 1 isolation carries into
  Phase 4); only an unexpected exception in sync/features is fatal.

- **Logging + rotation in Python.** `configure_logging(log_path, *, max_bytes,
  backup_count)` installs a `logging.handlers.RotatingFileHandler` (size-based)
  plus a console handler on the `garmin_coach` logger. Rotation lives in-process
  so it is OS-agnostic (launchd has no logrotate; macOS `newsyslog` is fiddly)
  and deterministic. The shell wrapper does no logging of its own beyond what
  launchd/cron capture from stdout/stderr.

- **New config.** `log_path` (`./logs/daily.log`), `log_max_bytes` (1_000_000),
  `log_backup_count` (5) in `Settings`. `logs/` is git-ignored.

- **No schema change.** Reuse `coach_thresholds`; `daily_metrics` and
  `training_status_daily` already exist. `test_schema_sync` stays green.

- **Scheduling is documented, not installed.** Ship `scripts/daily.sh` (thin:
  `set -euo pipefail`, `cd` to repo root, `exec poetry run garmin-coach daily`)
  and a launchd plist example. Installing into the user's launchd/cron is a
  side-effecting, user-owned action and is left to the operator, documented in
  the PRD and README.

## Consequences

- Orchestration (sequencing, partial-failure handling, exit codes, alert
  extraction) is unit-testable at one seam with an injected client, reusing the
  Phase 1 fake-client fixtures. Rotation and the shell/launchd wiring are proven
  by a live run.
- The nightly path never imports a chart or renders a PNG, so a broken display
  backend cannot fail the unattended run; the cost is that charts refresh only on
  the weekly `report` run.
- Alerts stay a thin projection of the Phase 3 digest, so signal semantics live
  in exactly one place (ADR-0003) and the daily path inherits any tuning of
  `coach_thresholds` for free.
- Exit codes let a cron/launchd monitor distinguish clean, degraded, and failed
  runs without parsing the log.
