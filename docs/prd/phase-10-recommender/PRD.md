# PRD - Garmin Coach - Phase 10: prospective session recommender

> Status: Ready for implementation (TDD) - Date: 2026-07-15
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 10, `docs/glossary.md`, grilling decisions 2026-07-15.
> Deps: Phase 6 (personal zones / pace), Phase 7 (sRPE load + niggle log), Phase 9
> (block / taper). Explicitly NOT Phase 9b (race-day pacing), which nothing here depends on.

## Problem Statement

The engine reads the past and stops. Every finding in the digest - hot ACWR, a low-HRV
morning, two hard days stacked, an aerobic-low shortage, a taper week - is a *statement
of fact* that leaves the athlete to assemble the verdict by hand. Today's read - "two
quality sessions are fine because HRV is 88 vs a baseline of 68, but ACWR 1.21 is at the
top of range and Z2 share is 0%, so run Z2 tomorrow" - was composed manually from the
digest, `plan_template`, and the zones mart. Nothing turns the signals into a single
prospective answer: *what should tomorrow's session be, how hard, at what pace, and what
should it avoid.* And when a week falls apart - two planned sessions missed - the system
silently recommends the next template day as if nothing happened, instead of noticing
that the plan itself now needs realigning.

Phase 9 supplied the missing context (block, weeks-to-event, taper, planned deload) but
deliberately did not act on it; `taper_active` was left as a fact for "the recommender's
decision, deliberately not taken here." Phase 10 is that decision.

## Solution

Add one pure function, `recommend(digest, planned_intent, thresholds) -> dict`, in a new
`recommend.py`, that turns the already-built digest into a single prospective
recommendation for **tomorrow** and returns a `recommendation` block. `build_digest`
reads tomorrow's planned intent from `plan_template`, passes it in, and appends the block
beside `weekly` / `zones` / `plan`. No Garmin, no re-query - the recommender consumes the
finished digest, exactly the seam Phase 3 established for signals.

The recommendation starts from what the weekly template planned for tomorrow and only ever
*softens* it. It reads the digest's own `signals` list and maps each fired code to a
downgrade action; the result is the most conservative outcome among the signals that
fired (signal-driven, most-conservative-wins). Every recommendation cites the exact signal
codes that changed it, so the advice is explainable by construction - the industry's
weakest point (unpublished black-box models) stays our advantage. One new signal,
`HARD_RPE_YESTERDAY`, brings the athlete's subjective session-RPE (Phase 7) into the same
uniform signals channel so the recommender need never touch `session_rpe` directly.

Two adaptation concerns ride alongside the intensity verdict:

- **What to avoid.** The `avoid[]` list is populated from the real movement/muscle stacks
  the Phase 8 signals already computed (`PATTERN_STACK`, `MUSCLE_OVERLAP`). An active
  niggle forces a global downgrade and is cited, but it does *not* fabricate a movement
  pattern - there is no honest map from a free-text body part to a movement pattern yet,
  and the system says less rather than guessing (that map is deferred; see Out of Scope).
- **Re-planning.** When the last complete week missed too many planned sessions, the block
  emits a small `replan` menu of three cited options - *extend*, *rebuild*, *continue* -
  with one flagged `recommended` by block context. It is a menu for the athlete to choose
  from, never an executed re-plan: the system has no session priorities to rebuild from
  and stays a reading-plus-suggestion, never a prescription.

The recommendation lands in `digest.json`; the coach skill renders the Polish
"Rekomendacja na dzis" section from the block. No new pipeline CLI command.

## User Stories

1. As an athlete, I want the report to tell me what tomorrow's session should be, so that I
   stop assembling the verdict by hand from separate facts.
2. As an athlete, I want that recommendation to start from what my weekly template already
   planned, so that it respects my own structure and only adjusts it when the data says to.
3. As an athlete, I want the recommendation to only ever make tomorrow *easier*, never
   harder, so that the system can never talk me into overreaching.
4. As an athlete, I want an intensity cap in my own personal zones (Z2/Z3/Z4), so that
   "easier" is a number I can train to, not a vibe.
5. As an athlete, I want a concrete pace target when one applies, so that an easy day is
   actually easy and a tempo day hits the right window.
6. As an athlete, I want every recommendation to cite which signals drove it, so that I can
   see the reasoning and overrule it when I know better.
7. As an athlete, I want a genuinely hard session yesterday to soften tomorrow even when the
   load number looks moderate, so that how it felt counts, not only what it measured.
8. As an athlete, I want a low-HRV morning, a hot ACWR, a stacked pair of hard days, an
   advised deload, or an active niggle to each be able to pull tomorrow back to easy, so
   that any one real warning is enough to protect me.
9. As an athlete, I want the worst of several warnings to win, so that conflicting signals
   resolve to the safest single answer rather than cancelling out.
10. As an athlete, I want tomorrow's avoid-list to name the movement patterns and muscles I
    already stacked, so that I do not load the same pattern a third day running.
11. As an athlete, I want an active niggle to quietly pull tomorrow back to easy and be named
    in the reasoning, so that I train around it without the system inventing which exercises
    to skip.
12. As an athlete, I want the report to notice when I missed too many sessions last week and
    offer me clear options - extend, rebuild, or carry on - so that a broken week changes the
    plan instead of being silently ignored.
13. As an athlete, I want those re-plan options to be a menu I choose from, with one marked
    as recommended for where I am in my cycle, so that the system advises without seizing
    control of my plan.
14. As an athlete, I want a taper week to stop the system from ever suggesting I add
    all-out intensity, so that the weeks that decide my race are protected.
15. As an athlete, I want the recommendation to appear automatically in my normal report,
    so that I do not have to run a separate command to get advice.

## Implementation Decisions

### Seam and inputs

- New module `recommend.py` with one public pure function:
  `recommend(digest: dict, planned_intent: str | None, thresholds: dict[str, float]) -> dict`.
- Pure: reads only the passed `digest` dict (its `signals`, `zones`, `weekly`, `plan`,
  `window`) and `planned_intent`. Never opens the DB, never calls Garmin.
- `build_digest` is the orchestrator (as it already is for `_plan_section` / `_zones_section`):
  it reads `plan_template.intent` for `weekday(to_date + 1)`, passes it as `planned_intent`,
  and appends the returned block under the key `recommendation`. Gated on `to_date is not
  None`; when there is no horizon there is no `recommendation` key (mirrors the empty-window
  branch, which omits it).
- The dosless PROJECT signature `recommend(digest, plan_block, zones)` is deliberately
  *not* used: `plan_block` and `zones` are already inside `digest`, and `plan_template`
  (the only missing input) was absent from it.

### Horizon

- The recommendation targets **tomorrow** = `to_date + 1`. The block carries `target_date`.
- "Yesterday" for adaptation triggers means `to_date` (the last day of the window), i.e. the
  most recent recorded day relative to the target.

### The `recommendation` block

Flat scalars plus one list and one nested `replan` object (the signals-facts convention):

```json
"recommendation": {
  "target_date": "2026-07-16",
  "planned_intent": "quality",
  "intended_type": "easy",
  "intensity_cap": "Z2",
  "pace_target_s_per_km": 330,
  "downgraded": true,
  "rationale": ["HRV_LOW_MORNING", "TWO_HARD_DAYS"],
  "avoid": ["hinge", "squat"],
  "replan": null
}
```

- `planned_intent` - tomorrow's `plan_template.intent`, unchanged; the starting point.
- `intended_type` - the recommendation, from the same vocabulary
  (`rest | quality | easy | hyrox | tempo`). Only ever softened from `planned_intent`.
- `intensity_cap` - personal zone ceiling from `athlete_zones`: one of `Z2 | Z3 | Z4`, or
  `null` when no cap applies. Never RPE, never a load number.
- `pace_target_s_per_km` - derived from the zones mart: `z2_pace_ceiling_s_per_km` when
  `intensity_cap == "Z2"`, `threshold_pace_s_per_km` for a tempo/quality target; `null`
  when the zones mart has no measured pace (still on a fallback multiplier) or no zones row.
- `downgraded` - `intended_type != planned_intent`.
- `rationale` - the signal codes that actually changed the recommendation from
  `planned_intent`. Empty when nothing fired (`downgraded == false`).
- `avoid` - movement-pattern / muscle keys to keep off tomorrow (see below). Possibly empty.
- `replan` - the re-plan menu object, or `null` (see below).

### Intensity vocabulary and the downgrade ladder

- Hardness order: `rest < easy < tempo < hyrox = quality`. `quality` and `hyrox` are both
  "top intensity"; a downgrade action names a *cap* (a maximum allowable type), and the
  final `intended_type` is the minimum of `planned_intent` and every cap that fired.
- Because actions can only cap downward, composition is order-independent: the outcome is
  the most conservative (lowest) type any fired signal demands.

### Composition: signal -> action (most-conservative-wins)

The recommender iterates `digest["signals"]` and applies:

| Signal code | Action |
|---|---|
| `HRV_LOW_MORNING` | cap -> `easy`; `intensity_cap = Z2` |
| `ACWR_OUT_OF_RANGE` (above sweet spot) | cap -> `easy`; `intensity_cap = Z2` |
| `TWO_HARD_DAYS` with `facts.trailing == true` | cap -> `easy` |
| `NIGGLE_REDUCED_MODE` | cap -> `easy`; `intensity_cap = Z2` |
| `DELOAD_ADVISED` | cap -> `easy`; `intensity_cap = Z2` |
| `HARD_RPE_YESTERDAY` | cap -> `easy` |
| `AEROBIC_LOW_SHORTAGE` | no type cap; if the resolved type is `easy`, set `pace_target_s_per_km` to the Z2 pace ceiling (force a genuine Z2) |
| `TAPER_ACTIVE` | no type cap; `intensity_cap` may not be left `null` (no all-out); cited in `rationale` |

- `TWO_HARD_DAYS` only downgrades when its pair ends at the window edge
  (`facts.trailing == true`); a historic mid-window pair is not a reason to soften tomorrow.
- `ACWR_OUT_OF_RANGE` downgrades only when the ratio is *above* the sweet spot
  (`facts.acwr > facts.sweet_hi`); a below-range ACWR is not a reason to go easier.
- A signal is added to `rationale` only when it actually changed the outcome (moved the type
  down, set a cap, or set the pace target). Informational signals that changed nothing are
  not cited.

### New signal: `HARD_RPE_YESTERDAY`

- Computed in `build_digest` (a new `signals.py` function), so it flows through the uniform
  signals channel and the recommender stays pure over the digest.
- Fires when the latest recorded day (`to_date`) has a `session_rpe` row (joined via
  `activities.date == to_date`) with `rpe >= hard_rpe`. When more than one session that day
  is rated, the maximum RPE governs.
- Shape: `{code: "HARD_RPE_YESTERDAY", severity: "warn", facts: {activity_id, rpe, date}}`.
- Stands alone: a very hard subjective session (Borg 8-10) warrants an easy next day on its
  own; the PROJECT "hard-RPE + low readiness" phrasing is the motivating example, not a
  required conjunction.

### The avoid-list

- `avoid[]` is built solely from the keys the Phase 8 signals already computed:
  `PATTERN_STACK.facts.keys` and `MUSCLE_OVERLAP.facts.keys` (comma-joined strings, split
  and de-duplicated, sorted). Real, measured stacks - no invention.
- An active niggle is handled by the `NIGGLE_REDUCED_MODE` composition row (global downgrade
  + citation); its `facts.body_part` is already in the digest for the coach skill to name in
  prose. The recommender does **not** map a body part to a movement pattern.

### Re-plan menu

- Source: `digest["weekly"]["plan_vs_actual"]` - the per-day facts of the latest *complete*
  week (already in the digest). Pure over the digest.
- `missed` = count of rows where `planned != "rest"` and `match == false`.
- Fires when `missed >= replan_missed_sessions`; otherwise `replan = null`.
- Shape:

```json
"replan": {
  "week_start": "2026-07-06",
  "missed": 3,
  "recommended": "continue",
  "options": [
    {"id": "extend",   "cite": "weeks_to_event=14, block=build"},
    {"id": "rebuild",  "cite": "manual: no session priorities in plan_template"},
    {"id": "continue", "cite": "weeks_to_event=2, block=taper"}
  ]
}
```

- All three options are always present. `recommended` is chosen by block context:
  - `base` / `build` (far from the race) -> `extend`;
  - `peak` / `taper` (near the race) -> `continue` (protect the taper; do not cram);
  - `rebuild` is always offered but is an explicitly *manual* suggestion ("drop the
    lowest-priority sessions first") - `plan_template` carries no session priority, so the
    system cannot deterministically decide what to drop and says so rather than guessing.
- The `cite` on each option carries the block facts that justify it; the coach skill renders
  the Polish menu.

### Thresholds

Two new keys in `thresholds.DEFAULTS` (overridable via `coach_thresholds`):

- `hard_rpe = 8` - Borg CR10 floor for `HARD_RPE_YESTERDAY`.
- `replan_missed_sessions = 2` - missed-session count that arms the re-plan menu.

No other new thresholds: everything else composes over existing ACWR / HRV / aerobic /
deload / taper thresholds.

### Rendering

- `report.py` is unchanged in shape: it writes `digest.json`, which now carries the
  `recommendation` block. No new artifact, no new CLI command.
- The coach skill (`SKILL.md`) gains a "Rekomendacja na dzis" section that renders the block:
  the recommended type + cap + pace, the cited signals in Polish prose, the avoid-list, and
  the re-plan menu when present. All Polish narrative stays in the skill; `recommend.py`
  emits only codes and scalars (code-style rule: Python is English-only).

## Testing Decisions

- `recommend()` is a pure function; unit tests drive it with hand-crafted `digest` dicts,
  one per representative state, and assert the returned block (golden-style). No DB.
- Required state fixtures (mirrors the PROJECT DoD enumeration):
  - **green** - no downgrade signals; `intended_type == planned_intent`, `downgraded ==
    false`, `rationale == []`, `replan == null`.
  - **hot ACWR** - `ACWR_OUT_OF_RANGE` above sweet -> capped to easy/Z2, cited.
  - **HRV low** - `HRV_LOW_MORNING` -> easy/Z2, cited.
  - **aerobic deficit** - `AEROBIC_LOW_SHORTAGE` with a resolved easy day -> `pace_target`
    forced to the Z2 ceiling.
  - **deload advised** - `DELOAD_ADVISED` -> easy/Z2, cited.
  - **taper week** - `TAPER_ACTIVE` -> `intensity_cap` not null, no all-out, cited; type not
    forced to easy on taper alone.
  - **hard RPE yesterday** - `HARD_RPE_YESTERDAY` alone -> easy, cited.
  - **active niggle** - `NIGGLE_REDUCED_MODE` -> easy/Z2, `body_part` present for prose,
    no fabricated pattern in `avoid`.
  - **pattern/muscle stack** - `PATTERN_STACK` / `MUSCLE_OVERLAP` -> `avoid` carries the keys.
  - **most-conservative-wins** - several downgrade signals at once resolve to the single
    lowest type with all of them cited.
  - **missed-week re-plan** - `plan_vs_actual` with `missed >= threshold` -> the three cited
    options with the block-appropriate `recommended`; and a below-threshold case ->
    `replan == null`.
- `HARD_RPE_YESTERDAY` gets its own `signals.py` unit tests (fires at/above `hard_rpe`,
  silent below, picks the max RPE of the day, silent with no rated session).
- One integration test through `build_digest` on a seeded fixture DB: the `recommendation`
  block is present, gated correctly on `to_date`, and reads tomorrow's `plan_template`
  intent.
- Follow the repo TDD flow (red -> green) and the existing golden-fixture style used by the
  digest/signals tests.

## Out of Scope

- **Body-part -> movement-pattern map.** Mapping a free-text niggle body part (Polish, e.g.
  "kolano") to a movement pattern is inherently lossy and would violate the repo's
  "say None rather than guess" rule. Deferred to its own small PRD once a controlled body-part
  vocabulary exists; until then a niggle downgrades globally and is named in prose only.
- **Executed re-planning.** The re-plan menu offers options; it never rewrites
  `plan_template` or materializes a new plan. `rebuild` in particular is a manual suggestion.
- **Day-by-day plan generation.** Phase 10 recommends *tomorrow's* single session; it does
  not author a training plan. Structured-workout authoring and push to Garmin is Phase 11.
- **Race-day pacing (`race_plan`).** Phase 9b, a leaf that nothing here depends on.
- **A dedicated `garmin-coach recommend` CLI command.** The recommendation rides in the
  normal report; a standalone command is a possible later convenience, not part of v1.
- **Upgrading intensity.** The recommender only ever softens the planned session; it never
  suggests going harder than the template.

## Further Notes

- Keep the disclaimer: the recommendation is a reading plus a suggestion, never a
  prescription. The coach skill must preserve the existing `DISCLAIMER` framing.
- The clean separation Phase 9 insisted on holds: `is_deload` (what the plan prescribed) and
  `DELOAD_ADVISED` (what the data observed) remain distinct; the recommender consumes the
  observed signal and never rewrites the prescribed flag.
- `HARD_RPE_YESTERDAY` is independently useful in the digest's `signals` list, not only to
  the recommender.
- An ADR is optional here: Phase 10 introduces no new mart and no schema-contract change
  beyond two threshold keys and one signal. If the composition table proves contentious in
  review, capture it as `docs/adr/00NN-phase-10-recommender.md` at that point.
