# PRD - Garmin Coach - Phase 4: automation (nightly orchestrator + alerts)

> Status: Ready for implementation (TDD) - Date: 2026-07-06
> Sources: `docs/PROJECT.md` Phase 4 + section 7, `docs/adr/0004-phase-4-automation.md`, `docs/adr/0003-phase-3-coach-signals.md`, grilling decisions.

## Problem Statement

The pipeline works but only by hand. The athlete has to remember to run `sync`,
then `features`, then `report`, in order, every day. Nothing runs unattended,
nothing tells them when a signal fires overnight (suppressed HRV this morning,
ACWR running hot, too much grey-zone work), and if a run breaks at 3 a.m. there
is no non-zero exit and no rotated log to notice or diagnose it. Automation is
the missing layer between "the metrics exist" and "the metrics reach me on
their own."

## Solution

A single nightly orchestrator plus a thin scheduling wrapper.

- **Deterministic (Python, testable).** A new seam `run_daily(client, conn, *,
  data_start_date, to_date=None, thresholds=None, max_attempts=3,
  retry_base_seconds=1.0) -> DailyResult` runs the
  pipeline in order: `sync_incremental` -> `features` -> `build_digest` (read
  only, to extract alerts). It never renders charts and never writes a report
  folder - that stays in the weekly `report` run. It returns a `DailyResult`
  carrying the sync outcome, whether features ran, the list of fired alerts, any
  stage errors, and a derived `status` (`ok` / `degraded` / `failed`) and
  `exit_code` (`0` / `1` / `2`).
- **Alerts reuse Phase 3 signals.** An alert is any digest signal with severity
  `warn` or `alert`. No new rules or thresholds; `coach_thresholds` and ADR-0003
  remain the single source of signal semantics. Alerts are logged (WARNING for
  `warn`, ERROR for `alert`).
- **Logging + rotation in Python.** `configure_logging(log_path, *, max_bytes,
  backup_count)` installs a size-based `RotatingFileHandler` plus a console
  handler on the `garmin_coach` logger, so rotation is OS-agnostic and
  deterministic.
- **Thin scheduling wrapper.** `garmin-coach daily [--to]` wires config -> login
  -> `run_daily`, prints a one-line summary, and returns the exit code.
  `scripts/daily.sh` execs that command; a launchd plist example schedules it.
  Installing the schedule is left to the operator.

The seam is the whole point: sequencing, partial-failure handling, the exit-code
contract, and alert extraction are all pure orchestration over an injected
client, testable with the existing Phase 1 fake-client fixtures. The shell and
launchd wiring are proven by a live run.

## User Stories

1. As the athlete, I want the whole pipeline to run overnight on its own, so that
   fresh metrics are waiting for me in the morning without any manual steps.
2. As the athlete, I want one command that runs sync then features then alert
   extraction in the right order, so that I never run them out of sequence.
3. As the athlete, I want a morning-HRV-low alert surfaced by the nightly run, so
   that I know to downgrade today's quality session before I train.
4. As the athlete, I want an ACWR-out-of-range alert surfaced overnight, so that
   over-reaching or detraining risk reaches me without opening a report.
5. As the athlete, I want an `AEROBIC_LOW_SHORTAGE` alert surfaced overnight, so
   that I am told to add Zone 2 before the week drifts further into grey zone.
6. As the athlete, I want a two-hard-days-stacked alert surfaced overnight, so
   that my Friday-into-Saturday risk is called out ahead of time.
7. As the athlete, I want alerts to be exactly the Phase 3 `warn`/`alert`
   signals, so that the overnight run and the weekly report never disagree about
   what matters.
8. As the athlete, I want the nightly run to keep going when one Garmin stream
   fails, so that a flaky sleep or RHR endpoint does not cost me the whole run.
9. As the operator, I want the nightly run to exit non-zero when it genuinely
   fails, so that cron/launchd or a monitor can alert me to a broken run.
10. As the operator, I want a distinct exit code for "completed but degraded"
    versus "failed", so that I can tell a single flaky stream from a real outage
    without reading the log.
11. As the operator, I want a clean no-op run (everything already current) to
    exit `0` with no alerts, so that a quiet night is not mistaken for a problem.
12. As the operator, I want the run to log to a rotating file, so that logs do
    not grow unbounded and old runs stay available for diagnosis.
13. As the operator, I want each fired alert written to the log at a severity
    matching the signal, so that grepping the log surfaces the night's alerts.
14. As the operator, I want the log path, max size, and backup count
    configurable via env, so that I can tune retention without editing code.
15. As the operator, I want `scripts/daily.sh` to be a thin wrapper that execs
    `garmin-coach daily`, so that scheduling has no logic of its own to drift.
16. As the operator, I want a launchd plist example checked in, so that I can
    schedule the run on macOS by copying and loading it, on my own terms.
17. As the operator, I want the daily command to never render charts, so that a
    broken display backend cannot fail an unattended run.
18. As the operator, I want a failed login (rate limit / expired token) to fail
    the run cleanly with a non-zero exit and a log entry, so that auth problems
    are visible, not silent.
19. As the operator, I want re-running the nightly command to be idempotent, so
    that a retry after a partial night does not double-write core rows.
20. As a developer, I want orchestration tested at one seam with an injected
    client, so that stage sequencing and the exit-code contract are covered
    without touching Garmin or the OS scheduler.
21. As a developer, I want the daily path to reuse `sync_incremental`,
    `features`, and `build_digest` unchanged, so that Phase 4 adds orchestration
    only, not a second copy of pipeline logic.
22. As a developer, I want no schema change in this phase, so that the mart and
    thresholds stay stable and `test_schema_sync` stays green.

## Implementation Decisions

- **Primary seam - `run_daily(client, conn, *, data_start_date, to_date=None,
  thresholds=None) -> DailyResult`** in a new `daily.py`. It calls
  `sync.sync_incremental`, then `features.features`, then `digest.build_digest`
  (with thresholds from `coach_thresholds` when not supplied) to extract alerts.
  It renders no charts and writes no report folder. The injected `client` is the
  same `sync.GarminClient` protocol used by the sync seam. `max_attempts` and
  `retry_base_seconds` pass straight through to the sync stage (production
  defaults `3` / `1.0`; tests set them to `1` / `0` for fast, deterministic
  failure-path runs).
- **`DailyResult`** is a dataclass: `sync: SyncResult | None`, `features_ok:
  bool`, `alerts: list[dict]`, `errors: list[str]`, `fatal: bool`. Derived
  properties:
  - `status`: `failed` when `fatal` or when sync totally failed
    (`warnings and attempted_streams and not had_progress`); `degraded` when sync
    warned but progressed, or a non-fatal (alert-stage) error was recorded; `ok`
    otherwise.
  - `exit_code`: `ok` -> `0`, `degraded` -> `1`, `failed` -> `2`.
- **Stage failure handling.** A raised exception in the sync or features stage is
  fatal (`fatal = True`, later stages skipped). Sync warnings from isolated
  stream failures are *not* fatal - the run continues (Phase 1 isolation carries
  through). An exception while building the digest for alerts is recorded in
  `errors` but is non-fatal (data is already synced and featured), degrading the
  run rather than failing it.
- **Alerts.** `alerts = [s for s in digest["signals"] if s["severity"] in
  {"warn", "alert"}]`. Each is logged: `WARNING` for `warn`, `ERROR` for `alert`,
  including its `code` and `facts`. Thresholds are read from `coach_thresholds`
  (via the existing `report.read_thresholds`) unless caller supplies them.
- **Logging - `configure_logging(log_path, *, max_bytes=1_000_000,
  backup_count=5, level=logging.INFO)`** in `daily.py`. Creates the log
  directory, clears and reconfigures the `garmin_coach` logger with a
  `RotatingFileHandler` (size-based) and a `StreamHandler`. `run_daily` logs
  stage boundaries, each sync warning, each alert, and a final status summary.
- **CLI - `garmin-coach daily [--to YYYY-MM-DD]`** in `cli.py`, wired like
  `sync`: load settings, `configure_logging`, bootstrap the DB, `client.login`.
  On login failure: log it, return exit `2`. Otherwise call `run_daily`, print a
  one-line summary (`status`, alert count, warning count), and return
  `result.exit_code`.
- **Config - `Settings`** gains `log_path` (`./logs/daily.log`),
  `log_max_bytes` (`1_000_000`), `log_backup_count` (`5`).
- **Scheduling artifacts.** `scripts/daily.sh` (executable, thin: `set -euo
  pipefail`, `cd` to repo root, `exec poetry run garmin-coach daily "$@"`) and
  `scripts/com.garmincoach.daily.plist.example` (a launchd `StartCalendarInterval`
  entry the operator copies to `~/Library/LaunchAgents` and loads). `logs/` is
  git-ignored. A `daily` target is added to the Taskfile for local runs.
- **No schema change.** `coach_thresholds`, `daily_metrics`, and
  `training_status_daily` already exist. If a column ever proves missing, edit the
  package copy `src/garmin_coach/schema.sql` and re-sync `docs/schema.sql`
  (guarded by `test_schema_sync.py`).

## Testing Decisions

- Good tests assert only external behavior of the seam: inject a fake client,
  hand `run_daily` an open temp SQLite (optionally pre-seeded with core rows), and
  assert on the returned `DailyResult` - `status`, `exit_code`, `features_ok`, and
  the `alerts` codes. No assertions on private helpers, log formatting, or
  intermediate structures. This is the same injected-client seam as
  `test_sync.py`, reusing the `conn` and `fake_client` fixtures from
  `conftest.py`.
- **Vertical slices (`tests/test_daily.py`), each red -> green:**
  - Clean run: default fake client over a fresh range progresses every stream
    with no warnings -> `status == "ok"`, `exit_code == 0`, `features_ok is True`.
  - Alerts surfaced: seed `hrv_nightly` so the latest night flags low (a
    descending series whose last value falls below `baseline - 1*SD`), run with a
    no-op client -> `alerts` contains `HRV_LOW_MORNING` and `status == "ok"`.
  - Degraded on partial failure: a client that fails one stream but lets another
    progress -> `status == "degraded"`, `exit_code == 1`, and the run still
    completes features.
  - Failed on total outage: a client that fails every stream -> `status ==
    "failed"`, `exit_code == 2`.
- **Prior art.** `test_sync.py` (injected client, isolated-stream-failure and
  fallback assertions), `test_features.py` (seed core -> run -> read mart),
  `test_digest.py` (signal presence assertions). Reuse their fixtures and the
  descending-HRV construction from `test_features.py::test_hrv_baseline_sd_and_low_flag`.
- **Out of seam (validated by a live run, not unit tests):** `configure_logging`
  and rotation (a real run rotates the file at size), `cli._cmd_daily` (login,
  summary line, exit code propagation), `scripts/daily.sh` and the launchd plist
  (schedules and runs unattended, non-zero exit on failure). A minimal parser
  test (`daily` accepts `--to`) mirrors `test_cli.py`.

## Out of Scope

- **Push / external alert delivery** (email, Notion, mobile push). Alerts are
  log lines this phase; wiring them to a channel is a later add.
- **Chart rendering and report-folder writing on the nightly path** - kept in the
  weekly `garmin-coach report` run driven by Cowork.
- **Installing the schedule** into the user's launchd/cron - shipped as an example
  the operator loads themselves.
- **New signals, rules, or thresholds** - alerts are exactly the Phase 3
  `warn`/`alert` signals; Rule 6 (plan vs actual) stays Phase 5.
- **Weekly rollups / `weekly_metrics`** - later phase.
- **Any live Garmin call from features/alerts** - forbidden by the golden rule;
  only the sync stage touches transport, through the injected client.

## Further Notes

- **Why no charts nightly.** Rendering matplotlib PNGs on an unattended macOS
  launchd job adds a headless-backend failure surface for artifacts nobody reads
  until the weekly review. Keeping charts in the weekly `report` run shrinks the
  nightly failure surface to network + DB.
- **Why exit codes and not just logs.** A cron/launchd monitor can act on a
  non-zero exit without parsing text; the three-way `ok`/`degraded`/`failed`
  split lets it distinguish a flaky stream from a real outage.
- **Idempotency is inherited.** `sync_incremental` upserts by key and advances
  watermarks; `features` and `build_digest` are recomputations. Re-running
  `daily` after a partial night does not double-write core rows.
