# ADR 0024 - The plan guard reads what the watch will ask, not what the template is called

## Status

Accepted

Supersedes the "one ladder read in two directions" decision of
[ADR 0021](0021-plan-guard-on-authoring-and-push.md); the rest of ADR 0021 stands.

## Context

ADR 0021 ranked both planned intent and the requested session type on one ladder.
Issue [#62](https://github.com/bchabros/garmin-coach/issues/62) exposed both failure
directions: an `easy` request with a 4:25–4:35/km work band passed on an easy day
despite a 5:14/km Z2 ceiling, while threshold repeats named `quality` were refused
on a planned tempo day. Renaming or editing the plan to satisfy the guard distorted
the agreed day and its later adherence record.

An athlete-declared hardness field would permit the same bypass under a new name.
The replacement must derive hardness from what the watch will actually ask, while
retaining one numeric ordering for the plan and authored workout.

## Decision

- Derive easy/threshold/hard ranks 1/2/3 from resolved authored steps, never from a
  caller-asserted hardness. The hardest targeted step wins, including nested repeats
  and warmup; faster pace/higher HR edge determines a band's difficulty.
- Easy is bounded by the athlete's Z2 pace/HR ceilings; threshold uses the authoring
  chain's threshold-pace margin or LTHR, not the lower Z4 ceiling. Beyond is hard.
- Untargeted work uses its target chain; untargeted other roles add no rank. Missing
  ladder, exercise and station sessions omit measured hardness and fall back to type;
  missing ladder also warns. Threshold calibration remains an evidence limitation.
- Freeze measurement at authoring and read it at push; receipts/divergence retain it.
  Do not recompute old specs/receipts against newer zones.
- Hardness is excluded from both hashes. Recommender still descends planned-intent
  rank; guard compares on that same scale. Both author/push guards and replace refusal
  remain. This changes guard measurement, not adherence classification.

## Consequences

A renamed hard session remains hard, while threshold repeats are allowed on an
agreed threshold day. Stored measurements preserve what was actually judged.
