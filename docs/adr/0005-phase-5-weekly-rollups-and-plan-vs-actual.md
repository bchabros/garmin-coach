# ADR 0005 - Phase 5: weekly rollups, plan-vs-actual, and deload detection

## Status

Accepted

## Context

Phases 0-4 built the deterministic pipeline (`sync` -> `features` ->
`report`/`daily`) at the daily grain. The schema already ships two Phase 5
tables that no code populates yet: `plan_template` (seeded with this athlete's
real weekly template) and `weekly_metrics` (~20 columns for load rollups, Foster
monotony/strain, plan adherence, and trends). BUILD Phase 5 asks the report to
show a "plan vs realizacja" divergence and to detect "two hard days in a row";
its backlog (§12) lists multi-sport and long-term VO2max/threshold trend charts.
A `TWO_HARD_DAYS` *daily* signal already exists from Phase 3.

The task, then, is to *fill in* pre-designed tables and extend the existing
digest/signals machinery - not to design new artefacts.

## Decision

- **Scope cut (DoD-driven + cheap wins).** Ship: a weekly rollup engine that
  populates `weekly_metrics` for every complete week; `plan_adherence`; a new
  prospective `DELOAD_ADVISED` signal; Foster `monotony`/`strain`. Defer:
  multi-sport/`discipline` weighting, VO2max/threshold **trend charts** (keep
  `hrv_trend`/`vo2max` as computed columns, no new chart), and PDF/Notion export.

- **Seam: `weekly.py`, run inside `features`.** A pure `rollup(conn, ...)` over
  `daily_metrics` rows materialises `weekly_metrics` (a mart-from-mart step). It
  runs as the second half of the existing `features` command, not a new command.
  Rationale: `weekly_metrics` is a pure derivative of `daily_metrics` and never
  touches Garmin, so one "recompute the marts" command keeps the medallion
  coherent and avoids command sprawl. Tested as a new seam beside `features.py`.

- **Complete weeks only.** Only Monday–Sunday weeks fully at/before yesterday are
  rolled up; the in-progress week is skipped (same spirit as "backfill excludes
  today"). Prevents `plan_adherence`/`monotony` lying from 1–2 days.

- **Plan adherence = load-based intent match on 7 days.** Classify each actual
  day to the `plan_template` intent vocabulary *by load* (`quality` at/above
  `hard_te_load` or with anaerobic load; `easy` for lighter activity; `rest` for
  none), then `plan_adherence` = exact-match count / 7. The report additionally
  shows the *direction* of each per-day mismatch, because the DoD asks to surface
  divergence, not just a ratio. Classifying by load (not by discipline/activity
  type) reuses the daily mart the coach already reasons on.

- **Deload: facts in the mart, one prospective signal.** Descriptive deload
  facts (`load_total`, `acwr_end`, `monotony`, `strain`, a ≥`deload_drop_pct`
  drop) live in `weekly_metrics`/the report. A new `DELOAD_ADVISED` signal
  (severity `warn`) fires when history is sufficient (`deload_min_history_weeks`)
  **and** `load_total` rose for `deload_load_rise_weeks` consecutive weeks
  **and** (`acwr_end` > `acwr_risk_high` **or** `monotony` > `monotony_high`).
  This mirrors Phase 3/4: facts in the mart, rules as a separate layer with
  thresholds in the `coach_thresholds` table.

- **Short history / uncomputable values stay silent.** When history is shorter
  than a rule needs, the signal is silent rather than guessing (same discipline
  as `n_chronic < 28` for ACWR). `monotony` is `NULL` when uncomputable (fewer
  than two training days), never `inf`.

- **Gaps in a load column read as `rest`.** Since Phase 5 rolls up only complete,
  historical weeks, all streams for those days are long past their watermarks, so
  a day with no activity is a real `rest`. Training performed without wearing the
  watch is, by decision, invisible to the system - an ETL limitation, not a bug
  to patch here. Recovery averages (`hrv_mean`, `rhr_mean`, `sleep_score_mean`)
  average over non-null days only, so a `has_data=0` gap never reads as "zero
  recovery".

- **Report integration: extend the digest, no second artefact.** `build_digest`
  gains a `weekly` section (last complete week's facts + the per-day plan-vs-
  actual table), and `DELOAD_ADVISED` joins the existing signal list - so the
  nightly `daily` alert path picks it up for free when a week has just closed.
  The coach skill writes a "Tydzień: plan vs realizacja" block from that section.
  No new charts.

## Thresholds (new keys in `coach_thresholds`)

| Key | Default | Meaning |
|---|---|---|
| `monotony_high` | `2.0` | Foster monotony above this is an overtraining flag |
| `deload_load_rise_weeks` | `3` | consecutive rising-load weeks that arm `DELOAD_ADVISED` |
| `deload_min_history_weeks` | `3` | below this many complete weeks the signal is silent |
| `deload_drop_pct` | `0.40` | retrospective: a `load_total` drop this large marks "this was a deload" |

`hard_te_load` (for `max_consec_hard` and actual-intent classification) and
`acwr_risk_high` already exist and are reused.

## Testing

- `test_weekly.py` (new seam): golden regression over the four complete weeks
  2026-06-08…06-29, asserting `load_total`, `monotony`, `plan_adherence`, and
  `max_consec_hard` against hand-computed values from a real anonymised fixture
  built deterministically from the DB (`features`), not hand-typed.
- Signals: `DELOAD_ADVISED` fire / silent-on-short-history / monotony-trigger
  cases; explicit `plan_adherence` classification test including a mismatch day.
- `test_schema_sync.py` keeps `docs/schema.sql` identical to the package copy
  after the new threshold rows are added.
