# ADR 0006 - Post-Phase-5 architecture deepening

## Status

Accepted

## Context

After Phase 5, the pipeline was functionally complete, but the architecture
review found several shallow interfaces where time and configuration semantics
could leak across modules:

- `build_digest(..., to_date=...)` scoped daily facts to a report window but read
  the latest weekly facts in the DB.
- `features.features(..., from_date=...)` could recompute only part of a week,
  then roll up `weekly_metrics` from stale earlier daily rows.
- `weekly_metrics.plan_adherence` was materialized, but the per-day
  plan-vs-actual direction was re-derived later from the current
  `plan_template` and current thresholds.
- Threshold defaults and DB overrides were split across several modules.
- `sync_incremental` held stream execution and outcome policy in one broad
  implementation.

One finding conflicts with ADR-0005's "No new tables" scope cut. That cut was
right for shipping Phase 5, but after the digest gained historical reporting and
plan-vs-actual narration, storing only the adherence ratio proved too shallow:
the explanatory facts could drift from the stored weekly mart.

## Decision

- Treat the digest `to_date` as a single **report horizon**. Daily rows, weekly
  rows, and weekly signals are all selected at or before that horizon.
- Expand partial daily mart recomputes to the Monday of the affected week before
  running the weekly rollup, preserving one `features` command while making the
  weekly coherence rule local to the materialization module.
- Add `weekly_plan_actual`, a recomputable mart detail table keyed by
  `(week_start, dow)`, and populate it during `weekly.rollup`. This supersedes
  ADR-0005's "No new tables" statement for this narrow derived table.
- Keep `weekly.plan_vs_actual(...)` as the public interface, but have it read the
  materialized facts first and fall back to deriving them for older local DBs.
- Introduce `thresholds.py` as the threshold policy module: code defaults, DB
  seed rows, and explicit test overrides merge in one place.
- Move sync partial-success and total-outage policy onto `SyncResult`, and move
  stream advancement into stream-level helpers while preserving
  `sync_incremental(client, conn, ...)` as the orchestration seam.
- Retire `docs/coach-skill.md` as executable guidance. The active coach narrative
  module is `skills/coach/SKILL.md`, which consumes only `digest.json`.
- `thresholds.read_raw` drops `NULL` rows from `coach_thresholds` before merging,
  so a null seed row can no longer override a code default with `None`. The old
  `report.read_thresholds` let nulls through; this is a behavior fix bundled into
  the consolidation, not a new policy decision.

## Consequences

- Historical reports no longer mix future weekly facts into past daily windows.
- Weekly rollups no longer read stale earlier days after a mid-week partial
  recompute.
- Plan-vs-actual narration is coherent with the weekly mart row that produced the
  adherence number.
- Existing DBs remain usable: `bootstrap` creates the new table, and
  `plan_vs_actual` falls back to derivation until the next `features` run
  repopulates weekly facts.
- Threshold behavior is easier to test and less coupled to `digest.py`.
- The sync module keeps the same seam but has better locality around stream
  persistence and outcome policy.
