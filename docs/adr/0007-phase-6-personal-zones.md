# ADR 0007 - Phase 6: personal training zones (`athlete_zones` mart)

## Status

Accepted

## Context

Every intensity call ("is this run truly easy?", "was the week too grey?") leaned
on `coach_thresholds.hr_z2_upper_bpm = 140` - a hardcoded placeholder annotated
*"approx Z2 ceiling; refine from user_settings zones"*. There was no derived,
athlete-specific set of HR or pace zones anywhere. Meanwhile the watch already
auto-detects a Lactate Threshold (LTHR 175 bpm, threshold pace ~4:17/km) that the
ETL never ingested, and `fitness_markers` sat empty.

The task: anchor the athlete's zones on their own watch-detected LTHR and keep
them fresh, so intensity advice is deterministic instead of hand-computed. See
`docs/prd/phase-6.md`.

## Decision

- **Recomputed `athlete_zones` mart (singleton).** Five %LTHR HR bands, `lthr_bpm`,
  `threshold_pace_s_per_km`, `z2_pace_ceiling_s_per_km`, `source`, `stale`,
  `lthr_detected_on`, `computed_at`. Derived values live only in the mart, never
  mixed into core (medallion discipline).

- **Seam: `zones.py`, run inside `features`.** A pure, total `compute(...)` builds
  the row from the latest LTHR plus aerobic runs; `rollup(conn, ...)` reads core +
  thresholds and upserts the singleton. Runs as the tail of `features` (a
  mart-from-core step, like `weekly.rollup`) - no new command, never touches
  Garmin (golden rule).

- **HR bands = %LTHR multipliers** (Garmin/Friel scheme), stored in
  `coach_thresholds`: Z1 <80%, Z2 80-89%, Z3 89-94%, Z4 94-99%, Z5 >=99%. At LTHR
  175 the Z2 HR ceiling is ~156 bpm - a deliberate, self-scaling shift up from the
  retired 140 placeholder. The per-activity `hr_z*_s` buckets (the watch's own zone
  semantics) are left untouched.

- **Z2 pace ceiling: hybrid with a heat guard.** A pace<->HR OLS regression over
  aerobic runs predicts pace at the Z2 HR ceiling, used only once qualifying-run
  count `>= zones_regression_min_runs` and fit quality `>= zones_regression_min_r2`;
  otherwise `threshold_pace * z2_pace_fallback_mult`. Runs with
  `temp_c > zones_heat_temp_c` are excluded so summer HR drift does not inflate
  "easy pace". The function is total: the mart is fully populated from day one and
  the regression takes over on its own as history accrues.

- **`source` records provenance.** `<method>+lthr` (e.g. `regression+lthr`,
  `threshold_pace_fallback+lthr`) names the pace-ceiling method *and* the HR-band
  provenance (bands are %LTHR-derived); `no_anchor` when there is no LTHR. Lets a
  future device-vs-derived disagreement be flagged.

- **Ingest the anchor (core), once per run.** LTHR is an occasional-change
  biometric, not a daily `SyncStream`; fetched once per sync/backfill, raw payload
  appended, normalized rows upserted into `fitness_markers`. **Backfill uses the
  ranged form** (parallel `speed`/`heart_rate` series, joined by detection day ->
  the detection history); **nightly uses latest** (single current detection).

- **Normalizers emit only owned columns.** `normalize_lactate` /
  `normalize_lactate_range` emit only `date` + `lactate_thr_hr` +
  `lactate_thr_pace`, so an upsert never clobbers sibling `fitness_markers` columns
  written by another marker source. Both are pure and total (US17: never crash on
  missing data). The garminconnect `latest=True` transport already merges Garmin's
  raw two-entry list and its `hearRate` typo into one `speed_and_heart_rate` dict,
  so the latest normalizer reads that merged dict (not the raw list).

- **Per-activity temperature (core).** `_store_activities` fans out one
  `get_activity_weather` call per activity and writes a converted `temp_c` (Garmin's
  `temp` is Fahrenheit even on a metric account). Failures are isolated (missing
  weather -> `temp_c` NULL, never aborts the stream), consistent with Phase 1.

- **Staleness = quiet metadata, not an alarm.** `stale = 1` when the anchoring
  detection is older than `zones_stale_days` (default 28, TrainerRoad AI-FTP
  cadence). Surfaced as an `info`-level fact in the digest `zones` block *plus the
  detection age* (`lthr_age_days`), not a signal in the alert list.

- **Report integration: extend the digest, retire the placeholder.** `build_digest`
  gains a `zones` block (HR bands, Z2 pace ceiling + `source`, `lthr_bpm`, `stale` +
  age). `AEROBIC_LOW_SHORTAGE` keeps its load-share logic and gains one fact
  alongside it - `personal_z2_minute_share` (share of running minutes at avg HR at/
  below the personal Z2 ceiling) - so the coach sees the load-bucket read and the
  personal-zone read and their divergence. `hr_z2_upper_bpm` and its readers are
  removed.

## Thresholds (new keys in `coach_thresholds`)

| Key | Default | Meaning |
|---|---|---|
| `z1_hi_pct_lthr` | `0.80` | Z1 upper bound as a fraction of LTHR |
| `z2_hi_pct_lthr` | `0.89` | Z2 upper bound (the Z2 HR ceiling) |
| `z3_hi_pct_lthr` | `0.94` | Z3 upper bound |
| `z4_hi_pct_lthr` | `0.99` | Z4 upper bound |
| `z2_pace_fallback_mult` | `1.30` | threshold-pace multiplier for the fallback ceiling |
| `zones_regression_min_runs` | `12` | qualifying clean runs before the regression activates |
| `zones_regression_min_r2` | `0.30` | fit-quality floor below which the fallback is used |
| `zones_heat_temp_c` | `22` | runs hotter than this (Celsius) are excluded from the fit |
| `zones_stale_days` | `28` | detection older than this marks the zones stale |

`hr_z2_upper_bpm` is removed from the seed and its readers.

## Testing

- `test_zones.py` (new seam): golden + case tests over `compute` - no-LTHR degraded
  row, thin-history fallback, regression with enough clean runs, heat exclusion
  dropping below the minimum, %LTHR bands, staleness, idempotent recompute.
- `test_models.py`: `normalize_lactate` (latest, owns only LTHR columns) and
  `normalize_lactate_range` (ranged history joined by day, power-only dates skipped)
  on real anonymised fixtures; `normalize_activity` temp_c Fahrenheit->Celsius.
- `test_features.py`: `features` writes exactly one `athlete_zones` row, idempotent.
- `test_digest.py`: the `zones` block (incl. `lthr_age_days`) and
  `personal_z2_minute_share` alongside the unchanged `AEROBIC_LOW_SHORTAGE` facts.
- `test_thresholds.py`: the new zone keys present, `hr_z2_upper_bpm` gone.
- `test_schema_sync.py`: `docs/schema.sql` identical to the package copy.
- Out of seam (validated by a live run, like the rest of `client.py`): the ranged
  transport and weather fan-out. Confirmed live - the ranged form backfilled the
  full detection history (179/177/174/172/175 bpm) and nightly latest upserted
  idempotently.
