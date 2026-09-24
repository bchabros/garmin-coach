# Authoring a custom workout

Read this before authoring or pushing any workout to Garmin. It carries the mapping from
the athlete's words to a `workout_request`, and the bound the plan of record puts on it.

When the athlete describes a run session in plain language ("Tempo Thursday: warm-up
on-click, 8x1km at 3:40-4:00 with 2:00 jog, cool-down on-click"), turn it into a
`workout_request` JSON, author it offline, and let the athlete confirm the push. You
compose the JSON; the deterministic layer fills the numbers and writes to Garmin - never
hand-edit Garmin, never push without the athlete's explicit go-ahead.

Map the words to the request's `structure` block. **Every run session type offers every
step role** - the type only sets what happens when you say nothing (ADR 0023):

- **origin** `athlete` (their idea) - keep the recommender's `pace_target_s_per_km` in the
  request when this refines a recommendation, so a faster band gets a cited warning.
- **session_type** the session's default shape, not a limit on it: `easy` -> one work
  step, `tempo` -> warm-up + work + cool-down, `quality` -> warm-up + repeats + cool-down.
  Pick the one that names the session honestly and add whatever roles it needs - the plan
  guard reads the targets, not the name (see *hardness* below).
- **`<role>_end` / `<role>_min` / `<role>_target`** per role (`warmup`/`work`/`recovery`/
  `rest`/`cooldown`). **Any one of these three summons the role**, so `"warmup_target":
  "z2"` on an `easy` run authors a Z2 warm-up. Ends: `"lap"` for "on-click",
  `{"distance_m": N}` for a distance ("1km" -> `1000`), `{"min": N}` for a clock ("2:00"
  -> `2`). A `work` step may not be `"lap"` (it needs a defined end). A summoned role runs
  10 min (warm-up, cool-down) or 2 min (recovery, rest) unless you say otherwise.
- **reps** the interval count (`8`), valid on any session type. It folds `work` and the
  pause after it into a repeat block, so `tempo` with `reps` is a normal interval session.
- **recovery vs rest** - `recovery` is a jog between repeats, `rest` is standing still.
  Exactly one per session: asking for both is refused, and asking for `rest` on `quality`
  replaces its default jog. A pause without `reps` is refused (it names a repeat session
  written without its count).
- **work_min** the work step's length in minutes on every type (`duration_min` still works
  on `easy`, as the older spelling; setting both is refused).
- **work_pace_band** `[fast_s_per_km, slow_s_per_km]`, faster bound first - convert
  mm:ss to seconds ("3:40-4:00" -> `[220, 240]`). It overrides the recommender's pace.

### What the plan guard measures

The authored spec carries **`hardness`** - `easy`, `threshold` or `hard` - measured from
the targets, not from `session_type` (ADR 0024). The hardest step decides, and a band's
harder edge decides: a work step at `[265, 275]` is threshold work whatever the session is
called, and a Z4 warm-up alone lifts the whole session. Show it in the preview beside the
session type; they answer different questions.

The guard refuses anything above the plan of record for that date and names what decided:
`2026-09-03 is planned as easy; the work step at 4:25-4:35/km is threshold - harder than
the plan of record.` The remedy is to ease the session or revise the plan - never to rename
the session type, which changes nothing the guard reads.

Two consequences worth telling the athlete before they ask:

- An easy-named session with a threshold band on a planned easy day is **refused**. That is
  the point: the name no longer buys anything.
- Threshold repeats are fine on a planned `tempo` day, whatever type you author them under.

When the spec has **no `hardness`** - no zone ladder yet, an exercise sport, or a Hyrox
station sequence - the guard falls back to ranking `session_type`, and the spec says so in
its warnings.

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

An easy run with edges - the 2026-09-03 session, which before ADR 0023 could not be
authored under `easy` at all:

```json
{
  "sport": "run", "origin": "athlete", "date": "2026-09-03",
  "session_type": "easy",
  "structure": {
    "warmup_end": "lap",
    "work_end": {"distance_m": 8000},
    "work_pace_band": [320, 340],
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
  labels. An unknown name still authors (warning; the athlete's words become the step's
  notes) - flag it to the athlete rather than inventing a different exercise.
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

**Hyrox race simulation** (run-station sequence) authors as `sport: run`,
`session_type: hyrox` with a `structure.stations` list - one run *before* each station,
in race order. Runs share one end and one target; stations share one target and end on
the lap button unless an entry says otherwise:

- **stations** - labels in the athlete's words (`"SkiErg 1000 m"`), shown on the watch as
  the step's notes; an entry may be `{"label": ..., "end": {"min": N}}` for a time-boxed
  station. A distance end is refused (the watch would GPS-measure an erg).
- **run_end** - `{"distance_m": 1000}` by default (training); `"lap"` for race day, where
  the course is never exactly a kilometre.
- **run_target** / **station_target** - the usual target spellings (`{"pace_band": [fast,
  slow]}`, a zone name, `{"hr_band": [...]}`, `"none"`); both default to no target. A
  `run_target` band faster than a recommendation gets the same cited warning as
  `work_pace_band`.
- **warmup_end** / **cooldown_end** (or the `_min` aliases) - optional; the step is
  authored only when given. Without `stations`, a run hyrox request still asks for the
  split.

```json
{
  "sport": "run", "origin": "athlete", "date": "2026-09-13", "session_type": "hyrox",
  "structure": {
    "warmup_end": "lap",
    "run_end": {"distance_m": 1000},
    "run_target": {"pace_band": [235, 250]},
    "station_target": "none",
    "stations": ["SkiErg 1000 m", "Sled Push 50 m", "Sled Pull 50 m",
                 "Burpee Broad Jumps 80 m", "Row 1000 m", "Farmers Carry 200 m",
                 "Sandbag Lunges 100 m", "Wall Balls 100"]
  }
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

## Naming the session, and repeating one

**Name it the way the athlete named it.** Every request may carry `label`: the part after
`GC <date>`, at most 30 characters, so the watch shows "GC 2026-09-25 4x2 km próg" instead
of "GC 2026-09-25 quality". Propose one for anything with a shape worth naming - tempo,
intervals, strength, HIIT, a Hyrox simulation - in the words the athlete used in this
conversation, in their language, or in the watch's language when they ask for that. Easy
runs and rest days keep the session type; a name there adds nothing. When the athlete
dictates a name, pass it whole as `name` (at most 80 characters) - no prefix, no date, it
is theirs. Empty, multi-line, over-long, and `GC `-prefixed labels are refused; fix and
re-author rather than arguing with the validator.

**A name without the day's date is a workout for many days.** Push "GC FBB A" once and the
same steps on another date are *scheduled* again, not uploaded twice - one workout on the
watch with several dates. Use it when the athlete repeats a session; keep the dated
default when the session is a one-off.

**Repeating one.** "Powtórz FBB A z 19.09 w piątek": find the day with
`get_pushed_workouts()` (the push receipts, newest first, with date, name and last known
state - it never touches Garmin), then `author_workout(date=<the new day>,
reuse_from=<the day it came from>)`. The steps and the name are copied and re-guarded
against the **new** day's plan of record, so a refusal there is about the new day, not the
old one. Then `push_preview` / `push_confirm` as always.

**Renaming is a change.** Garmin has no rename: a new name is a new workout as far as the
account is concerned, so the preview reports `replace` and the athlete confirms it like
any other change. Read what the preview says the replace will do - a workout named for
that date is deleted, a date-free one is only taken off the date and kept, because it may
be on days no receipt knows about. When the preview warns that the library will then hold
two workouts of the same name, offer the new version a name of its own before confirming.
