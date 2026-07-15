# Phase 11 spike: strength / HIIT push feasibility

> Outcome record for ticket 07. Non-blocking; the run deliverable does not depend
> on it. See `docs/PROJECT.md` Phase 11 DoD ("strength spike outcome documented")
> and ADR 0013.

## Question

Can the system push a structured **strength** or **HIIT** workout to Garmin the way
it pushes run workouts, or does strength stay local (Runna's model)?

## Library-level finding (verified, offline)

**garminconnect 0.3.6 has no typed strength or HIIT workout class.** The only typed
workout builders are `RunningWorkout`, `CyclingWorkout`, `SwimmingWorkout`,
`WalkingWorkout`, `MultiSportWorkout`, `FitnessEquipmentWorkout`, `HikingWorkout`
(plus the `BaseWorkout` base). There is a `SportType.STRENGTH_TRAINING = 5` and
`SportType.HIIT = 9` id, and a generic `upload_workout(raw_json)` endpoint, but no
modelled step/exercise structure for strength. This matches the PROJECT.md survey
note that "no `StrengthWorkout` class is documented" and that Garmin's partner
Training API is cardio-oriented.

So the run path is the only *modelled* surface; strength can only be attempted by
hand-building a raw payload against the private create endpoint.

## Endpoint-level probe (operator-run)

The probe script `scratch/phase11_strength_push_probe.py` hand-builds a
`STRENGTH_TRAINING` payload (a rep-ended squat step) and, with `--confirm`, uploads
it via `upload_workout` and deletes it. It is a manual, operator-run step (it writes
to the live account) and has not been run in this branch.

- **Dry-run verified**: the script builds and prints the payload offline.
- **Live outcome**: pending an operator `--confirm` run.

## Decision

**Default (shipped) behaviour is the Runna-style fallback**: `author` defers
`sport: hiit` and `sport: strength` with a clear "awaits the push spike" message,
and strength/HIIT stay local (spec + report, watch-free). No production strength
transport code exists, by design.

Flip only if the probe confirms the endpoint:

- **Probe accepts the payload** -> strength push is feasible. Open a **Phase 11b**
  GitHub issue for production support (an author path that expands strength/Hyrox
  sets into the accepted payload shape, plus a `StrengthWorkout`-style translator).
  This lands as a GitHub issue, not a new `docs/prd/` phase, per the tracker
  transition.
- **Probe rejects the payload** -> confirm the fallback is permanent: strength/HIIT
  execution stays watch-free, and `author`'s deferral message becomes the settled
  behaviour rather than a placeholder.

Either way, the run-workout deliverable is complete and unaffected.
