# ADR 0004 - Phase 4 automation (nightly orchestrator + alerts)

## Status

Accepted

## Context

Unattended execution needs observable stage failures without chart or display dependencies.

## Decision

- `run_daily` sequences plan import, sync, one mart pass and digest alert extraction
  over injected transport and SQLite. Thresholds are read from the DB policy.
- `warn`/`alert` digest signals become WARNING/ERROR log entries; there is no separate
  alert rule engine. The nightly path renders no charts and never pushes workouts.
- Exit 2 means a fatal sync/features exception or total sync outage; exit 1 means
  isolated stream/plan/alert failure with useful work; otherwise exit 0, including
  clean no-op runs. Enrichment misses alone do not degrade status (ADR 0018).
- Python owns rotating file and console logging. The shell wrapper only selects
  the checkout and execs the existing command.
- Legacy launchd/cron setup remains operator-owned. Managed opt-in scheduling is
  separately defined by ADR 0032 and is never inferred from successful installation.

## Consequences

Offline orchestration tests cover sequencing/status. Native scheduling requires
separate acceptance. A failed mart pass rolls back the complete generation (ADR 0017);
charts refresh only on an explicit report operation.
