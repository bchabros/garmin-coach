# ADR 0027 - Split every session's load by intensity, tuned to Garmin's own balance

## Status

Accepted

## Context

Whole-session TE bucketing mislabeled hybrid training; the split must match comparable Garmin evidence.

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
