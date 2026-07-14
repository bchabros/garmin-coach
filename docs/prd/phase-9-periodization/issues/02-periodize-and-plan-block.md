# 02 - `periodize()` + the `plan_block` mart

Status: ready-for-agent
Blocked by: 01 (needs `goal_event` + `anchor_event`)
Sources: `docs/prd/phase-9-periodization/PRD.md` (Block model, Planned deload, Mart:
`plan_block`, Pure periodization function, Thresholds).
ADR: `docs/adr/0012-phase-9-race-date-periodization.md`.

## Goal

The heart of the phase. Every week - past *and future* - gets a `block`, a
`weeks_to_event`, and an `is_deload` flag, counted back from the anchor event. All the
logic lives in one pure function; the mart is a thin materialization of it.

## Scope

- **Pure `periodize(event, today, thresholds) -> list[WeekPlan]`.** No database, no wall
  clock, no training history. The `PROJECT.md` sketch's `history` argument is deliberately
  dropped - blocks are a countdown from the race date and the deload cadence counts back
  from block ends, so history contributes nothing and would only cost determinism. Same
  shape as `zones.compute` / `snapshot.build`.

- **Four block labels: `base | build | peak | taper`.** Deload is *not* a block.
  - `taper`, `peak`, `build` have fixed lengths from thresholds, counted back from the
    anchor's race week (the Monday-anchored week containing the event date).
  - `base` absorbs everything earlier, left-bounded by `data_start`, so the athlete is
    always in *some* block however distant the race.
  - `weeks_to_event` = whole weeks from a week's Monday to the race week; `0` in the race
    week itself.

- **`is_deload`: planned recovery weeks.** Every `deload_every_n_weeks` week counted **back
  from the end of its block**, inside `base` and `build` only. Never in `peak` or `taper`
  (a taper is a downshift already). Block-end anchoring is the decision and it is load-
  bearing: it guarantees the athlete always enters the next block fresh. Do not replace it
  with a modulo counter over the calendar - both modulo variants were checked against the
  real calendar and land a deload either in the first week of base or in the last week of
  peak, directly before the taper.

- **`is_deload` does not interact with `DELOAD_ADVISED`.** They are two independent answers
  to one question: `is_deload` is what the plan intended, `DELOAD_ADVISED` (Phase 5) is
  what the actual load did. Write **no** arbitration rule. The divergence is the finding.

- **Mart `plan_block`.** One row per week keyed by `week_start` (Monday), carrying `block`,
  `weeks_to_event`, `is_deload`, and the anchoring event's id. It spans the whole plan
  horizon **including future weeks** out to the race week - which is why it is a separate
  mart and not two columns on `weekly_metrics` (whose rows only cover weeks that already
  happened; the row sets are disjoint). Add to `src/garmin_coach/schema.sql` and the
  `docs/schema.sql` mirror.

- **`periodize.rollup(conn, *, data_start_date, through_date)`** wired in **first** in the
  `features` tail, **ahead of `weekly.rollup`** - which copies each week's block from
  `plan_block`, so the plan must already be fresh. (Corrects an earlier draft of this
  ticket that said "after `weekly`": the plan depends only on the goal events, so nothing
  gates it, and every mirror of a block must run after it.) Safe to drop and rebuild, like
  every other mart.

- **No anchor -> empty plan.** `plan_block` is empty; nothing is invented.

- **No `week_intent` column.** It would be a pure function of `block`. What a block *means*
  for training is policy in code. The word "intent" stays reserved for `plan_template`'s
  daily category.

- **Thresholds** seeded in `coach_thresholds` and `thresholds.DEFAULTS`: `taper_weeks = 2`,
  `peak_weeks = 3`, `build_weeks = 5`, `deload_every_n_weeks = 4`.

## Tests (`test_periodize.py` new, `test_features.py`)

Golden tests over frozen dates carry this phase - `periodize` is pure, so nearly all of it
is reachable without a database.

- Block labels: deep in `base`, mid-`build`, `peak`, a `taper` week, the race week itself.
- An event far enough out that `base` stretches well beyond its nominal length.
- `is_deload` placement: correct weeks in `base` and `build`; **none** in `peak` or
  `taper`; a deload always lands in the last week of a block.
- `weeks_to_event = 0` in the race week.
- Empty plan for: no events at all; only a `tentative` event; only a past event.
- A `tentative` B event nearer than the `confirmed` A event -> the A event still anchors.
- `features` runs `periodize.rollup` in the tail in the right order, and a re-run is
  idempotent.

## Done when

- With the athlete's Hyrox recorded, `garmin-coach features` fills `plan_block` from
  `data_start` out to the race week.
- For the design-time calendar the rules reproduce: `base` 2026-06-08..08-03 (deloads
  07-06, 08-03), `build` 08-10..09-07 (deload 09-07), `peak` 09-14..09-28, `taper`
  10-05..10-12.
- Deleting the event and re-running leaves `plan_block` empty.
- `task check` green.