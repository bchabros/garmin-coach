# 05 - Publish: --replace + partial-failure idempotent retry

Status: ready-for-agent
Blocked by: 04
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Push semantics), ADR 0013.

## Goal

Make deliberate edits and flaky networks safe: a different workout for the same day
replaces cleanly only when asked, and a push that half-fails is fixable by simply
running push again.

## Scope

- **`--replace`** = `unschedule + delete + upload + schedule` (the library has no
  in-place update). Only path that overwrites a different-payload match.
- **Partial-failure retry, not rollback**: if `schedule` fails after a successful
  `upload`, write `push.json` with `schedule_id: null`, exit non-zero with a clear
  message. A re-run recognises the library workout (name+date, identical payload),
  skips the upload, and completes only the missing schedule. No compensating delete
  on error.

## Acceptance criteria

- [ ] `--replace` unschedules + deletes the old, uploads + schedules the new.
- [ ] Different-payload match without `--replace` refuses and says so.
- [ ] Simulated schedule failure leaves `schedule_id: null` and a non-zero exit.
- [ ] Re-run after that failure skips upload and completes the schedule (idempotent).
- [ ] Tested against the fake `WorkoutPublisher` (Seam 2) - no live calls.
