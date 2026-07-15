# 06 - Real write-wrapper over garminconnect + manual live-push acceptance

Status: ready-for-agent
Blocked by: 04, 05
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Transport / auth; Testing - live step),
ADR 0013.

## Goal

Wire the orchestration to the real Garmin account and complete the DoD's one confirmed
live push, verified on the watch. This is where the outbound surface is actually
connected.

## Scope

- **Real `WorkoutPublisher` implementation** wrapping `client.login()` (token cache,
  MFA, retry-on-expired) and garminconnect's `upload_workout`, `schedule_workout`,
  `unschedule_workout`, `delete_workout`, `get_workouts`. Kept separate from the
  read-side `GarminTransport`.
- **Manual live-push acceptance step** (outside `pytest`): a one-time
  `push --confirm` on a real date, verified on the account and watch - exactly one
  scheduled run workout, re-push a no-op. Documented in `docs/OPERATIONS.md` as an
  acceptance step, not in CI.

## Acceptance criteria

- [ ] Real wrapper authenticates via `client.login()` and implements the protocol.
- [ ] A confirmed live push creates exactly one scheduled run workout on the account.
- [ ] Re-running the same push is a no-op on the live account.
- [ ] `docs/OPERATIONS.md` documents the live-push acceptance step and confirm interlock.
- [ ] No live calls added to `pytest`.
