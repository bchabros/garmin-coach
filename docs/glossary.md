# Domain glossary

Shared vocabulary for the garmin-coach project. Use these terms consistently in
code, docstrings, PRDs, and ADRs.

## Data layers (medallion)

- **raw** - append-only `raw_payloads`; original Garmin JSON, never overwritten. A row's
  identity is `(endpoint, ref_date, fetched_at, payload_sha)`: the content hash is part
  of the key because `fetched_at` only resolves to the second (ADR 0018). Enrichment
  payloads are filed under the activity's own day, not the requested range start.
- **core** - normalized, upserted-by-PK tables (`activities`, `daily_wellness`,
  `sleep`, `hrv_nightly`, `sync_state`, plus the manually-logged `session_rpe`,
  `niggle` and `manual_activity_sets`). The system of record. Most core tables are
  ETL-written from Garmin; `session_rpe`/`niggle` are ground truth written by
  `garmin-coach log-rpe` (Phase 7), `manual_activity_sets` by `garmin-coach log-sets`
  (issue #60).
- **mart** - recomputed, derived tables (`daily_metrics`, `weekly_metrics`,
  `weekly_plan_actual`).
  Never a system of record; safe to drop and rebuild from core.

## Metrics (mart)

- **load_day** - sum of `training_load` across a day's activities; 0 on rest days.
- **acute7** - trailing-7-day (incl. today) sum of `load_day` / 7.
- **chronic28** - trailing-28-day sum of `load_day` / 28.
- **ACWR** - acute:chronic workload ratio = `acute7 / chronic28`. Risk > 1.5,
  detraining < 0.8, comfort zone 0.8-1.3.
- **n_chronic** - number of days in the 28-day window that have a real data row
  (date >= `data_start`). A credibility counter: while `n_chronic < 28`, ACWR is
  overstated and must be reported as indicative only.
- **hrv_baseline** - median of `avg_hrv` over the computed window (whole available
  window, capped to trailing 60 nights). Same value on every row of a run.
- **hrv_sd** - sample standard deviation (ddof=1) of `avg_hrv` over the same window.
- **hrv_low_flag** - 1 when `avg_hrv < hrv_baseline - 1 * hrv_sd`.
- **Load buckets (load balance)** - how one session's blended load is shared between
  easy, hard and anaerobic work, tuned to reproduce Garmin's own 28-day load balance.
  Every cardio session is split, never filed whole: the anaerobic part
  (`load_anaerobic`) is the anaerobic Training Effect at half weight against the aerobic
  one; the rest is divided between `load_low` (time in zones 1-2) and `load_high` (zones
  3-5) by intensity-weighted zone time, a hard minute counting for more than an easy
  one. A session with no zone time falls back to the old Training Effect rule
  (`aero_te < 2.5` is low). `load_strength` takes blended `Siła` load whole (Phase 7).
  The three cardio buckets feed `AEROBIC_LOW_SHORTAGE`; `load_day` sums all four.
- **sRPE (session-RPE load)** - Foster load from a subjective Borg CR10 rating:
  `sRPE = srpe_load_scale x rpe x duration_min`, scaled (`srpe_load_scale`, default
  0.3) into Garmin-load units so it is comparable to `training_load` (Phase 7).
- **load blend** - the per-activity rule turning Garmin load + sRPE into one
  `load_day` contribution: `Siła` takes sRPE (Garmin is blind to lifting), every other
  discipline takes `max(garmin_load, sRPE)` so a logged RPE can only raise an honest
  cardio load. `Siła` falls back to `sila_default_rpe` (default 7) when no RPE is
  logged; cardio gets no default injection.
- **load_strength** - the blended `Siła` load bucket; part of `load_day` (so ACWR /
  monotony / strain / hard-day logic see lifting) but excluded from the aerobic
  balance shares.
- **HR-zone minutes (z1..z5_min)** - time in each heart-rate zone, from
  `activities.hr_z1..z5_s` / 60. Answers "distribution of time", distinct from load
  buckets which answer "distribution of stimulus".

## Training terms

- **TE (Training Effect)** - Garmin's per-activity aerobic (`aero_te`) and anaerobic
  (`anaero_te`) impact scores, ~0-5.
- **Training load** - Garmin's `activityTrainingLoad`, the EPOC-based load of a session.
- **RHR** - resting heart rate; primary source `daily_wellness.rhr`, fallback
  `sleep.resting_hr`.
- **Discipline** - human-facing sport grouping (Bieganie, Hyrox/HIIT, Sila, Skitury,
  Trail) mapped from the Garmin `gtype`.

## Coach skill anatomy (skills/coach/, issue #51)

- **router** - `skills/coach/SKILL.md`: the only file always in context. It carries the
  frontmatter description that decides whether the skill loads at all, the rails that
  must hold for every flow, the CLI bootstrap, and the routing gates. It holds no flow
  detail.
- **reference file** - one `skills/coach/references/*.md` per flow (report, planning,
  authoring), read lazily, only once its flow starts. This is what keeps a report-only
  conversation from carrying the authoring vocabulary.
- **routing gate** - the categorical "before you do X, you MUST read
  `references/<file>.md`" line in the router. Guarded by `tests/test_coach_skill_routing.py`:
  every gated path exists, and every reference file is gated.

## Coach terms (mart -> report)

- **digest** - compact recomputed view built by `build_digest(conn, ...)` from
  `daily_metrics`, `weekly_metrics`, `weekly_plan_actual`, and
  `training_status_daily`: a headline block plus a list of signals.
  Serialized to `reports/{date}/digest.json`; the token boundary the coach skill reads
  instead of raw mart rows. Non-durable, not a system of record.
- **signal** - a single coach finding `{code, severity, facts, garmin_agrees?}` with
  `severity` in `info|warn|alert` and `facts` a flat dict of scalars. Codes:
  `AEROBIC_LOW_SHORTAGE`, `ACWR_OUT_OF_RANGE`, `HRV_LOW_MORNING`, `TWO_HARD_DAYS`,
  `HRV_SLEEP_CONFOUND`, `DELOAD_ADVISED`, `NIGGLE_REDUCED_MODE`.
- **niggle** - a logged body-part soreness/pain (`niggle` core table, PK `(date,
  body_part)`, severity 1-5). Ground truth written by `garmin-coach log-rpe --niggle`,
  not from Garmin (Phase 7).
- **active niggle** - a niggle whose latest per-body-part entry falls within the
  trailing `niggle_active_days` (default 7) window ending at the report horizon; one
  log stays active for the window, a lower-severity re-log clears it early.
- **reduced-mode** - the `NIGGLE_REDUCED_MODE` signal (severity `warn`): an active
  niggle at/above `niggle_reduced_mode_severity` tells the coach to dial back. The
  local equivalent of Runna's "Not Feeling 100%" dial-back.
- **report horizon** - the single `to_date`/window that scopes a digest; daily facts,
  weekly facts, and weekly signals must all sit at or before this horizon. Sources are
  bounded by it too, not merely stamped with it (ADR 0017).
- **matches_horizon** - on a standing block (the digest's `zones` section,
  `snapshot.json`): whether that singleton row was computed for this report's horizon.
  `False` means a current standing is being shown beside as-of daily/weekly facts, so
  the narrative should hedge it; `None` when either date is unknown.
- **AEROBIC_LOW_SHORTAGE** - too little easy work ("add Z2"): our easy-load share over
  the last 28 days (the span Garmin's balance covers, not the 7-day headline window) is
  below Garmin's own lower bound for low-aerobic load, expressed as
  a share of Garmin's balance total (`ml_aero_low_min` over the three `ml_*` sums; about
  23% in September 2026). On a day Garmin publishes no bound the fixed fallback share
  (`aero_low_target_share`, 0.25) stands in. Computed from our buckets; cross-checked
  against Garmin's `training_status_daily.balance_phrase` via `garmin_agrees`.
- **garmin_agrees** - whether our derived signal concurs with Garmin's own phrase for
  the same finding; strengthens or hedges the report wording, never a passthrough.
- **report** - the dated coach artifact under `reports/{date}/`: `report.md` (narrative
  written by the skill from the digest), `digest.json`, `snapshot.json`, and two PNG charts
  (`hrv_band.png`, `acwr.png`). `garmin-coach report` produces everything except the
  Markdown narrative.
- **athlete profile** - private qualitative context in `memory/athlete-profile.md`.
  Current-state sections hold today's understanding; the final dated `Decyzje` log
  preserves replaced decisions that still explain the coaching. About 150 lines
  triggers a consolidation proposal, never silent deletion. Edits require approval
  of the exact diff; the profile stays gitignored (ADR 0025).
- **report retention** - manual age-based expiry of an explicit set of report files,
  after lasting narrative lessons have reached the profile with source dates.
  `reports prune` previews by default and deletes only with `--confirm`; it preserves
  digest and snapshot unless explicitly included, and always preserves workout specs,
  push receipts and unknown files. The whole report folder is not a disposable cache
  (ADR 0025).

## Movement terms (mart -> overlap)

- **exercise set** - one logged work set of a strength/Hyrox activity, captured from
  Garmin's `exerciseSets` into the `activity_sets` core table (Phase 8). Only `ACTIVE`
  sets are kept; `REST` sets are dropped.
- **movement pattern** - a coarse classification of an exercise's movement:
  `push`, `pull`, `hinge`, `squat`, or `carry`. Mapped from Garmin's exercise
  `subcategory` via the hand-curated `exercise_pattern` core table.
- **muscle group** - the tissue an exercise loads (`chest`, `back`, `posterior`,
  `quads`, `shoulders`, `grip`, `core`, ...). The second axis of `exercise_pattern`;
  `grip` lives here (carries and pulls both tax it), not as a sixth movement pattern.
- **pattern_load** - a session's Phase 7 blended load split across its movement
  patterns / muscle groups by set-share: `(sets of that key / mapped sets) x
  session load`. Robust to a missing `max_weight` (Hyrox / bodyweight).
- **pattern overlap** - the same pattern or muscle group loaded above
  `pattern_load_floor` on two consecutive days: `overlap = min(load_D, load_D-1)`.
  Materialized in the long-format `pattern_overlap` mart; a single rest day clears it.
- **PATTERN_STACK / MUSCLE_OVERLAP** - the two `warn` signals (Phase 8) that fire when a
  movement pattern (respectively muscle group) overlaps at/above `pattern_overlap_high`
  on the report's latest day; `facts.keys` names the offending keys.
- **movement coverage** - the digest's `movement` fact: `sets_total`, `sets_unmapped`,
  and the `unmapped` subcategory names, so exercises missing from `exercise_pattern`
  stay visible (the overlap read is partial until they are mapped). Counted over
  `movement_sets`, so a logged circuit contributes its stations, not the watch's row.
- **manual set overlay** - the stations of a circuit the watch recorded as one nameless
  set (a Hyrox / group-HIIT session arrives as a single `UNKNOWN` set), logged by hand
  into the core table `manual_activity_sets` with `garmin-coach log-sets` or the MCP
  `log_sets` tool. Same shape as `activity_sets`, one row per station (set-share, not
  rounds x stations), never written or overwritten by the ETL. A re-log replaces the
  activity's stations wholesale. See ADR 0022.
- **movement_sets** - the view the overlap mart reads instead of `activity_sets`: an
  activity's manual rows when it has any, its captured rows otherwise (per-activity
  supersede, never a row-level merge - the captured `UNKNOWN` row stands in for the
  same work the stations describe). Its `source` column names which answered.

## Weekly terms (mart -> weekly)

- **complete week** - a Monday-Sunday span whose seven days all lie at or before
  yesterday. Only complete weeks are rolled up into `weekly_metrics`; the in-progress
  current week is skipped so weekly figures never lie from 1-2 days of data.
- **weekly rollup** - the derivation of one `weekly_metrics` row per complete week
  purely from `daily_metrics` (a mart-from-mart step). Never touches Garmin;
  recomputable and safe to rebuild.
- **plan of record** - the weekly plan the athlete actually agreed to, authored as
  `plans/<monday>_week.md`. The Markdown file is the source of record (it carries the
  paces, HR caps, rationale, and revision log the intent vocabulary cannot hold); the
  `plan_week` table is a derived cache of its intent column. See ADR 0015.
- **plan week (override)** - the cached per-week rows ingested from a plan file, keyed
  `(week_start, dow)`. Present only for weeks the athlete authored.
- **planned-intent resolver** - the single read path for "what was planned for this
  date": the authored `plan_week` for that week, else the `plan_template` fallback. It
  always reports which of the two answered (`source`). No code reads `plan_template`
  directly - that drift is exactly what issue #21 fixed.
- **plan template (fallback)** - the static day-of-week table that answers for weeks
  with no authored plan. A repeating *shape*, not a plan the athlete agreed to.
- **planned intent** - the training category the plan of record assigns to a date:
  `rest | easy | tempo | strength | hyrox | crossfit | quality`. It names the session
  the athlete meant.
- **actual intent** - what the day turned out to be, inferred from load alone:
  `quality` when the day's load reaches `hard_te_load` (or has anaerobic load),
  `strength` when strength load carries at least half a day with no anaerobic work
  (Garmin is HR-blind to lifting, so a real session scores a tiny load and would
  otherwise read `easy`), `easy` for any lighter activity, `rest` for no activity. A
  day the athlete trained without wearing the watch is invisible to the system and
  reads as `rest` (an ETL limitation, by decision, not a bug).
- **intent rank (hardness ladder)** - how hard each planned intent is, as one order
  over the whole vocabulary: `rest 0 < easy 1 < tempo = strength 2 < hyrox =
  crossfit = quality 3`. The top three share a rank because they are different
  sessions, not different intensities. One *scale* reached two ways: the recommender
  descends it by the plan's words (see *downgrade*), and the *plan guard* refuses to
  climb it by what a spec measures (see *hardness*). See ADR 0021 and ADR 0024.
- **hardness (workout spec)** - how hard an authored session actually is, measured
  from the targets its steps will put on the watch: `easy`, `threshold` or `hard`,
  on the same numeric scale as *intent rank* (1, 2, 3). The hardest step decides and
  a band's harder edge decides - the faster pace, the higher heart rate. The
  boundaries are the athlete's own: the Z2 pace ceiling and Z2 heart-rate bound for
  `easy`, threshold pace (within the authoring chain's margin) and LTHR for
  `threshold`. **Absent means unmeasured** - no zone ladder, an exercise sport, or a
  Hyrox run-station sequence - and the *plan guard* then ranks the session type
  instead, as it did before ADR 0024. Its own words on purpose: a threshold interval
  session is not a `tempo` session, it is one that measures `threshold`.
- **intent class** - the measurable class a planned intent collapses to for
  comparison: `rest`, `easy`, `strength`, or `quality` (which absorbs `tempo`,
  `hyrox`, `crossfit`). The planned vocabulary is deliberately richer than the mart
  can observe - load numbers cannot tell a crossfit session from a hyrox one - so
  adherence compares classes. Without it every intent outside `rest | easy | quality`
  would score as a permanent mismatch.
- **plan adherence** - the fraction of the week's seven days whose actual intent
  matches the planned intent *at intent-class granularity*. The report also shows the
  *direction* of each mismatch, since the DoD asks to surface divergence, not just a
  number.
- **weekly plan-vs-actual fact** - the per-day planned intent, actual intent, and match
  flag materialized alongside `weekly_metrics`. Planned and actual are stored as
  authored/observed (not collapsed), so the grid shows what was meant next to what
  happened; only `match` compares classes. The digest reads these stored weekly facts
  instead of re-deriving mismatch direction from a later plan.
- **PLAN_MISSING** - the informational signal that the current week has no authored
  plan, so the template is answering for it. A statement of fact and the coach's cue to
  propose a week; proposing is never automatic.
- **monotony / strain (Foster)** - `monotony` = mean daily load / SD of daily load
  across the week (`NULL` when uncomputable, e.g. fewer than two training days);
  `strain` = weekly load x monotony. Classic overtraining flags.
- **deload (retrospective)** - a descriptive fact that a completed week's `load_total`
  dropped by at least `deload_drop_pct` versus the preceding weeks; recorded from the
  mart, not an alert.
- **deload advised (prospective)** - the `DELOAD_ADVISED` signal: fires when there is
  enough history (`deload_min_history_weeks`) and `load_total` rose for
  `deload_load_rise_weeks` consecutive weeks and either `acwr_end` exceeds
  `acwr_risk_high` or `monotony` exceeds `monotony_high`. Silent when history is too
  short (it never guesses).

## Snapshot terms (mart -> snapshot)

- **athlete snapshot** - the current-standing read: a singleton `athlete_status` mart
  row (`id = 1`) mirroring where the athlete stands now - fitness markers, personal
  zones, HRV/load/recovery state, and the active plan. Recomputed as the tail of
  `features` after `weekly.rollup` and `zones.rollup`, serialized to
  `reports/{date}/snapshot.json`. A same-run copy of finished marts + core, never a
  system of record and never a recompute of the underlying numbers.
- **computed_at (snapshot)** - the as-of date the row is built for; every "latest" read
  is scoped to `date <= computed_at`, so a backfill to a past date reproduces that
  day's standing. `planned_intent_today` uses this date's weekday, not the wall clock.
- **trend delta** - a marker's signed change (`vo2max_delta`, `weight_delta`,
  `hrv_delta`) against the earliest reading on or after `computed_at - lookback`.
  Computed over whatever history exists; NULL only when the available span is below
  `snapshot_trend_min_span_days`. Never inferred from a single point.
- **span_days** - the actual number of days the trend delta spans, exposed alongside it
  (`vo2max_span_days`, ...); it can be shorter than the configured lookback while
  history is still accruing, letting the coach hedge ("over the last 24 days").

## Periodization terms (core + mart -> plan_block)

- **goal event** - a target race the athlete is training toward (`goal_event` core
  table): a date, a `type` (`hyrox | run_race`), a `priority` (A/B/C), and a target
  time in seconds. Manually entered ground truth, never from Garmin.
- **status (goal event)** - *whether the athlete will start*: `confirmed | tentative`.
  Only a `confirmed` event can anchor blocks or fire `TAPER_ACTIVE` - the system never
  tapers for a race the athlete may skip.
- **date_precision (goal event)** - *whether the exact day is known*: `exact | approx`.
  Orthogonal to `status`: a race can be certain with a fuzzy date, or dated but
  uncommitted. An `approx` date still drives every block; it only makes the report ask
  for the date to be pinned as the taper window approaches.
- **anchor event** - the goal event a *week* counts back from: the nearest `confirmed`
  priority-A race **on or after that week**. A function of the week, not of today, so a
  race keeps labelling the weeks that led up to it after it has been run - "what am I
  training for now" and "what block was that week in" are different questions, and only
  the first goes blank once the race is over. Weeks with no race ahead of them get no
  `plan_block` row, and `block` / `weeks_to_event` read NULL: the system says it does not
  know what is being trained for rather than inventing a phase.
- **current anchor** - the nearest *upcoming* confirmed priority-A race, i.e. what the
  athlete is training for **right now**. What `event list` marks and what goes blank the
  day after the goal race. Distinct from a past week's anchor.
- **block** - the phase of the training cycle a week sits in: `base | build | peak |
  taper`. A pure countdown from the anchor event's date. `taper`, `peak`, and `build`
  have fixed lengths; `base` absorbs everything earlier (bounded left by `data_start`),
  so the athlete is always in some block.
- **weeks_to_event** - whole weeks from a week's Monday to the anchor event's race week;
  0 in the race week itself.
- **planned deload** - the `is_deload` flag on a `plan_block` week: a recovery week the
  *plan* prescribes, placed every `deload_every_n_weeks` counted back from the end of
  its block and only inside `base` / `build` (never `peak` or `taper`, which are
  downshifts already). Anchoring to the block's end means the athlete always enters the
  next block fresh. A block never opens with a deload - its first week is for ramping up.
- **planned deload vs deload advised** - two answers to one question, deliberately kept
  apart. `is_deload` is what the plan intended; `DELOAD_ADVISED` is what the actual load
  did. Neither overrides the other; the divergence between them is itself the finding -
  the same plan-vs-actual shape as `weekly plan-vs-actual fact`.
- **plan_block** - the periodization mart, one row per week keyed by `week_start`,
  spanning the whole plan horizon *including future weeks* (unlike `weekly_metrics`,
  which only holds weeks that already happened). The single source of truth for `block`,
  `weeks_to_event`, and `is_deload`.
- **TAPER_ACTIVE** - the signal that the current week's `block` is `taper` *and the race
  has not yet been run*. The race week keeps its `taper` label afterwards (a fact about
  the week), but the taper itself ends at the gun. In the coaching layer it is the cue to
  suppress intensity; Phase 9 only states the fact.
- **RACE_PROXIMITY** - the signal that the nearest upcoming goal event (any priority,
  any status) falls inside `race_proximity_weeks`. Carries the event's type, priority,
  status, and `weeks_to_event`; asks for a `tentative` event to be decided and an
  `approx` date to be pinned.
- **intent** - reserved for the *daily* plan-of-record category (`rest | easy | tempo |
  strength | hyrox | crossfit | quality`). A week is described by its `block`, never by a
  competing "week intent"; what a block means for training is policy in code, not a
  stored column.
- **race plan (Phase 9b)** - the per-segment pacing and effort targets for race day.
  Deferred out of Phase 9: in HYROX Doubles the runs are shared and the stations are
  split with a partner, so a race plan needs inputs the DB does not hold. See the
  athlete-not-team rule below.
- **athlete, not team** - the coaching model optimizes *this athlete*, never the pair.
  There is no partner load, no shared readiness, no partner threshold pace. The partner
  exists only inside a race plan, because race day is paired by physics while training
  is solo by choice.

## Authoring terms (request -> author -> publish, Phase 11)

- **workout request** - the structured, source-agnostic ask consumed by `author`:
  session type, target date, optional explicit structure. Carries `origin:
  recommender | athlete`. Not "intent" - that word is reserved for the daily
  plan-of-record category.
- **workout spec** - the deterministic output of `author`: a complete,
  Garmin-shaped description of one workout (steps, targets, durations), written
  to `reports/{date}/`. The only thing `publish` is allowed to send.
- **origin (workout request)** - who produced the request: `recommender` (Phase 10
  output) or `athlete` (the user, composed conversationally). The hybrid mode is a
  *process*, not a third origin: an `athlete` request that passed through
  recommender validation before authoring.
- **sport (workout request)** - the authoring/push family of a session: `run |
  hiit | strength`, all three authored and pushable (issue #16). Distinct from
  both `discipline` (human-facing grouping) and the daily `intent`: a
  run-dominant Hyrox session is `run`, a station/crossfit-style one is `hiit`,
  FBB is `strength`. Each sport owns its session types (`run`: rest/easy/tempo/
  quality/hyrox; `strength`: strength; `hiit`: hyrox/crossfit). Unambiguous
  recommendation intents map to a sport automatically (`strength` -> strength,
  `crossfit` -> hiit); `intended_type: hyrox` never does - the athlete says
  which kind it is.
- **session type (workout request)** - the **default shape and default targets** a
  run request starts from, not the shape it is limited to: `easy` expands to one
  work step, `tempo` to warm-up + work + cool-down, `quality` to warm-up + repeats
  + cool-down, `rest` to no spec at all. Any role the vocabulary knows can be added
  to any of them (ADR 0023); the type says only what happens in silence. It is
  *not* how hard the session is - that is *hardness* (ADR 0024).
- **step role (workout request)** - one named part of a run session:
  `warmup | work | recovery | rest | cooldown`. Every run session type offers every
  role; each is shaped by its own keys (`<role>_end`, `<role>_min`,
  `<role>_target`), and any one of them summons the role into the spec. A role the
  request never mentions appears only when the session type defaults it, at that
  type's length; a summoned role takes the shared default length (warm-up and
  cool-down 10 min, recovery and rest 2 min). Distinct from *spec step kind*, which
  is what the role becomes on the watch.
- **rest (step role)** - standing rest between run repeats, as against *recovery*,
  which is a jog. One repeat block runs exactly one of the two: asking for both is
  refused, and a pause asked for explicitly replaces the session type's default
  pause. Not to be confused with the `rest` *session type* (a day off, which yields
  no spec) - the two share a word on different axes (ADR 0023).
- **run-station sequence (workout request)** - a Hyrox race simulation authored as
  `sport: run`, `session_type: hyrox` with a `structure.stations` list: one run
  before each station, in race order, the stations named on their steps so the watch
  says what comes next. Runs share one end and one target, stations share one target
  and end on the lap button unless an entry says otherwise; a distance-ended station
  is refused, since the watch would measure an erg by GPS. Like the exercise sports
  it expands from a **list**, not from the *step role* table (ADR 0023), so it
  carries no *hardness* and the *plan guard* ranks its session type instead. Without
  `stations`, a run hyrox request still asks whether the session is run-dominant or
  station-based.
- **structure override (workout request)** - the optional `structure` block in an
  `athlete`/hybrid request that shapes the session type's defaults: for runs,
  `reps` plus, per *step role*, an end condition and an intensity target; for the
  exercise sports, the `exercises` list (see *exercise entry*), where it is
  required rather than optional. A repeat count folds the work step and its pause
  into a repeat block on any session type. The recommender never emits an override
  (`request_from_recommendation` sets `structure: None`); overrides are the athlete
  finalizing what the recommender suggested (Phase 11a).
- **intensity target (workout request)** - what a role's `<role>_target` sets:
  how hard that step should be, as `none`, a *nameable zone*, or an explicit
  *target band*. Absent means the role's default - no target on `warmup`,
  `recovery`, and `cooldown`; the pace -> HR -> none chain on `work` (issue #24,
  ADR 0020). Distinct from the `intensity_cap` the recommender carries, which
  bounds a whole session rather than one step.
- **nameable zone (intensity target)** - a zone the target vocabulary can name:
  `z2`, `z3`, `z4`, each resolved to the heart-rate band spanning a pair of
  adjacent `athlete_zones` upper bounds and each written in any case (`Z2` reads as
  `z2`). A zone name **always** means heart rate -
  the stored ladder is a heart-rate ladder, while pace holds two anchors and no
  ladder to name rungs on. `z1` and `z5` are not nameable: the ladder stores four
  upper bounds, so the outer zones have no floor and no ceiling, and no
  athlete-level maximum heart rate is stored anywhere. Both are reachable as an
  explicit *target band* (ADR 0020).
- **target band (intensity target)** - an explicit window given as
  `{"hr_band": [low_bpm, high_bpm]}` or
  `{"pace_band": [fast_s_per_km, slow_s_per_km]}`, narrower bound first. Reads
  nothing from the database, so it authors with no `athlete_zones` row at all -
  which is what makes it the way to express the outer zones.
- **end condition (spec step)** - how a step finishes: `time` (seconds), `distance`
  (metres), `reps` (repetition count, exercise sports only), or `lap` (the watch
  lap button, "on-click"). Exactly one per step. `warmup`, `cooldown`, and
  `recovery` may use time/distance/lap; a run `work` step must be `time` or
  `distance` (lap is refused - a work interval needs a defined end); an exercise
  work step is `reps` or `time`. Unknowable ends (lap, reps, distance without a
  band) count 0 s toward the duration estimate.
- **custom pace band (spec step)** - an explicit `[fast_s_per_km, slow_s_per_km]`
  target the athlete sets on the work step, e.g. 3:40-4:00 as `[220, 240]`. It
  wins over the recommender's `pace_target_s_per_km` and suppresses the
  pace -> HR -> none degradation (it is already fully specified). Spelled
  `work_pace_band`; the same band is `work_target: {"pace_band": [...]}` in the
  newer per-role vocabulary (see *target band*), and setting both is refused.
- **exercise entry (workout request)** - one element of an exercise sport's
  `structure.exercises` list: an `exercise` name, `sets`, exactly one of `reps`
  or `time`, optional `weight_kg` (always kilograms) and `rest`. Sets within one
  entry are uniform; ramping weight is consecutive entries of the same exercise.
  The translator expands each set to its own flat step - never a repeat group
  (exercise metadata inside repeat groups is unproven; the flat shape
  round-tripped in the live probes).
- **session edge (workout request)** - the optional `warmup` / `cooldown` step of a
  session that expands from a list rather than the run role table: an exercise sport
  (issue #65) or a Hyrox run-station sequence (issue #64). Same keys, same vocabulary
  and the same 10-minute default as a run role, but never defaulted: the step exists
  only when the structure gives the role an end, a length, or a target. No target
  unless one is asked for. Authored before the first set and after the last, leaving
  everything between them unchanged.
- **rest default (exercise sports)** - the between-sets rest applied when an
  exercise entry gives none: 90 s for `strength`, 60 s for `hiit`; overridable
  per entry (`{"min"/"s"}` or `"lap"`). The session's trailing rest is dropped.
- **workout name (workout request)** - what the pushed workout is called on the
  account and the watch. The athlete's to set (issue #58, ADR 0029): `label` names
  the session after the prefix and the date (`GC 2026-09-25 4x2 km próg`), `name`
  replaces the whole thing, and with neither the session type answers as it always
  did. Trimmed; empty, multi-line, over-long (30 for a label, 80 for a name) and
  `GC `-prefixed labels are refused. The name is inside `spec_hash`, so renaming a
  pushed workout is a changed workout, not a cosmetic edit.
- **reusable workout** - a pushed workout whose name does **not** carry the session's
  date, so the same steps are one workout the athlete can have on several days. No
  separate mechanism: the fingerprint already schedules an existing workout instead of
  uploading a second copy when it reappears on a new date. Repeated from chat by
  copying a day's spec onto another date (`reuse_from`), which re-runs that day's date
  and plan guards.
- **replace rule** - what `--replace` does to the account's old workout: a workout
  named for the requested date is deleted (it has nowhere else to be), a date-free one
  is taken off that date and kept, because it may be on days no receipt knows about -
  including days scheduled by hand in Connect. The preview says which, and warns when
  the library will then hold two workouts of the same name (ADR 0029).
- **plan guard** - the refusal of any session harder than the plan of record for its
  date, measured by the spec's *hardness* where it has one and by its session type
  where it does not (issue #22, ADR 0021; issue #62, ADR 0024). It runs twice:
  `author` refuses to write such a spec, and `publish` refuses to send one, because a
  spec authored before the plan was revised is exactly what the author-time check
  cannot see. Softer is never refused - that is the recommender's downgrade. Not
  overridable by `--replace`; the remedy is to change the plan for that date.
- **plan divergence** - a workout already on the account that is harder than the plan
  of record now says for its date, reported (never repaired) as `{pushed_type,
  planned_intent, pushed_at}`. It can only arise from a plan revised after the push,
  since both guards refuse it at write time. Read from the DB and the receipt, so it
  is answered even when the account is unreachable.
- **invalidated push** - the same divergence found at plan-ingestion time: `plan
  import` and `plan_confirm` check the imported week's dates against their receipts
  and name the days that now need re-authoring. Revising a week is when a divergence
  is created, so it is reported there rather than waiting to be asked about.
- **exercise whitelist** - the curated map from the athlete's exercise names to
  Garmin `category`/`exerciseName` pairs, held to Garmin Connect's public
  exercise taxonomy by contract tests (the athlete's logged sets carry no enums,
  so the taxonomy is the mining source). Warn-never-block: an unknown exercise
  authors an unlabeled step and a spec warning.

## Coach MCP terms (mcp/tools.py -> mcp/server.py, epic #18)

- **coach MCP** - the local `coach` stdio server (`mcp__coach__*`, registered in the
  repo's `.mcp.json`): 23 tools in four groups (read / local write / transport /
  workout push), each a thin wrapper over a seam the CLI already uses. Distinct from
  the exploratory `mcp__garmin__*` server. See ADR 0014, and ADR 0028 for the second
  transport tool.
- **same-day refresh** - the opt-in pull of *today's* (partial) data plus a mart
  rebuild through today: `garmin-coach refresh-today` on the CLI, `refresh_today`
  over MCP. Never advances watermarks, so the nightly run re-pulls the day complete.
- **freshness envelope** - the metadata every coach-MCP response carries:
  `data_through` (the mart horizon), `today_included`, `partial_fields`, and
  `unconfirmed_days`. How a chat session knows what it may treat as final.
- **unconfirmed day** - a day inside the re-check window that the plan of record
  expected a session on and that has no stored activity. Not evidence of a skipped
  session: the window is exactly the stretch where "nothing recorded" can still mean
  "not uploaded yet" (issue #72). Carried by every coach-MCP response and by the
  digest's `window`, with the planned intent and which plan source answered. It is a
  question for the athlete - sync the watch, or confirm the session did not happen -
  and gap repair is the action when the answer is the former.
- **gap repair** - the two-step re-pull of finished days from chat: `repair_preview`
  reads what the DB holds for each day of a range (activity count, which daily
  streams answered, the planned intent) and returns a `confirm_token`;
  `repair_confirm` pulls the range whole through the `backfill` path and rebuilds the
  marts. At most 14 days, never today, watermarks untouched. See ADR 0028.
- **partial fields** - the intraday-accumulating mart fields (load, ACWR, zone
  minutes, RHR, stress, body battery) listed in the envelope when today is included.
  Morning-complete streams (sleep, HRV, readiness) are never flagged.
- **preview-hash handshake** - the MCP push interlock: `push_preview` returns a
  `confirm_token` alongside the payload, and `push_confirm` refuses any other value
  without touching the account. The MCP counterpart of the CLI's `--confirm`, strict
  enough for an agent caller.
- **account lookup order** - how a push finds the workout to act on: the receipt's
  `workout_id`, then the `gc-hash:` tag, then the name (issue #40). Ordered by how
  stable each key is - the id is the only one the athlete cannot change, and a rename
  plus an edit defeats the other two at once. The account still decides: an id it no
  longer knows falls through, so idempotency rests on the account, not the receipt. The
  name stays a full fallback rather than being narrowed to untagged workouts, because
  it is what detects a changed spec and yields `refuse`/`replace`. A lookup matching two
  workouts refuses and names them - an earlier push already duplicated something, and
  guessing would let `--replace` delete the wrong one.
- **push receipt** - `reports/{date}/push.json`: what a push *did* (`action`, `applied`,
  `workout_id`, `spec_hash`, `session_type`, `planned_intent`, `pushed_at`). A record of
  an event, never a statement about what the Garmin account holds now - that is what
  reconciliation is for. `session_type` and `planned_intent` are what went up and the
  plan it was measured against, so a spec re-authored later cannot be mistaken for what
  is on the watch (ADR 0021); receipts written before them fall back to the local spec.
- **reconciled block** - the finding appended to the receipt under `reconciled`, so a
  later read (offline included) is not thrown back on the stale claim. The receipt's own
  fields are never mutated: the file ends up saying both what was done and what became
  of it. Written only on a state change, never by an `unverified` read, and dropped
  wholesale when a new push rewrites the receipt - a new push is a new event. In a
  `get_workout_status` response the receipt is returned without this key: the finding is
  reported once, under `reconciled`, because two copies in one response invite reading
  the stale one.
- **last_known** - the previous reconciled block, served under that key when a read
  cannot reach the account. Degrading to older information beats degrading to none, but
  it is kept separate from the current finding so stale facts never read as fresh ones.
- **reconciliation** - resolving a push receipt's `workout_id` against the Garmin account
  on every `get_workout_status` read (issue #41). Keyed on the id alone: the question is
  whether what was pushed is still there, not whether something like it is. Hash and name
  lookups belong to the push path.
- **reconciliation state** - what the account actually holds, one of: `live` (in the
  library and on the date's calendar), `edited` (scheduled, but the steps were rewritten
  in Connect), `unscheduled` (in the library, not on that date - unpinned or moved),
  `missing` (gone from the library), `unverified` (the account could not be reached).
  Precedence runs `unverified > missing > unscheduled > edited > live`: `unscheduled`
  outranks `edited` because a workout that is not on the day is not on the watch whatever
  its steps say. `live` claims nothing about the steps on its own - it is where an
  unjudged comparison reports, because "we could not tell" is not evidence of a rewrite,
  so read `steps_changed` beside it. Reported alongside the facts behind it: `scheduled`,
  `steps_changed`, `renamed_to`, and `checked_at` - when reconciliation ran, which on
  `unverified` is when the attempt was made rather than when an answer came back.
- **steps_changed** - whether the account's steps differ from the ones the receipt says
  were pushed. `None` means it could not be judged: the local spec no longer hashes to
  the receipt's `spec_hash`, so it is no longer evidence of what the push sent. A `live`
  state carrying `None` therefore means "in the library, on the day, steps unjudged".
- **why `gc-hash:` cannot detect an edit** - the tag records what was *pushed*, and
  Garmin leaves the `description` untouched when steps change, so it agrees with the
  receipt forever. Detection is therefore two-stage: `updateDate` against `pushed_at`
  gates a `get_workout` call (an untouched copy answers for free), and the fetched steps
  are compared field by field on what this system authors - the account decorates every
  step with `stepId`, `weightValue: -1`, `strokeType`, and `endConditionCompare` that no
  upload ever sent. Renaming also bumps `updateDate`, which is exactly why the step
  comparison exists: without it every rename would read as an edit.
- **renamed_to** - the account's current workout name when it differs from the receipt's.
  A field, never a state: renaming a pushed workout in Garmin Connect is the athlete's
  prerogative and must not read as a fault.
- **confirm_token vs spec_hash** - two hashes with two jobs (ADR 0019). `spec_hash`
  (name + steps) is the account-side idempotency marker written into the Garmin
  workout description, and the tag that identifies a coach-authored workout. It takes
  no date of its own, so whether a workout is one-day or reusable is decided by its
  *name*: the default `GC <date> <type>` carries the date and cannot recur, while a
  date-free name makes the same steps the same workout on every date (ADR 0029).
  `confirm_token` (name + steps + date + planned intent)
  gates preview -> confirm, because the date decides what gets scheduled and which
  day's activity collision was checked, and the planned intent decides whether the
  push is allowed at all (ADR 0021).

## Process terms

- **data_start** - first date with real (non-onboarding) data: 2026-06-08. Earlier
  dates are explicit gaps, not zero training.
- **watermark** - per-stream `sync_state.last_synced_date`; the last date the
  incremental sync treats as final and never asks Garmin about again. It never passes
  the first day of the re-check window (ADR 0026).
- **re-check window** - the trailing 3 days, ending yesterday, that every nightly sync
  pulls again (`sync_recheck_days`). A day Garmin has answered for is not yet a day
  whose data has arrived: a watch can upload a run a day late, and Garmin fills HRV and
  readiness in after the fact. A day is pulled on the three nightly runs after it and
  becomes final on the third.
- **stream** - one independently synchronized Garmin data family: `activities`,
  `sleep`, `hrv`, `wellness`, `readiness`, or `status`.
- **daily stream** - a stream fetched one date at a time: sleep, HRV, wellness,
  readiness, and training status.
- **activities range** - the activities stream fetch window, first attempted as one
  range call and then retried per day if the range call fails.
- **partial success** - a sync run where at least one stream progresses while another
  stream fails and leaves its watermark unchanged.
- **seam** - the agreed boundary a test exercises: pure normalizers (`models.py`),
  the persistence layer (`db.py`), the sync orchestrator (`sync.py`), the features
  materializer (`features.py`), the weekly rollup (`weekly.py`), threshold policy
  (`thresholds.py`), and the digest builder (`build_digest`/`digest.py`) at the DB
  boundary.
- **golden regression** - a test that reproduces the reference hand-analysis
  (2026-06-09..07-04) from frozen real anonymized core data.
