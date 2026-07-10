# PRD - Garmin Coach - Phase 5: weekly rollups, plan-vs-actual, deload detection

> Status: Ready for implementation (TDD) - Date: 2026-07-06
> Sources: `docs/PROJECT.md` Phase 5 + section 7.6, `docs/adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md`, `docs/adr/0003-phase-3-coach-signals.md`, `CONTEXT.md` glossary, grilling decisions.

## Problem Statement

The coach reasons a day at a time. It can tell the athlete that this morning's
HRV is suppressed or that yesterday stacked onto the day before, but it cannot
see the *shape of the week*: whether training is drifting into monotony, whether
load has climbed for three straight weeks without a back-off, or whether what
actually happened matches the athlete's intended weekly template (Monday rest,
Tuesday quality, Wednesday easy, ...). The schema already ships the tables for
this - `plan_template` seeded with the athlete's real week and an empty
`weekly_metrics` - but nothing fills them, so the weekly picture and the
"plan vs realizacja" divergence the athlete wants simply do not exist yet.

## Solution

A weekly rollup layer that closes the loop, built entirely from the daily mart.

- **Weekly rollup engine (deterministic, testable).** A new pure seam
  `rollup(conn, *, data_start_date, through_date=None)` in `weekly.py` reads
  `daily_metrics` (plus `activities`/`plan_template` for adherence) and
  materialises one `weekly_metrics` row per **complete week** (Monday–Sunday,
  fully at/before yesterday). It never touches Garmin - a mart-from-mart step.
  It runs as the second half of the existing `features` command, so the athlete
  gains nothing new to remember.
- **Plan adherence.** Each day of a complete week is classified to the
  `plan_template` intent vocabulary *by load* (actual intent), compared to the
  planned intent, and reduced to `plan_adherence` = exact matches / 7. The report
  additionally shows the *direction* of every mismatch, because the DoD asks to
  surface divergence, not just a ratio.
- **Foster monotony/strain.** Cheap overtraining flags computed from the week's
  daily loads: `monotony` = mean daily load / SD daily load (NULL when
  uncomputable), `strain` = weekly load x monotony.
- **Deload detection.** Descriptive facts (`load_total`, `acwr_end`, `monotony`,
  `strain`, a large week-over-week load drop) live in `weekly_metrics`; a new
  prospective signal `DELOAD_ADVISED` fires when history is sufficient and load
  has risen for several consecutive weeks into a hot ACWR or high monotony.
- **Report integration.** `build_digest` gains a `weekly` section (last complete
  week's facts + the per-day plan-vs-actual table) and `DELOAD_ADVISED` joins the
  existing signal list, so the nightly `daily` alert path picks it up for free
  when a week has just closed. No new charts.

The seam is the point: rollup math, intent classification, adherence, monotony,
and the deload rule are all pure functions over the daily mart, testable with a
seeded SQLite and a golden fixture - no Garmin, no OS.

## User Stories

1. As the athlete, I want a rolled-up view of each completed training week, so
   that I can see the shape of my week, not just isolated days.
2. As the athlete, I want the total training load of each week, so that I can see
   whether I am building, holding, or backing off.
3. As the athlete, I want the low/high/anaerobic load split (and shares) per week,
   so that I can see whether my week was mostly easy or mostly hard.
4. As the athlete, I want weekly HR-zone minutes (Z2, threshold, Z5), so that I
   can see time-in-zone distribution alongside the load buckets.
5. As the athlete, I want a plan-vs-actual comparison for the week, so that I can
   see where my real training diverged from my intended template.
6. As the athlete, I want each day's mismatch shown with direction (planned rest
   but trained quality, planned quality but rested), so that I understand *how* I
   drifted, not just that I did.
7. As the athlete, I want a single adherence number for the week, so that I can
   track at a glance how closely I followed my plan over time.
8. As the athlete, I want a training day I did without wearing my watch to count
   as rest (and to understand why), so that I am not surprised the system cannot
   see off-watch sessions.
9. As the athlete, I want a Foster monotony figure per week, so that a
   dangerously samey week is flagged before it costs me.
10. As the athlete, I want a Foster strain figure per week, so that high-load,
    high-monotony weeks stand out as overtraining risk.
11. As the athlete, I want a "deload advised" signal when load has climbed for
    several weeks into a hot ACWR or high monotony, so that I am nudged to back
    off before I dig a hole.
12. As the athlete, I want the deload signal to stay silent when there is not
    enough history to judge, so that I am never nudged on a guess.
13. As the athlete, I want the report to note when a completed week was itself a
    deload (a big load drop), so that intentional back-off weeks are recognised,
    not mistaken for lost fitness.
14. As the athlete, I want the longest run of consecutive hard days in the week
    surfaced, so that my Friday-into-Saturday stacking pattern is visible weekly.
15. As the athlete, I want weekly mean HRV, RHR, and sleep score, so that I can
    correlate recovery with the week's load without doing the math myself.
16. As the athlete, I want recovery averages to skip days with no measurement, so
    that a night I did not wear the watch does not drag my weekly averages down.
17. As the athlete, I want the weekly rollup to be produced by the same
    `features` command I already run, so that I have no extra step to remember.
18. As the athlete, I want only complete Monday–Sunday weeks rolled up, so that a
    half-finished current week never shows misleading adherence or monotony.
19. As the athlete, I want the weekly view to appear in the same report/digest I
    already read, so that I do not have to open a second artifact.
20. As a developer, I want the weekly rollup tested at one pure seam over the
    daily mart, so that the rollup math is covered without Garmin or the OS.
21. As a developer, I want a golden regression over the four complete weeks
    2026-06-08…06-29, so that a change to the rollup math is caught immediately.
22. As a developer, I want `DELOAD_ADVISED` implemented as a pure signal like the
    Phase 3 signals, so that its fire/silent behavior is unit-tested in isolation.
23. As a developer, I want the new deload thresholds in `coach_thresholds`, so
    that the rule stays data-driven and tunable without code changes.
24. As a developer, I want `weekly_metrics` and `plan_template` to already exist
    in the schema, so that Phase 5 adds only threshold rows and `test_schema_sync`
    stays green.
25. As a developer, I want the rollup to reuse the existing `daily_metrics` mart
    and `_upsert` machinery, so that Phase 5 adds a rollup, not a second pipeline.

## Implementation Decisions

- **Primary seam - `rollup(conn, *, data_start_date, through_date=None) -> None`**
  in a new `weekly.py`. It computes every complete week (Monday–Sunday whose
  Sunday is at/before `through_date`, default yesterday relative to the latest
  daily-mart date) from `daily_metrics`, and upserts one `weekly_metrics` row per
  week keyed by `week_start` (the Monday, ISO date). Mirrors the shape of
  `features.features(conn, ...)`.
- **Runs inside `features`.** `features.features` calls `weekly.rollup` after it
  finishes writing `daily_metrics` and commits. `cli._cmd_features` is unchanged
  in interface; one command still recomputes both marts. Rationale in ADR-0005:
  `weekly_metrics` is a pure derivative of `daily_metrics`, so one "recompute the
  marts" command keeps the medallion coherent.
- **Persistence - `db.upsert_weekly(conn, row)`**, a thin helper mirroring
  `upsert_activity`, delegating to the existing private `_upsert(conn,
  "weekly_metrics", row, pk="week_start")`. No change to `_upsert`.
- **Complete-week selection.** A week qualifies only if all seven of its days are
  at/before the effective cutoff (yesterday). The in-progress current week is
  skipped. Weeks before `data_start_date` are not emitted.
- **Actual-intent classification (by load).** For each day in a week, derive the
  actual intent from `daily_metrics`/`activities`:
  - `quality` when the day's `load_day` reaches `hard_te_load` **or** the day has
    anaerobic load (`load_anaerobic > 0`);
  - `easy` when there is any lighter activity (load > 0 but below the quality bar);
  - `rest` when the day has no activity.
  A day with `has_data=0`/no activity reads as `rest` (off-watch training is
  invisible by decision - ETL limitation, not a bug).
- **Plan adherence.** Join the seven planned intents from `plan_template` (by day
  of week, 0=Mon) against the seven actual intents; `plan_adherence` = exact
  matches / 7. The per-day comparison (planned, actual, match) is carried into the
  digest so the report can render direction of divergence. `n_quality` and
  `n_rest_days` come from the actual-intent classification; `n_sessions` counts
  days with any activity.
- **Load rollups.** `load_total`, `load_low`, `load_high`, `load_anaerobic` are
  sums of the corresponding `daily_metrics` columns across the week; `low_share`,
  `high_share`, `anaero_share` are those over `load_total` (NULL/0-guarded).
  `z2_min`, `threshold_min`, `z5_min` roll up the daily HR-zone minutes
  (threshold_min = z3+z4 by the project's zone language; z2 and z5 direct).
- **Foster monotony/strain.** `monotony` = mean(daily load) / SD(daily load) over
  the week's seven days; **NULL** when uncomputable (fewer than two training days
  / zero SD). `strain` = `load_total` * `monotony` (NULL when monotony is NULL).
- **`max_consec_hard`.** Longest run of consecutive days in the week with
  `load_day >= hard_te_load` (weekly form of the Phase 3 `TWO_HARD_DAYS` daily
  signal). `acwr_end` = the ACWR of the week's Sunday from `daily_metrics`.
- **Recovery means skip nulls.** `hrv_mean`, `rhr_mean`, `sleep_score_mean` are
  means over non-null daily values only; a `has_data=0` gap never contributes a
  zero. `hrv_trend` = this week's `hrv_mean` minus the prior week's (NULL when no
  prior week).
- **New signal - `signals.deload_advised(weekly_rows, thresholds) -> dict |
  None`.** Given weekly rows in date order, fires when: at least
  `deload_min_history_weeks` complete weeks exist, `load_total` rose across the
  last `deload_load_rise_weeks` consecutive weeks, **and** (`acwr_end` >
  `acwr_risk_high` **or** `monotony` > `monotony_high`). Returns
  `{"code": "DELOAD_ADVISED", "severity": "warn", "facts": {...}}`; otherwise
  silent (None). Retrospective "this week was a deload" (a `load_total` drop >=
  `deload_drop_pct` vs the prior weeks) is a report fact derived from the mart,
  not a signal.
- **Digest integration.** `build_digest` reads the latest complete `weekly_metrics`
  row (and its predecessor for trend/deload context) and adds a `weekly` section:
  the week's facts plus the per-day plan-vs-actual list. It also appends
  `signals.deload_advised(...)` to the existing `signals` list. Thresholds resolve
  through the existing `report.read_thresholds` / `digest.merge_thresholds`.
- **New thresholds (`coach_thresholds` seed rows).** `monotony_high` = 2.0,
  `deload_load_rise_weeks` = 3, `deload_min_history_weeks` = 3, `deload_drop_pct`
  = 0.40. `hard_te_load` (150) and `acwr_risk_high` (1.5) already exist and are
  reused. Add the rows to the package copy `src/garmin_coach/schema.sql`, then
  re-sync `docs/schema.sql` (guarded by `test_schema_sync.py`).
- **Coach skill.** `skills/coach/SKILL.md` gains a short instruction: when the
  digest carries a `weekly` section, render a "Tydzień: plan vs realizacja" block
  in `report.md` (facts + divergence table). No new charts.
- **No new tables.** `weekly_metrics` and `plan_template` already exist; Phase 5
  adds only threshold seed rows.

## Testing Decisions

- Good tests assert only external behavior: seed an open temp SQLite (a daily
  mart, plus `activities`/`plan_template` where adherence is under test), call the
  seam, and read back `weekly_metrics` rows - or call the pure signal with
  hand-built weekly rows and assert on the returned dict. No assertions on private
  helpers or SQL text. Same discipline as `test_features.py` (seed core -> run ->
  read mart) and `test_signals`/`test_digest.py` (pure signal presence/shape).
- **`tests/test_weekly.py` (new seam), vertical slices red -> green:**
  - Complete-week selection: given a daily mart spanning a partial current week,
    only Monday–Sunday weeks fully in the past are emitted; the current week is
    absent.
  - Load rollup + shares: a hand-built week asserts `load_total` and the
    low/high/anaero shares.
  - Monotony/strain: a varied week yields a finite monotony; a single-training-day
    week yields `monotony IS NULL` and `strain IS NULL` (no inf).
  - `max_consec_hard`: a Fri->Sat stack yields the expected run length.
  - Plan adherence: a week with a known mix of matches/mismatches asserts
    `plan_adherence` and the per-day direction (e.g. planned rest, actual quality).
  - Recovery means skip nulls: a week with one `has_data=0` gap averages over the
    remaining days, not counting a zero.
  - Golden regression: over the four complete weeks 2026-06-08…06-29 from a real
    anonymised fixture, assert `load_total`, `monotony`, `plan_adherence`, and
    `max_consec_hard` against hand-computed values. The fixture is built
    deterministically from the DB (`features`), not hand-typed.
- **`DELOAD_ADVISED` (extend `test_signals`):** fire case (>= min history, load
  rising N weeks, ACWR hot); silent case (history shorter than
  `deload_min_history_weeks`); monotony-trigger case (ACWR fine but monotony >
  `monotony_high`); silent case (load not monotonically rising).
- **Digest (extend `test_digest.py`):** a seeded weekly mart makes `build_digest`
  return a `weekly` section with the plan-vs-actual list, and `DELOAD_ADVISED`
  appears in `signals` when its conditions hold.
- **`test_schema_sync.py`** keeps `docs/schema.sql` identical to the package copy
  after the new threshold rows are added.
- **Prior art.** `test_features.py` (golden mart regression, seed-and-read),
  `test_digest.py` / `test_signals` (pure signal shape), `test_schema_sync.py`
  (schema snapshot equality). Fixture-building mirrors the Phase 2 approach.

## Out of Scope

- **Multi-sport / `discipline` weighting** (ski-touring season). `discipline` is
  in the schema; weighting rollups by sport is deferred (BUILD §12).
- **VO2max / lactate-threshold trend charts.** `hrv_trend`/`vo2max` remain
  computed columns, but no trend chart is rendered this phase (BUILD §12).
- **New charts of any kind on the report or nightly path.** The weekly view is
  text/digest only.
- **PDF / Notion export** of the report (BUILD §12).
- **Rewriting the daily `TWO_HARD_DAYS` signal.** The daily signal stays; the
  weekly `max_consec_hard` is its rollup form, not a replacement.
- **`features_version` column / metric versioning** (BUILD §12).
- **Any live Garmin call from the rollup or signals** - forbidden by the golden
  rule; the rollup reads only the finished DB.
- **A separate `weekly` CLI command or `weekly_report.md` artifact** - the rollup
  runs inside `features` and surfaces through the existing digest.

## Further Notes

- **Why fold into `features` rather than a new command.** `weekly_metrics` is a
  pure function of `daily_metrics`; a separate command would let the two marts
  drift out of sync (weekly stale against daily) and multiply what the athlete and
  the nightly script must run. One "recompute the marts" step keeps them coherent.
- **Why complete weeks only.** Adherence and monotony computed from a 1–2 day
  in-progress week are actively misleading. Skipping the current week is the same
  discipline as "backfill excludes today" and `n_chronic` reliability for ACWR.
- **Why the deload signal can stay silent.** With only ~4 weeks of history at
  launch, a rising-load rule needs a floor before it means anything. Silence on
  short history mirrors the Phase 3 convention of never emitting a signal the data
  cannot support.
- **Off-watch training.** Classifying a no-activity day as `rest` is a deliberate
  ETL limitation, documented in `CONTEXT.md`: the system knows only what Garmin
  recorded. Surfacing it in the report's divergence view (planned quality, actual
  rest) is itself useful feedback.
