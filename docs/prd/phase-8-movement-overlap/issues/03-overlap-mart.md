# 03 - Overlap mart (`overlap.py` + `pattern_overlap`)

Status: ready-for-agent
Blocked by: 01, 02
Sources: `docs/prd/phase-8-movement-overlap/PRD.md` (Metric section).

## Goal

Compute the daily `pattern_overlap` metric: the same movement pattern or muscle group
loaded on adjacent days without a rest day, distributed by set-share of the session's
Phase 7 load. Pure, golden-tested, reproducible from core.

## Scope

- **New table `pattern_overlap`** in `schema.sql` (mirrored). Columns: `date`, `dim`
  (`'pattern'|'muscle'`), `key`, `load_d`, `load_prev`, `overlap`, PK
  `(date, dim, key)`. Only rows with `overlap > 0` are materialized.
- **New pure module `overlap.py`** (prior art: `weekly.py`, `zones.py`) that
  materializes the table and is wired into the `features` recompute.
- **`pattern_load` per (activity, dim, key).** Join `activity_sets.subcategory ->
  exercise_pattern`; for each axis and key,
  `pattern_load = (n_sets_key / n_sets_total) x sess_load`, where `sess_load` is the
  activity's Phase 7 blended load and `n_sets_total` counts the session's **mapped** sets.
- **Daily aggregation then adjacency.** Same-day sessions sum into `pat_load[D]` per
  `(dim, key)`. `overlap[D] = min(pat_load[D], pat_load[D-1])` when **both** exceed
  `pattern_load_floor`, else `0`. Strictly consecutive calendar days.
- **Unmapped subcategories.** Excluded from `pattern_load` (never counted). Log a WARN
  (`[Phase8] N unmapped subcategories: ...`) and compute the coverage numbers
  (`sets_total`, `sets_unmapped`, sorted unmapped names) for the digest to surface
  (the digest fact itself lands in ticket 04).

## Thresholds

Reads `pattern_load_floor` from `coach_thresholds` (seeded in ticket 04). Until that
lands, the test can inject the threshold directly at the seam.

## Tests (`test_overlap.py`, primary)

- Set-share splits a session's load across its patterns/muscles; same-day sessions sum.
- `overlap = min(D, D-1)` when both exceed the floor; `0` when a rest day intervenes or
  one day is below the floor.
- An unmapped subcategory is excluded and counted in coverage.
- Recompute is idempotent and reproduces a past day's overlap (as-of).

## Done when

- `garmin-coach features` rebuilds `pattern_overlap` from core; a constructed
  adjacent-day stack yields the expected `overlap` rows; mirror guard green.
- `task check` green.
