# 01 - Session-RPE load model: `session_rpe`, `load.blend`, `load_strength` bucket, `log-rpe --activity`

Status: done
Parent: `docs/prd/phase-7/PRD.md`

## What to build

Make strength and Hyrox work visible to the load model. A new core table
`session_rpe` captures a Borg CR10 rating per activity; a pure `load.blend(...)` maps
each activity to a single load in Garmin-load units (Foster `sRPE = srpe_load_scale x
rpe x duration_min`, scaled so a hard `Siła` lands near `hard_te_load`); and a new
`load_strength` bucket carries the blended strength load into `load_day` so ACWR,
monotony, strain, and the hard-day signals finally see lifting - while the aerobic
balance shares stay cardio-only. The `garmin-coach log-rpe --activity` writer is the
vertical slice: log an RPE and watch the load refresh.

Populated this ticket: `session_rpe` table (+ FK, upsert helper); `load.py` seam;
`load_strength` in `daily_metrics` and `load_strength` + `strength_share` in
`weekly_metrics`; `features._load_by_day` blend integration; the three new load
thresholds; `log-rpe --activity` with auto-recompute. Niggle / reduced-mode is
ticket 02.

## Acceptance criteria

- [ ] `session_rpe` core table (PK `activity_id`, FK `-> activities(activity_id) ON
      DELETE CASCADE`, columns `rpe, soreness, mood, source, notes`) added to the
      packaged `schema.sql` and mirrored to `docs/schema.sql`.
- [ ] `daily_metrics.load_strength` and `weekly_metrics.load_strength` +
      `weekly_metrics.strength_share` columns added (both schema copies).
- [ ] New `load.py` with a pure, total `blend(discipline, garmin_load, srpe_load) ->
      float`: `Siła` returns `srpe_load`; other disciplines return `max(garmin_load or
      0, srpe_load or 0)`. A helper computes `srpe_load` from RPE + `dur_s` and the
      scale; `Siła` substitutes `sila_default_rpe` when no RPE is logged, others do not.
- [ ] `db.upsert_session_rpe(conn, row)` helper following the `_upsert` pattern.
- [ ] `features._load_by_day` reads `session_rpe` once and attributes each activity's
      blended load to the TE buckets (cardio) or `load_strength` (`Siła`);
      `load_day = load_low + load_high + load_anaerobic + load_strength`.
- [ ] `weekly.rollup` sums `load_strength` and emits `strength_share`; the four shares
      sum to 1.0; `load_total`, monotony, and strain include strength.
- [ ] `AEROBIC_LOW_SHORTAGE` is unchanged: `signals.load_shares` still totals
      `load_low + load_high + load_anaerobic` (strength excluded).
- [ ] `garmin-coach log-rpe --activity <id> --rpe N [--soreness N] [--mood N] [--notes
      ...]` validates the activity exists (clear error otherwise), upserts
      `session_rpe`, then recomputes `features` for the activity's date. Range
      validation: RPE 1-10, soreness/mood 1-10. Transport-free.
- [ ] Three new `coach_thresholds` keys seeded in `schema.sql` and `DEFAULTS`:
      `srpe_load_scale=0.3`, `sila_default_rpe=7` (the fourth,
      `niggle_reduced_mode_severity`/`niggle_active_days`, lands in ticket 02).
- [ ] `test_load.py`: `Siła` default RPE -> ~150; logged RPE raises a run only when
      `sRPE > garmin`; low-RPE run keeps Garmin load; `NULL` garmin degrades cleanly;
      scale/default read from thresholds.
- [ ] `test_features.py`: a `Siła` RPE raises `load_day` + `load_strength` and shifts
      ACWR; `load_day` == sum of four buckets; aerobic shares unchanged by a strength
      day; idempotent; as-of reproduces a past day's blended load.
- [ ] `test_weekly.py`: `load_strength` + `strength_share` populate; four shares sum to
      1.0; `load_total`/monotony/strain include strength.
- [ ] `test_thresholds.py` and `test_schema_sync.py` stay green.

## Blocked by

- None - can start immediately.
