# 07 - Strength/HIIT push spike + documented outcome

Status: ready-for-agent
Blocked by: 03
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Out of Scope; strength spike), ADR 0013.

## Goal

Find out - without building a feature - whether Garmin will accept a system-authored
strength or HIIT workout. The deliverable is knowledge, not a working `sport: strength`.
Non-blocking: does not gate the run DoD.

## Scope

- **Manual probe in `scratch/`** (exempt from docstring/lint gate): hand-build a
  `STRENGTH_TRAINING` (id 5) or `HIIT` (id 9) workout payload, call `upload_workout()`
  with raw JSON, observe whether the endpoint accepts it and whether it appears on the
  account/watch.
- **No production strength seam code** in this phase regardless of outcome.
- **Record the outcome**:
  - endpoint works -> ADR note + a follow-up GitHub issue for Phase 11b (production
    strength push), outside this phase;
  - endpoint rejects / does not display -> documented Runna-style fallback: strength
    stays local (spec + report, watch-free), and `author` keeps returning the deferred
    answer for `sport: strength`.

## Acceptance criteria

- [ ] A hand-built strength/HIIT payload is probed against `upload_workout()`.
- [ ] The outcome (works / fallback) is documented (ADR note or GitHub issue).
- [ ] No production strength code added to `src/`.
