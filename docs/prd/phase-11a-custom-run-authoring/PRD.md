# PRD - Garmin Coach - Phase 11a: custom run authoring (lap ends, distance intervals, pace band)

> Status: Ready for implementation (TDD) - Date: 2026-07-15
> Triage: ready-for-agent
> Sources: `docs/glossary.md` (authoring terms: structure override, end condition,
> custom pace band), grilling decisions 2026-07-15 (Phase 11a), ADR 0013.
> Deps: Phase 11 (author/publish path, workout request/spec, `to_garmin`, the confirm
> interlock). Extends exactly what Phase 11 put in Out of Scope. NOT Phase 11b
> (HIIT/strength), which stays gated on the strength push spike.

## Problem Statement

Phase 11 shipped the outbound path: a session ask becomes a workout spec and pushes to
the watch. But the athlete can only ask for the shapes the built-in templates produce -
time-ended steps, a single derived pace band, a fixed interval count. A real, ordinary
session the athlete already trains does not fit:

> "Tempo Thursday: warm-up on-click, 8x(1km tempo 3:40-4:00, rest 2:00), cool-down
> on-click."

Three things in that sentence have no home in the current request:

1. **"on-click" (lap-button) ends.** A warmup/cooldown the athlete ends with the watch
   lap button, not a fixed clock. Phase 11 ends every step on time or distance and lists
   lap-button steps in Out of Scope.
2. **Distance intervals ("1km").** `to_garmin` already encodes a distance-ended step, but
   nothing in the request or the templates can ask for one - work steps are minutes only.
3. **A custom pace band ("3:40-4:00").** The work target is always a symmetric +/-5s band
   around a single pace the recommender supplies; the athlete cannot state their own
   window.

So the athlete asks Cowork for a session they run every week and the system cannot build
it - it either drops the detail (a fixed-clock warmup instead of on-click, a 3-minute
interval instead of 1km) or cannot express the pace window at all. The recommendation
loop closes, but the athlete's *own* concrete session does not.

## Solution

Extend the **athlete/hybrid structure override** (the `structure` block in a
`workout_request`) so the existing run template - `warmup + N x (work + recovery) +
cooldown` - can be parametrised richly enough to express the session above, without a new
authoring mode and without touching the transport (`publish`) at all.

Three additions, all inside `author` (the pure Seam 1) and its `to_garmin`:

- **Uniform end condition per role.** Each role (`warmup | work | recovery | cooldown`)
  gets one end descriptor: `{"min": N}` (time), `{"distance_m": N}` (distance), or `"lap"`
  (the watch lap button). One channel for all three end kinds, not three ad-hoc flags.
  Guardrail: `warmup`/`cooldown`/`recovery` may use any of the three; a `work` step must
  be `time` or `distance` (lap is refused - a work interval needs a defined end).
- **Custom pace band on the work step.** An explicit `work_pace_band: [fast_s_per_km,
  slow_s_per_km]` (seconds per km, faster bound first) that **wins over** the recommender's
  `pace_target_s_per_km` and **suppresses** the pace -> HR -> none degradation, because it
  is already fully specified. Applies to the work target in `easy`, `tempo`, and `quality`.
- **A pace-aware duration estimate.** So `estimatedDurationInSecs` stays honest when steps
  end on distance or lap rather than the clock (a distance work step with a pace band is
  estimated from the band midpoint; lap and pace-less distance steps contribute 0).

This is a hybrid *of pace*, mirroring the Phase 11 hybrid *of session type*: the
recommender suggests, the athlete finalises. The recommender never emits a structure
override (`request_from_recommendation` keeps `structure: None`); overrides are the
athlete tightening what the recommender proposed. If the athlete's custom band is clearly
harder than the recommender's suggestion, `author` warns (never blocks), cited by the
recommender's own signal codes - exactly like the existing session-type hybrid warning.

The **natural-language -> request** step stays with the agent (Cowork), guided by a
documented request schema, a canonical fixture (the tempo example), and a new section in
`skills/coach/SKILL.md`. The deterministic layer consumes structured JSON, never free
text - the golden rule, applied to the write path.

No new CLI surface, no new seam, no change to `publish`: the richer spec flows through the
unchanged `push` path, and the spec hash (which already covers `steps`) keeps idempotency
correct for the new step shapes.

## User Stories

1. As an athlete, I want a warmup I end with the lap button ("on-click"), so that I warm up
   until I feel ready instead of to a fixed clock.
2. As an athlete, I want a cooldown I end with the lap button, so that I jog down for as long
   as I want without the watch cutting me off.
3. As an athlete, I want a recovery I can end on the lap button, on a distance, or on a clock,
   so that "jog until you are ready", "400m float", and "2:00 easy" are all expressible.
4. As an athlete, I want work intervals measured by distance ("1km"), so that my track and
   tempo reps match how I actually think about them.
5. As an athlete, I want to state my own pace window on the work step ("3:40-4:00 per km"), so
   that the watch holds me to exactly the band I intend.
6. As an athlete, I want my explicit pace band to override the recommender's derived pace, so
   that when I know the window I want, the system uses it verbatim.
7. As an athlete, I want my explicit band to skip the heart-rate fallback, so that a fully
   specified pace is never quietly degraded.
8. As an athlete, I want a warning (not a block) when my band is clearly harder than the
   recommender advises, so that overriding a real signal is informed but still mine to make.
9. As an athlete, I want to set how many intervals I do, so that "8x1km" is not forced back to
   the template default.
10. As an athlete, I want to combine all of these in one request - lap warmup, 8 distance reps
    with my pace band, timed recoveries, lap cooldown - so that my whole weekly tempo is one
    authored workout.
11. As an athlete, I want the estimated duration to be sensible even when steps end on distance
    or the lap button, so that Connect does not show a nonsense total before I run.
12. As an athlete, I want a distance-ended easy run ("easy 8km"), so that distance is not only
    for interval work.
13. As an athlete, I want the system to refuse a lap-button *work* interval with a clear message,
    so that I do not accidentally author a structureless "hard bit".
14. As an athlete, I want a clear error if I give a role two ends at once (e.g. minutes and a
    distance), so that an ambiguous request fails loudly instead of guessing.
15. As an athlete, I want a clear error if my pace band is inverted (slow bound faster than the
    fast bound), so that a typo cannot produce a nonsense target.
16. As an athlete, I want my older `*_min` requests to keep working unchanged, so that the
    richer options are additive, not a migration.
17. As an athlete talking to Cowork, I want to describe the session in plain language and have a
    valid request built for me, so that I never hand-write JSON.
18. As a developer, I want a canonical request fixture (the tempo example) in the repo, so that
    the schema has one worked, tested reference.
19. As a developer, I want every new behaviour covered offline on the existing `author` seam, so
    that the phase keeps the fast, deterministic, network-free coverage Phase 11 established.
20. As a developer, I want `publish` and the CLI untouched, so that the transport surface and its
    manual live-push acceptance stay settled.

## Implementation Decisions

### Scope

- **Run only, athlete/hybrid origin only.** The recommender path is unchanged
  (`request_from_recommendation` still sets `structure: None`). HIIT/strength stay Phase 11b,
  gated on the strength push spike - untouched here.
- **Extend the template, do not add a mode.** The shape stays `warmup + N x (work + recovery)
  + cooldown`; intervals are **homogeneous** (one repeat block). Irregular structures (e.g.
  `4x1km + 4x400m`) are a future "explicit steps" mode - Out of Scope.
- **No new seam, no new CLI flag, no change to `publish`.** All logic lands in the pure
  `author` module (`_expand`, validation, targets, `to_garmin`, the estimate).

### Request schema - the `structure` override (in `docs/glossary.md`)

- **Per-role end condition.** Keys `warmup_end`, `work_end`, `recovery_end`, `cooldown_end`,
  each one of:
  - `{"min": N}` - time end, N minutes (N > 0).
  - `{"distance_m": N}` - distance end, N metres (N > 0, integer).
  - `"lap"` - lap-button end (`warmup`/`cooldown`/`recovery` only).
- **Custom pace band.** `work_pace_band: [fast_s_per_km, slow_s_per_km]`, seconds per km,
  faster (smaller) bound first; `fast < slow` required.
- **Interval count.** `reps: N` (existing, `quality`).
- **Back-compat.** The existing keys `warmup_min`, `work_min`, `recovery_min`, `cooldown_min`,
  `duration_min` (easy) remain, each equivalent to that role's `{"min": N}`. Giving both a
  `*_end` and the matching `*_min` for the same role is ambiguous -> `ValueError`.

### Author policy (in `author.py`)

- **End resolution.** A new helper turns a role's structure entry into a spec `end`
  descriptor (`{"type": "time", "seconds"}` / `{"type": "distance", "metres"}` /
  `{"type": "lap"}`), replacing today's minutes-only `_mins`. `easy`'s single work step reads
  `work_end` (new), falling back to `duration_min` (existing) for back-compat.
- **Work target with explicit band.** When `work_pace_band` is present, the work target is a
  `pace_band` built directly from it; the recommender pace and the HR/none degradation are not
  consulted. Absent, behaviour is exactly as Phase 11 (`pace_target_s_per_km` -> +/-band, else
  degrade). This applies wherever a work target is built (`easy`/`tempo`/`quality`).
- **Hybrid pace warning.** When an `athlete`/hybrid request carries an explicit band whose fast
  bound is meaningfully faster than the recommender's suggested pace, append a cited warning
  (reusing the recommendation's rationale codes) to `warnings[]`. Never blocks.
- **Validation (`_validate_structure`, hand-rolled, raising `ValueError`).** Allowed roles;
  exactly one end per role (no `*_end`+`*_min` clash); `distance_m` a positive integer; `min`
  positive; `lap` refused for `work`; `work_pace_band` a two-element `[fast, slow]` with
  `fast < slow`, both positive. Messages name the offending role/field. Consistent with the
  existing `_validate_request`; no formal JSON-schema.

### `to_garmin` translation

- Add a `lap` branch to the end-condition application: `ConditionType.LAP_BUTTON`
  (`conditionTypeKey: "lap.button"`), no end value. The existing `time` and `distance`
  branches are unchanged. Target encoding (pace band / HR band / none) is unchanged.

### Duration estimate

- `estimatedDurationInSecs` sums time-ended steps as today, plus: a **distance** step **with a
  pace band** contributes `metres/1000 * midpoint_pace_s_per_km` (midpoint of the band). A
  **lap** step, or a distance step with an HR/none target, contributes 0. Repeat groups
  multiply by `reps`, as today. The estimate is explicitly approximate; Garmin recomputes on
  the device.

### Documentation and agent guidance (the "B" half)

- `docs/glossary.md` - the three new terms (done during grilling: structure override, end
  condition, custom pace band).
- `docs/OPERATIONS.md` / `docs/DEVELOPMENT.md` - the request schema and the worked tempo
  example alongside the existing author/push runbook.
- A canonical request **fixture** in the repo (the tempo example) - the reference Cowork and the
  tests share.
- `skills/coach/SKILL.md` - a concise "Authoring a custom workout" section: mapping natural
  language to the `structure` block (roles, ends, pace band, reps), the tempo example, and the
  reminder that `push` is confirm-gated.

## Testing Decisions

Good tests assert **external behaviour** - the produced spec's steps and targets, the translated
Garmin JSON, the estimate, the raised errors - never private helpers. One seam, the existing
highest one:

- **Seam 1 - `author` (pure, offline, TDD).** Extend `tests/test_author.py`. Cover:
  - a lap-ended warmup/cooldown and a lap-ended recovery produce a `{"type": "lap"}` end;
  - a distance-ended work step (`{"distance_m": 1000}`) produces a distance end and, via
    `to_garmin`, a `ConditionType.DISTANCE` executable (extends the existing distance test);
  - a `"lap"` end via `to_garmin` produces a `ConditionType.LAP_BUTTON` executable;
  - an explicit `work_pace_band` becomes the work `pace_band` verbatim, overrides the
    recommender pace, and suppresses HR degradation (no degradation warning emitted);
  - the full tempo fixture (`8x1km` @ `[220,240]`, lap warmup/cooldown, 2:00 recovery) expands
    end-to-end to the expected spec and typed JSON;
  - the hybrid pace warning fires (cited) when the band is harder than the suggestion;
  - the estimate: distance-with-band estimated from the midpoint, lap and pace-less distance
    contribute 0;
  - back-compat: existing `*_min` requests are unchanged;
  - validation errors: lap on `work`; `*_end`+`*_min` clash; inverted band; non-positive
    distance/min - each a `ValueError` with a clear message.
- **No Seam 2 change.** `publish` and the CLI are untouched; their existing tests
  (`tests/test_publish.py`, the CLI tests) must stay green unchanged. The richer spec still hashes
  through the same `_spec_hash` (which covers `steps`), so idempotency is exercised only insofar
  as different shapes yield different hashes - no new publish test needed.

Prior art: the existing `tests/test_author.py` (spec-shape and `to_garmin` golden assertions, the
current distance-ended step test), and `tests/test_publish.py` for the untouched transport.

## Out of Scope

- **Heterogeneous interval blocks.** One homogeneous repeat block only; `4x1km + 4x400m` and
  pyramids await a future "explicit steps" request mode.
- **HIIT / strength authoring (Phase 11b).** Still gated on the strength push spike; nothing here.
- **Formal JSON-schema for the request.** Hand-rolled `_validate_structure` is the single source
  of truth.
- **`intensity_cap` enforcement.** The recommender's HR cap is not used to clamp or refuse a
  custom pace band (different unit); the hybrid warning is the only interaction.
- **Cadence / power targets.** Unchanged from Phase 11 - `pace_band` / `hr_band` / `none` only.
- **New CLI flags or a `publish` change.** The request carries all the new expressiveness; the
  commands and the transport are untouched.
- **Natural-language parsing in the engine.** Cowork composes the request JSON; `author` consumes
  structured JSON only.

## Further Notes

- `to_garmin` already encoded distance ends in Phase 11 (verified, tested) but nothing could ask
  for one; Phase 11a mostly makes existing transport capability *reachable* from a request and adds
  the one genuinely new Garmin branch (`lap.button`).
- The spec hash covering `steps` means a lap/distance/band change is a different workout to the
  account-of-record idempotency - re-pushing an edited session correctly resolves to `refuse`
  (needs `--replace`), with no publish change required.
- Consistent with the tracker transition note: Phase 11a is tracked as a `docs/prd/` phase like
  Phase 11; any follow-on (e.g. the "explicit steps" mode) is expected to be a GitHub issue.
