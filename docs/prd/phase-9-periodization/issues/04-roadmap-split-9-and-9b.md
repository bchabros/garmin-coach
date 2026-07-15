# 04 - Split Phase 9 / 9b in the roadmap

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-9-periodization/PRD.md` (Scope split, Out of Scope).
ADR: `docs/adr/0012-phase-9-race-date-periodization.md`.

## Goal

`docs/PROJECT.md` still promises that Phase 9 delivers `race_plan`. It does not, and that
was a deliberate decision, not an oversight. Make the roadmap say what the system actually
is - so the next reader (or agent) does not build against a stale brief.

## Scope

- **Status table**: Phase 9 becomes race-date periodization only. Add a **Phase 9b** row
  for race-day pacing (`race_plan`), status Planned.
- **Phase 9 section**: strip `race_plan`, the per-segment Hyrox targets, and the fueling
  note; point the DoD at what this phase actually ships (blocks, `weeks_to_event`,
  `TAPER_ACTIVE`, `RACE_PROXIMITY`). Correct the `periodize` signature (no `history`) and
  the block vocabulary (deload is a flag, not a fifth block).
- **New Phase 9b section**: `race_plan`, deferred. Record *why*, because it is the
  surprising part: the goal race is HYROX **Doubles**, where the running is shared with a
  partner and the stations are split by a strategy the database cannot know; and the
  athlete's 1:01:46 reference race predates `data_start`, so no segment splits exist
  anywhere. Name its real blockers: regression-backed zones, the partner's capability, and
  the pair's station split. It is scoped to run close to race day, when the information
  actually exists.
- **Ordering / dependency diagram**: Phase 10 gates on Phase 9's `block`, **not** on
  `race_plan`. 9b hangs off Phase 6 (threshold pace) and 6b (snapshot), not off 10.
- Cross-reference ADR 0012 from both sections.
- Use the vocabulary from the "Periodization terms" section of `docs/glossary.md`
  throughout; do not reintroduce "week intent".

## Tests

None - documentation only. `test_agents_mirror.py` guards the `CLAUDE.md` / `AGENTS.md`
mirror, which this ticket does not touch.

## Done when

- The status table lists Phase 9 (periodization) and Phase 9b (race-day pacing) as separate
  rows with honest statuses.
- No section of `docs/PROJECT.md` claims Phase 9 ships `race_plan`.
- The reason for the split is recorded where a reader will find it, not only in the ADR.
- `task check` green.
