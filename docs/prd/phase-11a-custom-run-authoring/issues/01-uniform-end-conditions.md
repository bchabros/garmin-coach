# 01 - Uniform end condition per role (lap + distance reachable from a request)

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-11a-custom-run-authoring/PRD.md`, `docs/glossary.md`
(end condition), ADR 0013. Deps: Phase 11 (`author.py`, `to_garmin`).

## Goal

Make every step's end condition expressible from a `workout_request`. A tracer bullet:
`author` can turn an athlete request with `warmup_end: "lap"`, `work_end:
{distance_m: 1000}`, `recovery_end: {min: 2}`, `cooldown_end: "lap"` into a
`workout_spec` whose steps carry the right ends, and `to_garmin` encodes them - including
the one genuinely new Garmin branch, the lap button.

## Scope

- **End resolution helper.** Replace the minutes-only `_mins` with a helper that turns a
  role's `structure` entry into a spec `end` descriptor: `{"min": N}` -> `{"type":
  "time", "seconds"}`, `{"distance_m": N}` -> `{"type": "distance", "metres"}`, `"lap"`
  -> `{"type": "lap"}`. Wire it into `_expand` for all roles (`warmup`/`work`/`recovery`/
  `cooldown`).
- **Back-compat.** The existing keys `warmup_min`, `work_min`, `recovery_min`,
  `cooldown_min`, `duration_min` (easy) still work, each equivalent to `{"min": N}`. A
  `*_end` and its matching `*_min` for the same role together is ambiguous -> `ValueError`.
- **`to_garmin` lap branch.** Add `lap` to the end-condition application:
  `ConditionType.LAP_BUTTON` (`lap.button`), no end value. The `time`/`distance` branches
  are unchanged (distance already exists and is tested).
- **Validation (`_validate_structure`, new, hand-rolled `ValueError`).** Allowed roles;
  exactly one end per role (no `*_end`+`*_min` clash); `distance_m` a positive integer;
  `min` positive; `lap` refused for `work` with a clear message. Consistent with the
  existing `_validate_request`; no formal JSON-schema.

## Acceptance criteria

- [ ] A lap-ended `warmup`, `cooldown`, and `recovery` each produce a `{"type": "lap"}`
      spec end; a `work` step accepts `{"distance_m": 1000}` and produces a distance end.
- [ ] `to_garmin` encodes a `lap` end as a `ConditionType.LAP_BUTTON` executable, and a
      distance end as `ConditionType.DISTANCE` (extends the existing distance test).
- [ ] `lap` on a `work` step raises `ValueError`; `*_end` + matching `*_min` clash raises
      `ValueError`; non-positive `min`/`distance_m` raise `ValueError` - each message names
      the role/field.
- [ ] Existing `*_min` / `duration_min` requests are unchanged (back-compat).
- [ ] All new coverage offline on Seam 1 (`tests/test_author.py`); `publish` and CLI
      untouched, their tests still green.
