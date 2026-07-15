# 01 - Author core: easy run + CLI + date guards + target degradation

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-11-workout-push/PRD.md`, ADR 0013. Deps: Phase 6
(`athlete_zones`), Phase 10 (recommendation block).

## Goal

Establish the whole pure authoring path end-to-end for the simplest session type. A
tracer bullet: `garmin-coach author --date D --from-recommendation` turns an `easy`
recommendation into a `workout_spec` on disk. Everything downstream (structure
templates, athlete requests, transport) builds on the contracts this ticket lands.

## Scope

- **New `author.py` (pure, offline).** `author(request, context) -> workout_spec` plus
  `to_garmin(spec) -> dict`. No DB access, no network. Context carries the digest's
  signals and `athlete_zones`.
- **Domain types**: `workout_request` (`sport`, `origin`, target date, session type,
  optional explicit structure) and `workout_spec` (ordered steps; each step ended by
  `time`/`distance`, target `pace_band`/`hr_band`/`none`; `warnings[]`). Use the
  glossary Authoring terms vocabulary.
- **Easy expansion**: single `work` step, default 45 min, target = Z2 ceiling.
- **Target degradation** (applies from here on): (1) regression-measured pace ->
  `pace_band`; (2) no measured pace but HR bounds -> `hr_band` + warning "no measured
  pace, targeting by heart rate"; (3) neither -> `none` + warning "no target,
  time/distance only".
- **Date guards**: past date -> hard refuse (non-zero exit); today -> allowed with a
  spec warning.
- **`rest`** -> no spec, exit 0 with "nothing to author".
- **`to_garmin(spec)`** for a single-step run: well-formed Garmin typed JSON
  (`RunningWorkout` shape), assert on structure not a live account.
- **CLI**: `author --date D --from-recommendation` writes `reports/{date}/workout.json`,
  prints the spec + warnings. Follows the existing `set_defaults(func=...)` pattern.

## Acceptance criteria

- [ ] `author` on an `easy` recommendation produces a one-step spec with a Z2 target.
- [ ] Target degrades pace -> HR -> none with the right warning at each step.
- [ ] Past date refused (non-zero); today warned; `rest` produces no spec (exit 0).
- [ ] `to_garmin()` yields well-formed run typed JSON (asserted on shape).
- [ ] `workout.json` written under `reports/{date}/`; spec + warnings printed.
- [ ] All author tests offline (Seam 1), golden-regression style.
