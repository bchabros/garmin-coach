# 02 - Trend deltas: value + signed delta + span for VO2max, weight, HRV

Status: ready-for-agent
Parent: `docs/prd/phase-6b/PRD.md`

## What to build

The snapshot's three trend markers gain a signed change and the span it was measured
over, so the coach can say "VO2max 52 (+1.0 over 24 days)". Each of VO2max, body
weight, and HRV baseline carries its current value plus a `*_delta` against the
earliest reading on or after `computed_at - lookback`, and the actual `*_span_days`.
The delta is computed over whatever history exists; it is NULL only when the available
span is below `snapshot_trend_min_span_days` - honest while history is still short,
never faked from a single point.

## Acceptance criteria

- [ ] Four new `coach_thresholds` keys seeded in `schema.sql` (and `docs/schema.sql`):
      `snapshot_vo2max_lookback_days=90`, `snapshot_weight_lookback_days=28`,
      `snapshot_hrv_lookback_days=28`, `snapshot_trend_min_span_days=7`.
- [ ] A shared pure trend helper computes `(delta, span_days)` from a marker's
      date-value series, `computed_at`, a lookback, and the min-span floor.
- [ ] `build` populates `vo2max_delta` / `vo2max_span_days`, `weight_delta` /
      `weight_span_days`, `hrv_delta` / `hrv_span_days`.
- [ ] Delta and span degrade to NULL when the available span is below the floor.
- [ ] `test_snapshot.py` thin-history cases: short `span_days` when younger than the
      lookback; NULL below the floor; correct signed delta over available history.
- [ ] `test_thresholds.py`: the four `snapshot_*` keys present with their defaults.
- [ ] `test_schema_sync.py` stays green.

## Blocked by

- 01 - Snapshot skeleton (extends the `athlete_status` table and `build`).
