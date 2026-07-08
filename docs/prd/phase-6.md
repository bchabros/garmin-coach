# PRD - Garmin Coach - Phase 6: personal training zones (`athlete_zones` mart)

> Status: Ready for implementation (TDD) - Date: 2026-07-08
> Sources: `docs/ROADMAP.md` Phase 6, `docs/adr/0006-post-phase-5-architecture-deepening.md`, `CONTEXT.md`/`docs/glossary.md`, grilling decisions 2026-07-08.

## Problem Statement

The coach reasons about intensity against numbers that are not the athlete's.
Saying "6:00/km is Zone 2" today means running an ad-hoc query over `activities`
by hand, and the only encoded HR boundary is `coach_thresholds.hr_z2_upper_bpm =
140` - a hardcoded placeholder literally annotated *"approx Z2 ceiling; refine
from user_settings zones"*. There is no derived, athlete-specific set of HR or
pace zones anywhere in the system, so every intensity call (is this run truly
easy? was the week too grey?) is either eyeballed or leans on that stale 140.
Meanwhile the athlete's own watch already auto-detects a Lactate Threshold
(LTHR 175 bpm, threshold pace ~4:17/km) that the ETL never ingests - the anchor
we need is sitting one endpoint away and unused, and `fitness_markers` is empty.

## Solution

A recomputed `athlete_zones` mart that derives the athlete's HR and pace zones
from their own data, anchored on the watch-detected Lactate Threshold, and keeps
them fresh - so intensity advice is deterministic instead of hand-computed.

- **Ingest the anchor (core).** A new transport path pulls `get_lactate_threshold`
  and upserts LTHR heart rate + threshold pace into the existing (currently empty)
  `fitness_markers` core table, keyed by date. This is the system-of-record for the
  threshold; the mart is derived from it.
- **Ingest per-activity temperature (core).** During activity storage, each
  activity is enriched with `get_activity_weather`, writing a converted
  `temp_c` onto the `activities` row (raw payload appended as always). This exists
  to let the pace regression exclude heat-inflated runs - Garmin's weather `temp`
  field is **Fahrenheit** and must be converted to Celsius on the way in.
- **Compute the zones (mart, deterministic seam).** A new pure function
  `zones.compute(...)` reads the latest LTHR plus aerobic runs and materialises a
  single current `athlete_zones` row: five %LTHR heart-rate bands, `lthr_bpm`,
  `threshold_pace_s_per_km`, a `z2_pace_ceiling_s_per_km`, plus `computed_at`,
  `source`, and `stale`. It runs as the tail of the existing `features` command,
  exactly like `weekly.rollup` - the athlete gains nothing new to remember, and it
  never touches Garmin (mart-from-core).
- **HR bands from %LTHR.** Zone edges are fixed fractions of LTHR (Garmin/Friel
  %LTHR scheme), stored as multipliers in `coach_thresholds` so they are tunable
  without code: Z1 <80%, Z2 80-89%, Z3 89-94%, Z4 94-99%, Z5 >=99%. At LTHR 175
  the Z2 HR ceiling is ~156 bpm. The per-activity `hr_z*_s` buckets on `activities`
  (the watch's own zone semantics) are left untouched.
- **Z2 pace ceiling, hybrid with a guard.** A pace<->HR regression over aerobic
  runs predicts pace at the Z2 HR ceiling, but only activates once there are
  enough qualifying runs and the fit is sound; below that it falls back to a fixed
  fraction of threshold pace. Heat-inflated runs (`temp_c` above a threshold) are
  excluded from the fit. `source` records which method produced the ceiling. The
  function is **total**: the mart is fully populated from day one, and the
  regression takes over on its own as history accrues.
- **Staleness.** `stale = 1` when the anchoring LTHR detection is older than
  `zones_stale_days` (default 28, TrainerRoad AI-FTP cadence). Surfaced as an
  `info`-level fact inside the digest `zones` block plus the detection age - a
  metadata note ("zones computed from a detection N days ago; consider a harder
  run"), not a training alarm in the signal list.
- **Report integration.** `build_digest` gains a `zones` block (HR bands, Z2 pace
  ceiling + `source`, `lthr_bpm`, `stale` + age). `AEROBIC_LOW_SHORTAGE` keeps its
  existing load-share logic and gains **one new fact alongside it**: the share of
  running minutes at avg HR at or below the personal Z2 ceiling over the window, so
  the coach sees both the load-bucket read and the personal-zone read (and their
  divergence). The hardcoded `hr_z2_upper_bpm` threshold is retired.

The seam is the point: the LTHR->bands math, the pace<->HR regression, the heat
guard, the fallback selection, and the staleness rule are all pure functions over
core rows, testable with a seeded SQLite and a golden fixture - no Garmin, no OS.

## User Stories

1. As the athlete, I want my HR zones derived from my own Lactate Threshold, so
   that "Zone 2" means my physiology, not a generic default.
2. As the athlete, I want a concrete Z2 heart-rate ceiling in bpm, so that I know
   the number to keep easy runs under without recomputing it.
3. As the athlete, I want a Z2 pace ceiling in min/km, so that I can hold an easy
   run to a pace target on the road, not just an HR cap.
4. As the athlete, I want the Z2 pace ceiling to come from my own pace<->HR
   relationship once I have enough runs, so that it reflects my current running
   economy, not a textbook ratio.
5. As the athlete, I want a sensible pace ceiling *before* I have enough runs, so
   that the number exists and is usable from the very first report.
6. As the athlete, I want to know which method produced my pace ceiling
   (regression vs threshold-pace fallback), so that I can weigh how personal it is.
7. As the athlete, I want hot-weather runs excluded from my pace<->HR fit, so that
   summer heat drift does not silently make my "easy pace" look slower than my
   fitness warrants.
8. As the athlete, I want my watch's auto-detected Lactate Threshold ingested and
   stored, so that the system has a real anchor and a history of it.
9. As the athlete, I want the anchor to update itself when my watch re-detects a
   new threshold, so that my zones track my fitness without manual entry.
10. As the athlete, I want to be told when my zones are stale (the underlying
    detection is old), so that I know to run a harder session to refresh them.
11. As the athlete, I want the staleness note to be a quiet informational flag,
    not a nightly alert, so that a calm base block does not nag me every week.
12. As the athlete, I want the Z2 pace ceiling in the report headline, so that the
    coach can say "keep easy runs under X:XX" directly.
13. As the athlete, I want the grey-zone signal to also tell me how many of my
    running minutes were actually under my personal Z2 ceiling, so that "too much
    grey zone" is grounded in my zones, not only in Garmin's load buckets.
14. As the athlete, I want the existing grey-zone signal to keep working exactly as
    before, so that adding personal zones does not regress a signal I already trust.
15. As the athlete, I want the placeholder `hr_z2_upper_bpm = 140` retired, so that
    nothing downstream leans on a number that was always a guess.
16. As the athlete, I want zone recompute folded into the command I already run
    (`features`), so that there is no new step to remember.
17. As the athlete, I want the zone computation to never crash on missing data
    (no LTHR yet, no runs, all-hot runs), so that the pipeline stays deterministic
    during onboarding and thin-history periods.
18. As the athlete, I want the zone multipliers and staleness cadence to live in
    `coach_thresholds`, so that I can retune them without touching code.
19. As the athlete, I want per-activity temperature stored on my runs, so that heat
    context is available to the engine (and future phases) rather than discarded.
20. As the developer, I want zone computation to read core only and never call
    Garmin, so that the golden rule (metrics/coach layers never hit Garmin live)
    holds.

## Implementation Decisions

### Schema (package copy `src/garmin_coach/schema.sql`, then re-sync `docs/schema.sql`)

- **New mart `athlete_zones`.** A recomputed table holding the single current
  standing (latest row; recompute truncates/replaces, mart-not-record). Columns:
  the five HR-zone lower/upper bounds in bpm, `lthr_bpm`, `threshold_pace_s_per_km`,
  `z2_pace_ceiling_s_per_km`, `computed_at`, `source` (an enum-ish text tag naming
  the pace-ceiling method, e.g. `regression` | `threshold_pace_fallback`, and the
  HR-band provenance), and `stale` (0/1). Derived values live only here, never mixed
  into core.
- **`activities` gains `temp_c REAL`.** Per-activity temperature in Celsius,
  converted from the Fahrenheit weather payload. Nullable (older activities,
  indoor sessions, weather-fetch failures).
- **`fitness_markers` starts getting filled.** No shape change; Phase 6 populates
  `lactate_thr_hr` and `lactate_thr_pace` (one row per date the value changes,
  matching the existing "row when any value changes" contract). `lactate_thr_pace`
  units are documented as seconds-per-km to remove the schema's "mps or s/km" TODO.
- **`coach_thresholds` seed rows added:** the five %LTHR band multipliers
  (`z1_hi_pct_lthr`=0.80, `z2_hi_pct_lthr`=0.89, `z3_hi_pct_lthr`=0.94,
  `z4_hi_pct_lthr`=0.99), `z2_pace_fallback_mult` (threshold-pace multiplier for
  the fallback ceiling, ~1.30), `zones_regression_min_runs` (~12),
  `zones_regression_min_r2` (fit-quality floor), `zones_heat_temp_c` (22, the
  heat-exclusion ceiling), and `zones_stale_days` (28). **`hr_z2_upper_bpm` is
  removed** from the seed and its readers.

### Transport (out-of-seam, validated by live run - like `client.py`)

- **`GarminClient` protocol gains two methods**, implemented on `GarminTransport`:
  one for the latest/ranged Lactate Threshold (`get_lactate_threshold`) and one for
  per-activity weather (`get_activity_weather(activity_id)`).
- **LTHR ingestion.** Fetched once per sync/backfill run (not a per-day
  `SyncStream`, because LTHR is an occasional-change biometric, not a daily
  series); raw payload appended to `raw_payloads`; normalized rows upserted into
  `fitness_markers`. Backfill uses the ranged form; nightly uses latest.
- **Weather enrichment.** `_store_activities` fans out one `get_activity_weather`
  call per stored activity, appends the raw payload, and writes `temp_c` onto the
  upserted activity. Failures are isolated (a missing weather payload leaves
  `temp_c` NULL and never aborts the activities stream), consistent with Phase 1
  stream isolation.

### Normalizers (pure, total, scalars only - `models.py`)

- **`normalize_lactate`** (latest): the garminconnect `latest=True` transport already
  merges Garmin's raw two-entry list (and its `hearRate` typo) into one
  `speed_and_heart_rate` dict, so the normalizer reads that merged dict. Onboarding is
  nulls. **`normalize_lactate_range`** (backfill): the ranged form returns parallel
  `speed`/`heart_rate` series, joined by detection day into one row per date. Both emit
  only the LTHR-owned columns (HR + pace) so an upsert never clobbers sibling markers;
  threshold speed -> `lactate_thr_pace` in s/km; missing fields -> None, never raise.
- **`normalize_activity` gains `temp_c`** via a small weather-merge step
  (Fahrenheit -> Celsius), keeping the normalizer pure and total.

### Compute seam (mart - `zones.py`, new)

- **`zones.compute(...) -> row dict`** (exact argument list is an implementation
  detail; it takes the latest LTHR, the aerobic runs with temp, and the
  thresholds). Pure, total, deterministic. Responsibilities:
  1. HR bands = %LTHR multipliers x `lthr_bpm`.
  2. Z2 pace ceiling: run a pace<->HR OLS regression over aerobic runs with
     `temp_c <= zones_heat_temp_c` (or missing temp treated per a documented rule);
     use it only when qualifying-run count `>= zones_regression_min_runs` and
     fit quality `>= zones_regression_min_r2`; else fall back to
     `threshold_pace * z2_pace_fallback_mult`. Record `source`.
  3. `stale` = LTHR detection age (days) `> zones_stale_days`.
  4. No LTHR at all -> a documented degraded row (HR bands None / mart signals no
     anchor), never an exception.
- Invoked from `features.features(...)` after the daily-mart write and the
  `weekly.rollup` call, writing the single `athlete_zones` row. No new command.

### Digest + signal (`digest.py`, `signals.py`)

- **`build_digest` gains a `zones` block**: HR bands, `z2_pace_ceiling_s_per_km`
  + `source`, `lthr_bpm`, `stale` + detection age. Sits in the headline surface so
  the report can quote the Z2 pace ceiling.
- **`AEROBIC_LOW_SHORTAGE` gains one fact** - `personal_z2_minute_share` (share of
  running minutes over the window at avg HR <= the personal Z2 HR ceiling) -
  alongside the untouched load-share logic and `garmin_agrees`. No threshold or
  severity change to the signal.

### Skill (`skills/coach/SKILL.md`)

- Procedure gains: read the digest `zones` block; quote the Z2 pace ceiling in the
  headline ("keep easy runs under X:XX"); if `stale`, note the detection age and
  suggest a harder run; when reading `AEROBIC_LOW_SHORTAGE`, cite both the
  load-share and the personal-Z2-minute-share, and call out any divergence.

## Testing Decisions

Good tests here assert **external behaviour at the seams**, not internals: given
seeded core rows, the mart row and digest block that come out. Follow the Phase 5
pattern (`tests/test_weekly.py`, `tests/test_features.py`): seed a SQLite conn,
run the pure function, assert on the row/dict; keep one golden regression.

- **`tests/test_zones.py` (new) - the primary seam.** Golden + case tests over
  `zones.compute`:
  - No LTHR (onboarding) -> degraded row, no crash.
  - LTHR present, too few qualifying runs -> `source = threshold_pace_fallback`,
    ceiling = threshold pace x multiplier.
  - LTHR present, enough good runs -> `source = regression`, ceiling from the fit.
  - Hot runs (`temp_c` above the heat ceiling) are excluded from the fit, and the
    fallback re-engages if that drops the count below the minimum.
  - HR bands equal the %LTHR multipliers x LTHR.
  - Staleness: old detection -> `stale = 1`; recent -> `stale = 0`.
  - Idempotent recompute (rerun -> identical single row).
- **`tests/test_models.py` - `normalize_lactate`** on both fixture shapes
  (onboarding null, post-onboarding split HR/speed list); speed->s/km conversion;
  missing fields -> None. **`normalize_activity`** temp_c: Fahrenheit->Celsius
  conversion, and NULL when weather is absent.
- **`tests/test_features.py`** - `features` now writes exactly one `athlete_zones`
  row; recompute stays idempotent (existing idempotency test extended).
- **`tests/test_digest.py`** - the golden digest gains the `zones` block;
  `AEROBIC_LOW_SHORTAGE` golden gains `personal_z2_minute_share` while its existing
  facts are unchanged.
- **`tests/test_schema_sync.py`** - keeps `docs/schema.sql` identical to the
  package copy after the `athlete_zones` / `temp_c` / threshold edits.
- **`tests/test_thresholds.py`** - the new `coach_thresholds` keys are present and
  `hr_z2_upper_bpm` is gone.
- **Out of seam (no unit tests):** the two new transport methods and the weather
  fan-out in `sync.py` are validated by a live backfill, like the rest of
  `client.py` (fixtures for the normalizers are captured from that run, anonymized
  per the PII rules - strip lat/lon and station ids from the weather payload).

## Out of Scope

- **`race_predictions` ingestion** and any other new stream - deferred to Phase 6b
  (snapshot), which is where race predictions are actually consumed. Phase 6 adds
  exactly one core stream (LTHR) plus per-activity temperature.
- **Switching `AEROBIC_LOW_SHORTAGE` to a fully personal-zone definition** - Phase
  6 adds the personal-Z2 fact *alongside* the existing load-share logic; it does
  not replace it (that would forfeit the proven signal and the `garmin_agrees`
  comparison, and we do not store per-minute HR series).
- **Reconstructing the watch's own device HR-zone edges** - not exposed by any
  ingested endpoint; the mart is %LTHR-derived by design, and the activity
  `hr_z*_s` buckets keep the watch's semantics untouched.
- **Regressing temperature out of the HR fit** (vs simply excluding hot runs) -
  the simple exclusion guard is the Phase 6 choice; a temperature covariate is a
  later refinement only if exclusion proves insufficient.
- **VO2max / threshold trend charts, altitude acclimation ingestion, sleep debt** -
  survey extras noted in ROADMAP; not Phase 6.
- **Event-driven "breakthrough" re-detection** beyond the 28-day staleness flag -
  the staleness cadence covers freshness; explicit PR-triggered recompute is a
  later nicety.
- **Any outbound/Garmin write.** Read-only, golden-rule intact.

## Further Notes

- **Gotcha - Fahrenheit weather.** `get_activity_weather` returns `temp` in
  Fahrenheit even on a metric account (observed: 67 -> 19.4 C on a July Warsaw
  run). Convert on ingest; the `zones_heat_temp_c = 22` guard is Celsius.
- **Gotcha - LTHR payload shape.** Garmin's raw latest endpoint returns a list of
  ~two near-identical dicts (HR in one, speed in the other, with a misspelled
  `hearRate` key), but the garminconnect `latest=True` transport already merges them
  (and tolerates the typo) into one `speed_and_heart_rate` dict - so the latest
  normalizer reads that merged dict, not the raw list. Onboarding returns nulls. The
  ranged (`latest=False`) form is a different shape: parallel `speed`/`heart_rate`
  series of `{from, value}` entries, joined by day. Test both (shape-drift rule).
- **Precedence / provenance.** The chosen anchor is the watch-detected LTHR
  (`thresholdHeartRateAutoDetected = true` in `user_settings`); `source` records
  provenance so a future device-vs-derived disagreement can be flagged. `user_
  settings` carries no HR-zone edges, so there is no device-zone path to prefer.
- **Deliberate 156-over-140 shift.** The %LTHR Z2 ceiling (~156 bpm) is bolder
  than the retired 140 placeholder but is the physiologically-grounded,
  self-scaling choice; multipliers are in `coach_thresholds` for retuning.
- **Housekeeping deviation from the skill template.** This project tracks PRDs as
  `docs/prd/phase-N.md` (CLAUDE.md house rule), not an external issue tracker, so
  there is no `ready-for-agent` label step. An ADR
  (`docs/adr/0007-phase-6-personal-zones.md`) will record the accepted decisions
  once implementation lands, matching Phases 1-5.
