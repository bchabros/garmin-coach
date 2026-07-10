# PRD - Garmin Coach - Phase 1: incremental sync + resilience

> Status: Ready for implementation (TDD) - Date: 2026-07-04
> Sources: `docs/PROJECT.md` Phase 1, `docs/prd/phase-0.md`, grilling decisions.

## Problem Statement

Phase 0 can backfill Garmin data, but every run still thinks in explicit date ranges and has no durable sync progress. A transient Garmin timeout can interrupt useful work, and the operator has to decide what to rerun. The system needs a daily `sync` command that only fetches missing data, survives partial stream failures, and leaves clear state for the next run.

## Solution

Add an incremental sync workflow that tracks a per-stream watermark in `sync_state`. `garmin-coach sync` pulls from each stream's next missing day through yesterday, writes raw payloads first, upserts core rows, and advances each stream watermark only through successfully processed days. Daily streams keep the current per-day transport calls and receive retry plus stream isolation. The activities stream first tries one range fetch and falls back to per-day range calls if the full range fails after retry.

## User Stories

1. As the athlete, I want `garmin-coach sync` to pull only new Garmin data, so that daily runs are fast and do not re-fetch history unnecessarily.
2. As the athlete, I want sync to ignore today, so that incomplete HRV and sleep data do not pollute the database.
3. As the athlete, I want each stream to remember its own progress, so that a sleep failure does not block wellness, readiness, or activities.
4. As the athlete, I want failed streams to retry automatically, so that short Garmin outages do not require manual intervention.
5. As the athlete, I want a failed stream to be retried on the next run from the first missing day, so that gaps are healed without manual date math.
6. As the athlete, I want successful streams to keep their progress after a partial failure, so that the next sync does not redo work that already succeeded.
7. As the athlete, I want `sync` to bootstrap from existing Phase 0 core data, so that the first incremental run starts after the latest known row.
8. As the athlete, I want a fresh database to bootstrap from `DATA_START_DATE - 1`, so that `sync` can still populate data without a separate backfill command.
9. As the athlete, I want raw payloads stored before normalization during sync, so that future normalizer changes can reprocess the original Garmin responses.
10. As the athlete, I want core tables to remain idempotent, so that running `sync` twice does not duplicate activities or daily rows.
11. As the athlete, I want activities to fall back from a broad range call to daily range calls, so that one timeout does not prevent all activity data from landing.
12. As the athlete, I want sleep, HRV, wellness, readiness, and status to stay per-day, so that large range payloads do not create avoidable timeout risk.
13. As the athlete, I want CLI warnings for partial failures, so that I know what did not sync without losing successful progress.
14. As the athlete, I want a partial but useful run to exit successfully, so that automation can keep moving when at least one stream progressed.
15. As the operator, I want a hard failure when login, schema, configuration, or all streams fail, so that automation can alert on truly failed runs.
16. As a developer, I want retry behavior to be testable without sleeping, so that tests stay deterministic and fast.
17. As a developer, I want the sync result to report progressed streams and warnings, so that CLI behavior can be built without inspecting internals.
18. As a developer, I want sync tests to observe only public database state and sync results, so that tests survive internal refactors.
19. As a future coach-layer builder, I want the database to have contiguous best-effort core data, so that feature calculations start from a trustworthy system-of-record.

## Implementation Decisions

- Add an incremental sync entrypoint in the sync orchestration module. It will accept an injected Garmin client, SQLite connection, `data_start_date`, optional `to_date`, and retry configuration.
- Keep backfill behavior available and compatible with Phase 0. Phase 1 adds `sync`; it does not remove `backfill`.
- Use existing `sync_state` with `stream`, `last_synced_date`, and `updated_at`.
- Treat streams independently: `activities`, `sleep`, `hrv`, `wellness`, `readiness`, and `status`.
- Empty `sync_state` is bootstrapped from core tables. If a core table has rows, the initial watermark is its maximum date. If it has no rows, the initial watermark is `DATA_START_DATE - 1`.
- A stream's effective fetch window is `[last_synced_date + 1 .. yesterday]`, with optional `to_date` for bounded tests and manual runs.
- A stream watermark advances only to the last date successfully processed by that stream.
- For daily streams, keep the current per-day transport methods. Each day is retried and isolated; a day failure stops that stream at the failed date and leaves later days for a future run.
- For activities, first call the existing range method for the whole missing window. If that range call fails after retry, fall back to calling the same range method one date at a time.
- Do not add new Garmin range transport methods for daily streams in Phase 1.
- Store raw payloads before normalizing/upserting core data, preserving Phase 0's raw-first contract.
- Return a structured sync result containing per-stream progress and warnings. CLI uses that result for exit code and messages.
- CLI adds `garmin-coach sync [--to YYYY-MM-DD]`. The command uses settings, bootstraps the schema, logs into Garmin, runs incremental sync, prints a concise summary, and returns `0` on full success, no-op, or partial progress with warnings.
- CLI returns `1` for configuration/login/schema/system errors or for a run where all attempted streams fail with no progress.

## Testing Decisions

- Good tests verify external behavior through public seams. Tests should observe `sync_state`, core row counts, raw payload rows, and the returned sync result; they should not assert private helper call order except where a fake client's public call log represents the transport boundary.
- Primary seam: incremental sync orchestration with an injected fake Garmin client and in-memory SQLite.
- Secondary seam: database helpers for reading/writing watermarks and bootstrapping from core.
- CLI seam is minimal: parser and command wiring can be tested only where needed without real Garmin login.
- `client.py` remains outside unit-test scope because it is the real network/auth boundary.
- Prior art: Phase 0 already tests `sync.backfill(...)` with `FakeGarminClient`, in-memory DB, and anonymized real fixtures.
- Work in vertical TDD slices: one behavior test, minimal implementation, then the next behavior.

## Out of Scope

- Metrics mart and `features.py`.
- Coach reports and charts.
- Cron/launchd automation.
- New Garmin range endpoints for daily streams.
- VCR cassettes or live Garmin integration tests.
- Activity set extraction, already deferred from Phase 0.
- Schema migrations beyond using the existing `sync_state` table.

## Further Notes

- Partial success is intentional. The database should converge over repeated runs as transient stream failures clear.
- `raw_payloads` remains append-only. Core tables remain idempotent through existing upserts.
- The first live validation remains operator-run because Garmin login/MFA and health data access stay local.
