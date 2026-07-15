# 03 - `avoid[]` from movement/muscle stacks

Status: ready-for-agent
Blocked by: 02
Sources: `docs/prd/phase-10-recommender/PRD.md` (The avoid-list). Deps: Phase 8
(`PATTERN_STACK`, `MUSCLE_OVERLAP`).

## Goal

Tell the athlete which movement patterns and muscle groups to keep off tomorrow, drawn only
from the real stacks the Phase 8 signals already computed - no invention.

## Scope

- **Populate `avoid[]`** in the recommendation from the keys the Phase 8 signals carry:
  `PATTERN_STACK.facts.keys` and `MUSCLE_OVERLAP.facts.keys` (comma-joined strings). Split,
  de-duplicate, and sort into a flat list of keys. Empty list when neither signal fired.
- **No niggle mapping.** An active niggle is already handled by the `NIGGLE_REDUCED_MODE`
  composition row (global downgrade + citation in ticket 02); its `facts.body_part` stays in
  the digest for the coach skill to name in prose. The recommender does **not** map a body
  part to a movement pattern (deferred; see PRD Out of Scope).

## Tests (`test_recommend.py`)

- `PATTERN_STACK` keys `"hinge,squat"` -> `avoid == ["hinge", "squat"]`.
- `MUSCLE_OVERLAP` keys merge with pattern keys, de-duplicated and sorted.
- Neither signal present -> `avoid == []`.
- An active `NIGGLE_REDUCED_MODE` with no stack signal -> `avoid == []` (no fabricated
  pattern), while the niggle still downgrades the type (unchanged from ticket 02).

## Done when

- The recommendation names the stacked patterns/muscles to avoid tomorrow when Phase 8
  flags them, and an empty list otherwise.
- `task check` green.
