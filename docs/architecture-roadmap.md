# Architecture Roadmap After Phase 5

Status: completed

This roadmap turns the post-Phase-5 architecture review into testable slices. The
goal is to deepen shallow modules without changing the core project decisions:
Garmin transport stays isolated in ETL, marts remain recomputable, and the coach
skill consumes the deterministic digest.

## 1. Scope the digest horizon

Problem: `build_digest(..., to_date=...)` scoped daily facts to the requested
window, but weekly facts were read from the latest `weekly_metrics` row in the DB.
A historical report could therefore mix past daily facts with future weekly facts.

Plan: resolve one report horizon first, then read daily rows, weekly rows, and
weekly signals through that same horizon.

## 2. Deepen mart materialization

Problem: `features.features(...)` writes both `daily_metrics` and
`weekly_metrics`, but `from_date` still looks like a daily-only interface. A
mid-week partial recompute can leave the weekly rollup reading stale earlier
days in that week.

Plan: make the materialization seam own weekly coherence by expanding partial
daily recomputes to the affected week before rolling up.

## 3. Persist plan-vs-actual weekly facts

Problem: `weekly_metrics.plan_adherence` is stored, while the per-day mismatch
direction is re-derived later by `digest` from current `daily_metrics`,
`plan_template`, and thresholds.

Plan: materialize the per-day plan-vs-actual facts alongside `weekly_metrics`,
then have the digest read those stored facts.

## 4. Collapse threshold policy

Problem: threshold defaults, DB reads, and fallback behavior are split between
`digest.py`, `report.py`, `weekly.py`, and schema seed rows.

Plan: introduce one threshold policy module that owns defaults, DB reads, and
merge behavior while preserving plain dicts for signal functions.

## 5. Deepen sync stream execution

Problem: `sync_incremental` owns stream selection, retry, raw insert, core
upsert, watermark movement, and commit cadence in one broad implementation.

Plan: concentrate stream advancement and raw/core/watermark persistence in
stream-level helpers while keeping `sync_incremental(client, conn, ...)` as the
public seam.

## 6. Retire stale coach guidance

Problem: `docs/coach-skill.md` still describes the old direct-DB narrative
workflow, while the active skill correctly consumes only `digest.json`.

Plan: replace the stale doc with a pointer to `skills/coach/SKILL.md` and the
digest-first contract.
