# 03 - Duration estimate + canonical fixture + docs + coach skill

Status: ready-for-agent
Blocked by: 01, 02
Sources: `docs/prd/phase-11a-custom-run-authoring/PRD.md`, `docs/glossary.md`
(structure override), ADR 0013.

## Goal

Close the loop end to end: the athlete's exact tempo sentence authors correctly, reads
sensibly in Connect, and is documented so Cowork can build the request from natural
language. A tracer bullet through the estimate, one canonical fixture, the docs, and the
coach skill.

## Scope

- **Pace-aware estimate.** `_estimated_duration` still sums time-ended steps, plus: a
  **distance** step **with a pace band** contributes `metres/1000 * midpoint_pace`
  (midpoint of the band); a **lap** step, or a distance step with an HR/none target,
  contributes 0. Repeat groups multiply by `reps`, as today. Explicitly approximate;
  Garmin recomputes on device.
- **Canonical fixture.** Add the tempo example as a request fixture in the repo (lap
  warmup/cooldown, `8x1km` @ `[220, 240]`, 2:00 recovery) - the reference Cowork and the
  tests share.
- **End-to-end test.** The fixture expands to the expected spec and typed JSON, with a
  sensible (non-trivial) estimated duration.
- **Docs.** Add the request schema and the worked tempo example to `docs/OPERATIONS.md`
  and/or `docs/DEVELOPMENT.md`, alongside the existing author/push runbook. (Glossary terms
  already landed during grilling.)
- **Coach skill.** Add a concise "Authoring a custom workout" section to
  `skills/coach/SKILL.md`: mapping natural language to the `structure` block (roles, ends,
  pace band, `reps`), the tempo example, and the reminder that `push` is confirm-gated.

## Acceptance criteria

- [ ] The tempo fixture authors end to end to the expected spec + typed JSON.
- [ ] The estimate is sensible for the tempo (distance reps counted via band midpoint;
      lap warmup/cooldown contribute 0), not the near-zero a time-only sum would give.
- [ ] The canonical request fixture exists in the repo and is exercised by a test.
- [ ] `docs/OPERATIONS.md`/`docs/DEVELOPMENT.md` document the `structure` schema with the
      worked example; `skills/coach/SKILL.md` has the "Authoring a custom workout" section.
- [ ] `task check` green (tests, lint, docstrings, mypy); `publish` and CLI untouched.
