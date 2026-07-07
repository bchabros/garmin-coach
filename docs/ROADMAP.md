# ROADMAP — Phases 6–11 + read-MCP (AI-coach capabilities)

**Single source of truth for everything after Phase 5.** `docs/garmin-coach-BUILD.md`
is the historical record of what was built (Phases 0–5, all done); this document is the
forward plan. It merges two inputs:

1. **Live-session evidence** — in a real coaching session the deterministic engine kept
   coming up short and advice had to be improvised; each phase cites the concrete gap
   from the current DB.
2. **Industry survey (2026-07-07)** — a primary-source review (vendor docs, help
   centers, first-party API docs; source index at the bottom) of TrainerRoad,
   TrainAsONE, Athletica, Humango, AI Endurance, Runna, WHOOP, Garmin's native
   features, JOIN, enduco, HRV4Training, Intervals.icu, Xert, Stryd, and Hyrox apps
   (ROXFIT, Ladder). It validated most of the original plan and exposed gaps now
   formalized below (periodization, re-planning, zone staleness, niggles, run/strength
   push split).

House rules hold for every phase: **grill → PRD (`docs/prd/phase-N.md`) → TDD
(red→green)**, medallion data (raw → core → mart), tests at agreed seams, Google-style
docstrings, Poetry, Python 3.13. The **golden rule** holds throughout — the
metrics/coach/recommender layers read the finished DB only and never call Garmin live.
The one new *outbound* transport path (Phase 11) is deliberately isolated and
out-of-seam, like `client.py`.

---

## What the industry survey established

- **Every serious product is built around a race date.** Periodized plan → adaptation →
  taper toward an event is baseline everywhere (TrainerRoad Plan Builder's
  Base/Build/Specialty with automatic tapers around A/B/C events; Stryd, Athletica,
  Runna, Garmin Coach, TrainAsONE). It was the roadmap's single biggest hole — now
  **Phase 9**.
- **The universal adaptation loop is: session done → subjective feedback → plan
  adjusts.** TrainerRoad's post-workout survey feeds its Progression Levels; JOIN asks
  RPE + a soreness/readiness question; Athletica and Humango rate every session. Phase
  7's sRPE plan is validated — but industry uses RPE as an **adaptation trigger**, not
  just a load number, so Phase 10 consumes it.
- **Multi-week re-planning after missed sessions is a first-class feature** (Runna
  "Plan Realignment": extend / rebuild-to-race-date / skip; TrainAsONE rebuilds the
  whole plan after every run; Humango rebuilds remaining weeks). Folded into Phase 10.
- **Threshold auto-detection has a cadence everywhere**: TrainerRoad AI FTP is
  deliberately limited to every 28 days; Stryd auto-Critical-Power runs continuously;
  Xert re-estimates on any "breakthrough" effort. Phase 6 gains a re-detection cadence
  and a staleness flag.
- **Environment adjustment is common**: TrainAsONE adjusts paces for forecast
  temperature/terrain and discounts heat-inflated HR from its fitness model; Garmin
  natively computes heat (>22 °C) / altitude (>800 m) acclimation the ETL could ingest.
  Matters *now*: Phase 6's pace↔HR regression would silently absorb summer HR drift.
- **Injury/niggle dial-back modes are standard lightweight features** (Runna "Not
  Feeling 100%" = 3–14-day reduced plan; JOIN/enduco soreness prompts). Phase 7 gains a
  niggle log; Phase 10 maps it to an avoid-list.
- **Phase 10 (recommender) is the most industry-validated design** — readiness +
  load-share deficits → today's suggested session is essentially Garmin Daily Suggested
  Workouts + Xert XATA. But both anchor to a training phase/goal, which is exactly why
  periodization (Phase 9) must land first.
- **Phase 11 run-push is feasible today**: garminconnect 0.3.6 (this repo's own
  library) documents `upload_running_workout()`, `schedule_workout()`,
  `delete_workout()`, `unschedule_workout()`. **Strength push is the risk**: no
  `StrengthWorkout` class is documented, and even Runna pushes *only run-type* workouts
  to Garmin, keeping strength in-app.
- **Phase 8 goes beyond the industry** — no endurance app models cross-session
  muscle/pattern overlap explicitly. Closest analogues: Garmin Strength Coach's
  push/pull-day organization and Athletica's HYROX strength library organized by
  **push / pull / hinge / squat / carry** patterns (a sane starting vocabulary for the
  Phase 8 taxonomy). A genuine differentiator for a Hyrox athlete.
- **Where this local system beats the industry: explainability.** TrainerRoad RLGL,
  Humango, and TrainAsONE models are unpublished black boxes; the digest's
  cited-signal approach is strictly better and worth protecting (see non-goals).

### Capability matrix (products × capabilities)

Legend: ● = yes (documented), ◐ = partial/limited, — = no/not found. TR = TrainerRoad,
TAO = TrainAsONE, ATH = Athletica, HUM = Humango, AIE = AI Endurance, RUN = Runna,
WHP = WHOOP, GAR = Garmin, JOI = JOIN, END = enduco, HRV4 = HRV4Training,
INT = Intervals.icu, XRT = Xert, STR = Stryd.

| Capability | TR | TAO | ATH | HUM | AIE | RUN | WHP | GAR | JOI | END | HRV4 | INT | XRT | STR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Race-date periodized plan (phases + taper) | ● | ● | ● | ● | ● | ● | — | ● | ● | ● | — | ◐ manual | ◐ | ● |
| Post-workout subjective feedback loop | ● | ◐ | ● | ● | ● | ◐ | — | — | ● | ● | ● tags | ◐ | — | — |
| Auto re-planning after missed sessions | ● | ● | ● | ● | ● | ● | — | ● | ● | ● | — | — | ● daily | — |
| Wearable recovery input (HRV/sleep) | — | ◐ | ● | ● | ● | — | ● | ● | ◐ | ◐ | ● | ● store | — | — |
| Prospective fatigue guardrail / readiness gate | ● RLGL | ● | ● | ● | ● | ◐ | ● | ● | ● | ● | ● | — | ● | — |
| Threshold auto-detection (cadence) | ● 28d | ● | ● CP | ◐ | ● | ◐ | — | ● | ◐ | ◐ | — | ● eFTP | ● event | ● cont. |
| Strength / hybrid programming | — | — | ● Hyrox | ◐ | — | ● | — | ● | — | — | — | — | — | — |
| Per-set strength tracking | — | — | ◐ log | — | — | ◐ | — | ● | — | — | — | — | — | — |
| Structured workout push → Garmin device | ◐ | ● | ● | ● | ● | ● run-only | — | n/a | — | ● | — | ◐ | ● | ● |
| Environment adjustment (heat/altitude/terrain) | — | ● | — | ◐ | — | ◐ wx | ◐ wx | ● | — | — | — | — | — | ◐ |
| Injury/illness/niggle mode | ◐ RLGL | ◐ | ◐ | ● | ◐ | ● | — | — | ● soreness | ● | ◐ | ◐ custom | — | — |
| Sleep-debt guidance | — | — | ◐ | ◐ | — | — | ● | ● | — | ◐ | ◐ | ◐ | — | — |
| Race-day pacing plan | — | — | — | — | — | ◐ | — | ● PacePro | — | — | — | — | — | ● |
| Fueling/nutrition | — | — | — | — | — | — | ◐ | ● Connect+ | — | — | — | — | — | — |
| Explainable recommendations (cites reasons) | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | — LLM | ◐ | ◐ | ◐ | ● | ● raw | ● | ● |
| Open API over athlete data | — | — | — | — | — | — | ◐ | ● partner | — | — | ◐ | ● full | ◐ | ◐ |

### Phase validation at a glance

| Phase | Industry analogue | Verdict |
|---|---|---|
| 6 — personal zones | TrainerRoad AI FTP, Stryd auto-CP, Athletica CP, AI Endurance zone detection, Intervals.icu eFTP | Strongly validated; add re-detection cadence + staleness + heat guards |
| 6b — snapshot | Humango Goal Readiness, Intervals.icu athlete page, Garmin dashboard | Validated as the recommender's input surface; add Training Readiness + acclimation + sleep debt |
| 7 — sRPE load | Foster sRPE is the scientific standard; JOIN/Athletica/enduco/Humango all collect RPE; Athletica asks Hyrox athletes to log sets/reps/RPE | Strongly validated; add niggle log; RPE also becomes an adaptation trigger (Phase 10) |
| 8 — per-set + overlap | Garmin Strength Coach push/pull days; Athletica push/pull/hinge/squat/carry | Validated *and differentiating* — nobody models cross-session overlap |
| 9 — periodization (new) | Universal: TrainerRoad, Runna, Athletica, Stryd, Garmin Coach, TrainAsONE | Was the roadmap's biggest gap; promoted from a "maybe Phase 11" note |
| 10 — recommender | Garmin DSW, Xert XATA, TrainerRoad TrainNow, WHOOP strain target | Most validated phase; needs block/goal input from Phase 9 + re-planning rules |
| 11 — authoring & push | Runna/Athletica/Stryd/TrainAsONE push structured workouts; Garmin Training API exists for this | Run-push verified in garminconnect 0.3.6; strength-push must be spiked first |
| read-MCP | Intervals.icu open REST API over the athlete's own data | Validated pattern: deterministic engine + thin read surface |

---

## Ordering & dependencies

```
6 zones ──┬──► 6b snapshot ──► 9 periodization ──► 10 recommender ──► 11 push-to-Garmin
7 load  ──┤         ▲                ▲                  ▲                  ▲
8 sets/overlap ─────┴────────────────┴──────────────────┘                  │
6 zones (pace targets) ────────────────────────────────────────────────────┘

read-MCP (conversational read layer) ── wraps 6/6b/7/8/9 marts + digest, built last
```

Rationale: **6 (zones)** and **7 (load)** are foundational corrections everything
downstream trusts — 6 is lighter and unblocks pace advice + Phase 11; 7 is the biggest
*correctness* fix; they are independent, reorder freely. **6b (snapshot)** rolls the
current standing into one place. **9 (periodization)** gives the system a notion of
"what block am I in" — without it the recommender advises in a vacuum (both Garmin DSW
and Xert XATA anchor to a phase/goal). **10** composes 6–9 into forward-looking,
re-planning-aware advice. **11** turns advice into a real workout on the watch. The
**read-MCP** is tooling, not a training phase — build it last, once the marts it
exposes have stabilized.

---

## Phase 6 — Personal training zones (`athlete_zones` mart)

**Goal.** Derive the athlete's HR and pace zones from recorded data so intensity advice
is deterministic instead of computed by hand each time — and keep them *fresh*.

**Why (evidence).** Saying "6:00/km = Zone 2" today required an ad-hoc query over
`activities` (10.2 km @ 6:13/km sat at avg_hr **128**, ~61 min in Z2; a 5:19/km run ran
avg_hr **143** with 38 min in Z3 — so the Z2/threshold boundary lives ~5:30–6:00).
Meanwhile `coach_thresholds.hr_z2_upper_bpm = 140` is a hardcoded placeholder literally
annotated *"approx Z2 ceiling; refine from user_settings zones"*.

**Data.** New mart `athlete_zones`: per-zone HR bounds + `z2_pace_ceiling_s_per_km`,
`threshold_pace_s_per_km`, `lthr_bpm`, plus `computed_at`, `source`, `stale`. Method
precedence: (1) Garmin `user_settings` HR zones if present, else (2) data-derived —
LTHR estimate + a pace↔HR regression over aerobic runs. Recomputed mart, never mixed
into core.

**Re-detection cadence (survey).** Recompute on a fixed cadence (~28 days —
TrainerRoad's published rationale: meaningful threshold change needs weeks) **and**
event-driven after a race/PR effort (Xert's "breakthrough" pattern). Digest warns when
zones are `stale`.

**Environment guards (survey).** Exclude hot-weather runs from the pace↔HR fit, or
regress temperature out — per-activity temperature is already in activity weather
payloads; TrainAsONE's rationale: heat-elevated HR is thermoregulatory drift, not
fitness loss. Optionally ingest Garmin heat/altitude acclimation into `daily_wellness`.

**Thresholds.** Retire the hardcoded `hr_z2_upper_bpm`; store zone bounds with a
`source` tag (device vs derived).

**Signals / surface.** `AEROBIC_LOW_SHORTAGE` reclassifies grey-zone vs true Z2 against
the *personal* ceiling. Digest headline exposes the Z2 pace ceiling so the coach can
say "keep easy runs under X:XX" without recomputing.

**Seam & tests.** Pure `zones.compute(activities, user_settings) → zone rows`; golden
test on onboarding + post-onboarding fixtures (shape drift applies here too).

**DoD.** `features` recompute uses personal zones; digest carries `zones` (+ staleness);
golden green.

**Risk.** Device zones may be stale/auto-set — document the precedence and flag
disagreement.

---

## Phase 6b — Athlete snapshot (`athlete_status` mart + `snapshot` command)

**Goal.** One place that answers "where do I stand right now" — current fitness
markers, zones, load/recovery state, and the active plan — as a compact, deterministic
read.

**Why (evidence).** In conversation the standing picture had to be reassembled by hand
from scattered tables (`fitness_markers`, `race_predictions`, `daily_metrics`,
`plan_template`) plus ad-hoc queries. There is no single "current stats + plan"
surface, and the recommender (Phase 10) needs exactly this as its input.

**Data.** New mart `athlete_status` (latest row, recomputed): VO2max + trend, race
predictions, personal zones (Phase 6), HRV baseline/SD, latest ACWR + reliability,
7-day load + shares, body weight trend, and the active `plan_template` /
periodization block (Phase 9) for the current week. Survey additions: Garmin
**Training Readiness** score, heat/altitude **acclimation**, and a **sleep-debt** fact
(7-day sleep vs personal baseline — WHOOP and Garmin both treat sleep history as a
first-class readiness input; the data is already in the DB). No new Garmin — reads
finished marts + core only.

**Command.** `garmin-coach snapshot` (prints/writes `reports/{date}/snapshot.json`);
optionally a short "Twoje aktualne staty" header in the coach report.

**Seam & tests.** Pure `snapshot.build(conn) → status dict`; golden test over fixtures.

**DoD.** `snapshot` emits current markers + zones + ACWR/load + active plan in one
object; green. Recommender (Phase 10) consumes it directly.

**Deps.** Best after 6 (zones) and 7 (honest load); harmless without them (fields
degrade to device values / None).

**Risk.** Keep it a *read* — a snapshot, never a recompute; all numbers come from the
marts.

---

## Phase 7 — Load model for strength & Hyrox (session-RPE) + niggle log

**Goal.** Stop under-counting non-cardio work so ACWR, monotony, and deload reflect
real stress; capture the subjective signals (RPE, soreness, niggles) the industry
treats as core inputs.

**Why (evidence).** `Siła` sessions get `training_load` **21.8** and **33.5** for
**74** and **68** minutes of work — because Garmin's load is HR-driven and lifting
barely moves HR. The model effectively can't see a full strength session. Hyrox/HIIT
sessions also carry no `total_sets`/`total_reps`. Survey: Foster sRPE is the scientific
standard and JOIN/Athletica/enduco/Humango all collect post-session RPE; Athletica
explicitly asks Hyrox athletes to log sets/reps/RPE for strength days.

**Data.** Capture a session-RPE (Borg CR10) + optional soreness/mood in a new core
table `session_rpe(activity_id, rpe, soreness, mood, source, notes)`. Compute Foster
`sRPE_load = rpe × duration_min`. Blend into `daily_metrics.load` with a discipline
rule (e.g. `max(garmin_load, sRPE_load)` for cardio; sRPE for strength). **Fallback:**
a default RPE per discipline so the pipeline stays deterministic when no RPE is logged.

**Niggle log (survey).** Sibling core table `niggle(date, body_part, severity, note)`
written by the same thin CLI writer. This is the local equivalent of Runna's
"Not Feeling 100%" (3–14-day dial-back) and JOIN/enduco soreness prompts: severity ≥
threshold surfaces a *reduced-mode* state in the digest; Phase 10 maps active niggles
to an avoid-list (synergy with Phase 8's exercise→pattern map).

**Thresholds.** Reuse `hard_te_load = 150`; add `rpe_hard`, strength weighting,
`niggle_reduced_mode_severity`. Feeds `monotony`/`strain`, `TWO_HARD_DAYS`,
`ACWR_OUT_OF_RANGE`, `DELOAD_ADVISED` (all currently blind to lifting).

**Command.** `garmin-coach log-rpe --activity <id> --rpe N [--soreness N]` and
`garmin-coach log-rpe --niggle <body_part> --severity N` (thin transport-free writers
to core).

**Seam & tests.** Pure `load.blend(...)`; golden regression proving a strength day now
contributes load and shifts weekly totals; niggle → reduced-mode state test.

**DoD.** A logged strength session raises daily/weekly load and ACWR; an active niggle
surfaces in the digest; golden green.

**Risk.** Subjective input — keep defaults so nightly automation never blocks on
missing RPE.

---

## Phase 8 — Per-set capture + modality/muscle overlap (finishes D9)

**Goal.** Capture per-set exercise data and model cross-session overlap (grip,
posterior chain, movement pattern).

**Why (evidence).** `activity_sets` is **empty (0 rows)** — the per-set ingestion
committed as Phase 0 D9 was deferred. Today's grip / posterior-chain warning (cable row
+ KB complex, then row/ski/farmer carry an hour later) was eyeballed, not computed.
Survey: no endurance app models this explicitly — a genuine differentiator; Athletica's
HYROX strength library organizes by **push / pull / hinge / squat / carry**, a sane
starting taxonomy, and Garmin Strength Coach organizes push/pull days with deload
weeks.

**Data.** ETL pull `get_activity_exercise_sets` → normalize into `activity_sets` (pure
normalizer, scalars only). New lookup mart mapping exercise → movement pattern / muscle
group (start from push/pull/hinge/squat/carry + grip). Daily/weekly `pattern_overlap`
metric: same pattern loaded on adjacent sessions.

**Thresholds.** `pattern_overlap_high` in `coach_thresholds`.

**Signals.** New `PATTERN_STACK` / `MUSCLE_OVERLAP` (warn) when high-load patterns
repeat without recovery.

**Seam & tests.** Set normalizer unit-tested (both fixture shapes); overlap computation
golden test. ETL write stays in the pull pipeline; mart reads only.

**DoD.** `activity_sets` populated on backfill; overlap metric in mart; signal fires on
a constructed stack; green.

**Risk.** Exercise-name drift across sessions — maintain the exercise→pattern map by
hand.

---

## Phase 9 — Race-date periodization + race-day pacing (NEW, promoted by the survey)

**Goal.** Give the system a goal: an event date, training blocks counted back from it,
taper awareness, and a deterministic race-day pacing plan. Without this, Phase 10
recommends sessions with no notion of "3 weeks out vs 20 weeks out".

**Why (survey).** The single most universal industry capability: TrainerRoad Plan
Builder (Base → Build → Specialty with automatic tapers and openers around A/B/C
events), Stryd (plans timed to finish on race day), Athletica (race date + weekly hours
→ adaptive HYROX plan), Runna, Garmin Coach, TrainAsONE. The repo's `plan_template` is
a static weekly pattern with no concept of race date, block, or taper.

**Data.** New core table `goal_event(date, type 'hyrox'|'run_race', priority A/B/C,
target, note)`. Extend `plan_template` into a date-anchored `plan_block`
(base/build/peak/taper/deload weeks counted **back from the event date**; deload
cadence reuses Phase 5's `DELOAD_ADVISED` thresholds). Weekly rollup gains
`weeks_to_event` and `block`.

**Signals.** `TAPER_ACTIVE` (suppresses intensity recommendations), `RACE_PROXIMITY`
facts in the digest.

**Race-day pacing (survey).** Deterministic `race_plan(event, athlete_status) →
per-segment targets`: for Hyrox, 8×1 km run paces + station effort caps from current
threshold pace/HR (Garmin PacePro / ROXFIT "PaceMe" analogue); output into
`reports/{race_date}/`. Include a one-paragraph fueling note here — that covers ~90% of
nutrition's value without building any nutrition feature. Optionally authored as a
Phase-11 multisport workout later.

**Seam & tests.** Pure `periodize(event, today, history) → block + week intent`; golden
tests over fixed dates (deep in base, peak week, taper week, race week, no event).
`race_plan` golden test from a fixture snapshot.

**DoD.** Digest carries `block`/`weeks_to_event`; `TAPER_ACTIVE` fires in a constructed
taper; `race_plan` renders per-segment targets; green.

**Deps.** 6 (threshold pace for race targets), 6b (snapshot as `race_plan` input).

**Risk.** Don't over-model: blocks are labels + week intents, not a generated day-by-day
plan — the weekly template stays the athlete's, the engine annotates it.

---

## Phase 10 — Prospective session recommender (re-planning-aware)

**Goal.** Flip the engine from retrospective reading to forward advice: given readiness
+ plan + block + deficits, recommend today's/tomorrow's intensity and what to avoid —
and when the week falls apart, propose how to re-plan instead of pretending it didn't.

**Why (evidence).** Today's verdict — "two quality sessions OK because HRV 88 vs
baseline 68, but ACWR 1.21 is top of range and 0% Z2, so run Z2 tomorrow" — was
assembled by hand from the digest, `plan_template`, and zones. Survey: this shape is
almost exactly Garmin Daily Suggested Workouts (readiness + load-share deficits →
today's workout) and Xert XATA (surplus/deficit + freshness + phase) — both anchored to
a phase/goal, hence the Phase 9 dependency.

**Data.** Pure `recommend(digest, plan_block, zones) → {intended_type, intensity_cap,
pace_target, rationale, avoid[]}`. No Garmin. New `recommendation` block in
`digest.json` and a "Rekomendacja na dziś" section in the coach report.

**Re-planning rules (survey).** Industry consensus (Runna Plan Realignment, TrainAsONE,
Humango): missed sessions change the *plan*, not just the day. Deterministic and tiny:
if ≥N planned sessions were missed in the trailing week (already computable from Phase
5 plan-vs-actual), emit one of three **cited options** — *extend*, *rebuild toward the
event date* (drop lowest-priority sessions first), or *continue* — instead of silently
recommending the next template day.

**Adaptation triggers (survey).** Consume Phase 7 subjective inputs: yesterday
hard-RPE + low readiness ⇒ cap today's intensity (the TrainerRoad survey loop as one
rule); active niggle ⇒ avoid-list gains the mapped movement patterns (Phase 8 map);
`TAPER_ACTIVE` ⇒ suppress intensity suggestions.

**Thresholds.** None new beyond `replan_missed_sessions` — composition rules over
existing ACWR / HRV / aerobic-target / deload / taper thresholds. Every recommendation
must cite which signals drove it (explainable — the industry's weakest point is
unpublished black-box models; keep the advantage).

**Command.** Fold into `garmin-coach report`, or a dedicated `garmin-coach recommend`.

**Seam & tests.** Deterministic state→recommendation mapping; golden test over
representative digest states (green day, hot ACWR, HRV low, aerobic deficit, deload
advised, taper week, missed-week re-plan, active niggle).

**DoD.** Report renders a cited recommendation; missed-week fixture produces the three
cited options; golden green.

**Deps.** 6 (pace caps), 7 (honest load + RPE/niggles), 9 (block awareness).

**Risk.** Stays a "reading + suggestion", never a prescription — keep the disclaimer.

---

## Phase 11 — Structured workout authoring & push to Garmin (run first, strength spiked)

**Goal.** Turn a recommendation into a concrete Garmin workout — tempo run with pace
targets, or a strength session with named exercises/sets — and schedule it to the
watch.

**Why.** Direct user request; industry-standard delivery (Runna syncs two weeks of run
workouts to Garmin every Monday; Athletica, Stryd, TrainAsONE, enduco all push
structured workouts).

**Architecture (important).** This is a NEW **outbound** transport path and it *bends
the golden rule*, so isolate it exactly like `client.py`:

- `author.py` — **pure**: `recommendation → workout spec (JSON)` written to
  `reports/{date}/`. Deterministic, unit-tested, no network.
- `publish.py` — **transport, out-of-seam**: reads the spec and calls garminconnect
  workout-create / schedule endpoints. The coach/recommender read path still never
  writes.
- Commands: `garmin-coach author --date` (pure spec) and `garmin-coach push --date`
  (explicit transport; `--dry-run` default; never invoked from the nightly automation).

**Split delivery (survey — de-risk).**

1. **Run-push first (verified surface).** garminconnect 0.3.6 documents typed workout
   upload and scheduling: `upload_running_workout()` (+ cycling/swimming/walking/
   hiking), `schedule_workout(workout_id, date)`, `delete_workout()`,
   `unschedule_workout()`; workout classes include `RunningWorkout` and
   `MultiSportWorkout`. Ship pace-target run workouts on this.
2. **Strength-push as a separate spike.** **No `StrengthWorkout` class is documented**;
   Garmin's partner Training API is cardio-oriented; Runna pushes only run-type
   workouts and keeps strength in-app. Probe the private workout-create endpoint with a
   hand-built strength payload *before* writing any seam code; if it fails, fall back
   to Runna's model — strength stays local (spec + report, watch-free execution) —
   without blocking the run-workout deliverable.

**Seam & tests.** `author` unit-tested (spec ↔ Garmin workout JSON); `push` validated
by a live run, idempotent by workout name+date (re-push must not duplicate — this
matches how Runna re-syncs its scheduled fortnight).

**DoD.** `author` produces a valid spec from a recommendation; `push --dry-run` shows
the payload; a confirmed live push creates exactly one scheduled *run* workout; author
tests green. Strength spike outcome documented (endpoint works / fallback chosen).

**Deps.** 6 (pace targets), 10 (what to prescribe); 7/8 sharpen strength authoring.

**Risk.** Writing is near-irreversible (creates account-side workouts). Dry-run by
default, explicit confirm, never auto-schedule from the nightly path.

---

## Read-MCP — conversational read layer over the local marts (tooling, build last)

**Goal.** A thin, read-only MCP server exposing the finished marts, digest, and
snapshot so a chat session can pull "current stats", the latest digest, or recent
activities in one call — instead of hand-written SQL/Python.

**Why (evidence).** This very kind of session mixed two read patterns: hand-written
SQLite queries over the local DB (digest, activities, zone deduction) and ad-hoc
`mcp__garmin__*` calls for same-day data. The first pattern is repetitive and would
collapse to a single tool call. Survey precedent: Intervals.icu — a deterministic data
platform with a thin open API surface, not an AI product.

**Not a second Garmin MCP.** A `mcp__garmin__*` server already exists (~150 tools,
verbose payloads) and stays scoped to **ad-hoc exploration / fixtures** per the golden
rule. This new MCP reads the **local DB only** — the finished marts, `digest.json`,
`snapshot.json`, recent `activities`. It never calls Garmin live, so the golden rule
holds.

**Surface.** Small, e.g. `get_snapshot`, `get_digest`, `get_recent_activities(n)`,
`get_weekly(week_start)`, `get_zones`, `get_recommendation`. Each wraps a function the
CLI already uses; returns compact JSON (no profile URLs / auth scopes / per-minute
series).

**Build.** Thin server (the repo's `mcp-builder` skill applies). Reuses the same pure
readers as `report`/`snapshot`; no new computation. Ship after the marts (6, 6b, 7, 8,
9) have stabilized so the surface doesn't churn.

**Risk.** Read-only by construction — no write tools, no Garmin transport in this
server. Keep it a window onto the deterministic engine, nothing more.

---

## Explicit non-goals (informed by the survey)

- **No LLM chat layer inside the engine** (WHOOP Coach / Garmin "Active Intelligence"
  territory) — the coach skill + read-MCP already provide the conversational surface
  over deterministic numbers, which is a *better* architecture for auditability.
- **No nutrition tracking.** Only Garmin Connect+ and Ladder do it among the surveyed
  set; it needs a food-logging input surface for little single-athlete value. The
  race-day fueling note in Phase 9 covers most of it.
- **No social / streaks / motivation UX.** SaaS retention features, not signal.
- **No black-box readiness score.** Keep the digest's cited-signal explainability —
  the industry's weakest point (RLGL, Humango, TrainAsONE are unpublished models) is
  exactly where a local deterministic engine can be strictly better.

## Deferred / open questions

- Multi-sport `discipline` weighting in weekly rollups (deferred from Phase 5); ski
  touring season will force it.
- VO2max / threshold **trend charts** and PDF/Notion export (deferred in BUILD §12).
- DFA-alpha-1 style in-exercise HRV readiness (AI Endurance) — needs beat-to-beat data
  the current ETL doesn't pull; revisit only if a real need shows up.
- Weather-forecast-aware pace adjustment for *upcoming* runs (TrainAsONE) — needs a
  forecast source, i.e. a new inbound transport; keep out until Phase 6's
  historical-temperature guard proves insufficient.

## Source index (primary unless noted)

- TrainerRoad: [Adaptive Training Help Guide](https://support.trainerroad.com/hc/en-us/articles/4409099184283-Adaptive-Training-Help-Guide) · [AT recommendations](https://support.trainerroad.com/hc/en-us/articles/4404968208923-How-Does-Adaptive-Training-Recommend-Workouts) · [AI FTP](https://support.trainerroad.com/hc/en-us/articles/4415864080155-How-to-Use-AI-FTP-Detection) · [AI FTP 28-day rationale](https://support.trainerroad.com/hc/en-us/articles/8697549554587-Why-is-AI-FTP-Detection-Only-Available-Every-28-Days) · [Plan Builder](https://support.trainerroad.com/hc/en-us/articles/360037923191-Plan-Builder-Overview) · [TrainNow](https://support.trainerroad.com/hc/en-us/articles/360057075531-TrainNow-Overview) · [RLGL blog](https://www.trainerroad.com/blog/new-sport-types-supported-by-trainerroad-and-red-light-green-light/)
- TrainAsONE: [how it works](https://trainasone.com/how-it-works) · [adaptive plan](https://www.trainasone.com/features/adaptive-training-plan) · [weather FAQ](https://trainasone.com/faqs/2026/how-does-trainasone-handle-temperature-and-weather) · [race elevation FAQ](https://trainasone.com/faqs/2024/does-trainasone-consider-the-elevation-of-my-target-race)
- Athletica: [home](https://athletica.ai/) · [HYROX setup](https://athletica.ai/getting-started-hyrox-athletica/) · [HYROX strength library](https://athletica.ai/hyrox-strength-training-athletica-global-library/) · [Garmin workout sync](https://support.athletica.ai/hc/en-us/articles/23513848128923-How-to-Get-Your-Athletica-Workouts-To-Sync-With-Garmin) · [release notes](https://app.athletica.ai/release-notes)
- Humango: [home](https://humango.ai/) · [for triathletes](https://humango.ai/humango-for-triathletes2/)
- AI Endurance: [product](https://aiendurance.com/en/product) · [readiness & durability (DFA α1)](https://aiendurance.com/blog/readiness-to-train-and-durability-hrv-metrics)
- Runna: [Plan Realignment](https://support.runna.com/en/articles/10026375-how-to-use-the-plan-realignment-feature) · [Not Feeling 100%](https://support.runna.com/en/articles/13531498-how-to-use-not-feeling-100) · [aches & pains](https://support.runna.com/en/articles/13225357-adjusting-your-plan-around-minor-aches-and-pains) · [strength](https://support.runna.com/en/articles/6262149-everything-you-need-to-know-about-strength-training-for-runners) · [Garmin sync (run-only)](https://support.runna.com/en/articles/6169639-using-your-garmin-watch-with-runna)
- WHOOP: [Coach announcement](https://www.whoop.com/us/en/thelocker/whoop-unveils-the-new-whoop-coach-powered-by-openai/) · [Coach support](https://support.whoop.com/s/article/How-to-Use-the-AI-Powered-WHOOP-Coach?language=en_US) · [Strain Target](https://www.whoop.com/us/en/thelocker/strain-coach/)
- Garmin: [Training Readiness](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/training-readiness/) · [Daily Suggested Workouts FAQ](https://support.garmin.com/en-US/?faq=oYknGZ910l1pfBNzkDHX6A) · [Coach overview](https://www.garmin.com/en-US/garmin-coach/overview/) · [Strength Coach](https://www.garmin.com/en-US/garmin-coach/strength/) · [Strength Coach FAQ](https://support.garmin.com/en-US/?faq=pVtmVTZz7C97GZHxtMWgs8) · [Connect+ press release](https://www.garmin.com/en-US/newsroom/press-release/wearables-health/elevate-your-health-and-fitness-goals-with-garmin-connect/) · [Active Intelligence FAQ](https://support.garmin.com/en-US/?faq=kWi5DoaMPZ4VCJBA0lFWP7) · [heat/altitude acclimation](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/heat-and-altitude-acclimation/) · [PacePro](https://support.garmin.com/en-US/?faq=svpm2I38YB2sU5CiqFXyfA) · [Training API (partner-gated)](https://developer.garmin.com/gc-developer-program/training-api/)
- JOIN: [why JOIN](https://join.cc/why-join) · [integrations](https://help.join.cc/hc/en-150/articles/4404775688081-Integrations) · [workout player & Garmin](https://help.join.cc/hc/en-150/articles/20489720343185-Workout-player-Garmin-connect)
- enduco: [home](https://enduco.app/) · [v7 announcement](https://enduco.app/blog/relaunch-with-strategy-enduco-launches-version-7-featuring-new-technology-and-creator-collaborations)
- HRV4Training: [Pro user guide](https://marcoaltini.substack.com/p/hrv4training-pro-user-guide) · [Altini: HRV-guided training](https://medium.com/@altini_marco/heart-rate-variability-hrv-guided-training-to-improve-performance-24b0ec24e6f8)
- Intervals.icu: [open API](https://www.intervals.icu/features/open-api/) · [API thread](https://forum.intervals.icu/t/api-access-to-intervals-icu/609) · [API cookbook](https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090) · [wellness](https://www.intervals.icu/features/wellness/) · [power curve](https://www.intervals.icu/features/power-curve/)
- Xert: [XATA glossary](https://www.baronbiosys.com/glossary/xert-adaptive-training-advisor/) · [training with XATA](https://www.baronbiosys.com/training-with-the-xert-adaptive-training-advisor/)
- Stryd: [auto-CP](https://blog.stryd.com/2019/07/09/introducing-auto-calculated-critical-power/) · [CP definition](https://help.stryd.com/en/articles/6879345-critical-power-definition) · [plan FAQ](https://help.stryd.com/en/articles/8923322-stryd-training-plan-faq)
- ROXFIT: [how it works](https://roxfit.app/how-it-works/) · Ladder: [home](https://www.joinladder.com/) · [nutrition PR](https://www.businesswire.com/news/home/20251027087979/en/)
- Toolchain: [garminconnect on PyPI (0.3.6)](https://pypi.org/project/garminconnect/) · [python-garminconnect GitHub](https://github.com/cyberjunky/python-garminconnect) · *(secondary)* [the5krunner on Garmin API & strength apps](https://the5krunner.com/2026/03/24/garmin-connect-plus-strength-apps/)
