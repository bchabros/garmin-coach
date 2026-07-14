# 03 - Block-aware snapshot, weekly, signals and report

Status: ready-for-agent
Blocked by: 02 (needs the `plan_block` mart)
Sources: `docs/prd/phase-9-periodization/PRD.md` (Copies into existing marts, Signals,
Thresholds). ADR: `docs/adr/0012-phase-9-race-date-periodization.md`.

## Goal

Everything that reads `plan_block` and shows it to the athlete. The snapshot stops lying
with NULLs, past weeks carry their block, and the report finally says what phase of the
cycle the athlete is in and how long is left.

All of this rides **existing seams** - no new module.

## Scope

### Copies into existing marts

- **`athlete_status`** fills `block`, `weeks_to_event`, and `taper_active` - the columns
  Phase 6b seeded as NULL placeholders explicitly "until Phase 9" (see the comment in
  `schema.sql`). `taper_active` is derived (`block == 'taper'`), not stored in
  `plan_block`.
- **`weekly_metrics`** gains `block` and `weeks_to_event` for the weeks it holds. A
  same-run copy, following the `athlete_status` precedent, so the weekly report needs no
  join. Drift is impossible: both recompute in the same `features` run.
- **`plan_template` is not modified.** Blocks annotate the athlete's weekly template; they
  never replace it or generate a day-by-day plan.
- **No anchor -> explicit NULL** in all of the above, never a guess.

### Signals

- **`TAPER_ACTIVE`** - the current week's `block` is `taper`. In Phase 9 this is a *fact*
  only. Do **not** suppress intensity, cap sessions, or change any recommendation on its
  basis - that is Phase 10's decision, and this ticket must not pre-empt it.
- **`RACE_PROXIMITY`** - the nearest upcoming goal event (**any** priority, **any** status)
  falls inside `race_proximity_weeks`. Facts carry the event's type, priority, status, and
  `weeks_to_event`. It asks for a `tentative` event to be **decided** and an `approx` date
  to be **pinned** - this is the payoff for the two-axis event model, and the only reason
  `date_precision` exists.
- Both follow the existing pure-function-over-rows shape in `signals.py`; facts stay flat
  scalars, honoring the signal contract.
- Threshold: `race_proximity_weeks = 3`, seeded in `coach_thresholds` and
  `thresholds.DEFAULTS`.

### Digest + report

- The digest carries the current `block`, `weeks_to_event`, and `is_deload` as headline
  facts, plus the two new signals, all scoped to the report horizon.
- The coach report renders the block and countdown. No new chart this phase.

## Tests (`test_snapshot.py`, `test_weekly.py`, `test_signals.py`, `test_digest.py`)

- Snapshot shows `block` / `weeks_to_event` / `taper_active` for an anchored plan, and
  NULL for all three when there is no anchor.
- A completed week in `weekly_metrics` carries its block label.
- `TAPER_ACTIVE` fires in a constructed taper week and stays silent everywhere else,
  including when the anchor is missing.
- `RACE_PROXIMITY` fires inside the window for a `tentative` event and for a `confirmed`
  one; carries the right facts; stays silent outside the window.
- `RACE_PROXIMITY` asks to pin the date when `date_precision = approx`, and to decide when
  `status = tentative`.
- The digest carries the block facts at the report horizon.

## Done when

- `garmin-coach snapshot` shows the block, the countdown, and the taper flag instead of
  NULLs.
- `garmin-coach report` renders the current block and weeks-to-event, and fires
  `RACE_PROXIMITY` on a race inside the window.
- A constructed taper week fires `TAPER_ACTIVE` and changes no recommendation.
- `task check` green.