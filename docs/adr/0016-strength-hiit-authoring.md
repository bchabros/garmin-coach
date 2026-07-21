# ADR 0016 - Strength/HIIT authoring: flat steps and a taxonomy-held whitelist

## Status

Accepted

## Context

Issue #16 turns the strength/HIIT deferral into a real authoring path. Two live
probes settled the transport question: the raw create endpoint accepts hand-built
payloads for `strength_training` (5) (probe 2026-07-15) and `hiit` (9) (probe
2026-07-21) - rep-ended and time-ended intervals, time-ended rest steps, and
first-class `weightValue`/`weightUnit` all round-trip. garminconnect 0.3.6 ships
no typed strength/HIIT builder, so the translator hand-builds the dict either way.

Two design questions had genuine alternatives and would surprise a future reader:
how sets are shaped in the payload, and where the exercise vocabulary comes from.

## Decision

- **One flat `ExecutableStepDTO` per set; never a repeat group.** The run path
  models intervals as `RepeatGroupDTO`s, so mirroring it (sets x (work + rest) as
  a group) was the obvious shape. Rejected: the probes proved exercise metadata
  (`category`/`exerciseName`) round-trips on *flat* steps only - inside repeat
  groups it is unverified against an undocumented endpoint, and a repeat group
  always carries a trailing rest after the last set. Flat expansion is exactly the
  proven shape, shows "set N" progress on the watch, drops the session's trailing
  rest, and keeps non-uniform sets (ramping weight as consecutive entries)
  uniform. Cost: more steps per workout - irrelevant at gym-session sizes.

- **The exercise whitelist is curated and held to Garmin's public taxonomy, not
  mined from the athlete's history.** The plan was to seed `category`/
  `exerciseName` from the athlete's logged exercise sets; that source turned out
  empty - the watch records whole strength sessions as a single `UNKNOWN` set.
  The fallback source is Garmin Connect's own public exercise taxonomy
  (`connect.garmin.com/web-data/exercises/Exercises.json`), snapshotted to
  `tests/fixtures/garmin_exercise_taxonomy.json` (2026-07-21); contract tests
  hold every whitelist entry to it, so no guessed enum can enter the map.
  Resolution stays warn-never-block (ADR 0013's stance): an unknown exercise
  authors an unlabeled step plus a spec warning. Notable curation: no taxonomy
  entry exists for the ski erg (stays unmapped) or burpee broad jumps (labeled
  `TOTAL_BODY`/`BURPEE`, the closest honest pair).

## Consequences

- The authoring intelligence stays entirely in the pure `author` seam; `publish`,
  idempotency, and the confirm interlock are reused unchanged.
- Repeat-group modelling would need its own probe before ever being adopted; the
  spec's flat shape is the contract until then.
- The taxonomy snapshot can drift from Garmin's live list; refreshing the fixture
  re-validates the whole map by construction (the contract tests fail on any
  entry the new snapshot no longer carries).
- Weight units are fixed to kilograms end to end; the `weightUnit` descriptor is
  validated by the per-sport live acceptance step in the runbook, not by CI.
