# ADR 0011 - Phase 8: per-set capture + movement-pattern overlap

## Status

Accepted

## Context

A session load does not reveal which movement patterns or muscle groups it taxed.

## Decision

- Capture active exercise sets as raw-first, best-effort per-activity enrichment.
  Normalize parent category/subcategory; a missing name falls back to the known category.
  Replace all captured sets per activity, so a shrinking response leaves no orphan rows.
- `exercise_pattern` is editable shared reference vocabulary. Patterns are
  push/pull/hinge/squat/carry; grip belongs to the muscle axis, shared by carries/pulls.
  Unknown exercises remain visible in coverage rather than being guessed.
- Known nameless `CARDIO` pseudo-sets have null pattern/muscle: exclude them from
  mapped-set denominators without reporting them as mapping drift.
- Split the session's blended load by mapped-set share, not tonnage, which is often
  absent for bodyweight/Hyrox. Unmapped sets do not silently dilute known movement load.
- Sum daily movement loads. Consecutive-day overlap is their minimum when both clear
  the configured floor; a rest day clears it. Store positive overlaps in a derived mart.
- Latest-day pattern/muscle signals compare that overlap with configured policy and
  name offending keys using flat facts. Separate per-axis thresholds and a weekly
  overlap rollup remain deferred. ADR 0022 adds the manual-overlay read surface.

## Consequences

Re-import fills missing sets; offline `features` refreshes their derivatives.
Coverage exposes map drift. Thresholds are provisional placeholders rather than
measured personal limits; no new overlap chart is implied.
