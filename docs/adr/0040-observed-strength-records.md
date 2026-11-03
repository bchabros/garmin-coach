# ADR 0040 - Rank observed external-load records only under explicit comparison conditions

## Status

Accepted recommendation for #121 and delivery slice #130, 2026-11-03.

## Context

Captured exercise rows and legacy manual circuit summaries lack equipment,
weight basis and resistance mode. Treating all movement rows as comparable lifts
would turn station aggregates, per-hand weights or assistance into misleading
records. Movement classifications also merge distinct exercises by design.

## Decision

Report heaviest observed successful working-set load and best observed load at
an exact positive repetition count. Preserve every tie, chronological eligible
history, record progression, kilograms, dates and sources. Do not estimate 1RM.

Rank only individually named captured working sets whose complete stored source
observation agrees with the selected normalized row. An active legacy manual
circuit overlay hides captured sets but cannot itself establish individual work.
Unknown/missing/aggregate evidence remains visible with an unranked reason.

The athlete explicitly declares external-load mode, equipment and total/per-hand
basis against a binding of current selected/source evidence. Source changes
invalidate the old declaration. Exact exercise category/name, equipment and basis
form separate comparisons; movement-group equivalence and workout aliases do not.
Bodyweight-only, assisted-load and estimated records are unsupported.

Reads calculate from current selected facts in one SQLite snapshot, so corrected
or removed leaders retract without rewriting retained historical reports. The
result is best in available comparable recorded history, independent of Garmin PRs;
no lifetime-best or complete-import guarantee is made.

## Consequences

Fewer results rank automatically, but unknown equipment and weight conventions do
not silently become comparable measurements. Explicit declarations require review
again after changed source evidence. Derived records are recomputed reads, while
comparison declarations remain durable athlete-entered facts. No live account call
is introduced.
