# PRD - Garmin Coach - Phase 7: strength and Hyrox load model + niggle log

> Status: Ready for implementation (TDD) - Date: 2026-07-11
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 7, `docs/adr/0010-phase-7-strength-load-and-niggle.md`,
> `docs/glossary.md`, grilling decisions 2026-07-11.

## Problem Statement

The load model is blind to non-cardio work. Garmin's `activityTrainingLoad` is
HR-driven, so a full `Siła` session barely registers: 74 and 68 minutes of lifting
scored `training_load` **21.8** and **33.5**. Because every downstream metric -
`load_day`, ACWR, Foster monotony/strain, the `TWO_HARD_DAYS` / `ACWR_OUT_OF_RANGE` /
`DELOAD_ADVISED` signals - is computed from `training_load`, strength and Hyrox stress
is effectively invisible: a hard lifting week looks like a light week, and the coach
can advise "add load" on top of real fatigue. There is also no channel for the
subjective signals every endurance app treats as core inputs (Foster session-RPE;
Runna's "Not Feeling 100%"; JOIN/enduco soreness prompts): the athlete cannot tell the
system "that session was brutal" or "my knee is niggling", so advice cannot dial back.

## Solution

Two additions, both feeding the existing load/signal machinery without new Garmin
fetches:

- **Session-RPE load blend (deterministic seam).** A new core table `session_rpe`
  captures a Borg CR10 rating per activity. A pure `load.blend(...)` maps each
  activity to a single load in Garmin-load units: Foster `sRPE = srpe_load_scale x rpe
  x duration_min`, scaled (`srpe_load_scale = 0.3`) so a hard `Siła` session (~70 min,
  RPE 7) lands near `hard_te_load` (150) instead of ~22. `Siła` takes the sRPE value
  directly (Garmin is blind there); every other discipline takes `max(garmin_load,
  sRPE)` so a logged RPE can only *raise* an already-honest cardio load, never lower
  it. `Siła` falls back to a default RPE (`sila_default_rpe = 7`) when none is logged,
  so the pipeline stays deterministic; cardio without a logged RPE keeps its pure
  Garmin load (no phantom sRPE). The blended load flows into a new `load_strength`
  bucket so `load_day`, ACWR, monotony, strain, and the hard-day signals finally see
  lifting, while the aerobic balance shares (`load_low/high/anaerobic`) stay
  cardio-only and the `AEROBIC_LOW_SHORTAGE` signal remains honest.

- **Niggle log + reduced-mode.** A new core table `niggle` records a body-part
  soreness/pain severity. The digest surfaces a `NIGGLE_REDUCED_MODE` signal when an
  active niggle (logged within the trailing `niggle_active_days = 7`) meets
  `niggle_reduced_mode_severity`. This is the local equivalent of Runna's dial-back:
  one log stays active for a window, and re-logging the same body part at a lower
  severity clears it early. Phase 7 surfaces the state only; Phase 10 will map active
  niggles to an avoid-list.

- **One thin writer command.** `garmin-coach log-rpe` writes ground truth to core in
  two mutually exclusive modes (`--activity ... --rpe` or `--niggle ... --severity`).
  The RPE mode recomputes `features` for the affected session date so the new load is
  immediately visible; the niggle mode only writes (reduced-mode is a digest-layer
  read that appears on the next `report`).

## User Stories

1. As the athlete, I want a logged `Siła` session to raise my daily and weekly load,
   so that a hard lifting week is not scored as a light week.
2. As the athlete, I want to log how hard a session felt (RPE) right after it, so that
   the model reflects the real stress of strength and Hyrox work.
3. As the athlete, I want a session I did not rate to still count, so that forgetting
   to log RPE never zeroes out a strength day or blocks the nightly run.
4. As the athlete, I want a logged RPE on a run to only bump the load up when I truly
   went harder than the watch thought, so that my honest cardio load is never lowered.
5. As the athlete, I want strength load kept out of my aerobic-balance shares, so that
   lifting does not disguise a shortage of easy aerobic running.
6. As the athlete, I want my ACWR, monotony, and `TWO_HARD_DAYS` to account for
   lifting, so that overtraining flags fire on my real total stress.
7. As the athlete, I want to log a niggle ("kolano, severity 4"), so that the coach
   knows to dial back before it becomes an injury.
8. As the athlete, I want one niggle log to keep me in reduced-mode for a week, so that
   I do not have to re-log the same pain every day.
9. As the athlete, I want to clear a niggle by logging it again at a lower severity, so
   that reduced-mode ends when I recover without a separate command.
10. As the athlete, I want an active niggle to show up in my coach report, so that the
    narrative opens knowing I am compromised.
11. As the athlete, I want RPE logging to refresh my load immediately, so that I can
    see the effect without waiting for the nightly run.
12. As the athlete, I want to record soreness and mood alongside RPE, so that the data
    is captured now even though nothing consumes it yet.
13. As the maintainer, I want the load blend to be a pure, total function, so that it
    is golden-tested and deterministic from core.
14. As the maintainer, I want default RPE to be config applied at blend time, not rows
    written to core, so that core stays a system of record of real observations only.
15. As the maintainer, I want `session_rpe` tied to a real activity by foreign key, so
    that an RPE can never dangle against a session that does not exist.
16. As the maintainer, I want the strength load reproducible from core on any recompute,
    so that a backfill to a past date reproduces that day's blended load.

## Implementation Decisions

Full rationale in `docs/adr/0010-phase-7-strength-load-and-niggle.md`.

- **New core table `session_rpe`** (system of record, upserted by PK). Columns:
  `activity_id` (PK), `rpe`, `soreness`, `mood`, `source`, `notes`. Foreign key
  `activity_id -> activities(activity_id) ON DELETE CASCADE`, mirroring `activity_sets`.
  One row per activity; re-logging corrects it. `source` records how the row was
  entered (`'manual'` for the CLI). Added to `schema.sql` (package copy) and mirrored to
  `docs/schema.sql` (guarded by `test_schema_sync.py`).
- **New core table `niggle`** (system of record). Composite PK `(date, body_part)` -
  one severity per body part per day, re-logging updates. Columns: `date`, `body_part`,
  `severity`, `note`.
- **New pure module `load.py`** with the primary seam
  `blend(discipline, garmin_load, srpe_load) -> float`. Rule: `Siła` returns
  `srpe_load`; every other discipline returns `max(garmin_load or 0, srpe_load or 0)`.
  A helper computes `srpe_load = srpe_load_scale * rpe * duration_min` from an RPE and
  `dur_s` (total session duration, the Foster standard); it is `None` when there is no
  logged RPE and the discipline has no default. `Siła` substitutes `sila_default_rpe`
  when no RPE is logged; other disciplines do not. Pure and total: `NULL` garmin_load
  is treated as 0.
- **`features._load_by_day` consumes the blend.** It reads `session_rpe` once, and for
  each activity computes the blended load via `load.blend`, then attributes it to the
  existing TE buckets for cardio (`load_low/high/anaerobic`) or to the **new
  `load_strength` bucket** for `Siła`. `load_day = load_low + load_high +
  load_anaerobic + load_strength`, so ACWR / monotony / strain / hard-day logic (all
  reading `load_day`) see strength automatically, with no change to their code.
- **`daily_metrics` gains `load_strength`.** `weekly_metrics` gains `load_strength` and
  `strength_share`; `load_total` still sums `load_day` (so it includes strength), and
  the four shares (`low_share`, `high_share`, `anaero_share`, `strength_share`) sum to
  1.0.
- **`AEROBIC_LOW_SHORTAGE` stays cardio-only.** `signals.load_shares` continues to
  total `load_low + load_high + load_anaerobic` (strength excluded), so the
  easy-vs-hard aerobic judgment is unaffected - the whole reason for a separate bucket.
- **No `rpe_hard` threshold.** "Hard day" remains the single criterion
  `load_day >= hard_te_load`; a hard `Siła` session crosses it through the blend, so
  `TWO_HARD_DAYS`, `DELOAD_ADVISED`, and `weekly` hard-day logic are unchanged.
- **New digest signal `NIGGLE_REDUCED_MODE`** (severity `warn`). The digest reads the
  `niggle` table live (like it reads `weekly` and `zones`), selects the latest entry
  per body part within `[to_date - niggle_active_days + 1, to_date]`, and fires when any
  active niggle's severity `>= niggle_reduced_mode_severity`. Facts are flat scalars:
  `body_part` (the worst), `severity`, `n_active`, `days_active`.
- **New CLI command `garmin-coach log-rpe`** with two mutually exclusive modes.
  `--activity <id> --rpe N [--soreness N] [--mood N] [--notes ...]` validates the
  activity exists (clear error otherwise), upserts `session_rpe`, then calls
  `features(from_date=<activity date>)` so the blended load is refreshed. `--niggle
  <body_part> --severity N [--date YYYY-MM-DD] [--note ...]` upserts `niggle` and
  returns (reduced-mode appears on the next `report`); `--date` defaults to today so a
  niggle can be logged against the day it was noticed. Validation: RPE 1-10,
  soreness/mood 1-10 (optional), severity 1-5. The command is transport-free (never
  calls Garmin).
- **New `db.upsert_session_rpe` and `db.upsert_niggle` helpers**, following the
  existing `_upsert` pattern (PK `activity_id`, and composite `(date, body_part)`).
- **Four new `coach_thresholds` keys** (seeded in `schema.sql` and mirrored to
  `DEFAULTS`): `srpe_load_scale = 0.3`, `sila_default_rpe = 7`, `niggle_active_days = 7`,
  `niggle_reduced_mode_severity = 3`.

## Testing Decisions

Good tests exercise external behavior at the seams - `load.blend` and the composed
`daily_metrics` / digest - over frozen fixtures, not internal SQL. Prior art:
`test_features.py`, `test_digest.py`, `test_weekly.py`, `test_zones.py`.

- **`test_load.py` (new seam, primary):** `blend` maps a `Siła` session at default RPE
  to ~150; a logged RPE on a run only raises load when `sRPE > garmin`; a low-RPE run
  keeps its Garmin load; `NULL` garmin_load degrades to sRPE / 0; the scale and default
  are read from thresholds.
- **`test_features.py`:** a logged `Siła` RPE raises `load_day` and `load_strength` and
  shifts ACWR; `load_day` equals the sum of the four buckets; the aerobic shares are
  unchanged by a strength day; recompute is idempotent and reproduces a past day's
  blended load (as-of).
- **`test_weekly.py`:** `load_strength` and `strength_share` populate; the four shares
  sum to 1.0; `load_total`, monotony, and strain include the strength load.
- **`test_digest.py`:** `NIGGLE_REDUCED_MODE` fires for an active niggle at/above the
  severity threshold with flat facts; it is silent when the newest entry is below the
  threshold or older than `niggle_active_days`; a re-log at lower severity clears it.
- **`test_thresholds.py`:** the four new keys are present with their defaults.
- **`test_cli.py`:** `log-rpe --activity` rejects a non-existent activity, upserts, and
  recomputes; `log-rpe --niggle` writes and does not recompute; range validation fails
  loudly.
- **`test_schema_sync.py`:** `docs/schema.sql` stays byte-identical after the two new
  tables + seed rows.

## Out of Scope

- **Per-set capture (Phase 8)** - `activity_sets` stays empty; no exercise -> movement
  pattern map, no `pattern_overlap`.
- **Niggle avoid-list (Phase 10)** - active niggles surface as a reduced-mode state
  only; mapping a body part to an avoid-list of exercises is deferred.
- **Consuming soreness/mood** - both are captured in `session_rpe` for future phases;
  no signal reads them in Phase 7.
- **Snapshot integration** - `athlete_status` does not gain a strength-load or
  reduced-mode field this phase; the reduced-mode state lives in the digest.
- **A separate `rpe_hard` notion** - hard-day detection stays the single blended-load
  criterion.
- **New Garmin fetches** - `session_rpe` and `niggle` are manual ground truth written by
  the CLI; the ETL is untouched.

## Further Notes

- `srpe_load_scale` and `sila_default_rpe` are stored in `coach_thresholds` so the
  calibration is tunable without code as history grows; 0.3 / 7 target a hard ~70-minute
  `Siła` session at ~150 (= `hard_te_load`).
- The blend is deliberately conservative for cardio (`max`, no default injection) so it
  can never *reduce* an honest Garmin load - RPE is a floor-raiser, not a replacement.
- `NIGGLE_REDUCED_MODE` facts stay flat scalars (worst body part + counts) to honor the
  `signals.py` "facts are always flat scalars" contract; the full active list is not
  needed until the Phase 10 avoid-list.
- This phase introduces the first CLI writer to a **core** table that does not originate
  from Garmin. It stays a system-of-record write (observed ground truth, like an
  activity), keeping the medallion discipline: derived values still live only in marts.
