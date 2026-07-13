# 04 - Overlap signals + thresholds + report surface

Status: ready-for-agent
Blocked by: 03
Sources: `docs/prd/phase-8-movement-overlap/PRD.md` (Signals + Thresholds sections).

## Goal

Turn the `pattern_overlap` mart into coach-facing warnings and expose the coverage of
the movement map, with tunable thresholds. Close the phase DoD.

## Scope

- **Two new `coach_thresholds` keys** seeded in `schema.sql` (mirrored) and added to
  `DEFAULTS`: `pattern_load_floor = 20`, `pattern_overlap_high = 40`. Placeholders,
  noted to tune as `activity_sets` history grows.
- **Two new digest signal functions** in `signals.py`, both severity `warn`, reading the
  `pattern_overlap` mart live (like `deload_advised` reads `weekly`):
  - `PATTERN_STACK` - `dim = 'pattern'`; fires when any key has
    `overlap >= pattern_overlap_high` on the latest day of the window. Facts (flat
    scalars): `keys` (offending, sorted), `overlap_max`, `date`.
  - `MUSCLE_OVERLAP` - identical on `dim = 'muscle'`.
- **Coverage fact.** The digest carries `sets_total`, `sets_unmapped`, and the sorted
  unmapped names (computed in ticket 03), surfaced as a fact in `report.md`.
- **Report surface.** Both signals join the existing signal list in `report.md`; no new
  section, no new chart this phase.

## Tests (`test_digest.py`, `test_thresholds.py`)

- `PATTERN_STACK` / `MUSCLE_OVERLAP` fire on a constructed adjacent-day stack at/above
  `pattern_overlap_high` with flat facts naming the keys.
- Both silent below the threshold and when a rest day clears the stack.
- The coverage fact reports unmapped sets.
- The two new threshold keys are present with their defaults.

## Done when (phase DoD)

- `activity_sets` populated on backfill (ticket 01); overlap metric in the mart
  (ticket 03); `PATTERN_STACK` / `MUSCLE_OVERLAP` fire on a constructed stack.
- `task check` green.

## Wrap-up (with this ticket)

- Write `docs/adr/0011-phase-8-movement-overlap.md` capturing the grip-as-muscle and
  daily-only deviations and the set-share load-distribution decision.
- Add glossary terms: *movement pattern*, *muscle group*, *pattern_load*,
  *pattern overlap*, `PATTERN_STACK`, `MUSCLE_OVERLAP`.
- Flip PROJECT.md's Phase 8 status row to Done and point it at this PRD.
