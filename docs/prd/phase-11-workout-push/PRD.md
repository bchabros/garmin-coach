# PRD - Garmin Coach - Phase 11: workout authoring and push (run-first)

> Status: Ready for implementation (TDD) - Date: 2026-07-15
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 11, `docs/glossary.md` (authoring terms),
> grilling decisions 2026-07-15, ADR 0013.
> Deps: Phase 6 (personal zones / pace + HR bands), Phase 10 (recommendation to
> author from). Phase 7/8 sharpen strength authoring but the strength path is a
> non-blocking spike here. NOT Phase 9b.

## Problem Statement

The engine now reads the past (Phases 0-9) and, since Phase 10, tells the athlete
what tomorrow's session should be - the intended type, an intensity cap in personal
zones, a pace target when one applies. But the recommendation dies on the page. The
athlete still has to open Garmin Connect, hand-build the workout (warmup, the right
number of intervals, the pace window on each one, the recovery, the cooldown),
name it, and schedule it to the correct day so the watch picks it up in the morning.
Every industry peer closes this loop - Runna syncs a fortnight of run workouts to
Garmin each Monday; Athletica, Stryd, TrainAsONE and enduco all push structured
workouts - and doing it by hand is exactly the repetitive, error-prone step the rest
of the system was built to remove.

The athlete also wants the workout to be able to come from *either* side. Sometimes
the recommender's suggestion is enough. Sometimes the athlete has their own idea
("4x1km at threshold with 2 min jog") and wants the system to fill in the concrete
numbers from their zones. And sometimes they want to propose an idea and have the
recommender check it against what it knows - fresh niggle, hot ACWR, a taper week -
before it goes to the watch. None of that exists today.

## Solution

Add a new **outbound** path that turns a session ask into a concrete Garmin workout
and schedules it to the watch - built as two strictly separated modules on the two
sides of the golden rule (see ADR 0013):

- **`author` (pure).** Consumes a **workout request** - the source-agnostic ask
  (`sport`, `origin`, target date, session type, optional explicit structure) - plus
  the finished marts it reads (the digest's signals, `athlete_zones`), and produces a
  **workout spec**: a complete, Garmin-shaped description of one workout (steps,
  targets, durations) written to `reports/{target_date}/`. Deterministic, offline,
  unit-tested. No network, no DB writes.
- **`publish` (transport, out-of-seam).** Reads the finished spec and calls the
  Garmin workout-create / schedule endpoints. This is the only place in the whole
  system that writes to Garmin. It reuses `client.login()` for authentication and a
  dedicated write-wrapper for the calls; the coach/recommender read path still never
  writes.

The request is **source-agnostic**, so one authoring path and one transport path
serve all three origin modes:

1. **Pure recommender.** `author --from-recommendation` expands the Phase 10
   recommendation block into concrete steps.
2. **Athlete's own.** The athlete states the session conversationally; the agent
   composes a `workout_request` JSON (`origin: athlete`) and `author --request`
   fills in the concrete pace/HR numbers from `athlete_zones`.
3. **Hybrid (a process, not a third origin).** An `athlete` request passed through
   recommender validation before authoring: `author` checks the request against the
   digest's signals and writes any conflicts into the spec's `warnings[]` (cited by
   signal code). It never blocks - the athlete is the sovereign, and `push` already
   requires an explicit confirm.

Two CLI commands, deliberately separated by an on-disk artifact so the spec can be
inspected (or hand-edited) between authoring and transport, and so a near-irreversible
write is never one keystroke away:

- `garmin-coach author --date YYYY-MM-DD (--from-recommendation | --request <path>)`
  - writes `workout.json`, prints the spec and any warnings, touches no network.
- `garmin-coach push --date YYYY-MM-DD [--replace] [--confirm]` - reads
  `workout.json`, queries the account, prints the payload, diff and warnings.
  **Without `--confirm` it always dry-runs** (shows and exits); it sends only with
  `--confirm`. Never invoked from the nightly automation.

v1 authors and pushes **run** workouts only. HIIT and strength requests validate
against the schema but `author` answers that they await the push spike (see Out of
Scope). This keeps the phase finished and shippable on the one transport surface
garminconnect 0.3.6 verifiably supports.

## User Stories

1. As an athlete, I want to turn tomorrow's recommendation into a real Garmin workout
   with one command, so that I stop hand-building it in Connect.
2. As an athlete, I want that workout scheduled to the right day automatically, so
   that my watch offers it in the morning without me touching the calendar.
3. As an athlete, I want the workout to carry concrete pace windows on each step, so
   that the watch tells me when I am too fast or too slow.
4. As an athlete, I want to author straight from the recommender, so that the system's
   own suggestion becomes a workout without me retyping it.
5. As an athlete, I want to state my own session in plain language and have the system
   fill in the numbers from my zones, so that "4x1km at threshold" becomes exact pace
   windows I can train to.
6. As an athlete, I want to propose my own idea and have the recommender check it
   against my current state, so that I am warned when I am about to override a real
   signal - but still allowed to.
7. As an athlete, I want those warnings written into the workout and shown right before
   I confirm the push, so that the decision to override is informed and leaves a trace.
8. As an athlete, I want the warnings to never hard-block me, so that I stay the one who
   decides what my body does.
9. As an athlete, I want a warmup, the right number of intervals, recoveries and a
   cooldown built for me from the session type, so that I do not assemble the structure
   by hand.
10. As an athlete, I want to override the default structure in my request (session
    length, interval count), so that the template is a starting point, not a cage.
11. As an athlete, I want the spec written to disk before anything is sent, so that I can
    read exactly what will hit my account and edit it if I want.
12. As an athlete, I want `push` to do nothing but show me the payload unless I add an
    explicit confirm, so that I can never accidentally write to my account.
13. As an athlete, I want the workout named with a recognisable prefix, so that I can tell
    system-authored workouts from ones I built by hand in Connect.
14. As an athlete, I want re-pushing the same workout to be a no-op, so that running the
    command twice never leaves two copies on the same day.
15. As an athlete, I want to change my mind and re-push a *different* workout for the same
    day only when I explicitly ask to replace, so that an edit is deliberate.
16. As an athlete, I want a push that half-fails (uploaded but not scheduled) to be fixable
    by simply running push again, so that a flaky network never leaves me stuck.
17. As an athlete, I want the system to refuse to author a workout for a date in the past,
    so that a typo cannot create a pointless workout.
18. As an athlete, I want a warning when I author for today, so that I know the watch may
    not sync before I train.
19. As an athlete, I want a warning when the target day already has a logged activity, so
    that I catch a wrong date before I push.
20. As an athlete, I want a pace target when my zones are regression-measured, and an
    honest fall back to a heart-rate target (or none) when they are not, so that I always
    get the best target my data supports and know when it degraded.
21. As an athlete, I want `rest` days to produce no workout at all, so that "nothing to do"
    is a correct, quiet outcome rather than an error.
22. As an athlete, I want a Hyrox recommendation to ask me whether it is a run-dominant or a
    station session, so that the system never guesses the split and pushes the wrong thing.
23. As an athlete, I want strength and HIIT requests to be understood but honestly deferred,
    so that the run deliverable is not held hostage to an unverified endpoint.
24. As an athlete, I want a receipt of every push written to disk, so that there is an audit
    trail of what was created on my account and when.
25. As a developer, I want the pure authoring logic tested entirely offline, so that the
    intelligence of the phase has fast, deterministic coverage.
26. As a developer, I want the transport orchestration tested against a fake publisher, so
    that idempotency and retry are covered without touching the live account.
27. As a developer, I want exactly one manual live-push acceptance step, so that CI never
    depends on Garmin being up.

## Implementation Decisions

### Scope

- **v1 = run only, all three origin modes.** The modes differ only in who produces the
  request; `author` and `publish` are shared. `sport: hiit | strength` are valid in the
  schema but not authored/pushed in v1.
- **Strength/HIIT push is a separate, non-blocking spike** - the last ticket. It does not
  gate the run DoD. See Out of Scope and ADR 0013.

### Domain contracts (see `docs/glossary.md`, Authoring terms)

- **`workout_request`** - source-agnostic ask: `sport (run|hiit|strength)`,
  `origin (recommender|athlete)`, target date, session type, optional explicit structure.
  Deliberately not called "intent" (reserved for the daily `plan_template` category).
- **`workout_spec`** - `author`'s deterministic output: ordered steps
  `warmup | work | recovery | cooldown | repeat(n, [steps])`, each step ended by
  `time` (seconds) or `distance` (metres), each carrying a target `pace_band` (s/km
  range), `hr_band` (bpm range) or `none`. Units are domain units (s/km, bpm); a pure
  `to_garmin(spec)` converts to Garmin typed JSON. Carries `warnings[]`.
- **`origin`** - `recommender` or `athlete`. Hybrid is a process (an `athlete` request
  that passed recommender validation), not a third origin value.
- **`sport`** - the authoring/push family, distinct from `discipline` and `intent`. A
  run-dominant Hyrox is `run` (pushable v1); a station/crossfit session is `hiit`; FBB is
  `strength`. The recommender's `intended_type: hyrox` never maps to a sport
  automatically - the athlete says which kind it is.

### Modules

- **`author.py` (new, pure).** `author(request, context) -> workout_spec` plus
  `to_garmin(spec) -> dict`. Reads the digest's signals and `athlete_zones` from the
  passed-in context; no DB access, no network. Owns: request-schema validation, per-type
  structure templates, recommendation-to-structure expansion, hybrid validation warnings,
  target degradation, date guards, and the Garmin-JSON translation.
- **`publish.py` (new, transport, out-of-seam).** Reads `workout.json`, orchestrates the
  account calls through an injected `WorkoutPublisher` protocol, writes `push.json`.
  Reuses `client.login()` for auth; a dedicated write-wrapper implements the protocol over
  garminconnect. `author.py` never imports `publish.py`.
- **`cli.py` (modified).** Two new subcommands, `author` and `push`, following the existing
  `set_defaults(func=...)` pattern.

### Structure expansion (policy in `author.py`)

- Deterministic templates per session type, overridable by explicit structure in the
  request:
  - `easy` -> single `work` step, default 45 min, target = Z2 ceiling.
  - `tempo` -> `warmup 10min + work 20min @ threshold band + cooldown 10min`.
  - `quality` -> a conservative default interval set (e.g. `4x3min @ threshold` with
    2 min recovery), warmup + cooldown wrapped.
- `rest` -> no spec; `author` exits 0 with "nothing to author".
- `hyrox` from the recommender -> `author` does not guess the run/station split; it asks
  the athlete to specify (run -> authorable; station -> `hiit`, deferred).

### Origin and hybrid validation

- `--from-recommendation` reads the Phase 10 recommendation block (`intended_type`,
  `intensity_cap`, `pace_target_s_per_km`) and expands it.
- `--request <path>` reads an athlete-authored `request.json`.
- Hybrid validation runs the request's session type against the digest's fired signals
  (the same signal set the recommender uses). Any conflict (e.g. request asks `tempo`,
  signals cap to easy/Z2) becomes a cited entry in `warnings[]`. **Never a block.** The
  spec is always produced to match the request; warnings ride in the spec and are shown
  before `--confirm`.

### Target degradation

- Ordered: (1) regression-measured pace -> `pace_band`; (2) no measured pace but HR-zone
  bounds present -> `hr_band` (threshold ~Z4, easy ~Z2) + warning "no measured pace,
  targeting by heart rate"; (3) neither -> `none` + warning "no target, time/distance
  only". A workout is always produced; only target precision degrades.

### CLI contract and the confirm interlock

- `author --date D (--from-recommendation | --request <path>)` - writes `workout.json`,
  prints spec + warnings, no network.
- `push --date D [--replace] [--confirm]` - reads `workout.json`, queries the account,
  prints payload + diff + warnings. **Absence of `--confirm` is always a dry-run** (there
  is no `--dry-run` flag to forget; there is a `--confirm` flag to add deliberately). Sends
  only with `--confirm`.
- `author` and `push` never call each other; the on-disk `workout.json` is the only handoff.

### Push semantics (idempotency, replace, partial failure)

- `push --confirm` performs **upload + schedule atomically**: upload the workout to the
  library, take the `workout_id`, schedule it to `--date`. `push.json` records both
  `workout_id` and `schedule_id`, the payload hash, and a timestamp.
- **The account is the source of truth for idempotency**, not local state. Before writing,
  `publish` lists the account's workouts and matches by the `GC`-prefixed name + date.
  Present and identical payload -> no-op success. Present but different payload -> requires
  `--replace`. Present in library but not scheduled -> schedule only. Absent -> create.
- **Workout naming**: a stable `GC` prefix (e.g. `GC 2026-07-17 tempo`) so idempotency scans
  only system-authored workouts and the athlete can tell them apart in Connect.
- **`--replace`** = `unschedule + delete + upload + schedule` (the library has no in-place
  update).
- **Partial failure is handled by idempotent retry, not rollback.** If `schedule` fails after
  a successful `upload`, `publish` writes `push.json` with `schedule_id: null`, exits
  non-zero with a clear message, and a re-run recognises the library workout (name+date,
  identical payload), skips the upload, and completes only the missing schedule. No
  compensating delete on error.

### Date guards

- Target date in the past -> `author` hard-refuses (non-zero exit).
- Target date is today -> allowed, with a warning in the spec.
- Target day already has a logged activity in core `activities` -> `push` warns (not a block).
- Beyond the `plan_block` / `goal_event` horizon -> no reaction in v1 (the recommender
  already respects taper via signals; no second periodization policy in `author`).

### Transport / auth

- `publish.py` reuses `client.login()` (token cache, MFA, retry-on-expired) for
  authentication, and a dedicated write-wrapper (`list_workouts`, `upload`, `schedule`,
  `unschedule`, `delete`) implementing the injected `WorkoutPublisher` protocol. Kept
  separate from the read-side `GarminTransport`, which serves `sync`.

### Artifacts

- All under the existing `reports/{target_date}/`: `request.json` (input), `workout.json`
  (the spec), `push.json` (the receipt). No new top-level directories.

## Testing Decisions

Good tests here assert **external behaviour** - the shape of the produced spec, the
translated Garmin JSON, the orchestration outcome given an injected transport - never
private helpers. Two seams, matching the two sides of the golden rule:

- **Seam 1 - `author` (pure, offline, TDD).** The primary and highest seam, the same class
  as `recommend()` (Phase 10) and `features`: pure functions over already-finished data.
  Cover: per-type structure expansion (easy/tempo/quality); expansion from a recommendation
  vs from an athlete request; hybrid validation producing cited `warnings[]`; target
  degradation pace -> HR -> none; date guards (past refused, today warned); `rest` producing
  no spec; a `hyrox` recommendation asking for the split; and `to_garmin(spec)` producing
  well-formed typed JSON (assert on the shape, never against a live Garmin). Golden-regression
  in the repo's style.
- **Seam 2 - `publish` orchestration (fake transport).** Reuses the `GarminClient` protocol
  pattern that `sync` already uses (`src/garmin_coach/sync.py`, `tests/test_sync.py`): inject
  a fake `WorkoutPublisher`, assert orchestration without network. Cover: idempotency
  (existing identical -> no-op; different -> requires `--replace`; library-only -> schedule);
  partial-failure retry (schedule fails -> non-zero + `schedule_id: null`; re-run skips upload,
  completes schedule); `--replace` doing unschedule+delete+upload+schedule; and the `push.json`
  receipt contents. Garmin client mocked - no live calls in `pytest`, per the repo's testing
  rule.
- **One manual live-push acceptance step, outside `pytest`.** The DoD's "a confirmed live push
  creates exactly one scheduled run workout" is a one-time manual `push --confirm` against a
  real date, verified on the account/watch, documented in `docs/OPERATIONS.md`. Not in CI.

Prior art: `tests/test_sync.py` (injected fake client, idempotency assertions),
`tests/test_recommend.py` (pure-function golden coverage), the Phase 6/10 zone/recommendation
fixtures for authoring context.

## Out of Scope

- **Strength and HIIT push (v1).** `sport: hiit | strength` are schema-valid but not authored
  or pushed. The strength/HIIT path is a separate, non-blocking spike (last ticket): a manual
  probe in `scratch/` that hand-builds a `STRENGTH_TRAINING` / `HIIT` payload, calls
  `upload_workout()` with raw JSON, and records the outcome (endpoint works -> ADR + a follow-up
  GitHub issue for Phase 11b; endpoint rejects -> documented Runna-style fallback where strength
  stays local, spec + report, watch-free). No production strength seam code in this phase either
  way.
- **Cadence and power targets.** No mart source for them; `pace_band`/`hr_band`/`none` only.
- **Lap-button (open) steps.** Every v1 step ends on time or distance.
- **Periodization policy in `author`.** No block/taper reasoning beyond what the recommender
  already folds into signals.
- **Natural-language parsing in the engine.** The athlete's ask is turned into a
  `workout_request` JSON conversationally (by the agent) or hand-written; `author` consumes
  structured JSON, never free text.
- **Automation.** Neither `author` nor `push` is ever called from the nightly path.

## Further Notes

- This is the first module that deliberately bends the golden rule ("separate transport from
  intelligence") with an *outbound* write. It is isolated exactly like `client.py`/`sync.py`;
  the rationale, the confirm-interlock, and the account-of-record idempotency are recorded in
  ADR 0013.
- After Phase 11 and read-MCP, the tracker transition to GitHub issues applies (Phase 9b already
  lives in issue #13); the strength-spike follow-up, if the endpoint works, is expected to be a
  GitHub issue rather than a new `docs/prd/` phase.
- garminconnect 0.3.6 verified: `upload_workout()` (raw JSON accepted), typed `RunningWorkout`
  and the step/target builders, `schedule_workout`, `unschedule_workout`, `delete_workout`,
  `get_workouts`. Sport IDs include `STRENGTH_TRAINING=5` and `HIIT=9`, but only run-type typed
  classes are provided - hence the spike for the rest.
