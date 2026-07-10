# PRD - Garmin Coach - Phase 6b: athlete snapshot (`athlete_status` mart)

> Status: Ready for implementation (TDD) - Date: 2026-07-10
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 6b, `docs/adr/0009-phase-6b-athlete-snapshot.md`,
> `docs/glossary.md`, grilling decisions 2026-07-10.

## Problem Statement

Answering "where do I stand right now" always means reassembling the standing
picture by hand. The current fitness markers live in `fitness_markers` and
`race_predictions`, body weight in `weight_log`, the personal zones in
`athlete_zones`, HRV/load/recovery state in `daily_metrics`, readiness in
`training_readiness`, heat/altitude acclimation in `training_status_daily`, and the
weekly plan in `plan_template` - eight places, joined with ad-hoc SQL every time.
There is no single current-standing surface. The Phase 10 recommender will need
exactly this object as its input, and the coach report has no compact "current
stats" header to open with. Every value needed already exists in a finished mart or
core table - nothing new must be fetched from Garmin; the data is just scattered.

## Solution

A recomputed **athlete snapshot**: a singleton `athlete_status` mart row (`id = 1`)
that mirrors the athlete's current standing in one place, plus a `garmin-coach
snapshot` command that serializes it to `reports/{date}/snapshot.json`.

- **Compose from finished marts + core (deterministic seam).** A new pure function
  `snapshot.build(conn, through_date) -> dict` reads the latest rows of
  `fitness_markers`, `race_predictions`, `weight_log`, `daily_metrics`,
  `athlete_zones`, `training_readiness`, `training_status_daily`, and `plan_template`
  and assembles one current-standing dict. `snapshot.rollup(conn, through_date)`
  upserts the singleton `athlete_status` row and commits. It runs as the **tail of
  the existing `features` command, after `weekly.rollup` and `zones.rollup`**, so it
  reads their freshly-written rows in the same run - exactly like the Phase 6 zones
  rollup. It never touches Garmin (mart-from-core, golden rule).
- **As-of reproducibility.** `computed_at` (the `through_date`) bounds every "latest"
  read to `date <= computed_at`, so a backfill to a past date reproduces that day's
  standing rather than leaking today's. `planned_intent_today` /
  `planned_label_today` use the weekday of `computed_at`, not the wall clock. Same
  contract as `athlete_zones.computed_at`.
- **Full mirror of the zones bounds.** The snapshot copies the complete
  `athlete_zones` row (all HR band edges, both paces, `source`, `lthr_detected_on`,
  `zones_stale`) so `snapshot.json` is self-contained for the recommender / read-MCP
  without a second read. `athlete_zones` stays the source of truth; because the copy
  is made in the same `features` run, it cannot drift within a run.
- **Trends as value + signed delta + span.** VO2max, body weight, and HRV baseline
  each carry the current value, a signed `*_delta` against the earliest reading on or
  after `computed_at - lookback`, and the actual `*_span_days`. The delta is computed
  over whatever history exists; it is NULL only when the available span is below
  `snapshot_trend_min_span_days`. The coach skill turns the sign into words.
- **Forward-compatible plan block.** `block`, `weeks_to_event`, and `taper_active`
  columns exist now but are NULL - Phase 9 will populate them without a schema change.
- **Report + coach integration.** `report.generate_report` writes `snapshot.json`
  next to `digest.json`, and `skills/coach/SKILL.md` gains a "Twoje aktualne staty"
  header read from `snapshot.json`.

## User Stories

1. As the athlete, I want one command that prints my current standing, so that I do
   not have to hand-write SQL across eight tables to see where I am.
2. As the athlete, I want my current VO2max and whether it is rising or falling, so
   that I can tell if my fitness is trending the right way.
3. As the athlete, I want my latest race predictions (5k, 10k, half, marathon) in the
   snapshot, so that I can see my current implied race fitness at a glance.
4. As the athlete, I want my body weight and its recent trend, so that I can relate
   load and recovery to weight change.
5. As the athlete, I want my HRV baseline, SD, and its trend, so that I know my normal
   band and whether it is drifting.
6. As the athlete, I want my latest ACWR with its `n_chronic` reliability, so that I
   know both the number and whether to trust it yet.
7. As the athlete, I want my trailing-7-day load and its low/high/anaerobic shares, so
   that I can see the recent balance of my training.
8. As the athlete, I want my personal HR zones and Z2 pace ceiling in the snapshot, so
   that I can check "keep easy runs under X:XX" without recomputing.
9. As the athlete, I want to know if my zones are stale, so that I know when a fresh
   threshold effort would improve the advice.
10. As the athlete, I want my current Training Readiness score and level, so that the
    standing view reflects Garmin's own recovery read.
11. As the athlete, I want my sleep-debt figure in the snapshot, so that I can see
    whether accumulated sleep loss is dragging on readiness.
12. As the athlete, I want my heat and altitude acclimation, so that I can interpret
    HR drift in hot or high conditions.
13. As the athlete, I want today's planned intent and label from my weekly template,
    so that the snapshot says "today: quality (FBB + Hyrox)".
14. As the athlete, I want the snapshot to reproduce a past day's standing when I
    backfill to that date, so that the view is deterministic and testable.
15. As the athlete, I want trends to stay honest while my history is short, so that a
    delta over 24 days is labelled as such rather than hidden or faked from one point.
16. As the athlete, I want the snapshot written to `reports/{date}/snapshot.json`
    whenever a report is generated, so that the artifact is always available beside the
    digest.
17. As the athlete, I want a short "Twoje aktualne staty" header at the top of my coach
    report, so that the narrative opens with where I stand.
18. As the future recommender (Phase 10), I want a single self-contained standing
    object, so that I can consume current markers, zones, load, and plan without
    re-reading eight tables.
19. As the maintainer, I want the snapshot to be a same-run copy of finished marts, so
    that it never drifts from its sources and never recomputes underlying numbers.
20. As the maintainer, I want the standing refreshed automatically on every nightly
    `daily`/`features` run, so that it is never stale between manual invocations.
21. As the maintainer, I want the snapshot fields to degrade to NULL when a source is
    missing (no zones anchor, no readiness, thin history), so that the pipeline stays
    total and never crashes.

## Implementation Decisions

Full rationale in `docs/adr/0009-phase-6b-athlete-snapshot.md`.

- **New mart table `athlete_status` (singleton `id = 1 CHECK`).** Recomputed, never a
  system of record, safe to drop and rebuild - medallion discipline, mirroring
  `athlete_zones`. Added to `schema.sql` (package copy) and mirrored to
  `docs/schema.sql` (guarded by `test_schema_sync.py`). Column groups:
  - as-of: `computed_at`
  - fitness markers: `vo2max`, `vo2max_delta`, `vo2max_span_days`; `weight_kg`,
    `weight_delta`, `weight_span_days`; `t_5k_s`, `t_10k_s`, `t_half_s`, `t_marathon_s`
  - HRV: `hrv_baseline`, `hrv_sd`, `hrv_delta`, `hrv_span_days`
  - load / ACWR: `acwr`, `n_chronic`, `acwr_reliable`, `load_7d`, `low_share`,
    `high_share`, `anaero_share`
  - recovery: `readiness_score`, `readiness_level`, `sleep_debt_h`, `heat_accl_pct`,
    `heat_trend`, `altitude_accl`
  - zones (full mirror of `athlete_zones`): `lthr_bpm`, `z1_hi_bpm`, `z2_hi_bpm`,
    `z3_hi_bpm`, `z4_hi_bpm`, `threshold_pace_s_per_km`, `z2_pace_ceiling_s_per_km`,
    `zones_source`, `lthr_detected_on`, `zones_stale`
  - plan: `block`, `weeks_to_event`, `taper_active` (all NULL until Phase 9),
    `planned_intent_today`, `planned_label_today`
- **New module `snapshot.py`** with the primary seam `build(conn, through_date) ->
  dict` (pure, total compose) and `rollup(conn, through_date)` (upsert + commit),
  structured like `zones.py`. Sources for each field:
  - `vo2max` from `fitness_markers.vo2max_running` series; `weight_kg` from
    `weight_log.weight_g / 1000`; race predictions from the latest `race_predictions`
    row; `hrv_baseline`/`hrv_sd` and `sleep_debt_h` from the latest `daily_metrics`
    row; `acwr`/`n_chronic`/`load_7d`/shares reuse the digest headline logic and
    `signals.load_shares` (no re-implementation); readiness from the latest
    `training_readiness`; acclimation from the latest `training_status_daily`; zones
    from `athlete_zones`; plan from `plan_template` at the `computed_at` weekday.
- **Trend helper.** A small pure function computes `(delta, span_days)` for a marker's
  date-value series given `computed_at`, a lookback, and `snapshot_trend_min_span_days`:
  pick the earliest reading with `date >= computed_at - lookback`, delta = current -
  that reading, `span_days` = their date difference; return `(None, None)` when
  `span_days < min_span`. Shared by all three trend markers.
- **New `db.upsert_status(conn, row)` helper**, following `upsert_zones`.
- **`features.features(...)` calls `snapshot.rollup` last**, after `weekly.rollup` and
  `zones.rollup`, passing the same `through_date`/`end`.
- **New `garmin-coach snapshot` CLI command.** Reads the `athlete_status` row and
  writes `reports/{date}/snapshot.json` (plus a short stdout summary), like
  `garmin-coach report`. It does not recompute - `features` owns recomputation.
- **`report.generate_report` also emits `snapshot.json`** alongside `digest.json` and
  the charts (one added read + write).
- **`skills/coach/SKILL.md`** gains a "Twoje aktualne staty" section fed from
  `snapshot.json`; the coach skill now reads two deterministic artifacts
  (`digest.json` + `snapshot.json`), both finished-DB reads - the golden rule holds.
- **Four new `coach_thresholds` keys** (seeded in `schema.sql`):
  `snapshot_vo2max_lookback_days=90`, `snapshot_weight_lookback_days=28`,
  `snapshot_hrv_lookback_days=28`, `snapshot_trend_min_span_days=7`.

## Testing Decisions

Good tests here exercise external behavior at the `snapshot.build` seam - the shape
and values of the returned dict given frozen core/mart fixtures - not the internal
SQL. Prior art: `test_zones.py` (golden + degraded-case tests over `zones.compute`)
and `test_digest.py` (block assertions over `build_digest`).

- **`test_snapshot.py` (new seam, primary):**
  - golden: a full post-onboarding standing over frozen fixtures (all groups
    populated), asserting the composed dict and mirrored zone bounds;
  - thin history: trend deltas NULL or short `span_days` when the series is younger
    than the lookback / below `snapshot_trend_min_span_days`;
  - no zones anchor: `zones_stale = 1` and zone ceilings NULL degrade cleanly;
  - as-of: `computed_at` in the past reproduces the then-current standing (later rows
    ignored);
  - idempotent recompute: two `rollup` calls leave exactly one identical row.
- **`test_features.py`:** `features` writes exactly one `athlete_status` row and is
  idempotent, ordered after weekly + zones.
- **`test_thresholds.py`:** the four `snapshot_*` keys are present with their defaults.
- **`test_schema_sync.py`:** `docs/schema.sql` stays byte-identical to the package
  copy after the new table + seed rows.

## Out of Scope

- **Phase 9 periodization** - `block`, `weeks_to_event`, `taper_active` ship as NULL
  placeholders only; no `goal_event`, no date-anchored plan blocks, no taper logic.
- **New Garmin fetches** - every field reads a finished mart or core table; no new
  transport, no endpoint additions.
- **Recommendations** - the snapshot is a read of current standing, not forward advice;
  "what to do today" is Phase 10.
- **Charts** - the snapshot is JSON + a coach header; no new PNG.
- **read-MCP** - exposing the snapshot over MCP is deferred to the read-MCP phase; this
  phase only guarantees a self-contained `snapshot.json` it can later wrap.
- **New trend visualizations** - VO2max/threshold trend charts remain deferred
  (PROJECT.md Part I s12).

## Further Notes

- The full mirror of zone bounds is a deliberate self-containment choice for the
  recommender / read-MCP consumer, accepted despite duplicating `athlete_zones`,
  because the copy is a same-run snapshot and cannot drift within a `features` run.
- Trend windows differ by marker cadence (VO2max slow at 90d, weight/HRV at 28d) and
  are stored in `coach_thresholds` so they are tunable without code.
- The snapshot deliberately keeps the `n_chronic`-style "compute but expose
  credibility" stance for trends via `span_days`, consistent with how ACWR reliability
  is surfaced elsewhere.
