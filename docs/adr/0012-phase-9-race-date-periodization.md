# ADR 0012 - Phase 9: race-date periodization

## Status

Accepted

## Context

Training needs race-date blocks; paired race execution additionally needs partner capability and station strategy.

## Decision

- Separate deterministic periodization from race-day pacing. Optimize the recorded
  athlete's training, never invent partner load/readiness/pace. Race-plan work was
  separated into #13 rather than made a dependency of training blocks.
- Event commitment (`confirmed`/`tentative`) and date precision (`exact`/`approx`) are
  independent. Only confirmed priority-A events anchor blocks/taper; approximate
  dates retain their anchor and trigger requests to pin the date near race day.
- Use base/build/peak/taper blocks; planned deload is a separate flag. Observed load
  can disagree with a planned deload, and that divergence remains visible.
- Count deload cadence backward from each base/build block's end. Never open a block
  with deload; peak/taper have none. This avoids artifacts from data-start boundaries.
- `plan_block` covers historical and future weeks from data start. Compute it before
  weekly rollup; weekly metrics/snapshot carry same-run copies, not competing plans.
- Each week anchors to its nearest confirmed A race on/after that week, not today's
  upcoming race. Preserve completed cycles after race day; later races own later weeks.
  Rebuilding the calendar has no wall-clock as-of dependency.
- Fixed taper/peak/build lengths leave the remainder as base. Without a confirmed
  future-to-that-week anchor, block/countdown are null. Taper's weekly label remains,
  but `taper_active` ends at the race date, not the end of its week.
- Periodization uses calendar and policy, not training history. No derived week-intent
  column: intent remains the daily plan category. Store targets as seconds; events
  are user-entered through event add/list/update, never personal schema seeds.

## Consequences

Historical blocks survive racing and recomputation. Planned recovery and reactive
load advice stay distinct. The event calendar supplies facts; recommendation policy
interprets them without inventing team evidence.
