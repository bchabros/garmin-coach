# 04 - Re-plan menu (`replan`) + `replan_missed_sessions` threshold

Status: ready-for-agent
Blocked by: 02
Sources: `docs/prd/phase-10-recommender/PRD.md` (Re-plan menu; Thresholds). Deps: Phase 5
(`plan_vs_actual`), Phase 9 (block context).

## Goal

When last week fell apart, stop silently recommending the next template day: offer the
athlete a small menu of cited options - extend, rebuild, continue - with one flagged as
recommended for where they are in the cycle. A menu to choose from, never an executed
re-plan.

## Scope

- **Source** `digest["weekly"]["plan_vs_actual"]` - the per-day facts of the latest complete
  week, already in the digest. Pure over the digest; no new query.
- **`missed`** = count of rows where `planned != "rest"` and `match == false`.
- **New threshold** `replan_missed_sessions = 2` in `thresholds.DEFAULTS` (overridable via
  `coach_thresholds`). No schema change.
- **Emit `replan`** when `missed >= replan_missed_sessions`; otherwise `replan = null`.
  Shape: `{week_start, missed, recommended, options: [{id, cite}, ...]}` with all three
  options (`extend`, `rebuild`, `continue`) always present.
- **`recommended`** by block context (from `digest["plan"]`): `base`/`build` -> `extend`;
  `peak`/`taper` -> `continue`. `rebuild` is always offered but is an explicitly *manual*
  suggestion ("drop the lowest-priority sessions first") - `plan_template` has no session
  priority, so its `cite` says the system cannot decide what to drop.
- **`cite`** on each option carries the block facts that justify it (e.g.
  `weeks_to_event`, `block`). Codes/scalars only; the Polish menu is rendered by the coach
  skill (ticket 05).

## Tests (`test_recommend.py`)

- `missed >= threshold` -> `replan` with the three options; below threshold -> `replan ==
  null`.
- `recommended == "extend"` in a `build` block; `recommended == "continue"` in a `taper`
  block.
- `rebuild` is always in `options` and its `cite` marks it manual.
- `missed` counts only planned non-rest days that did not match; a rest day that was skipped
  is not a miss.
- No `plan_vs_actual` in the digest (no complete week yet) -> `replan == null`.

## Done when

- A week with two or more missed planned sessions produces the three cited options with the
  block-appropriate `recommended`; a clean week produces `replan == null`.
- The miss threshold is configurable through `coach_thresholds` and defaults to 2.
- `task check` green.
