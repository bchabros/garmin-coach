# ADR 0010 - Phase 7: strength and Hyrox load model + niggle log

## Status

Accepted

## Context

The load model is blind to non-cardio work. Garmin's `activityTrainingLoad` is
HR-driven, so a full `Siła` session scores almost nothing (74 and 68 minutes of
lifting scored `training_load` 21.8 and 33.5). Every downstream metric - `load_day`,
ACWR, Foster monotony/strain, and the `TWO_HARD_DAYS` / `ACWR_OUT_OF_RANGE` /
`DELOAD_ADVISED` signals - is derived from `training_load`, so strength and Hyrox
stress is invisible and a hard lifting week looks like a light one. There is also no
channel for the subjective inputs endurance apps treat as core (Foster session-RPE;
Runna's dial-back; JOIN/enduco soreness). See `docs/prd/phase-7/PRD.md`.

## Decision

- **Scale sRPE into Garmin-load units; keep one load currency.** Garmin
  `training_load` (EPOC-based) and raw Foster sRPE (`rpe x minutes`) are different
  units - a 70-minute session at RPE 7 is `sRPE = 490`, ~20x the Garmin score, and raw
  sRPE would dominate cardio too, breaking the ACWR / `hard_te_load=150` /
  `monotony_high=2.0` scale. We introduce `srpe_load_scale` (0.3) so
  `sRPE = srpe_load_scale x rpe x duration_min`, calibrated with `sila_default_rpe=7`
  so a hard ~70-minute `Siła` session lands near `hard_te_load` (150). One currency
  means the signals need no rescaling.

- **Discipline blend rule: `Siła` = sRPE, everything else = `max(garmin, sRPE)`.**
  Only `Siła` is genuinely blind to Garmin's HR-driven load, so it takes the sRPE
  value directly. Cardio (running / trail / ski / Hyrox-HIIT) keeps its honest Garmin
  load and a logged RPE can only *raise* it via `max` - RPE is a floor-raiser, never a
  replacement, so the blend can never lower an honest load. Hyrox/HIIT stays in the
  max branch because it carries real HR load; treating it as pure sRPE would discard
  that. Chosen over "everything is sRPE" (conservative) and over the plan's raw
  unscaled `max` (would invert the scale).

- **Default RPE fills `Siła` only.** A missing RPE must not zero out a strength day, so
  `Siła` substitutes `sila_default_rpe` when nothing is logged, keeping the pipeline
  deterministic. Cardio gets no default injection: without a logged RPE it keeps its
  pure Garmin load, so a phantom sRPE can never inflate a light run.

- **Defaults are config applied at blend time, never rows in core.** The default RPE
  lives in `coach_thresholds` and is applied inside the pure `load.blend`, not written
  to `session_rpe`. Core stays a system of record of real observations only (medallion
  discipline); `session_rpe` holds only what the athlete actually logged.

- **Pure seam `load.blend`, consumed by `features`.** A new `load.py` exposes
  `blend(discipline, garmin_load, srpe_load) -> float` plus an sRPE helper over RPE and
  `dur_s` (total session duration, the Foster standard). `features._load_by_day` reads
  `session_rpe` once and routes each activity's blended load to the TE buckets (cardio)
  or the new `load_strength` bucket (`Siła`). The blend is pure and total (`NULL`
  garmin_load treated as 0), golden-testable, and reproducible from core - a backfill
  reproduces a past day's blended load.

- **New `load_strength` bucket; strength stays out of the aerobic shares.** Strength's
  TE is near zero, so without a dedicated bucket its blended 150 would fall into
  `load_low` and mask a real shortage of easy aerobic running. We add
  `load_strength` to `daily_metrics` with `load_day = load_low + load_high +
  load_anaerobic + load_strength`, so ACWR / monotony / strain / hard-day logic see
  strength with no code change, while `signals.load_shares` keeps totalling only the
  three cardio buckets - `AEROBIC_LOW_SHORTAGE` stays honest. This also cleans the
  existing pollution of `load_low` by raw strength `training_load`. `weekly_metrics`
  gains `load_strength` and `strength_share` so its four shares still sum to 1.0 and
  `load_total`/monotony/strain include strength.

- **No `rpe_hard` threshold.** With one currency, "hard day" stays the single
  criterion `load_day >= hard_te_load`; a hard `Siła` session crosses it through the
  blend. A second, session-level `rpe_hard` notion would compete with the load
  criterion (e.g. a short RPE 9 session that is rpe-hard but not load-hard) and
  complicate `TWO_HARD_DAYS` / `DELOAD_ADVISED` for no gain.

- **Niggle: one log active for a window, cleared by a lower re-log.** `niggle`
  (composite PK `(date, body_part)`) records a body-part severity. A log stays active
  for `niggle_active_days` (7) from its date - Runna's dial-back model - so the athlete
  does not re-log daily; the digest takes the latest entry per body part inside the
  trailing window and re-logging at a lower severity clears it early. No `resolve`
  command (avoids "eternal" niggles the athlete forgets to close and keeps the CLI
  small).

- **Reduced-mode is a digest signal, read live from core.** `NIGGLE_REDUCED_MODE`
  (severity `warn`) is computed in the digest, which already reads `weekly` and `zones`
  directly, so the niggle need not enter `daily_metrics`. Facts stay flat scalars
  (worst `body_part`, its `severity`, `n_active`, `days_active`) per the `signals.py`
  contract; the full active list is deferred to the Phase 10 avoid-list.

- **One thin, transport-free writer `log-rpe`.** A single command with two mutually
  exclusive modes (`--activity ... --rpe` / `--niggle ... --severity`) writes ground
  truth to core. The RPE mode recomputes `features` for the session date so the new
  load is immediately visible (a local mart recompute from core - the golden rule still
  holds, `features` never calls Garmin); the niggle mode only writes, since
  reduced-mode is a digest-layer read surfaced on the next `report`. This is the first
  CLI writer to a core table not sourced from Garmin, and it stays a system-of-record
  write of observed ground truth (like an activity).

## Thresholds (new keys in `coach_thresholds`)

| Key | Default | Meaning |
|---|---|---|
| `srpe_load_scale` | `0.3` | scales Foster sRPE (`rpe x min`) into Garmin-load units |
| `sila_default_rpe` | `7` | default Borg CR10 RPE for a `Siła` session with no logged RPE |
| `niggle_active_days` | `7` | days a single niggle log stays active for reduced-mode |
| `niggle_reduced_mode_severity` | `3` | active-niggle severity (1-5) that arms reduced-mode |

## Testing

- `test_load.py` (new seam): `Siła` default RPE -> ~150; a logged RPE raises a run only
  when `sRPE > garmin`; a low-RPE run keeps its Garmin load; `NULL` garmin degrades to
  sRPE / 0; scale and default read from thresholds.
- `test_features.py`: a `Siła` RPE raises `load_day` + `load_strength` and shifts ACWR;
  `load_day` == sum of the four buckets; aerobic shares unchanged by a strength day;
  idempotent; as-of reproduces a past day's blended load.
- `test_weekly.py`: `load_strength` + `strength_share` populate; four shares sum to
  1.0; `load_total` / monotony / strain include strength.
- `test_digest.py`: `NIGGLE_REDUCED_MODE` fires for an active niggle at/above the
  threshold with flat facts; silent below threshold or outside the window; cleared by a
  lower-severity re-log.
- `test_cli.py`: `log-rpe --activity` rejects a missing activity, upserts, and
  recomputes; `log-rpe --niggle` writes and does not recompute; mutually exclusive
  modes; range validation fails loudly.
- `test_thresholds.py`: the four new keys present with defaults.
- `test_schema_sync.py`: `docs/schema.sql` identical to the package copy.
