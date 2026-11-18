# ADR 0040 - Rank observed external-load records only under explicit comparison conditions

## Status

Accepted recommendation for #121 and delivery slice #130, 2026-11-03.

## Context

Captured exercise rows and legacy manual circuit summaries lack equipment,
weight basis and resistance mode. Treating all movement rows as comparable lifts
would turn station aggregates, per-hand weights or assistance into misleading
records. Movement classifications also merge distinct exercises by design.
This extends the selected manual/captured evidence contract in
[ADR 0022](0022-manual-set-overlay.md) and the corrected source observations in
[ADR 0039](0039-recorded-exercise-set-corrections.md).

## Decision

Report heaviest observed working-set load and best observed load at
an exact positive repetition count. Preserve every tie, chronological eligible
history, overall and per-repetition record progression, kilograms, dates and sources.
Reject estimated 1RM because an equation would turn observed work into an inferred
maximum; no estimation method or calibration was approved in #121/#130. Recorded
weights and repetitions support the observed comparisons without that assumption.
Compare at whole-gram resolution, rounding captured gram measurements for ranking
without changing raw observations. Compare integer grams for ties/progression and
convert to kilograms only for presentation.

Rank only individually named captured working sets whose complete stored source
observation agrees with the selected normalized row. An active legacy manual
circuit overlay hides captured sets but cannot itself establish individual work.
Unknown/missing/aggregate evidence remains visible with an unranked reason.

The athlete explicitly declares resistance mode, equipment and total/per-hand
basis against a binding of current selected/source evidence. Store the mode as
`external_load`, `assisted_load` or `bodyweight_only`; only external load ranks.
Missing declarations and migrated rows whose mode was never retained remain unknown,
requiring a fresh declaration rather than an invented external-load assumption.
Distinct reasons identify legacy circuits, missing source observations, mismatching
source facts, missing measurements, stale conditions and unsupported modes.
Exact underscore-delimited ASSIST/ASSISTED/ASSISTANCE name tokens provide only a
conservative exclusion hint, never a declared mode or a complete assistance detector.
Source changes invalidate the old declaration. Exact exercise category/name,
equipment and basis
form separate comparisons; movement-group equivalence and workout aliases do not.
Bodyweight-only, assisted-load and estimated records are unsupported.

Reads calculate from current selected facts in one SQLite snapshot, so corrected
or removed leaders retract without rewriting retained historical reports. The
result is best in available comparable recorded history, independent of Garmin PRs;
no lifetime-best or complete-import guarantee is made. Coverage includes every known
strength/HIIT activity in the onboarding/report horizon, even without selected sets,
and distinguishes missing source, observed-empty sets and missing normalized rows.
Shared historical-check facts show failed, due and never-checked source reads.
The caller supplies today's date, so the calculation has no hidden wall-clock read.

## Consequences

Fewer results rank automatically, but unknown equipment and weight conventions do
not silently become comparable measurements. Explicit declarations require review
again after changed source evidence. Derived records are recomputed reads, while
comparison declarations remain durable athlete-entered facts. No live account call
is introduced.
