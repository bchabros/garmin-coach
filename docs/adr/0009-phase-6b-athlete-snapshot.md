# ADR 0009 - Phase 6b: athlete snapshot (`athlete_status` mart)

## Status

Accepted

## Context

Answering "where do I stand right now" always meant reassembling the standing
picture by hand from scattered tables (`fitness_markers`, `race_predictions`,
`weight_log`, `daily_metrics`, `athlete_zones`, `training_readiness`,
`training_status_daily`, `plan_template`) plus ad-hoc SQL. There is no single
current-standing surface, and the Phase 10 recommender needs exactly this object as
its input. Every value it needs already lives in a finished mart or core table - no
new Garmin data. See `docs/prd/phase-6b/PRD.md`.

## Decision

- **Recomputed `athlete_status` mart (singleton `id = 1`).** A persisted mirror of
  the current standing, following the `athlete_zones` pattern - not a pure on-the-fly
  read. Chosen over a table-free compose so the command, the recommender, and the
  read-MCP all read one stable place, and `snapshot.json` is a plain serialization of
  the row. Derived values live only in the mart, never mixed into core (medallion
  discipline).

- **Seam: `snapshot.py`, run inside `features`.** A pure, total
  `build(conn, through_date) -> dict` composes the row from finished marts + core;
  `rollup(conn, through_date)` upserts the singleton and commits. Runs as the **tail
  of `features`, after `weekly.rollup` and `zones.rollup`**, so it always reads their
  freshly-written rows in the same run - no drift between the snapshot and its source
  marts. No Garmin (golden rule). The `snapshot` command only reads the table.

- **`computed_at` bounds every "latest" read.** Each latest-row lookup is scoped to
  `row_date <= computed_at` (the `through_date`), so a backfill to a past date
  reproduces that day's standing rather than leaking today's. `planned_*_today` uses
  the weekday of `computed_at`, not the wall clock. Same reproducibility contract as
  `zones.computed_at`.

- **Full mirror of the zones bounds.** `athlete_status` copies the complete
  `athlete_zones` row (all `z1_hi..z4_hi_bpm`, both paces, `source`,
  `lthr_detected_on`, and `zones_stale`) so `snapshot.json` is self-contained for the
  recommender / read-MCP without a second read. `athlete_zones` remains the source of
  truth; the snapshot is a same-run copy, so the duplication cannot drift within a
  `features` run.

- **Trends = value + signed delta over an available window, with a span.** Each of
  VO2max, body weight, and HRV baseline carries the current value plus a signed
  `*_delta` against the earliest reading on or after `computed_at - lookback` and the
  actual `*_span_days` used. When history is shorter than the lookback the delta is
  computed over what exists (not NULL), and `span_days` tells the coach how much; when
  the available span is below `snapshot_trend_min_span_days` the delta is NULL (never
  fabricated from one point). Magnitude is kept as a number; the coach skill turns the
  sign into words. Same "compute but expose credibility" stance as `n_chronic`.

- **Implementation finding (HRV trend source).** VO2max and body weight are genuine
  time series (`fitness_markers.vo2max_running`, `weight_log.weight_g`), so their deltas
  ride on their own values. Our own `daily_metrics.hrv_baseline` cannot: the nightly
  `daily` run recomputes the whole mart with no `from_date`, so the stored baseline is a
  single current value stamped onto every row - constant across history, always delta 0.
  The HRV **value** in the snapshot stays our `hrv_baseline` / `hrv_sd`, but the HRV
  **delta** rides on `hrv_nightly.weekly_avg` (Garmin's smoothed weekly HRV) - a real,
  read-only trending series. No recompute.

- **Load / ACWR reuse existing logic.** `load_7d` and the low/high/anaero shares reuse
  `signals.load_shares` (and the digest headline's ACWR/`n_chronic`/reliability read)
  rather than re-implementing share math, keeping one source of truth.

- **Plan block: forward-compatible placeholders.** `block`, `weeks_to_event`, and
  `taper_active` columns exist now but are NULL - Phase 9 fills them without a schema
  change. `planned_intent_today` / `planned_label_today` come from `plan_template` for
  the `computed_at` weekday, so the snapshot answers "today: quality" immediately.

- **Report + coach integration.** `report.generate_report` writes `snapshot.json`
  alongside `digest.json`, and `skills/coach/SKILL.md` gains a "Twoje aktualne staty"
  header read from `snapshot.json`. The coach skill is no longer digest-only, but
  `snapshot.json` is still a deterministic finished-DB read, so the golden rule and
  the auditability of the cited-signal approach hold - the coach now reads two
  deterministic artifacts instead of one.

## Thresholds (new keys in `coach_thresholds`)

| Key | Default | Meaning |
|---|---|---|
| `snapshot_vo2max_lookback_days` | `90` | window for the VO2max trend delta (slow-moving marker) |
| `snapshot_weight_lookback_days` | `28` | window for the body-weight trend delta |
| `snapshot_hrv_lookback_days` | `28` | window for the HRV-baseline trend delta |
| `snapshot_trend_min_span_days` | `7` | below this available span a trend delta is NULL |

## Testing

- `test_snapshot.py` (new seam): golden + case tests over `build` - a full
  post-onboarding standing; thin history (deltas NULL / short `span_days`); no zones
  anchor (`zones_stale = 1`, ceilings NULL); `computed_at` in the past reproducing the
  then-current standing; idempotent recompute.
- `test_features.py`: `features` writes exactly one `athlete_status` row, idempotent,
  after weekly + zones.
- `test_thresholds.py`: the four new `snapshot_*` keys present with defaults.
- `test_schema_sync.py`: `docs/schema.sql` identical to the package copy.
