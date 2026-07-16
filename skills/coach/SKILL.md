---
name: coach
description: Read the athlete's daily_metrics mart and current-standing snapshot via the deterministic digest and write a concise, number-dense coaching report with two charts. Use when the user asks for a training report, coach read, weekly review, "how am I doing", or their current standing / stats / form / fitness snapshot ("where do I stand", "what are my numbers", "gdzie stoje", "jakie mam staty", "jaka mam forme").
---

# Coach

Turn the `daily_metrics` mart into a short coaching read. The heavy lifting is
deterministic Python; your job is the narrative. **Never query Garmin live and never
read the raw mart** - you consume the compact digest and the current-standing snapshot
only.

## Procedure

1. **Generate the digest and charts.** Run:

   ```bash
   poetry run garmin-coach plan import       # cache plans/<monday>_week.md (transport-free)
   poetry run garmin-coach report            # or: --from YYYY-MM-DD --to YYYY-MM-DD
   ```

   `plan import` first, because the report reads the plan of record from the DB cache,
   not from `plans/` - without it a plan the athlete edited since the last nightly run
   is invisible and the report scores adherence against a stale week. It is idempotent
   and never touches Garmin. If it fails, the plan file is malformed: say so and stop
   rather than reporting against the fallback template.

   `report` writes `reports/{today}/`: `digest.json`, `hrv_band.png`, `acwr.png`, and
   `snapshot.json` (the current standing). If it fails because the mart is empty, run
   `poetry run garmin-coach features` first, then retry. Run `features` too when
   `plan import` actually changed a week: `planned_intent_today` and the weekly
   adherence are materialized, so they only catch up when the marts are rebuilt. Never
   run `sync`/`backfill` yourself - that would call Garmin live, which the golden rule
   forbids from the coach layer; tell the operator to run it instead.

   **If `poetry` is missing or fails** (the Cowork sandbox ships Python 3.10, not the
   3.13 that poetry needs), do NOT build a venv or install a new Python - the code runs
   fine on 3.10. Use the read-side fallback: install the runtime deps once and invoke
   the CLI module directly.

   ```bash
   pip install matplotlib pydantic pydantic-settings python-dotenv garminconnect curl-cffi --break-system-packages
   PYTHONPATH=src python3 -m garmin_coach.cli plan import
   PYTHONPATH=src python3 -m garmin_coach.cli report     # or: features, if the mart is empty
   ```

   Only ever run **read-side** commands (`plan import`, `report`, `features`) this way -
   the same golden-rule limit applies. See `docs/OPERATIONS.md` ("For Claude running in
   Cowork") for the full note.

2. **Read only `reports/{today}/digest.json`.** It has `window`, a `headline` block
   (latest ACWR + `acwr_reliable`, latest HRV vs its band, 7-day load + shares), a
   `signals` list already ordered alert > warn > info, a `zones` block (personal
   training zones; may be null), a `plan` block (the periodization standing; may be
   null), a `movement` block (per-set map coverage; may be null), a `recommendation`
   block (tomorrow's prospective session advice; absent when there is no horizon), and a
   `disclaimer`. Do not open the mart or recompute anything.

   Also read `reports/{today}/snapshot.json` (may be absent if the mart was never
   materialized). It is the current standing: `computed_at`, `vo2max` (+ `vo2max_delta`
   / `vo2max_span_days`), `weight_kg` (+ trend), `hrv_baseline`/`hrv_sd` (+ `hrv_delta`
   / `hrv_span_days`), race predictions (`t_5k_s`..`t_marathon_s`), `acwr` +
   `acwr_reliable`, `load_7d` + shares, `readiness_score`/`readiness_level`,
   `sleep_debt_h`, heat/altitude acclimation, the personal zones (mirrored), and
   `planned_intent_today`/`planned_label_today` + `plan_source_today`. Every field may
   be null; never invent a number a null field does not provide.

   `plan_source_today` says where today's plan came from: `plan_week` is the week the
   athlete authored (the plan of record), `plan_template` is the repeating fallback
   shape - a default, never something they agreed to. Never present a `plan_template`
   day as "your plan"; say the week is unplanned and the template is standing in.

3. **Write `reports/{today}/report.md`.** Structure:
   - **Twoje aktualne staty** - only when `snapshot.json` is present. One compact block
     opening the report with where the athlete stands now: VO2max and its trend (state
     `vo2max_delta` over `vo2max_span_days` days when non-null, e.g. "VO2max 52, +1.0 w
     24 dni"), body weight + trend, HRV baseline + its weekly-average trend, latest race
     predictions (format seconds as h:mm:ss / mm:ss), Training Readiness
     (`readiness_score` + `readiness_level`), the load/ACWR standing (`acwr` with
     `acwr_reliable` - call it *orientacyjny* when false - plus `load_7d` and the
     `low_share`/`high_share`/`anaero_share` split), the zones headline (`z2_hi_bpm` HR
     ceiling and `z2_pace_ceiling_s_per_km` as min:sec - "easy pod X:XX/km"; note
     `zones_stale` when 1), and today's plan (`planned_label_today`, flagged as the
     fallback template when `plan_source_today` is `plan_template`). Skip any sub-item
     whose value is null. Skip the whole block when `snapshot.json` is absent.
   - **Nagłówek** - one line on the window and the headline numbers (ACWR + reliability,
     latest HRV vs baseline, 7-day load split). When the `zones` block is present, add
     the Z2 pace ceiling so the read is actionable: "trzymaj easy run pod X:XX/km"
     (convert `z2_pace_ceiling_s_per_km` to min:sec) and the Z2 HR ceiling
     (`z2_hi_bpm`). If `zones.stale` is 1, note it briefly - the zones come from an LTHR
     detection `lthr_age_days` days ago (on `lthr_detected_on`), past the staleness
     cadence; suggest a harder threshold effort to refresh them. Do not invent numbers
     when `zones` is null.
   - **Sygnały** - one short paragraph per signal, most severe first. State the actual
     numbers from `facts`. Map each code to a concrete action:
     - `HRV_LOW_MORNING` -> degrade today's quality session to easy.
     - `ACWR_OUT_OF_RANGE` -> over-reaching / detraining risk; if `reliable` is false,
       call the ratio *orientacyjny* (indicative), do not over-react.
     - `AEROBIC_LOW_SHORTAGE` -> too much grey zone, add Zone 2. Note whether Garmin
       agrees (`garmin_agrees`): agreement strengthens the call, disagreement -> hedge.
       When `facts.personal_z2_minute_share` is present, cite both reads: the load-bucket
       share and the personal-zone share (how much of your run time was actually at avg
       HR under your Z2 ceiling). If the two diverge, say so - it is itself informative.
     - `TWO_HARD_DAYS` -> flag the back-to-back stack; if `trailing` is true, it is an
       *upcoming* risk (the Friday-into-Saturday pattern), not just history.
     - `HRV_SLEEP_CONFOUND` -> caution: the worst HRV may be sleep-driven, not training;
       do not confuse causes.
     - `DELOAD_ADVISED` -> load has climbed for several weeks into a hot ACWR or high
       monotony; suggest a back-off (deload) week. State `rise_weeks`, `acwr_end`, and
       `monotony` from `facts`.
     - `PATTERN_STACK` -> the same movement pattern(s) (`facts.keys`, e.g. hinge) were
       loaded on back-to-back days without a rest day; name the pattern(s) and
       `overlap_max`, suggest spacing them or inserting recovery.
     - `MUSCLE_OVERLAP` -> the same muscle group(s) (`facts.keys`, e.g. grip + posterior
       chain) stacked across adjacent sessions; flag the recovery risk on those tissues.
     - `HARD_RPE_YESTERDAY` -> yesterday's session was subjectively very hard
       (`facts.rpe` on Borg CR10); the next day should back off. This feeds tomorrow's
       recommendation below.
     - `PLAN_MISSING` -> the week starting `facts.week_start` has no authored plan, so
       the repeating template is standing in and every planned-intent read for it is a
       default, not the athlete's intent. Offer to plan the week (see "Planning the
       week" below); do not treat the template days as agreed sessions.
   - **Movement coverage** - when the digest's `movement` block is present and
     `sets_unmapped` > 0, add one brief line that the overlap read is partial: N of
     `sets_total` sets are unmapped (`unmapped` names), so those exercises need adding to
     the movement map. Skip entirely when `movement` is null or nothing is unmapped.
   - **Tydzień: plan vs realizacja** - only if the digest has a non-null `weekly` block
     (the latest complete week). One line on the week's numbers (`load_total`, the
     low/high/anaero shares, `monotony`/`strain`, `max_consec_hard`), then the adherence:
     state `plan_adherence` and walk the `plan_vs_actual` rows where `match` is false,
     naming the direction (e.g. "pt: plan quality, było rest"). If `was_deload` is true,
     say so - a deliberate deload is not lost fitness. Skip this block entirely when
     `weekly` is null.

     `planned` and `actual` are recorded at different granularities on purpose, so trust
     `match` over your own comparison of the two strings. `planned` is what the athlete
     meant (any of the seven intents); `actual` is only what the load can show
     (`rest`/`easy`/`strength`/`quality`), because load numbers cannot tell a crossfit
     session from a hyrox one. So `planned: crossfit` with `actual: quality` is a
     **match** - do not report it as a divergence.
   - **Blok i odliczanie** - only if the digest has a non-null `plan` block. State the
     training block (`block`: base/build/peak/taper) and the countdown
     (`weeks_to_event`) to `race_date` (`race_type`), and say whether the plan calls
     this week a deload (`is_deload`). Interpret the block, do not just name it: base is
     volume, build is specific work, peak is sharpening, taper is cutting load.
     **Never invent a phase when `plan` is null** - say plainly that no goal race is
     recorded, so the system does not know what is being trained for.
     - `TAPER_ACTIVE` -> the taper has started; do not add load. This phase only states
       the fact - do not turn it into a prescription beyond that.
     - `RACE_PROXIMITY` -> a race is `facts.weeks_to_event` weeks out. When
       `facts.needs_decision` is true, ask the athlete to commit or drop it; when
       `facts.needs_date_pinned` is true, ask them to pin the exact date, because the
       taper is planned off it.
     - When the `plan` says `is_deload` but the `weekly` block shows no drop in
       `load_total` (and no `DELOAD_ADVISED`), name the divergence - the plan asked for
       a recovery week and it did not happen. Plan and reality are tracked separately on
       purpose; the gap is the finding.
   - **Rekomendacja na dziś** - only when the digest has a non-null `recommendation`
     block. It advises the *next* session (`target_date`, i.e. tomorrow relative to the
     window) and only ever softens what the plan of record planned - it can never raise
     an easy day into a hard one. Render:
     - The session: `intended_type` (rest/easy/tempo/strength/hyrox/crossfit/quality)
       with the `intensity_cap` when non-null (Z2/Z3/Z4 HR ceiling) and
       `pace_target_s_per_km` as min:sec/km when non-null. A `strength` session never
       carries a pace - a running threshold pace is meaningless for lifting, so its
       absence there is by design, not missing data.
       If `pace_target_s_per_km` is null, do not invent a pace
       (the zones are still on a fallback multiplier). If `downgraded` is true, say it is
       a step down from the planned `planned_intent` and give the reason; if false, tell
       the athlete to keep the planned session.
     - The reasons: translate every code in `rationale` using the Sygnały mapping above
       (e.g. `HRV_LOW_MORNING`, `ACWR_OUT_OF_RANGE`, `TWO_HARD_DAYS`,
       `HARD_RPE_YESTERDAY`, `DELOAD_ADVISED`, `AEROBIC_LOW_SHORTAGE`, `TAPER_ACTIVE`).
       For `NIGGLE_REDUCED_MODE`, name the affected body part from that signal's
       `facts.body_part` in the `signals` list. An empty `rationale` with `downgraded`
       false means "trzymaj plan" - say so plainly.
     - `avoid` - when non-empty, one short line naming the movement patterns / muscle
       groups to keep off tomorrow (they stacked on back-to-back days).
     - `replan` - when present (not null), the last complete week missed `replan.missed`
       planned sessions, so a broken week now needs realigning. Lay out the three
       `options` (`extend` / `rebuild` / `continue`) with their `cite`, and mark
       `replan.recommended` as the suggested one for the current block. Frame it as the
       athlete's choice, not an order; note that `rebuild` is a manual call, because the
       system holds no session priorities to rebuild from.
     - This whole block is a suggestion, never a prescription - keep the reading framing.
     Skip the entire block when `recommendation` is absent.
   - **Wykresy** - embed both: `![HRV](hrv_band.png)` and `![ACWR](acwr.png)`.
   - **Zastrzeżenie** - end with the digest `disclaimer` verbatim.

## Planning the week

The athlete's plan of record is `plans/<monday>_week.md`, one file per week, authored by
hand and revised mid-week when signals warrant. It drives today's planned intent, the
recommendation's starting point, and weekly adherence. A week with no file falls back to
the repeating `plan_template` - a default shape, never an agreed plan.

**Read it** with `mcp__coach__get_plan(week_start)` when the tools are present (defaults
to the current week). `has_plan: false`, every day sourced `plan_template`, or a
`PLAN_MISSING` signal all mean the same thing: that week is unplanned. Say so plainly.

**Propose one** when the athlete asks, or when you spot an unplanned week - offer, never
write unasked. Compose the seven days yourself from what you already read (`get_weekly`
for the recent shape and adherence, `get_digest` for form and signals, `get_events` for
what they are training for); the deterministic layer only validates and stores. Each day
is `{planned, intent}`:

- **`planned`** - the free-text session, in the athlete's own style ("bieg easy 10 km,
  Zone 2 (HR <145)"). It carries the detail the engine cannot hold: paces, HR caps,
  distances. It cannot contain `|` or a line break - both break the plan file's table
  row, and the tool will reject the proposal rather than corrupt it. Rephrase instead.
- **`intent`** - exactly one of `rest | easy | tempo | strength | hyrox | crossfit |
  quality`. This is what the engine reads: it names how hard the day is, not the
  exercises. `crossfit` and `hyrox` both mean a hard mixed session; the discipline lives
  in `planned`.

Then `plan_preview(week_start, days)` to validate, **show the athlete the table**, and
only on their explicit go-ahead `plan_confirm(week_start, days)`, which writes the file
and caches it. Confirm refuses a week that already has a file - revisions are the
athlete's own edit plus `garmin-coach plan import`, so their prose and revision log are
never overwritten by a tool that cannot read them. Without the MCP tools, write the file
in the same table format by hand and run `plan import`.

## Authoring a custom workout

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

Then, per the runbook in `docs/OPERATIONS.md`: `garmin-coach author --date D --request
<path>` writes the spec, `push --date D` dry-runs it (show the athlete), and `push --date
D --confirm` is the athlete's deliberate write. `hiit`/`strength` are not authored yet
(they await the strength push spike).

## Tone

Concrete, numbers first, no filler. Polish prose (matches the athlete). This is a
reading of recorded data, not medical or coaching prescription - never phrase a signal
as a diagnosis or an order.

## Rules

- Thresholds and signal logic live in Python (`signals.py`, `coach_thresholds`). Do not
  reinvent them or hardcode numbers in prose beyond what `facts` provides.
- If a signal is absent from the digest, do not mention it. Silence means "not flagged".
- One report per run day; re-running overwrites `reports/{today}/`.
