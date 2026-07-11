# ADR 0011 - Phase 8: per-set capture + movement-pattern overlap

## Status

Accepted

## Context

The Phase 7 load blend made strength and Hyrox stress visible as a single per-session
number, but the system is blind to *what* that stress loads. `activity_sets` was empty
(the per-set ingestion committed as Phase 0 D9 was deferred), so nothing notices that
the same movement pattern or muscle group is loaded on adjacent sessions without
recovery - today's grip / posterior-chain warning was eyeballed, not computed. No
endurance app models this, so it is a differentiator, but it needs concrete per-set data
and a deterministic overlap metric. See `docs/prd/phase-8-movement-overlap/PRD.md`.

## Decision

- **Per-set ingest reuses the `_fetch_weather` seam, not a new stream.** Exercise sets
  are a per-activity enrichment, so `_fetch_sets` mirrors the existing best-effort
  weather fetch inside `_store_activities`: raw-first, non-blocking, on every `sync` and
  `backfill`. A failure leaves the activity without sets and never aborts the run.
  `normalize_exercise_sets` keeps only `ACTIVE` sets and reads Garmin's
  `exercises[].category` (parent) and `exercises[].name` (sub-category); `name=None`
  under a known parent falls back to the category so the row keeps a join key.

- **The exercise->pattern map is core reference seed data.** A hand-curated
  `exercise_pattern(subcategory, pattern, muscle_group)` table is seeded in `schema.sql`
  (mirrored to `docs/schema.sql`), editable without touching Python and visible in
  migrations - consistent with `coach_thresholds` / `plan_template`. Unseen
  subcategories are surfaced as a coverage fact, never auto-classified.

- **Grip is a muscle group, not a sixth pattern.** `pattern` is the five clean movement
  patterns (`push/pull/hinge/squat/carry`); `grip` lives on the `muscle_group` axis,
  because carries and pulls both tax grip. This is a deliberate deviation from the
  PROJECT.md sketch, which listed `grip` as a sixth pattern. It lets `MUSCLE_OVERLAP`
  catch a grip + posterior-chain stack in one signal.

- **`pattern_load` = set-share x session load, not tonnage.** Each session's Phase 7
  blended load is split across its patterns / muscles by mapped-set share. Tonnage
  (`sets x reps x weight`) collapses for Hyrox / bodyweight work where `max_weight` is
  NULL - exactly where grip / carry overlap is most likely - so set-share is the robust
  choice. The denominator is mapped sets only; unmapped sets are excluded entirely.

- **Overlap is daily and consecutive-day.** Same-day sessions sum into `pat_load[D]`;
  `overlap[D] = min(pat_load[D], pat_load[D-1])` when both exceed `pattern_load_floor`.
  A single rest day clears the stack (consistent with Phase 5's `max_consec_hard`
  day-granularity). Materialized long-format in `pattern_overlap(date, dim, key, ...)`,
  only overlap>0 rows. A weekly rollup is deferred (deliberate deviation from
  PROJECT.md's "daily/weekly").

- **Two `warn` signals fire on the latest day.** `PATTERN_STACK` (dim=pattern) and
  `MUSCLE_OVERLAP` (dim=muscle) fire when any key on that axis has
  `overlap >= pattern_overlap_high` on the window's `to_date`, naming the offending keys
  (a comma-joined scalar, honoring the flat-facts contract). Unmapped exercises surface
  as a `movement` coverage fact so map drift stays visible; the overlap metric never
  silently under-counts.

- **Two placeholder thresholds.** `pattern_load_floor = 20` and
  `pattern_overlap_high = 40` (Garmin-load units) are seeded in `coach_thresholds` and
  `thresholds.DEFAULTS`, tunable as `activity_sets` history grows. Separate per-axis
  thresholds were rejected as premature without data.

## Consequences

- `activity_sets` populates on a re-run `backfill` (idempotent, replace-per-activity),
  so no separate migration is needed for the existing activities.
- The coach report gains two overlap signals and a coverage line; no new report section
  or chart this phase (deferred until there is real per-set history to visualize).
- `load.activity_load` is factored out as the shared per-activity blend helper, reused
  by `overlap.py`; `features._load_by_day` keeps its own inlined blend for now.
- The map is hand-maintained: exercise-name drift shows up as unmapped-set coverage, the
  signal to extend `exercise_pattern`.
