# Authoring a custom workout

Read this before authoring or pushing any workout to Garmin. It carries the mapping from
the athlete's words to a `workout_request`, and the bound the plan of record puts on it.

When the athlete describes a run session in plain language ("Tempo Thursday: warm-up
on-click, 8x1km at 3:40-4:00 with 2:00 jog, cool-down on-click"), turn it into a
`workout_request` JSON, author it offline, and let the athlete confirm the push. You
compose the JSON; the deterministic layer fills the numbers and writes to Garmin - never
hand-edit Garmin, never push without the athlete's explicit go-ahead.

Map the words to the request's `structure` block (the template is `warmup + reps x (work
+ recovery) + cooldown`, one homogeneous interval block):

- **origin** `athlete` (their idea) - keep the recommender's `pace_target_s_per_km` in the
  request when this refines a recommendation, so a faster band gets a cited warning.
- **session_type** `quality` for reps, `tempo` for one continuous block, `easy` for a
  single steady run.
- **reps** the interval count (`8`).
- **`<role>_end`** per role (`warmup`/`work`/`recovery`/`cooldown`): `"lap"` for
  "on-click", `{"distance_m": N}` for a distance ("1km" -> `1000`), `{"min": N}` for a
  clock ("2:00" -> `2`). A `work` step may not be `"lap"` (it needs a defined end).
- **work_pace_band** `[fast_s_per_km, slow_s_per_km]`, faster bound first - convert
  mm:ss to seconds ("3:40-4:00" -> `[220, 240]`). It overrides the recommender's pace.

Example (the tempo above), also in `tests/fixtures/tempo_request.json`:

```json
{
  "sport": "run", "origin": "athlete", "date": "2026-07-23",
  "session_type": "quality", "pace_target_s_per_km": null,
  "structure": {
    "reps": 8,
    "warmup_end": "lap",
    "work_end": {"distance_m": 1000},
    "work_pace_band": [220, 240],
    "recovery_end": {"min": 2},
    "cooldown_end": "lap"
  }
}
```

**Strength / HIIT sessions** (issue #16) author the same way from a
`structure.exercises` list. Map the words to entries `{exercise, sets, reps | time,
weight_kg?, rest?}`:

- **sport + session_type**: FBB/gym -> `strength`/`strength`; stations/metcon ->
  `hiit` with `crossfit`, or `hyrox` for Hyrox-specific station work. A run-dominant
  Hyrox day is a run request under a run session type (`easy`/`tempo`/`quality`) with
  explicit structure - `session_type: hyrox` only authors under `sport: hiit`.
- **exercise** - the athlete's own words ("przysiad" -> "back squat", "wall balls",
  "sled push"); the whitelist in `workouts/exercises.py` resolves them to Garmin's
  labels. An unknown name still authors (warning + unlabeled step) - flag it to the
  athlete rather than inventing a different exercise.
- **sets/reps/weight**: "5x5 100 kg" -> `{"sets": 5, "reps": 5, "weight_kg": 100}`;
  ramping ("100/105/110") -> consecutive single-set entries. Time-boxed stations
  ("45 s sled") -> `{"time": {"s": 45}}` instead of reps.
- **rest**: only when stated ("przerwa 2 min" -> `{"rest": {"min": 2}}`, "do
  gotowości" -> `"lap"`); defaults are 90 s (strength) / 60 s (hiit).

Example (fuller ones in `tests/fixtures/strength_request.json` and
`tests/fixtures/hiit_request.json`) - "Piątek FBB: przysiad 5x5 100 kg, wyciskanie
3x8 80 kg, wall balls 3x20":

```json
{
  "sport": "strength", "origin": "athlete", "date": "2026-07-24",
  "session_type": "strength",
  "structure": {"exercises": [
    {"exercise": "back squat", "sets": 5, "reps": 5, "weight_kg": 100},
    {"exercise": "bench press", "sets": 3, "reps": 8, "weight_kg": 80},
    {"exercise": "wall balls", "sets": 3, "reps": 20}
  ]}
}
```

Then, per the runbook in `docs/OPERATIONS.md`: `garmin-coach author --date D --request
<path>` writes the spec, `push --date D` dry-runs it (show the athlete), and `push --date
D --confirm` is the athlete's deliberate write. Where the coach tools are present the same
three steps are `author_workout`, `push_preview`, and `push_confirm` - same order, same
consent, and the dry run is never skipped because the tool made it one call away.

**The plan of record bounds what you may author** (issue #22). Authoring and pushing both
refuse a session harder than the plan for that date - `rest < easy < tempo = strength <
hyrox = crossfit = quality` - and softer is always allowed. When a refusal comes back, do
not look for a way around it: tell the athlete what the plan says for that day and that
changing it is their call. For a week that already has a plan file, revising is their own
edit plus `garmin-coach plan import`; for a week with none, `plan_preview` / `plan_confirm`
writes one (read `references/planning.md` first). If `get_workout_status(date)` returns a
non-null `plan_divergence`, a workout already on the watch is harder than the plan now
says: report it with both intents and offer to re-author that day. Never delete or
overwrite what is on the account to resolve it - that is the athlete's decision, taken
through a normal push.
