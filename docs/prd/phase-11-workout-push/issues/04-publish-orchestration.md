# 04 - Publish orchestration: create + schedule, idempotency, confirm, receipt

Status: ready-for-agent
Blocked by: 01
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Push semantics; CLI contract), ADR 0013.

## Goal

Establish the whole transport path end-to-end against a fake publisher: read a spec,
create + schedule the workout on the account, and prove idempotency and the confirm
interlock - all without touching the live account. The transport tracer bullet.

## Scope

- **New `publish.py` (transport, out-of-seam).** Reads `reports/{date}/workout.json`,
  orchestrates account calls through an injected `WorkoutPublisher` protocol
  (`list_workouts`, `upload`, `schedule`, `unschedule`, `delete`). `author.py` never
  imports `publish.py`.
- **Atomic create+schedule**: upload -> take `workout_id` -> schedule to `--date`.
- **Naming**: stable `GC` prefix (e.g. `GC 2026-07-17 tempo`).
- **Account-of-record idempotency**: list the account's `GC` workouts, match by
  name+date. Identical payload -> no-op; different -> requires `--replace` (ticket 05);
  library-only -> schedule; absent -> create.
- **Confirm interlock**: `push --date D [--confirm]`. Absence of `--confirm` is always a
  dry-run (print payload + diff + warnings, exit). Sends only with `--confirm`. No
  `--dry-run` flag.
- **Activity-collision warning**: target day already has a logged activity in core
  `activities` -> warn (not a block).
- **Receipt**: write `reports/{date}/push.json` (`workout_id`, `schedule_id`, payload
  hash, timestamp).
- **CLI**: `push` subcommand, `set_defaults(func=...)` pattern.

## Acceptance criteria

- [ ] Dry-run (no `--confirm`) prints payload + warnings and writes nothing to the account.
- [ ] `--confirm` uploads then schedules atomically; `push.json` records both ids.
- [ ] Re-push of an identical workout is a no-op (matched by `GC` name+date via list).
- [ ] Library-only match schedules without re-uploading.
- [ ] Activity collision on the date warns (not a block).
- [ ] Orchestration tested against a fake `WorkoutPublisher` (Seam 2) - no live calls.
