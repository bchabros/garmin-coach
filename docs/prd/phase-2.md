# PRD - Garmin Coach - Phase 2: metrics layer (mart)

> Status: Ready for implementation (TDD) - Date: 2026-07-05
> Sources: `docs/garmin-coach-BUILD.md` Phase 2 + sections 4/6, `docs/adr/0002-phase-2-metrics-semantics.md`, `docs/glossary.md`, grilling decisions.

## Problem Statement

Phase 0/1 land normalized Garmin data in core SQLite tables, but the athlete still
has no computed view of training state. Raw HRV nights, per-activity loads, and
heart-rate seconds are not yet turned into the metrics a coach reasons about -
whether HRV dipped below its normal band, whether acute load is running hot relative
to chronic load, and how the week's stimulus splits across easy/hard/anaerobic work.
The data exists; the interpretation layer does not.

## Solution

Add `features.py` with a `features(conn)` entrypoint and a `garmin-coach features`
command. It reads the core tables and materializes the `daily_metrics` mart, one row
per calendar day from `data_start` through the latest core date: HRV baseline + SD +
low flag, own ACWR (`acute7`/`chronic28`) with an `n_chronic` credibility counter,
TE-based load buckets, HR-zone minutes, plus carried-through `sleep_score` and `rhr`.
The mart is fully recomputed and upserted by date on every run, so it is always
reproducible from core and never a system of record. Correctness is pinned by a
golden regression that seeds real anonymized core data and reproduces the reference
hand-analysis.

## User Stories

1. As the athlete, I want a `daily_metrics` row for every calendar day, so that
   trailing-window metrics have no gaps to reason around.
2. As the athlete, I want my HRV baseline computed from my own history, so that a
   "low" night is judged against what is normal for me.
3. As the athlete, I want a night flagged when HRV drops more than one SD below
   baseline, so that I can spot suppressed recovery.
4. As the athlete, I want the HRV baseline to use my whole available history (capped
   to the last 60 nights), so that early sparse data still yields a usable band.
5. As the athlete, I want a daily training load that sums my sessions, so that rest
   days read as zero and busy days read high.
6. As the athlete, I want an ACWR comparing my last 7 days to my last 28, so that I
   can see whether I am ramping too fast.
7. As the athlete, I want ACWR to carry an `n_chronic` count, so that I know when the
   ratio is still unreliable because my history is short.
8. As the athlete, I want ACWR to read as overstated while my chronic window is
   incomplete, so that the number never flatters me early on.
9. As the athlete, I want my training load split into easy / hard / anaerobic buckets
   by Training Effect, so that I can see the shape of my stimulus.
10. As the athlete, I want load buckets to always add up to my total load, so that no
    session's load silently disappears.
11. As the athlete, I want minutes in each HR zone per day, so that I can see time
    distribution separately from load distribution.
12. As the athlete, I want resting HR carried into the mart with a sleep fallback, so
    that a wellness-RHR gap does not leave the day blank when the value is recoverable.
13. As the athlete, I want sleep score carried into the mart, so that recovery signals
    sit alongside HRV in one place.
14. As the athlete, I want rest days and pre-onboarding days handled explicitly, so
    that gaps are never confused with zero training in the wrong direction.
15. As the operator, I want `garmin-coach features` to recompute the whole mart by
    default, so that a single command refreshes everything after a sync.
16. As the operator, I want `features --from/--to` to narrow the recomputed range, so
    that I can rebuild a slice without recomputing all of history.
17. As the operator, I want narrowing the range to still produce correct trailing
    windows, so that ACWR/baseline for the slice are not corrupted by a truncated look-back.
18. As the operator, I want re-running `features` to be idempotent, so that repeated
    runs converge to identical `daily_metrics`.
19. As a developer, I want `features` tested at the DB boundary, so that tests survive
    internal refactors of the metric math.
20. As a developer, I want a golden regression over real anonymized data, so that any
    change to the metric definitions is caught as a diff against known-good numbers.
21. As a future coach-layer builder, I want a gap-free, deterministic `daily_metrics`,
    so that report and alert rules can read a trustworthy derived source.

## Implementation Decisions

- New module `features.py` exposing `features(conn, from_date=None, to_date=None)`.
  It reads core, computes per-day metrics, and upserts `daily_metrics` by `date`
  (`INSERT ... ON CONFLICT(date) DO UPDATE`). Default range is `data_start` through
  the latest core date. Trailing windows always look back into core beyond `from_date`.
- Wire `garmin-coach features [--from --to]` in `cli.py`, mirroring how `sync`/`backfill`
  are wired.
- **Row coverage:** emit one `daily_metrics` row per calendar day in range, gap-filled.
  Rest days: `load_day`/buckets/zones = 0. Days missing HRV/sleep: those columns NULL.
- **HRV:** `hrv_baseline` = median of every non-null `avg_hrv` night in the window;
  `hrv_sd` = sample std (ddof=1) over the same set; both stamped identically on every
  row. `hrv_low_flag = 1` when `avg_hrv < hrv_baseline - 1 * hrv_sd` (strict `<`).
  Default window = whole available history, capped to trailing 60 nights (config knob).
- **ACWR:** `load_day` = sum of activities' `training_load` for `date(start_local)` (0
  if none). `acute7` = trailing-7-day (incl. today) sum / 7; `chronic28` = trailing-28
  / 28. `acwr = acute7 / chronic28`. Rest and pre-`data_start` days zero-fill. `n_chronic`
  = count of in-window days with a real data row (date >= `data_start`).
- **Load buckets:** per activity, `load_anaerobic` when `anaero_te >= 1.0`; else
  `load_low` when `aero_te < 2.5`; else `load_high`. NULL `anaero_te`/`aero_te` -> 0
  (no-TE activity falls into `load_low`); NULL `training_load` -> 0 contribution. Bucket
  totals always sum to the day's total `training_load`.
- **HR zones:** `z1..z5_min` = sum of `hr_z1..z5_s` for the day / 60.
- **Carried columns:** `hrv` from `hrv_nightly.avg_hrv`; `sleep_score` from `sleep.score`;
  `rhr` from `daily_wellness.rhr` with fallback to `sleep.resting_hr`.
- Schema: `daily_metrics` already exists in `schema.sql`; no schema change expected. If
  a column is missing, edit the package copy `src/garmin_coach/schema.sql` and re-sync
  `docs/schema.sql` (guarded by `tests/test_schema_sync.py`). No `features_version` yet.

## Testing Decisions

- Good tests here assert only external behavior: seed core rows into a temp SQLite,
  run `features(conn)`, read `daily_metrics` back, assert on the rows. No assertions on
  internal helper functions or intermediate structures. This is the same DB-boundary
  seam used by `test_sync.py` and `test_db.py`; reuse `conftest.py` DB fixtures.
- **Golden regression (`test_features.py`).** Seed a frozen SQL dump of the core tables
  (`activities`, `hrv_nightly`, `daily_wellness`, `sleep`) for the reference range from
  `tests/fixtures/features_golden.sql` (anonymized, regenerable from `data/garmin.db`).
  Run `features(conn)` and assert the deterministic output:
  - `hrv_baseline == 68`, `hrv_sd` within 11 +/- 0.1, threshold (`baseline - sd`) approx. 57.0.
  - `hrv_low_flag` set exactly on 2026-06-11, 06-17, 06-18, 06-19, 06-27 (see note below).
  - `acwr` on 2026-07-03 within 1.0 +/- 0.15 (computed 1.065), `n_chronic == 26`.
  - Bucket totals for a spot day sum to that day's total `training_load`.
  - Zone minutes for a spot activity equal `hr_zX_s / 60`.
- **Vertical slices (unit-level behavior via the same seam), each red -> green:**
  gap-fill emits a row for a day with no core data; ACWR zero-fill and `n_chronic`;
  bucketing null-TE into `load_low` and null-load contributing 0; RHR fallback to sleep;
  idempotency (run twice, identical rows); `--from/--to` narrowing still yields correct
  trailing-window values.
- Prior art: `test_sync.py` (seeded DB + injected behavior, asserts on core state),
  `test_db.py` (upsert idempotency), `test_models.py` (pure-value expectations).

## Out of Scope

- The coach report text and plots (HRV band chart, ACWR-over-time) - Phase 3.
- Comparing load buckets to `get_training_status` `monthlyLoad*Target*` and emitting
  `AEROBIC_LOW_SHORTAGE` - Phase 3 coach rules.
- `weekly_metrics` mart - later phase.
- `features_version` column - deferred per BUILD doc.
- Any live Garmin call from the metrics layer - forbidden by the golden rule.

## Further Notes

- **Reference vs. computed flag set.** The BUILD doc's informal reference lists HRV low
  flags on 11/18/19/27.06 + 04.07. Computing the pinned algorithm on the frozen fixture
  instead yields 11/17/18/19/27.06, for two explainable reasons: (a) 2026-06-17 has
  `avg_hrv == 57`, and the real SD (10.998) puts the threshold at 57.002, so strict `<`
  flags it - the doc rounded the threshold to 57 and treated the boundary as not-flagged;
  (b) 2026-07-04 is outside this DB's 07-03 cutoff. The golden test therefore asserts the
  algorithm's actual deterministic output, using the doc's numbers only as sanity anchors
  (baseline 68, SD approx. 11, ACWR approx. 1.0-1.1). This divergence is expected and
  documented, not a bug.
- The mart is intentionally non-durable: historical `hrv_baseline`/flags shift as new
  nights arrive because the baseline is whole-window. This is correct for a recomputed
  mart and is why `daily_metrics` is never a system of record.
