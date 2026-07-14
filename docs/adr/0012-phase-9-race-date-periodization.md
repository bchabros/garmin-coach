# ADR 0012 - Phase 9: race-date periodization

## Status

Accepted

## Context

Everything the system computes is retrospective: it reads what happened and reports on
it. Nothing knows what the athlete is training *for*, so `plan_template` is a static
weekly pattern with no notion of a race date, a training block, or a taper - and Phase 10
(the prospective recommender) would advise in a vacuum, unable to distinguish "20 weeks
out" from "3 weeks out". `PROJECT.md`'s Phase 9 sketch bundles two deliverables:
periodization (blocks counted back from an event) and a race-day pacing plan.

The athlete's actual calendar exposed the sketch's soft spots. The goal is HYROX
**Doubles** Men Pro, ~mid-October 2026, sub-1:00:00 (last result 1:01:46), plus a
*possible* half-marathon in early September, sub-1:30. That is two events of different
priority and different *kinds* of uncertainty, a paired race format the sketch's solo
`race_plan(event, athlete_status)` signature cannot express, and - at the time of design -
zones still derived from `threshold_pace_fallback` rather than regression, with no race
splits in the DB at all (the 1:01:46 predates `data_start`).
See `docs/prd/phase-9-periodization/PRD.md`.

## Decision

- **Phase 9 ships periodization only; `race_plan` is deferred to Phase 9b.** A deliberate
  deviation from `PROJECT.md`'s Phase 9 DoD. In Doubles the 8 km of running is *shared*
  (pace is bounded by the slower partner) and the stations are *split* by a strategy the
  DB has no way to know; `athlete_status` holds no partner threshold pace and never will,
  because the partner wears no watch we read. Building the pacing plan now would mean
  inventing a partner model, a station-split policy, and baseline station times all at
  once, producing a confidently wrong plan for the athlete's only A race. Periodization,
  by contrast, needs nothing but a date and is what Phase 10 actually depends on (the
  ordering diagram gates the recommender on `block`, not on `race_plan`). Timing settles
  it: periodization is needed *now* because it shapes 14 weeks of training; the pacing
  plan is needed in October, when zones are regression-backed and the station split is
  known - building it in July builds it at the moment of least information.

- **The coach optimizes the athlete, not the team.** No partner load, no shared
  readiness, no partner pace. Training is solo by choice; race day is paired by physics.
  The partner therefore exists in exactly one place in the system - a future `race_plan` -
  which is itself the argument for that module being a separate seam.

- **Event uncertainty has two orthogonal axes, not one.** `status` (`confirmed |
  tentative`) is *whether the athlete will start*; `date_precision` (`exact | approx`) is
  *whether the exact day is known*. Collapsing them into one column breaks on the real
  calendar: the Hyrox is committed with a fuzzy date, the half is dated but uncommitted.
  A single `status` column would have marked the Hyrox `tentative`, suppressing
  `TAPER_ACTIVE` and walking the athlete into their A race untapered. Only `confirmed`
  events anchor blocks or fire the taper; `date_precision` suppresses nothing and merely
  makes the report ask for the date to be pinned as the taper window nears.

- **`block` has four labels; deload is not one of them.** `base | build | peak | taper`.
  The sketch listed `deload` as a fifth block *and* reused Phase 5's reactive
  `DELOAD_ADVISED` thresholds - two sources of truth for one question, requiring an
  arbitration rule for "calendar says deload, athlete is fresh". Instead: `is_deload` is
  a separate flag meaning *the plan prescribed a recovery week*, and `DELOAD_ADVISED`
  stays what it is - *the actual load says step down*. Neither wins; the divergence is
  the finding. This reuses the plan-vs-actual shape Phase 5 already established rather
  than inventing an arbitration policy no one will understand in six months.

- **Planned deloads anchor to the end of a block, not to a modulo counter.** Every
  `deload_every_n_weeks` (4) counted *back from the block's end*, inside `base` / `build`
  only. Modulo anchoring was tried both ways on the real calendar and produced nonsense at
  each edge: counted back from race week it lands a deload in the athlete's first week of
  base; counted forward from `data_start` it lands one in the last week of peak, directly
  before the taper. Block-end anchoring guarantees the useful property instead - the
  athlete always enters the next block fresh. `peak` and `taper` get no planned deload;
  a taper is a downshift already.

- **`plan_block` is a separate mart, not two columns on `weekly_metrics`.** The sketch
  said the weekly rollup "gains `weeks_to_event` and `block`", but the row sets are
  disjoint: `weekly_metrics` holds only weeks that already happened, while blocks must
  cover the *future* out to race week. `plan_block(week_start, block, weeks_to_event,
  is_deload, anchor_event_id)` spans the whole horizon (left-bounded by `data_start`) and
  is the single source of truth; `weekly_metrics` and `athlete_status` carry same-run
  copies for the weeks they do hold, following the `athlete_status` precedent of a
  same-run copy of finished marts. Recomputed as a tail of `features`, like `zones`,
  `snapshot`, and `overlap`.

- **`base` absorbs the remainder; a missing anchor is an explicit NULL.** `taper` (2),
  `peak` (3), and `build` (5) have fixed lengths from thresholds; everything earlier is
  `base`, so the athlete is never in a no-block limbo regardless of how distant the race
  is. With no upcoming confirmed A event - the athlete's state the evening after the race
  - `block` and `weeks_to_event` are NULL and `plan_block` is empty. The system states
  that it does not know what is being trained for instead of quietly counting down to a
  race in the past.

- **`periodize` takes no training history.** The sketch's `periodize(event, today,
  history)` is reduced to `periodize(event, today, thresholds)`: blocks are a countdown
  from the race date and the deload cadence counts back from block ends, so history
  contributes nothing and would only cost determinism. Confronting the plan with what
  actually happened is Phase 10's job.

- **No `week_intent` column.** It would be a pure function of `block` (every `base` week
  shares one intent), and storing a derived value invites drift. What a block means for
  training is policy in code, keyed on `block`. The word "intent" stays reserved for
  `plan_template`'s *daily* category, avoiding one word for two concepts at two
  granularities.

- **`target_s` is seconds, not free text.** The sketch's free-text `target` would force
  Phase 9b to parse "sub 1 hour". Storing 3600 gives `race_plan` a number to split across
  segments; the 1:01:46 reference point lives in `note`.

- **Events are entered through `garmin-coach event add|list|update`.** `status` and
  `date_precision` are *designed to change* (buy the slot, commit to the half). If the
  only way to change them were hand-written SQL, they would never be updated, and the
  "pin the date" nag would have nothing to act on. The CLI is what makes the two-axis
  model function rather than decorate.

## Consequences

- `athlete_status.block` / `weeks_to_event` / `taper_active` stop being NULL placeholders
  (seeded in Phase 6b explicitly "until Phase 9").
- The digest and report gain `TAPER_ACTIVE` and `RACE_PROXIMITY` as *facts*; suppressing
  intensity on their basis is Phase 10's decision, not this phase's.
- Phase 10 can key its rules on `block` and `is_deload` without any further data work.
- Phase 9b (`race_plan`) is blocked on inputs this phase deliberately does not invent:
  regression-backed zones (one qualifying run away at design time), the partner's
  capability, and the pair's station split. It is scoped to run close to race day.
- The athlete's own events are *not* seeded into `schema.sql` - a race calendar is
  personal data, not repo content. They are entered via the CLI.
