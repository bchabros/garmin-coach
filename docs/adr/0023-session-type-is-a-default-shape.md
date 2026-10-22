# ADR 0023 - A run session type is a default shape, not a permitted shape

## Status

Accepted

## Context

Session-type restrictions prevented reasonable combinations of running steps.

## Decision

- Every run type offers warmup/work/recovery/rest/cooldown and repeats. Type defines
  default shape/targets, not which roles are allowed; any role key summons that role.
- Use fixed role order. Summoned warmup/cooldown default to ten minutes, recovery/rest
  to two; work length remains type-specific.
- Explicit pause replaces the default. Refuse simultaneous recovery/rest and a pause
  without explicit or default repeats. Standing-rest role differs from a rest-day type.
- `work_min` applies across run types; easy `duration_min` remains supported but conflicts
  with it. #103 explicitly retains this input compatibility.
- List-shaped exercise/station requests remain separate from the run-role table.
  Exercise/station edge support subsequently shipped (#65). Removing a default role
  still has no implicit omission syntax.

## Consequences

A session name does not determine its measured difficulty (ADR 0024). Existing
requests preserve defaults while explicit structure composes the desired session.
