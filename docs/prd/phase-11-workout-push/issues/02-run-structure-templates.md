# 02 - Run structure templates: tempo + quality intervals

Status: ready-for-agent
Blocked by: 01
Sources: `docs/prd/phase-11-workout-push/PRD.md` (Structure expansion), ADR 0013.

## Goal

Extend `author` from the single-step easy case to the full run structure vocabulary,
so tempo and interval sessions author into real multi-step workouts with warmup,
work, recovery, cooldown and repeat groups.

## Scope

- **`tempo`** -> `warmup 10min + work 20min @ threshold band + cooldown 10min`.
- **`quality`** -> a conservative default interval set (e.g. `4x3min @ threshold`
  with 2 min recovery), wrapped in warmup + cooldown. Modelled with a
  `repeat(n, [steps])` step.
- **`to_garmin(spec)`** gains repeat-group translation (`RepeatGroup` with nested
  executable steps) and threshold `pace_band` / `hr_band` targets on work steps.
- Threshold-band targets reuse the same degradation ladder from ticket 01.

## Acceptance criteria

- [ ] `tempo` produces warmup + work@threshold + cooldown.
- [ ] `quality` produces warmup + a repeat group of work+recovery + cooldown.
- [ ] `to_garmin()` translates repeat groups into well-formed nested Garmin JSON.
- [ ] Threshold targets degrade pace -> HR -> none like the easy case.
- [ ] Tests offline (Seam 1), covering each type's step shape.
