# 02 - Exercise-pattern map (core seed)

Status: ready-for-agent
Blocked by: 01
Sources: `docs/prd/phase-8-movement-overlap/PRD.md` (Movement-pattern map section).

## Goal

Add the hand-maintained lookup that turns a Garmin `subcategory` into a movement
`pattern` and a `muscle_group`. This is core reference data seeded in the schema, not
a Garmin-written table and not a mart.

## Scope

- **New table `exercise_pattern`** in `src/garmin_coach/schema.sql`, mirrored to
  `docs/schema.sql`. Columns: `subcategory` (PK), `pattern`, `muscle_group`.
- **Seed rows** via `INSERT OR IGNORE`, using the real `subcategory` values from the
  ticket-01 fixtures. Taxonomy:
  - `pattern in {push, pull, hinge, squat, carry}` - the five clean movement patterns.
  - `muscle_group in {chest, back, posterior, quads, shoulders, grip, core, ...}` -
    **grip lives here**, not as a sixth pattern (deliberate deviation from PROJECT.md).
- Keep the seed block grouped and commented like `coach_thresholds` / `plan_template`,
  so the map is editable without touching Python and is visible in migrations.

## Tests (`test_schema_sync.py`)

- `docs/schema.sql` stays byte-identical to the package copy after the new table and
  seed rows (existing guard).

## Done when

- `exercise_pattern` exists with seed rows covering every `subcategory` seen in the
  fixtures; mirror guard green.
- `task check` green.

## Notes

Grip on the muscle axis is intentional: carries and pulls both tax grip, so it is a
muscle group, not a movement pattern. New/unseen `subcategory` values are handled by
ticket 03 (excluded + surfaced), not auto-classified here.
