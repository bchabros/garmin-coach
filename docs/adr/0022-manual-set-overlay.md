# ADR 0022 - A hand-logged set overlay makes Hyrox circuits visible to the movement mart

## Status

Accepted

## Context

Garmin records some Hyrox circuits as one UNKNOWN set, hiding their movement load.

## Decision

- Manual stations are durable core observations written only by explicit local tools,
  never ETL. They survive imports and mart rebuilds; captured rows remain unchanged.
- Manual rows wholly supersede captured rows per activity through `movement_sets`,
  whose source metadata keeps overlap and coverage on one definition. Row-level merging
  would double-count the captured pseudo-set.
- Equal set-share means one row per station, not rounds times stations. Optional
  reps/sets/time/weight are record-only, not weights in the overlap calculation.
- Normalize names to screaming snake case, accepting only letters/digits/underscores.
  Unknown mapping names remain accepted and visible as coverage drift.
- Re-log replaces the entire manual station set; empty lists refuse rather than clear.
  Recompute from the activity date. A single circuit alone creates no adjacent-day stack.
- Reference-map judgments: SPANISH_SQUAT/BOX_STEP_OVER -> squat/quads; SKI_ERG ->
  hinge/posterior; BATTLE_ROPE -> no pattern/shoulders; V_UP -> no pattern/core.

## Consequences

Observed station descriptions become usable without transport or fake captured
exercise names. Removing the overlay can reveal later improved Garmin captures;
no implicit destructive clear is introduced.
