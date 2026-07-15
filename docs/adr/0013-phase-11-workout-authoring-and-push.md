# ADR 0013 - Phase 11: workout authoring and push

## Status

Accepted

## Context

The golden rule of this system is that transport is separated from intelligence: the
deterministic ETL (`client.py`, `sync.py`) is the only code that talks to Garmin, and
the metrics/coach layer only ever reads the finished DB. Every phase so far has read
*from* Garmin. Phase 11 introduces the first **outbound write**: turning a session ask
into a structured Garmin workout and scheduling it to the watch, so the athlete stops
hand-building each recommendation in Garmin Connect (the step every industry peer
automates - Runna, Athletica, Stryd, TrainAsONE, enduco).

This bends the golden rule in the one direction it has never gone, and the write is
near-irreversible: it creates workouts and calendar entries on the athlete's real
account. The design also has to serve three origin modes the athlete asked for - the
recommender's own suggestion, the athlete's own idea filled in from their zones, and a
hybrid where the recommender validates the athlete's idea before it goes to the watch -
without three parallel code paths. And garminconnect 0.3.6 verifiably supports only
run-type typed workouts; strength/HIIT have sport IDs but no typed classes and an
unverified create path. See `docs/prd/phase-11-workout-push/PRD.md`.

## Decision

- **Split the outbound path into a pure `author` and an out-of-seam `publish`.**
  `author` consumes a workout request plus the finished marts and produces a workout
  spec on disk (deterministic, offline, unit-tested). `publish` reads the spec and makes
  the account calls, reusing `client.login()` for auth and a dedicated write-wrapper for
  the endpoints. `author` never imports `publish`. This is the same isolation the golden
  rule already mandates for reads (`sync` depends on an injected `GarminClient` protocol;
  the coach layer never calls Garmin) - applied to the write direction. The bend is
  contained to exactly one module, on the far side of a seam, and the intelligence stays
  pure and testable.

- **The workout request is source-agnostic; the three origin modes share one path.**
  A request carries `origin: recommender | athlete` and `sport`, and both `author` and
  `publish` are identical regardless of who produced it. The hybrid mode is a *process*,
  not a third origin: an `athlete` request passed through recommender validation before
  authoring. This keeps the surface at two commands and one spec format rather than three
  code paths, and means the third mode is a validation step, not new infrastructure.

- **Hybrid validation warns, never blocks.** When the athlete's request conflicts with the
  digest's fired signals (asks `tempo` when signals cap to easy), `author` writes cited
  warnings into the spec rather than refusing. Rationale: this is a single-athlete system
  where the athlete is the sovereign of their own training; `push` already gates on an
  explicit confirm, so a second hard block on top of an explicit confirm is redundant
  bureaucracy that also forbids legitimate exceptions (a deliberate control effort under
  fatigue). Writing the warnings *into the spec* (not just to the screen) leaves an audit
  trail that the athlete pushed over a warning, which a later report can reconcile against
  what actually happened. This matches the system's explainability stance (cited signals,
  never a black box that commands).

- **The confirm interlock is inverted from a `--dry-run` flag.** `push` dry-runs whenever
  `--confirm` is absent - there is no `--dry-run` flag to forget, only a `--confirm` flag to
  add. Semantically identical to "dry-run by default" but strictly safer: a forgotten flag
  yields the safe outcome, never an accidental write to the account. `author` and `push` are
  separate commands handed off by an on-disk `workout.json`, so the spec can be inspected or
  edited before a near-irreversible write, and the write is never one keystroke away. Neither
  command is ever called from the nightly automation.

- **The Garmin account is the source of truth for idempotency, not local state.** Before
  writing, `publish` lists the account's `GC`-prefixed workouts and matches by name + date:
  identical payload is a no-op, a different payload requires `--replace`, a library-only
  workout is scheduled, absence creates. A local `pushed_workout` table was rejected: the
  account is mutable from the phone (the athlete can delete a workout there), so a local
  ledger would drift and lie. `push.json` is kept as an audit receipt but makes no decision.
  Because `publish` is already on the transport side of the seam, querying the account for
  idempotency does not widen the bend.

- **Partial failure is handled by idempotent retry, not a compensating rollback.** If
  `schedule` fails after a successful `upload`, `publish` records what succeeded, exits
  non-zero, and a re-run recognises the orphaned library workout and completes only the
  missing schedule. A transactional rollback (deleting the orphan on error) was rejected: a
  compensating *write* issued in reaction to a network error is itself a write that can fail,
  and deleting in response to a signal whose cause is unknown is exactly the destructive
  reflex to avoid. The orphan is harmless (it never reaches the watch without a schedule) and
  is cleaned up by the next successful push or `--replace`.

- **Ship run-only; strength/HIIT is a documented spike, not a feature.** v1 authors and
  pushes `sport: run` (including run-dominant Hyrox). `sport: hiit | strength` are schema-valid
  so the contract will not churn, but `author` answers that they await the spike. The spike is
  a manual probe in `scratch/` that hand-builds a `STRENGTH_TRAINING`/`HIIT` payload and
  records whether the create endpoint accepts it - its deliverable is *knowledge*, not a
  working `sport: strength`. This isolates the one genuinely unverified surface (a private,
  undocumented endpoint) from the shippable run deliverable, and matches the PROJECT.md DoD,
  which asks for the spike *outcome* to be documented rather than for strength push to work.

## Consequences

- The system gains an outbound transport path for the first time. The bend to the golden rule
  is real but contained to `publish.py`, which is isolated exactly like `client.py`/`sync.py`;
  the read path is untouched and still never writes.
- The intelligence of the phase lives entirely in the pure `author` seam, so it has fast,
  deterministic, offline coverage; `publish` is thin orchestration tested against a fake
  transport, with a single manual live-push acceptance step kept out of CI.
- Idempotency depends on a Garmin list call per push and on the `GC` naming prefix; a workout
  the athlete renames in Connect would defeat the match (accepted - renaming a system workout
  is a deliberate act).
- If the strength/HIIT spike succeeds, the production implementation is a follow-up (expected as
  a GitHub issue / Phase 11b), not part of this phase.
- Writing to the account is near-irreversible by nature; the confirm interlock, the on-disk spec
  handoff, and the account-of-record idempotency are the safeguards, and the automation path
  never touches either command.
