# ADR 0027 - Split every session's load by intensity, tuned to Garmin's own balance

## Status

Accepted

Supersedes the "Load buckets are total over nulls" clause of
[ADR 0002](0002-phase-2-metrics-semantics.md) and the `AEROBIC_LOW_SHORTAGE` targets
of [ADR 0003](0003-phase-3-coach-signals.md); their other decisions remain in force.

## Context

Issue [#70](https://github.com/bchabros/garmin-coach/issues/70) showed that aerobic
Training Effect confounded duration with intensity: 45–55-minute easy runs scored
2.8–3.6 and were filed wholly as high. From 2026-06-08 through 2026-09-13 no day
had low-aerobic load, despite 28 of 35 runs averaging at or below the personal Z2
ceiling. An easy run with strides on 2026-08-24 spent 96% of its time in watch
zones 1–2 but was filed wholly as anaerobic. The resulting 28-day anaerobic share
was 67%, against Garmin's 20%, and the shortage signal fired every night.

The athlete wanted a per-session split close to Garmin's balance. Over 74 correlated
days (2026-07-06 through 2026-09-20), the chosen rule's mean absolute share difference
was 2.7 percentage points, versus 28 for the old TE flags and 8–10 for textbook zone
weights. Reading Garmin's balance directly would match the monthly headline but
leave daily and weekly load without a consistent allocation; average-HR bucketing
would still misclassify mixed sessions as a whole.

## Decision

- Split every cardio activity's blended load. Anaerobic share is
  `0.5 * anaero_te / (aero_te + 0.5 * anaero_te)`, zero when unavailable.
- Split the remainder by watch HR-zone seconds weighted 1,2,4,4,8; Z1–2 are low,
  Z3–5 high. With no zone time, the old aerobic-TE threshold splits the remainder.
  Strength stays wholly in strength. Null handling preserves the four-bucket total.
- These are mart coefficients, not coach thresholds. Changing them requires full
  offline recomputation; total load/ACWR/monotony/strain do not change from splitting.
- Compare exactly 28 ending days with the newest complete Garmin balance's low minimum
  over its three-bucket total. Use 0.25 only if no Garmin lower bound is available.
  Remove the old hard-share condition; retain target provenance and Garmin agreement.
- Nightly logs mean absolute share difference in percentage points. A sustained gap
  around five points motivates recalibration, not a new athlete-facing diagnosis.

## Consequences

The split was fitted on correlated evidence from the original athlete, not validated
for a population. Historical marts restate after coefficient changes. Personal-zone
minute share remains a separate description of time rather than load stimulus.

The alert compares matching 28-day windows because the first version compared a
seven-day share with Garmin's monthly bound: on 2026-09-20 it flagged a peak week
at 19% low aerobic even though the month was 33% and Garmin called it balanced.
Keeping a fixed 60% target would also fire on all 74 comparison days; Garmin's
own lower bound supplies a comparable target instead.
