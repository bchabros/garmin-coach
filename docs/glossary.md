# Domain glossary

One vocabulary for code, decisions and coaching. Procedures belong in the guides and
coach references; specs and acceptance evidence belong in GitHub issues/PRs.

## Installation

- **Athlete installation** - one athlete's personal configuration, observations, plans,
  reports, profile, race archive and saved login, separate from the program.
- **Selected installation** - the installation explicitly chosen for the whole run.
- **Source checkout** - the program repository used for development; it is an athlete
  installation only when separately configured as one.
- **Managed runtime** - installed program versions and public coach resources, separate
  from personal athlete state.
- **Runtime root** - the directory containing the managed runtime and ownership records.
- **Program receipt** - the record of runtime ownership, selected installation and
  outstanding program operations.
- **Integration receipt** - generated assistant assets and their native registration state.
- **Client binding** - the user-wide association of an assistant host with one runtime
  and athlete installation; Work/Codex share the OpenAI host.
- **Stable launcher** - the entry point that keeps its location while program versions change.
- **Recovery backup** - preserved database, configuration and program/integration state
  from before a maintenance change.
- **Client acceptance** - an observed fresh assistant conversation using the intended
  installation, skill and tools; registration/protocol success alone is insufficient.
- **Initial setup** - establishing the athlete's own history start, optional weekly shape,
  saved login and first read, with incomplete evidence explicitly identified.
- **Installation adoption** - reusing existing personal state without replacing it;
  a local read does not establish complete history.
- **Setup checkpoint** - the last fully committed initial-import day for the same
  database/history identity, independent of nightly sync finality.

## Data and synchronization

- **raw** - original captured payloads, append-only. Identity includes endpoint,
  reference date, fetch time and content hash; later identical fetches remain evidence.
- **core** - durable normalized observations and explicitly reported athlete facts;
  the system of record.
- **mart** - recomputable derived facts, safe to rebuild from core. Daily/weekly
  metrics, plan comparisons, zones, movement overlap, blocks and standing are marts.
- **data_start** - the athlete's first real-history date. Earlier days are onboarding
  evidence gaps, not observed rest. The original installation starts 2026-06-08.
- **stream** - one independently synchronized data family: activities, sleep, HRV,
  wellness, readiness or training status.
- **daily stream** - a stream fetched one calendar day at a time.
- **activities range** - the activities fetch window, retried per day if its range call fails.
- **watermark** - the last date a stream treats as final, capped by the re-check window.
- **re-check window** - recent days pulled again because uploads and Garmin observations
  can arrive late; default three days ending yesterday.
- **partial success** - useful stream progress alongside another stream's failure.
- **same-day refresh** - an explicitly requested pull/rebuild through today, whose
  accumulating observations remain partial and whose watermarks do not advance.
- **gap repair** - a previewed and confirmed re-import of finished missing days,
  followed by recomputation, without changing sync finality.

## Metrics and training

- **Training load** - Garmin's EPOC-based estimate of one activity's physiological load.
- **TE (Training Effect)** - Garmin's separate aerobic/anaerobic impact estimates,
  approximately 0–5; neither is a directly measured fitness improvement.
- **Discipline** - the human-facing sport grouping mapped from Garmin's activity type.
- **RHR** - resting heart rate; wellness is preferred over sleep's fallback observation.
- **sRPE (session-RPE load)** - subjective exertion times total session minutes,
  scaled into the model's load currency.
- **load blend** - strength uses sRPE, with a configured default only when unreported;
  other disciplines use the greater of Garmin load and explicitly reported sRPE.
- **load_day** - total blended load across that day's activities; zero on recorded rest.
- **load_strength** - strength's contribution, included in total load and excluded
  from the three aerobic/anaerobic shares.
- **Load buckets (load balance)** - low aerobic, high aerobic, anaerobic and strength
  contributions. Cardio splits by TE contribution and weighted watch-zone time;
  a bucket describes modeled stimulus, not minutes spent in that zone.
- **HR-zone minutes (z1..z5_min)** - captured time in each watch HR zone, expressed
  in minutes, distinct from load buckets and derived personal-zone boundaries.
- **acute7** - trailing seven calendar days' daily load divided by seven.
- **chronic28** - trailing 28 calendar days' daily load divided by 28.
- **ACWR** - acute7/chronic28, a modeled load ratio with configurable interpretation;
  fixed denominators overstate it while recorded chronic history is short.
- **n_chronic** - count of actual in-window history days supporting chronic load;
  the model marks ACWR indicative below its configured minimum.
- **hrv_baseline** - median non-null nightly HRV in the selected history, capped
  to trailing 60 nights, shared by the current recomputed daily rows.
- **hrv_sd** - sample HRV standard deviation over the same nights.
- **hrv_low_flag** - HRV strictly below the mart baseline minus one sample SD;
  the digest's configurable HRV threshold is a separate policy.
- **monotony / strain (Foster)** - daily mean load divided by SD, and weekly load
  times monotony; uncomputable values are unknown, not infinite.
- **deload (retrospective)** - an observed complete-week load drop meeting the
  configured comparison, distinct from prescribed recovery or prospective advice.
- **deload advised (prospective)** - load-history evidence supporting a future reduction,
  requiring enough weeks, rising loads and high ACWR or monotony.
- **athlete snapshot** - the current deterministic standing composed from recorded
  VO2max, HRV, load, readiness, zones and plan context.
- **computed_at (snapshot)** - the standing's evidence cutoff, not a wall-clock claim.
- **trend delta** - signed change over available observations within the chosen lookback;
  insufficient span means no supported delta.
- **span_days** - actual elapsed days supporting a trend, which may be shorter than
  the nominal lookback.

## Coaching evidence

- **router** - the coach entry guide that selects the applicable flow and evidence rules.
- **reference file** - one lazily read flow guide for reporting, planning, authoring
  or saved-context lookup.
- **routing gate** - the rule requiring the applicable reference before entering its flow.
- **digest** - compact derived headline, signals and weekly/zone/plan context for narration.
- **signal** - a deterministic finding with severity and cited scalar facts. Current
  codes/threshold logic belong to Python, not a duplicated glossary inventory.
- **report** - deterministic digest/snapshot/charts plus the coach-written narrative;
  exact narrative judgments are not recoverable merely by rebuilding metrics.
- **report horizon** - the inclusive cutoff for a report's daily, weekly and source evidence.
- **matches_horizon** - whether a standing's evidence cutoff matches a report horizon;
  false requires qualification, null means the relationship is unknown.
- **freshness envelope** - evidence dates, today's inclusion, partial observations
  and unconfirmed planned days accompanying coach reads.
- **partial fields** - intraday-accumulating quantities that remain provisional when
  today is included; morning-complete streams are distinguished from them.
- **unconfirmed day** - a recent planned training day with no stored activity yet,
  which may mean an unsynced watch rather than a skipped session.
- **AEROBIC_LOW_SHORTAGE** - modeled low-aerobic share below the comparable Garmin
  target over 28 days; it is a separate finding from personal-zone minute share.
- **garmin_agrees** - whether Garmin's phrase concurs with our independently computed
  finding, strengthening or qualifying the narrative.
- **athlete profile** - private qualitative goals, tendencies, decisions and open threads,
  with dated context and athlete-approved amendments; copied numbers are older summaries.
- **report retention** - deliberate expiry of eligible derived files after lasting
  narrative lessons have been preserved; plans, workout specs and receipts remain records.
- **niggle** - an athlete-reported body-part complaint and severity, distinct from diagnosis.
- **active niggle** - the latest report for a body part within the configured active window.
- **reduced-mode** - the conservative coaching adjustment signaled by an active niggle.

## Movement

- **exercise set** - an active captured unit with exercise category/subcategory and
  any available repetitions, duration or weight.
- **movement pattern** - push, pull, hinge, squat or carry.
- **muscle group** - the separately tracked anatomical load grouping; grip is a muscle
  dimension rather than another movement pattern.
- **pattern_load** - the session's blended load allocated by mapped-set share,
  rather than tonnage or number of rounds.
- **pattern overlap** - the minimum of consecutive days' movement loads when both
  clear the configured floor; one rest day clears the stack.
- **PATTERN_STACK / MUSCLE_OVERLAP** - findings for above-policy adjacent-day load
  on the pattern or muscle axis.
- **movement coverage** - how much captured/manual station evidence has a known map,
  with unknown names visible rather than silently classified.
- **manual set overlay** - the athlete's durable station descriptions for a circuit,
  superseding that activity's captured sets as a whole while preserving them.
- **movement_sets** - the selected manual-or-captured station evidence with its source;
  coverage and overlap refer to that same selection.

## Plans and events

- **complete week** - a Monday–Sunday span entirely through yesterday; partial
  current weeks do not establish complete weekly metrics.
- **weekly rollup** - a derived complete-week aggregation of daily evidence.
- **plan of record** - the athlete-agreed dated weekly plan, including its prose,
  targets and revision history.
- **plan week (override)** - the parsed-intent representation of an authored week,
  which takes precedence over a repeating shape.
- **plan template (fallback)** - an optional athlete-chosen repeating weekly shape;
  it does not establish agreement to a specific dated week.
- **planned-intent resolver** - the choice of authored week, then agreed template,
  otherwise unknown intent, with the answering source identified.
- **planned intent / intent** - the daily category rest, easy, tempo, strength,
  hyrox, crossfit or quality; a week has a block, not a competing weekly intent.
- **actual intent** - the load-observable rest/easy/strength/quality category;
  it cannot distinguish Crossfit from Hyrox merely from load.
- **intent class** - the comparable observable group of planned intents; tempo,
  Hyrox, Crossfit and quality all compare as quality.
- **intent rank (hardness ladder)** - the ordered plan intensity: rest < easy <
  tempo/strength < Hyrox/Crossfit/quality.
- **plan adherence** - matching observable intent classes across seven planned days;
  missing plan evidence makes it unknown rather than zero.
- **weekly plan-vs-actual fact** - a dated planned/observed intent and match, preserving
  its original evidence rather than reinterpreting a later plan.
- **PLAN_MISSING** - no authored dated week, whether or not a template can answer.
- **goal event** - a user-recorded race with priority, commitment, date precision
  and optional target; the calendar is personal data.
- **status (goal event)** - confirmed or tentative participation, independent of date precision.
- **date_precision (goal event)** - exact or approximate calendar date, independent of commitment.
- **anchor event** - the nearest confirmed priority-A race on/after a given week;
  historical weeks retain their completed cycle's anchor.
- **current anchor** - the nearest upcoming confirmed priority-A race for today's question.
- **block** - base, build, peak or taper within an anchored race cycle.
- **weeks_to_event** - week-based distance to that block's anchor race.
- **planned deload** - a prescribed recovery week inside base/build, independent
  of whether actual load dropped.
- **planned deload vs deload advised** - calendar prescription versus a finding from
  actual load; disagreement is evidence, not an automatic arbitration rule.
- **plan_block** - the historical/future race-cycle calendar, distinct from completed
  weekly training evidence.
- **TAPER_ACTIVE** - the taper period before the race; the weekly taper label can
  remain after racing without implying continued taper instructions.
- **RACE_PROXIMITY** - a nearby event whose commitment or exact date may still need a decision.
- **race plan** - per-segment race-day pacing/effort targets. The proposed automated
  feature (#13) was closed as not planned; it is not an implemented system capability.
- **athlete, not team** - coaching models this athlete; partner capability and station
  allocation would need explicit separate evidence for a paired race scenario.

## Workout authoring

- **dated workout** - an authored workout assigned to a calendar day, with its
  preparation, push history and current account finding kept distinct.
- **workout request** - the structured session ask, independent of who originated it.
- **workout spec** - the deterministic complete session description available for
  review before account mutation.
- **origin (workout request)** - recommender or athlete. Hybrid is athlete input
  validated against recommendation evidence, not a third origin.
- **sport (workout request)** - run, strength or HIIT authoring family, distinct
  from discipline and planned intent.
- **session type (workout request)** - default shape/targets, not the session's measured hardness.
- **step role (workout request)** - warmup, work, recovery, rest or cooldown;
  the intended role is distinct from its executed Garmin step kind.
- **rest (step role)** - standing between repeats, distinct from jog recovery and a rest day.
- **station sequence (workout request)** - named station blocks with optional adjacent
  runs; run-dominant and indoor station sessions share the sequence concept.
- **sequence sport (station sequence)** - how the watch measures a sequence: run for
  GPS/distance work, HIIT for indoor/time/lap-based stations.
- **structure override (workout request)** - explicit step arrangement/length/targets
  replacing the corresponding defaults.
- **intensity target (workout request)** - no target, a named HR zone or an explicit
  HR/pace band, consistent across offered roles.
- **nameable zone (intensity target)** - Z2/Z3/Z4 with known HR bounds; outer-zone
  targets require explicit bands rather than invented floors or maximum HR.
- **target band (intensity target)** - an explicit bounded HR or pace range,
  usable without a derived zone ladder.
- **end condition (spec step)** - the time, distance, repetitions or lap event ending a step.
- **custom pace band (spec step)** - an athlete-specified work pace range, including
  the supported legacy work_pace_band spelling.
- **exercise entry (workout request)** - a named exercise's uniform sets, reps or
  duration, optional kilogram weight and rest; ramps use consecutive entries.
- **session edge (workout request)** - an explicitly requested warmup/cooldown around
  an exercise or station list, without changing its interior work.
- **rest default (exercise sports)** - between-set rest in the absence of an explicit choice.
- **workout name (workout request)** - the athlete's label or full account/watch name,
  part of the session fingerprint rather than cosmetic metadata.
- **hardness (workout spec)** - easy/threshold/hard measured from resolved authored
  targets on the plan rank scale; unavailable measurement falls back to session type.
- **exercise whitelist** - curated known Garmin workout exercise identities;
  unknown names remain warned, honest descriptions rather than guessed enums.

## Account operations

- **coach MCP** - the sanctioned local conversational surface for cached reads,
  explicit manual logs and guarded transport/account operations.
- **reusable workout** - date-free account content that can be scheduled on several days.
- **replace rule** - dated content may be deleted on replacement; reusable content
  is unscheduled only for the selected day and retained in the library.
- **unschedule** - remove one day's calendar occurrences while preserving library
  content and other scheduled days.
- **plan guard** - refusal of harder-than-agreed work at authoring and push;
  softer work remains allowed and replacement is no bypass.
- **plan divergence** - already-pushed work now harder than its revised dated plan,
  reported as a conflict rather than automatically repaired.
- **invalidated push** - the same conflict discovered while importing/confirming a plan.
- **preview-hash handshake** - explicit approval bound to exactly the previewed
  operation, invalidated by relevant state changes.
- **confirm_token vs spec_hash** - consent identity versus account content identity;
  consent additionally binds date and resolved plan where applicable.
- **account lookup order** - receipt workout ID, content hash, then name; ambiguity
  refuses rather than guessing an account object.
- **push receipt** - the record of what a push did, distinct from what the account holds now.
- **reconciled block** - a changed verified account finding beside untouched push history.
- **last_known** - the previous verified finding when the current account read is unavailable.
- **reconciliation** - checking the receipt's workout identity against the current account.
- **reconciliation state** - live, edited, unscheduled, missing or unverified; a live
  finding with unjudged steps does not establish unchanged content.
- **steps_changed** - whether current account steps differ from the evidence of what
  was pushed; null means the comparison lacks sufficient evidence.
- **why gc-hash cannot detect an edit** - it records pushed content and can remain
  unchanged when Garmin steps change; current steps need separate comparison.
- **renamed_to** - a changed account name, an athlete action rather than a fault state.

## Engineering context

- **seam** - an agreed public boundary where behavior is observed independently
  of internal implementation; the current seam inventory is in DEVELOPMENT.
- **golden regression** - reproduction of independently established reference values
  from frozen anonymized evidence, not expectations regenerated from current code.
- **agent notes** - external coding-agent memory about the repository/tooling,
  distinct from the private athlete profile.
- **consolidation pass** - a backed-up review of agent notes that merges duplicates
  and corrects false facts; age alone prompts checking rather than deletion.
