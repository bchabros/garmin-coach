# Domain glossary

Shared vocabulary for the garmin-coach project. Use these terms consistently in
code, docstrings, PRDs, and ADRs.

## Data layers (medallion)

- **raw** - append-only `raw_payloads`; original Garmin JSON, never overwritten.
- **core** - normalized, upserted-by-PK tables (`activities`, `daily_wellness`,
  `sleep`, `hrv_nightly`, `sync_state`, plus the manually-logged `session_rpe` and
  `niggle`). The system of record. Most core tables are ETL-written from Garmin;
  `session_rpe`/`niggle` are ground truth written by `garmin-coach log-rpe` (Phase 7).
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
- **Load buckets (load balance)** - Garmin-style attribution of `training_load` by
  Training Effect: `load_anaerobic` (`anaero_te >= 1.0`), else `load_low`
  (`aero_te < 2.5`), else `load_high`, plus `load_strength` for blended `Siła` load
  (Phase 7). A different language from HR zones. The three TE buckets remain
  cardio-only (they feed `AEROBIC_LOW_SHORTAGE`); `load_day` sums all four.
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
  weekly facts, and weekly signals must all sit at or before this horizon.
- **AEROBIC_LOW_SHORTAGE** - too much grey-zone work: our easy-load share is below
  target while hard-load share is above ("add Z2"). Computed from our buckets;
  cross-checked against Garmin's `training_status_daily.balance_phrase` via
  `garmin_agrees`.
- **garmin_agrees** - whether our derived signal concurs with Garmin's own phrase for
  the same finding; strengthens or hedges the report wording, never a passthrough.
- **report** - the dated coach artifact under `reports/{date}/`: `report.md` (narrative
  written by the skill from the digest), `digest.json`, and two PNG charts
  (`hrv_band.png`, `acwr.png`). `garmin-coach report` produces everything except the
  Markdown narrative.

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
  stay visible (the overlap read is partial until they are mapped).

## Weekly terms (mart -> weekly)

- **complete week** - a Monday-Sunday span whose seven days all lie at or before
  yesterday. Only complete weeks are rolled up into `weekly_metrics`; the in-progress
  current week is skipped so weekly figures never lie from 1-2 days of data.
- **weekly rollup** - the derivation of one `weekly_metrics` row per complete week
  purely from `daily_metrics` (a mart-from-mart step). Never touches Garmin;
  recomputable and safe to rebuild.
- **planned intent** - the training category the user's `plan_template` assigns to a
  day of week (`rest | quality | easy | ...`).
- **actual intent** - the same category inferred from what actually happened that day,
  classified by load: `quality` when the day's load reaches `hard_te_load` (or has
  anaerobic load), `easy` for any lighter activity, `rest` for no activity. A day the
  athlete trained without wearing the watch is invisible to the system and reads as
  `rest` (an ETL limitation, by decision, not a bug).
- **plan adherence** - the fraction of the week's seven days whose actual intent
  exactly matches the planned intent. The report also shows the *direction* of each
  mismatch, since the DoD asks to surface divergence, not just a number.
- **weekly plan-vs-actual fact** - the per-day planned intent, actual intent, and match
  flag materialized alongside `weekly_metrics`. The digest reads these stored weekly
  facts instead of re-deriving mismatch direction from a later `plan_template`.
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
- **intent** - reserved for the *daily* `plan_template` category (`rest | quality |
  easy | ...`). A week is described by its `block`, never by a competing "week intent";
  what a block means for training is policy in code, not a stored column.
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
  `plan_template` category.
- **workout spec** - the deterministic output of `author`: a complete,
  Garmin-shaped description of one workout (steps, targets, durations), written
  to `reports/{date}/`. The only thing `publish` is allowed to send.
- **origin (workout request)** - who produced the request: `recommender` (Phase 10
  output) or `athlete` (the user, composed conversationally). The hybrid mode is a
  *process*, not a third origin: an `athlete` request that passed through
  recommender validation before authoring.
- **sport (workout request)** - the authoring/push family of a session: `run |
  hiit | strength`. Distinct from both `discipline` (human-facing grouping) and
  the daily `intent`: a run-dominant Hyrox session is `run` (pushable), a
  station/crossfit-style one is `hiit`, FBB is `strength` (both await the
  strength/HIIT push spike). The recommender's `intended_type: hyrox` never maps
  to a sport automatically - the athlete says which kind it is.

## Process terms

- **data_start** - first date with real (non-onboarding) data: 2026-06-08. Earlier
  dates are explicit gaps, not zero training.
- **watermark** - per-stream `sync_state.last_synced_date`; how incremental sync
  tracks progress.
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
