# ADR 0002 - Phase 2 metrics (mart) semantics

## Status

Accepted

## Context

Phase 0/1 land normalized core data in SQLite. Phase 2 adds `features.py`, which
reads core tables and materializes the `daily_metrics` mart: HRV baseline + low
flag, own ACWR with an `n_chronic` credibility counter, TE-based load buckets, and
HR-zone minutes. The Definition of Done is a golden regression that reproduces a
reference hand-analysis of 2026-06-09..07-04 (HRV baseline approx. 68 ms, SD approx.
11 ms, threshold approx. 57 ms; ACWR on 2026-07-03 approx. 1.0). Several metric
definitions in the BUILD doc are ambiguous in ways that change whether the reference
numbers reproduce; this ADR pins them.

## Decision

- **Test seam.** `features(conn)` reads core and upserts `daily_metrics`. Tests seed
  a temporary SQLite DB with core rows, run `features(conn)`, read `daily_metrics`
  back, and assert on it - the same DB-boundary seam as `test_sync.py` / `test_db.py`.
- **HRV baseline is whole-window, not causal.** `hrv_baseline` = median of every
  non-null `avg_hrv` night in the computed range; `hrv_sd` = sample std (ddof=1) over
  the same set. The same baseline/SD is stamped on every row. `hrv_low_flag = 1` when
  `avg_hrv < hrv_baseline - 1 * hrv_sd`. This is the only interpretation that
  reproduces the reference (a causal window on 2026-06-11 sees ~3 nights and cannot
  yield threshold 57). It is not a correctness problem: because the mart is fully
  recomputed each run, "all nights in range" for *today's* row is every night up to
  today, i.e. causal at run time. The window is capped to the trailing 60 nights once
  history exceeds 60 (config knob); default is the whole available window.
- **ACWR uses fixed denominators with zero-fill.** `load_day` = sum of `training_load`
  of the day's activities (0 if none). `acute7` = sum(`load_day`) over the trailing 7
  calendar days incl. today / 7. `chronic28` = sum over trailing 28 days / 28.
  `acwr = acute7 / chronic28`. Rest days and pre-`data_start` days contribute 0, which
  dilutes chronic low while history is short, so ACWR reads *overstated* until the
  window fills - exactly the documented behavior. `n_chronic` = count of in-window
  days that have a real data row (date >= `data_start`); the report must flag ACWR as
  indicative while `n_chronic < 28`.
- **Row coverage is one row per calendar day.** `features` emits a `daily_metrics` row
  for every calendar day from `data_start` through the latest core date, gap-filled
  (rest days: `load_day`/buckets/zones = 0; days missing HRV/sleep: those columns
  NULL). Trailing windows always read core beyond `--from`, so a narrowed range still
  produces correct ACWR/baseline.
- **RHR source.** `rhr` = `daily_wellness.rhr`, falling back to `sleep.resting_hr` for
  the date when wellness RHR is NULL; NULL if neither.
- **Activity-to-day bucketing** is by the date part of `activities.start_local`.
- **Load buckets are total over nulls.** Per activity: `load_anaerobic` when
  `anaero_te >= 1.0`; else `load_low` when `aero_te < 2.5`; else `load_high`. NULL
  `anaero_te`/`aero_te` are treated as 0 (so a no-TE activity falls into `load_low`);
  NULL `training_load` contributes 0. Bucket totals therefore always sum to the day's
  total `training_load`.
- **HR-zone minutes** `z1..z5_min` = sum of `activities.hr_z1..z5_s` for the day / 60.
- **Recompute strategy.** `garmin-coach features [--from --to]` recomputes every day in
  range and upserts by `date` (`INSERT ... ON CONFLICT(date) DO UPDATE`). Default range
  is the full history (`data_start` -> latest core date). Rerun yields identical
  `daily_metrics`. No `features_version` column yet (deferred per BUILD doc).
- **Golden fixture.** A SQL dump of the core tables (`activities`, `hrv_nightly`,
  `daily_wellness`, `sleep`) restricted to the reference range, anonymized, lives at
  `tests/fixtures/features_golden.sql` and is regenerable from `data/garmin.db`. The
  golden test asserts baseline = 68, SD approx. 11, threshold approx. 57, flags on
  2026-06-11/18/19/27, and ACWR(2026-07-03) approx. 1.0 within tolerance.

## Out of scope (Phase 3+)

- Comparing load buckets to `monthlyLoad*Target*` and emitting `AEROBIC_LOW_SHORTAGE`.
- The coach report text and plots (HRV band, ACWR over time).
- `garmin_acwr` stays a core `daily_wellness` value; `features` does not recompute it.

## Consequences

- The golden regression is deterministic and reproducible from real anonymized data.
- Historical `hrv_baseline`/`hrv_low_flag` values shift as new nights arrive; this is
  expected for a recomputed mart and is why the mart is never a system of record.
- ACWR is honest-by-construction about its own unreliability via `n_chronic`.
- `daily_metrics` is gap-free per calendar day, so downstream windowing is trivial.
