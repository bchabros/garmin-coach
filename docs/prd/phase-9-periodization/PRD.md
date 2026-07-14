# PRD - Garmin Coach - Phase 9: race-date periodization

> Status: Ready for implementation (TDD) - Date: 2026-07-14
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 9, `docs/glossary.md` (Periodization terms),
> grilling decisions 2026-07-14.
> ADR: `docs/adr/0012-phase-9-race-date-periodization.md` (accepted, written alongside
> this PRD).

## Problem Statement

The system reads the past and says nothing about what the athlete is training *for*.
`plan_template` is a static weekly pattern with no notion of a race date, a training
block, or a taper, so every reading is made in a vacuum: a week 20 weeks from the goal
race and a week 3 weeks out are treated identically. The athlete has a concrete goal -
HYROX Doubles Men Pro in mid-October 2026, chasing sub-1:00:00 after a 1:01:46 - and a
possible half-marathon in early September, and the engine cannot express either. Nothing
tells the athlete which block they are in, when the taper starts, or when the plan says
to take a recovery week. Phase 10's recommender is blocked on all of it: without a block
it can only advise from today's readiness, never from where today sits in a cycle.

## Solution

Give the system a goal and a countdown. The athlete records their goal events; a pure
periodization function counts training blocks back from the anchor race and materializes
one row per week - past *and future* - into a new `plan_block` mart. Every week gains a
`block` (`base | build | peak | taper`), a `weeks_to_event`, and an `is_deload` flag for
recovery weeks the plan prescribes. The digest and report gain two facts: `TAPER_ACTIVE`
when the current week is a taper week, and `RACE_PROXIMITY` when a race is near - nagging
the athlete to commit to a tentative race and to pin an approximate date before the taper
window depends on it. The `athlete_status` snapshot stops showing NULLs where Phase 6b
left placeholders.

Crucially, this phase makes the plan *legible*, not prescriptive. Blocks annotate the
athlete's own weekly template; they never generate a day-by-day plan. And what the plan
prescribed (`is_deload`) is kept strictly apart from what the data observed
(`DELOAD_ADVISED`) - the divergence between them is the finding, not a conflict to
arbitrate.

The race-day pacing plan (`race_plan`) is deliberately **not** part of this phase; see
Out of Scope.

## User Stories

1. As an athlete, I want to record my goal race with its date, so that the system knows
   what I am training for.
2. As an athlete, I want to record more than one race at once, so that a tune-up event
   and my main goal can coexist without me choosing between them.
3. As an athlete, I want to mark a race A, B, or C, so that a secondary race does not
   reshape the plan built around my main goal.
4. As an athlete, I want to say that I am *committed* to a race whose exact day I do not
   yet know, so that the system still counts my blocks back from it.
5. As an athlete, I want to say that a race has a *known date* but that I have not
   decided whether to start, so that the system does not taper me for a race I may skip.
6. As an athlete, I want to see which training block I am in right now, so that I can
   interpret my week against the cycle rather than in isolation.
7. As an athlete, I want to see how many weeks remain to my goal race, so that I feel the
   countdown and can judge whether my training matches it.
8. As an athlete, I want the plan to tell me in advance which weeks are recovery weeks, so
   that I can arrange life around them instead of discovering them by burning out.
9. As an athlete, I want to enter each block fresh, so that a recovery week always lands
   before the step up in intensity rather than at a random point in the calendar.
10. As an athlete, I want to know when my taper has started, so that I stop adding load in
    the weeks that decide my race.
11. As an athlete, I want a warning as my race approaches, so that a race three weeks out
    is never a surprise in my weekly report.
12. As an athlete, I want the report to ask me to pin an approximate race date as the
    taper nears, so that my taper is not planned a week off my actual race.
13. As an athlete, I want the report to ask me to decide about a tentative race, so that
    an undecided event does not silently sit in my plan forever.
14. As an athlete, I want to update a race's date and status as my plans firm up, so that
    the plan follows reality without me editing the database by hand.
15. As an athlete, I want to list my recorded races and see the countdown to each, so that
    I can check what the system believes before I trust its advice.
16. As an athlete, I want to always be in some block, even when my race is far away, so
    that the system never leaves me in an unlabeled limbo.
17. As an athlete, I want the system to say plainly that it does not know what I am
    training for once my race has passed, so that it does not keep counting down to a race
    I have already run.
18. As an athlete, I want my planned recovery weeks to stay separate from the system's
    load-based deload warning, so that I can see when I overreached *beyond* what the plan
    intended.
19. As an athlete, I want my own weekly template left untouched, so that the engine
    annotates my training rather than replacing it.
20. As an athlete, I want my past weeks labeled with the block they fell in, so that I can
    review a completed week in the context of the cycle.
21. As an athlete, I want to record my target time as a real number, so that a later phase
    can split it across race segments instead of parsing my prose.
22. As an athlete, I want my current standing to show my block, my countdown, and whether
    I am tapering, so that "where do I stand" answers the question in one place.
23. As a coach agent, I want the digest to carry the current block and countdown, so that
    my narrative can anchor advice to the phase of the cycle.
24. As a coach agent, I want the taper stated as a signal, so that a later phase can key
    intensity suppression off it without re-deriving the calendar.
25. As a developer, I want the block calendar computed by a pure function of the event and
    the date, so that I can pin its behavior with golden tests instead of a database.
26. As a developer, I want the periodization mart rebuilt from core like every other mart,
    so that I can drop and rebuild it without fear.

## Implementation Decisions

All decisions below are recorded and justified in ADR 0012; this section states them as
build instructions. Domain vocabulary follows the "Periodization terms" section of
`docs/glossary.md`.

### Scope split

Phase 9 delivers periodization only. `race_plan` (per-segment race-day pacing) is deferred
to a future Phase 9b. This is a deliberate deviation from the `PROJECT.md` Phase 9 DoD:
the goal race is HYROX **Doubles**, where the running is shared with a partner and the
stations are split by a strategy the database has no way to know, and the athlete's
reference race predates `data_start` so no segment splits exist. `PROJECT.md`'s status
table and Phase 9 section must be updated to reflect the split.

The coaching model optimizes **the athlete, not the team**: no partner load, no shared
readiness, no partner threshold pace anywhere in this phase.

### Core: `goal_event`

A new core table, manually-written ground truth (like `session_rpe` / `niggle`), never
sourced from Garmin. It carries the event date, a `type` (`hyrox | run_race`), a
`priority` (A/B/C), a target time in **seconds** (nullable), a free-text note, and two
independent uncertainty axes:

- `status` (`confirmed | tentative`) - *whether the athlete will start*. Only `confirmed`
  events may anchor blocks or fire `TAPER_ACTIVE`.
- `date_precision` (`exact | approx`) - *whether the exact day is known*. Suppresses
  nothing; an `approx` date drives every block exactly as an `exact` one does. It only
  makes `RACE_PROXIMITY` ask for the date to be pinned.

Collapsing these into one column is the error this design exists to avoid: the athlete's
Hyrox is committed with a fuzzy date while the half-marathon is dated but uncommitted, and
a single `status` column would mark the Hyrox `tentative` and walk the athlete into their
A race untapered.

The athlete's own events are **not** seeded into `schema.sql` - a race calendar is personal
data, not repo content.

### Anchoring rules

- The **anchor event** is the nearest *upcoming* event with `priority = 'A'` and
  `status = 'confirmed'`. Nothing else anchors, regardless of proximity.
- With no anchor event (including the evening after the goal race), `block` and
  `weeks_to_event` are `NULL`, `plan_block` is empty, and `TAPER_ACTIVE` never fires. The
  system states that it does not know what is being trained for rather than counting down
  to a race in the past.
- Past events remain in `goal_event` as history; they never anchor.

### Block model

Four labels only: `base | build | peak | taper`. Deload is **not** a block.

- `taper`, `peak`, and `build` have fixed lengths taken from thresholds, counted back from
  the anchor event's race week (the Monday-anchored week containing the event date).
- `base` absorbs everything earlier, left-bounded by `data_start`, so the athlete is always
  in some block no matter how distant the race.
- `weeks_to_event` is whole weeks from a week's Monday to the race week; `0` in the race
  week itself.

### Planned deload

`is_deload` marks recovery weeks the *plan* prescribes: every `deload_every_n_weeks` week
counted **back from the end of its block**, inside `base` and `build` only. `peak` and
`taper` never carry a planned deload - a taper is a downshift already.

Block-end anchoring (rather than a modulo counter over the calendar) is the decision: it
guarantees the athlete always enters the next block fresh. Modulo anchoring was checked
against the real calendar and lands a deload either in the first week of base or in the
last week of peak, directly before the taper.

`is_deload` and the existing `DELOAD_ADVISED` signal are **two independent answers to one
question** and neither overrides the other: `is_deload` is what the plan intended,
`DELOAD_ADVISED` is what the actual load did. The divergence is the finding - the same
plan-vs-actual shape Phase 5 already established. No arbitration rule is written.

### Mart: `plan_block`

One row per week keyed by `week_start` (Monday), carrying `block`, `weeks_to_event`,
`is_deload`, and the anchoring event's id.

It spans the **whole plan horizon including future weeks**, out to the race week. This is
why it is a separate mart rather than two new columns on `weekly_metrics` as the
`PROJECT.md` sketch proposed: `weekly_metrics` holds only weeks that already happened, so
the row sets are disjoint and future blocks have nowhere to live there.

`plan_block` is the single source of truth. Recomputed as a tail of `features`, after
`weekly.rollup` and before `snapshot.rollup`, alongside `zones` and `overlap`. Safe to drop
and rebuild.

### Pure periodization function

`periodize(event, today, thresholds) -> list[WeekPlan]` - no database, no wall clock, no
training history. The `PROJECT.md` sketch's `history` argument is dropped: blocks are a
countdown from the race date and the deload cadence counts back from block ends, so history
contributes nothing and would only cost determinism. Confronting the plan with what actually
happened is Phase 10's job.

No `week_intent` column anywhere. It would be a pure function of `block` (every `base` week
shares one intent), and storing a derived value invites drift; what a block means for
training is policy in code, keyed on `block`. The word **"intent" stays reserved** for
`plan_template`'s *daily* category.

### Copies into existing marts

- `weekly_metrics` gains `block` and `weeks_to_event` for the weeks it holds - a same-run
  copy, following the `athlete_status` precedent, so the weekly report needs no join.
- `athlete_status` fills its `block`, `weeks_to_event`, and `taper_active` columns, which
  Phase 6b seeded as NULL placeholders explicitly "until Phase 9".
- `plan_template` is **not modified**. Blocks annotate the athlete's weekly template; they
  never replace it or generate a day-by-day plan.

### Signals

- `TAPER_ACTIVE` - the current week's `block` is `taper`. In Phase 9 this is a *fact* in
  the digest and report only; suppressing intensity on its basis is Phase 10's decision.
- `RACE_PROXIMITY` - the nearest upcoming goal event (any priority, any status) falls
  inside `race_proximity_weeks`. Facts carry the event's type, priority, status, and
  `weeks_to_event`; it asks for a `tentative` event to be decided and an `approx` date to
  be pinned. Facts stay flat scalars, honoring the existing signal contract.

### Thresholds

Seeded in `coach_thresholds` and `thresholds.DEFAULTS`, tunable like every other policy
value: `taper_weeks = 2`, `peak_weeks = 3`, `build_weeks = 5`, `deload_every_n_weeks = 4`,
`race_proximity_weeks = 3`.

### CLI

`garmin-coach event add | list | update`.

`status` and `date_precision` are *designed to change* - the athlete buys the race slot, or
commits to the tune-up. If the only way to change them were hand-written SQL they would
never be updated, and the "pin the date" nag would have nothing to act on. The CLI is what
makes the two-axis model function rather than decorate. `event list` shows the recorded
events, which one is the anchor, and the countdown to each.

## Testing Decisions

A good test here exercises external behavior - the block calendar a given event and date
produce, the facts a digest carries - never the internals of how a week was labeled. The
phase is designed so that nearly all of its logic is reachable without a database.

**One new seam, tested hardest.** `periodize()` is pure, so it carries the phase's golden
tests over frozen dates. Prior art: `zones.compute` and `snapshot.build` are the same shape
(pure function + thin `rollup` at the DB boundary), tested in `tests/test_zones.py` and
`tests/test_snapshot.py`. Cases to pin:

- deep in `base`, mid-`build`, `peak`, a `taper` week, and the race week itself
- an event far enough out that `base` stretches well beyond its nominal length
- `is_deload` placement: correct weeks in `base` and `build`, none in `peak` or `taper`
- no anchor event at all -> empty plan
- only a `tentative` event -> empty plan (no anchor)
- only a past event -> empty plan
- a `tentative` B event nearer than a `confirmed` A event -> the A event still anchors
- `weeks_to_event = 0` in the race week

**Existing seams gain behavior; no new seams are introduced for them:**

- `signals.py` (`tests/test_signals.py`) - `TAPER_ACTIVE` and `RACE_PROXIMITY` as pure
  functions over rows, matching the existing signal-function shape.
- `digest.py` (`tests/test_digest.py`) - the new facts and signals appear in the digest at
  the report horizon.
- `cli.py` (`tests/test_cli.py`) - `event add | list | update` round-trips, including
  flipping `status` and `date_precision`.
- `db.py` (`tests/test_db.py`) - `goal_event` writes.
- `weekly.py` / `snapshot.py` (`tests/test_weekly.py`, `tests/test_snapshot.py`) - the
  copied `block` / `weeks_to_event` / `taper_active` values, and that they are NULL when
  there is no anchor.
- `features.py` (`tests/test_features.py`) - `periodize.rollup` runs in the tail in the
  right order (after `weekly`, before `snapshot`).
- `tests/test_schema_sync.py` already guards the `src/garmin_coach/schema.sql` <->
  `docs/schema.sql` mirror; both must gain `goal_event` and `plan_block`.

Tests stay offline against the fake client and fixtures, as everywhere else in this repo.

## Out of Scope

- **`race_plan` / race-day pacing (deferred to Phase 9b).** Per-segment run paces and
  station effort caps need a partner model, a station-split policy, and baseline station
  times - none of which the database holds for a Doubles race. It is scoped to run close to
  race day, when zones are regression-backed and the pair's split is known. Building it now
  would produce a confidently wrong plan for the athlete's only A race.
- **Any partner modeling.** No partner load, no shared readiness, no partner pace.
- **Prospective recommendations.** `TAPER_ACTIVE` is stated, not acted on. Suppressing
  intensity, capping sessions, and re-planning a missed week are Phase 10.
- **Day-by-day plan generation.** Blocks are week labels over the athlete's own
  `plan_template`, which this phase does not touch.
- **Arbitration between `is_deload` and `DELOAD_ADVISED`.** Deliberately absent; the
  divergence is surfaced, not resolved.
- **A fueling note**, which the `PROJECT.md` sketch attached to `race_plan`; it travels
  with `race_plan` to Phase 9b.
- **New charts.** No visualization this phase.

## Further Notes

- **The athlete's calendar at design time.** Hyrox assumed `2026-10-17`, `confirmed`,
  `date_precision = approx`, `target_s = 3600`, note recording the 1:01:46 reference.
  Half-marathon assumed `2026-09-05`, `tentative`, `date_precision = exact`,
  `target_s = 5400`. Both are entered via the CLI, not seeded, and the athlete updates them
  as plans firm up.
- **What the rules produce for that calendar** (a useful sanity check during
  implementation): `base` 2026-06-08..08-03 (deloads 07-06, 08-03), `build` 08-10..09-07
  (deload 09-07), `peak` 09-14..09-28, `taper` 10-05..10-12. The half-marathon falls in the
  build block and gets no taper of its own; the build-closing deload lands the week after
  it, which is a consequence of block-end anchoring rather than a special case.
- **`plan_block` is a mart, and the future is part of it.** This is the one place in the
  system where a mart holds rows for days that have not happened. It is not a system of
  record: change the event and it rebuilds.
- **Phase 9b's blockers, tracked here so they are not forgotten:** zones were still
  `threshold_pace_fallback`-derived at design time (one qualifying run short of the
  regression path), `race_predictions` was empty, and the 1:01:46 race predates `data_start`
  so no station splits exist anywhere in the database.
