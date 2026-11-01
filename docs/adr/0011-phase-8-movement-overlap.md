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
- #118 makes classification changes sanctioned local athlete operations. New
  assignments and replacements are shown for approval; replacing a different value
  binds to the reviewed current assignment so another change cannot be overwritten
  silently. The shared athlete-change flow retains saved mappings on refresh failure.
- Packaged mappings seed missing names while preserving installed choices. Local
  additions and overrides are compared against the packaged schema for review;
  they travel with the retained database, not automatically into a new empty one.
  This avoids making a personal chat edit silently change the shared reference map.
- The fourteen #118 additions retain exact captured aliases and agreed simplified
  judgments: curls use pull/grip, lateral raises push/shoulders, burpees muscle-only
  core, and jumping jacks/jump rope neither dimension. These model groupings are
  explicit choices, not a complete anatomical description.
- Known nameless `CARDIO` pseudo-sets have null pattern/muscle: exclude them from
  mapped-set denominators without reporting them as mapping drift.
- Split the session's blended load by mapped-set share, not tonnage, which is often
  absent for bodyweight/Hyrox. Unmapped sets do not silently dilute known movement load.
- Sum daily movement loads. Consecutive-day overlap is their minimum when both clear
  the configured floor; a rest day clears it. Store positive overlaps in a derived mart.
- Latest-day pattern/muscle signals compare that overlap with configured policy and
  name offending keys using flat facts. Separate per-axis thresholds and a weekly
  overlap rollup remain deferred. ADR 0022 adds the manual-overlay read surface.
- Classification corrections apply to selected historical sets using the current
  map; no effective-date mapping history is introduced. Recomputed historical
  analyses may change, while retained report files and original set evidence remain.

## Consequences

Re-import fills missing sets; offline `features` refreshes their derivatives.
Coverage exposes map drift. Thresholds are provisional placeholders rather than
measured personal limits; no new overlap chart is implied.
