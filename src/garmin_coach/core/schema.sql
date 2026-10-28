-- =============================================================================
-- garmin-coach  ·  schema.sql
-- System-of-record (SQLite). Layout: raw -> core -> config -> sync -> mart -> views.
-- All CREATE ... IF NOT EXISTS (idempotent). Comments in English.
-- Column set is intentionally rich: it captures fields actually present in the
-- Garmin payloads (activities, sleep, hrv, readiness, training status, body
-- battery) plus tables for streams worth pulling later. See BUILD doc §6/§12.
-- Derived/analytical values live in `daily_metrics` / `weekly_metrics` (marts)
-- and in views — never mixed into core tables.
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================================
-- RAW  (append-only; never overwrite — lets you reprocess without re-hitting API)
-- =============================================================================
CREATE TABLE IF NOT EXISTS raw_payloads (
  fetched_at  TEXT NOT NULL,          -- ISO ts of the pull (second resolution)
  endpoint    TEXT NOT NULL,          -- e.g. 'get_sleep_data', 'get_activities_by_date'
  ref_date    TEXT NOT NULL,          -- date the payload refers to (YYYY-MM-DD)
  payload     TEXT NOT NULL,          -- raw JSON string
  payload_sha TEXT NOT NULL,          -- sha256 of payload; completes the identity
  -- payload_sha is in the key because fetched_at only resolves to the second: a
  -- fan-out (weather/sets per activity) issues several distinct payloads for the
  -- same endpoint and ref_date within one second, and without the hash the second
  -- one would be silently dropped by INSERT OR IGNORE. See issue #34.
  PRIMARY KEY (endpoint, ref_date, fetched_at, payload_sha)
);

-- =============================================================================
-- CORE · ACTIVITIES
-- =============================================================================
CREATE TABLE IF NOT EXISTS activities (
  activity_id     INTEGER PRIMARY KEY,     -- dedup key (activityId)
  start_local     TEXT NOT NULL,           -- startTimeLocal
  start_gmt       TEXT,
  date            TEXT NOT NULL,           -- date(start_local); join key to daily tables
  gtype           TEXT NOT NULL,           -- activityType.typeKey: running|hiit|strength_training|...
  discipline      TEXT,                    -- mapped: Bieganie|Hyrox/HIIT|Siła|Trail|Skitury
  name            TEXT,                    -- activityName
  workout_id      INTEGER,                 -- structured-workout link (planned session)

  -- duration / distance / elevation
  dur_s           REAL,                    -- duration
  moving_dur_s    REAL,
  elapsed_dur_s   REAL,
  distance_m      REAL,
  elev_gain_m     REAL,
  elev_loss_m     REAL,
  min_elev_m      REAL,
  max_elev_m      REAL,

  -- speed / pace
  avg_speed_mps   REAL,
  max_speed_mps   REAL,
  avg_gap_speed   REAL,                    -- avgGradeAdjustedSpeed (trail/hills)

  -- heart rate
  avg_hr          INTEGER,
  max_hr          INTEGER,
  hr_z1_s REAL, hr_z2_s REAL, hr_z3_s REAL, hr_z4_s REAL, hr_z5_s REAL,

  -- power (running/cycling)
  avg_power       REAL,
  max_power       REAL,
  norm_power      REAL,
  pwr_z1_s REAL, pwr_z2_s REAL, pwr_z3_s REAL, pwr_z4_s REAL, pwr_z5_s REAL,

  -- training effect / load  (THE core coaching signals)
  aero_te         REAL,                    -- aerobicTrainingEffect
  anaero_te       REAL,                    -- anaerobicTrainingEffect
  te_label        TEXT,                    -- trainingEffectLabel: AEROBIC_BASE|TEMPO|LACTATE_THRESHOLD|VO2MAX
  training_load   REAL,                    -- activityTrainingLoad
  aero_te_msg     TEXT,                    -- e.g. OVERREACHING_17, HIGHLY_IMPROVING_VO2_MAX_16
  anaero_te_msg   TEXT,

  -- running dynamics
  avg_cadence     REAL,                    -- avgRunningCadenceInStepsPerMinute
  max_cadence     REAL,
  avg_stride_cm   REAL,                    -- avgStrideLength
  avg_gct_ms      REAL,                    -- avgGroundContactTime
  avg_gct_balance REAL,                    -- avgGroundContactBalance (L/R %)
  avg_vosc_cm     REAL,                    -- avgVerticalOscillation
  avg_vratio      REAL,                    -- avgVerticalRatio

  -- respiration / physiology
  avg_resp        REAL,
  min_resp        REAL,
  max_resp        REAL,
  vo2max_at       REAL,                    -- vO2MaxValue attributed to this activity

  -- energy / misc
  calories        INTEGER,
  bmr_calories    INTEGER,
  water_ml        INTEGER,                 -- waterEstimated
  steps           INTEGER,
  mod_intensity_min INTEGER,              -- moderateIntensityMinutes
  vig_intensity_min INTEGER,              -- vigorousIntensityMinutes
  bb_diff         INTEGER,                 -- differenceBodyBattery (drain from this session)


  -- fastest splits (running)
  split_1k_s      REAL,
  split_1mi_s     REAL,
  split_5k_s      REAL,
  split_10k_s     REAL,

  -- geo / flags
  location_name   TEXT,
  is_pr           INTEGER DEFAULT 0,       -- personal-record flag on the activity

  -- environment (per-activity weather; Fahrenheit payload converted to Celsius)
  temp_c          REAL                     -- air temperature; NULL when no weather
);
CREATE INDEX IF NOT EXISTS ix_activities_date ON activities(date);
CREATE INDEX IF NOT EXISTS ix_activities_disc ON activities(discipline);

-- Captured laps and typed execution groups are separate source observations.
CREATE TABLE IF NOT EXISTS activity_laps (
    activity_id INTEGER NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    row_idx INTEGER NOT NULL,
    lap_index INTEGER,
    message_index INTEGER,
    wkt_step_index INTEGER,
    wkt_index INTEGER,
    intensity_type TEXT,
    start_gmt TEXT,
    dur_s REAL,
    moving_dur_s REAL,
    elapsed_dur_s REAL,
    distance_m REAL,
    avg_speed_mps REAL,
    avg_hr REAL,
    max_hr REAL,
    PRIMARY KEY (activity_id, row_idx)
);
CREATE TABLE IF NOT EXISTS activity_splits (
    activity_id INTEGER NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    row_idx INTEGER NOT NULL,
    source_type TEXT,
    message_index INTEGER,
    start_gmt TEXT,
    dur_s REAL,
    moving_dur_s REAL,
    elapsed_dur_s REAL,
    distance_m REAL,
    avg_speed_mps REAL,
    avg_hr REAL,
    max_hr REAL,
    PRIMARY KEY (activity_id, row_idx)
);
CREATE TABLE IF NOT EXISTS activity_split_laps (
    activity_id INTEGER NOT NULL,
    split_idx INTEGER NOT NULL,
    member_idx INTEGER NOT NULL,
    lap_index INTEGER,
    PRIMARY KEY (activity_id, split_idx, member_idx),
    FOREIGN KEY (activity_id, split_idx)
        REFERENCES activity_splits(activity_id, row_idx) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS activity_interval_summaries (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(activity_id) ON DELETE CASCADE,
    summary_json TEXT NOT NULL
);

-- Manual console reports belong to activities, never replaceable captured rows.
CREATE TABLE IF NOT EXISTS treadmill_speed_sets (
    activity_id INTEGER PRIMARY KEY REFERENCES activities(activity_id) ON DELETE CASCADE,
    binding TEXT NOT NULL,
    work_count INTEGER NOT NULL CHECK (work_count > 0)
);
CREATE TABLE IF NOT EXISTS treadmill_speed_reports (
    activity_id INTEGER NOT NULL REFERENCES treadmill_speed_sets(activity_id) ON DELETE CASCADE,
    repetition INTEGER NOT NULL CHECK (repetition > 0),
    speed_kmh REAL NOT NULL CHECK (speed_kmh > 0),
    PRIMARY KEY (activity_id, repetition)
);

-- Per-set breakdown for HIIT/Hyrox/strength (summarizedExerciseSets).
-- Gold for a Hyrox athlete: jump rope / box jump / row / reps / load per movement.
CREATE TABLE IF NOT EXISTS activity_sets (
  activity_id  INTEGER NOT NULL,
  set_idx      INTEGER NOT NULL,
  category     TEXT,                       -- CARDIO|PLYO|ROW|UNKNOWN...
  subcategory  TEXT,                       -- JUMP_ROPE|BOX_JUMP|INDOOR_ROW...
  reps         INTEGER,
  sets         INTEGER,
  duration_s   REAL,
  max_weight   REAL,
  PRIMARY KEY (activity_id, set_idx),
  FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

-- Manual set overlay: the stations of a circuit the watch recorded as one nameless
-- round (a Hyrox / group-HIIT session comes back as a single ACTIVE set with
-- category=UNKNOWN). Hand-logged (garmin-coach log-sets), never written or
-- overwritten by the ETL - ground truth, system of record, like session_rpe.
-- Same shape as activity_sets. One row per station (set-share, not tonnage, ADR
-- 0011), so reps/sets/duration_s/max_weight are recorded for the record only.
-- Read through the movement_sets view, which lets these rows supersede the captured
-- ones per activity. Issue #60, ADR 0022.
CREATE TABLE IF NOT EXISTS manual_activity_sets (
  activity_id  INTEGER NOT NULL,
  set_idx      INTEGER NOT NULL,
  category     TEXT,
  subcategory  TEXT,                       -- Garmin vocabulary, e.g. SLED_PULL
  reps         INTEGER,
  sets         INTEGER,
  duration_s   REAL,
  max_weight   REAL,
  PRIMARY KEY (activity_id, set_idx),
  FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

-- Session-RPE (Borg CR10) per activity: the subjective load for strength/Hyrox
-- work that Garmin's HR-driven training_load under-counts. Manually logged
-- (garmin-coach log-rpe), never from Garmin - ground truth, system of record. Phase 7.
CREATE TABLE IF NOT EXISTS session_rpe (
  activity_id  INTEGER PRIMARY KEY,        -- one rating per activity
  rpe          INTEGER NOT NULL,           -- Borg CR10 (1-10)
  soreness     INTEGER,                    -- optional (1-10)
  mood         INTEGER,                    -- optional (1-10)
  source       TEXT,                       -- how it was entered (e.g. 'manual')
  notes        TEXT,
  FOREIGN KEY (activity_id) REFERENCES activities(activity_id) ON DELETE CASCADE
);

-- Niggle log: a body-part soreness/pain severity, manually logged (garmin-coach
-- log-rpe --niggle). One active entry dials the coach into reduced-mode. A later
-- entry for the same body part supersedes it (re-log lower to clear). Phase 7.
CREATE TABLE IF NOT EXISTS niggle (
  date       TEXT NOT NULL,                -- day the niggle was logged
  body_part  TEXT NOT NULL,                -- e.g. 'knee', 'shoulder' (free text)
  severity   INTEGER NOT NULL,             -- 1-5; >= threshold arms reduced-mode
  note       TEXT,
  PRIMARY KEY (date, body_part)
);

-- Goal events: the races the athlete is training toward. Manually logged
-- (garmin-coach event), never from Garmin - ground truth, system of record.
-- Two ORTHOGONAL uncertainty axes, deliberately not collapsed into one column:
--   status         - will the athlete start?      (a race may be skipped)
--   date_precision - is the exact day known?      (a committed race may be undated)
-- Only a 'confirmed' priority-A event anchors the block calendar; date_precision
-- suppresses nothing and merely asks the report to have the date pinned. Phase 9.
CREATE TABLE IF NOT EXISTS goal_event (
  id              INTEGER PRIMARY KEY,
  date            TEXT NOT NULL,             -- race day (best known estimate)
  type            TEXT NOT NULL CHECK (type IN ('hyrox','run_race')),
  priority        TEXT NOT NULL CHECK (priority IN ('A','B','C')),
  status          TEXT NOT NULL CHECK (status IN ('confirmed','tentative')),
  date_precision  TEXT NOT NULL CHECK (date_precision IN ('exact','approx')),
  target_s        INTEGER,                   -- goal time in seconds (not free text)
  note            TEXT,
  UNIQUE (date, type)                        -- `event add` must not clobber a race
);

-- Movement-pattern map: Garmin exercise subcategory -> movement pattern + muscle
-- group. Hand-curated core reference data (not Garmin-written, not a mart), seeded
-- here and extended by hand as new exercises appear. 'grip' lives on the muscle
-- axis (carries and pulls both tax it), not as a sixth movement pattern. Phase 8.
CREATE TABLE IF NOT EXISTS exercise_pattern (
  subcategory   TEXT PRIMARY KEY,           -- Garmin exercise name, e.g. BARBELL_DEADLIFT
  pattern       TEXT,                       -- push|pull|hinge|squat|carry
  muscle_group  TEXT                        -- chest|back|posterior|quads|shoulders|grip|core
);
-- seed rows (extend by hand; unmapped subcategories surface as a coverage fact)
INSERT OR IGNORE INTO exercise_pattern(subcategory, pattern, muscle_group) VALUES
 -- push
 ('BARBELL_BENCH_PRESS',      'push',  'chest'),
 ('DUMBBELL_BENCH_PRESS',     'push',  'chest'),
 ('INCLINE_BENCH_PRESS',      'push',  'chest'),
 ('PUSH_UP',                  'push',  'chest'),
 ('OVERHEAD_PRESS',           'push',  'shoulders'),
 ('BARBELL_OVERHEAD_PRESS',   'push',  'shoulders'),
 ('DUMBBELL_SHOULDER_PRESS',  'push',  'shoulders'),
 ('PUSH_PRESS',               'push',  'shoulders'),
 ('DIP',                      'push',  'chest'),
 -- pull
 ('PULL_UP',                  'pull',  'back'),
 ('CHIN_UP',                  'pull',  'back'),
 ('LAT_PULLDOWN',             'pull',  'back'),
 ('BARBELL_ROW',              'pull',  'back'),
 ('BENT_OVER_ROW',            'pull',  'back'),
 ('CABLE_ROW',                'pull',  'back'),
 ('DUMBBELL_ROW',             'pull',  'back'),
 ('FACE_PULL',                'pull',  'shoulders'),
 -- hinge
 ('BARBELL_DEADLIFT',         'hinge', 'posterior'),
 ('ROMANIAN_DEADLIFT',        'hinge', 'posterior'),
 ('SUMO_DEADLIFT',            'hinge', 'posterior'),
 ('KETTLEBELL_SWING',         'hinge', 'posterior'),
 ('HIP_THRUST',               'hinge', 'posterior'),
 ('GOOD_MORNING',             'hinge', 'posterior'),
 -- squat
 ('BARBELL_BACK_SQUAT',       'squat', 'quads'),
 ('BARBELL_FRONT_SQUAT',      'squat', 'quads'),
 ('GOBLET_SQUAT',             'squat', 'quads'),
 ('AIR_SQUAT',                'squat', 'quads'),
 ('LUNGE',                    'squat', 'quads'),
 ('WALL_BALL',                'squat', 'quads'),
 ('LEG_PRESS',                'squat', 'quads'),
 -- carry (grip-heavy)
 ('FARMERS_WALK',             'carry', 'grip'),
 ('FARMERS_CARRY',            'carry', 'grip'),
 ('SANDBAG_CARRY',            'carry', 'grip'),
 ('SLED_PUSH',                'carry', 'quads'),
 ('SLED_PULL',                'carry', 'grip'),
 -- Hyrox circuit stations logged by hand through the manual set overlay (issue #60,
 -- ADR 0022). Judgment calls: knee-dominant stations read as squat/quads, the
 -- double-pole ski erg as a hip hinge, and two stations with no clean pattern count
 -- on the muscle axis only.
 ('SPANISH_SQUAT',            'squat', 'quads'),
 ('BOX_STEP_OVER',            'squat', 'quads'),
 ('SKI_ERG',                  'hinge', 'posterior'),
 ('BATTLE_ROPE',              NULL,    'shoulders'),
 ('V_UP',                     NULL,    'core'),
 -- known non-movement: a Garmin CARDIO pseudo-set (e.g. a Hyrox run leg) is a
 -- real ACTIVE set with no exercise name. Mapped to no pattern/muscle so it is
 -- excluded from the overlap load-split, yet stays out of the unmapped drift fact.
 ('CARDIO',                   NULL,    NULL);

-- =============================================================================
-- CORE · DAILY WELLNESS STREAMS  (one row per date; has_data=0 = explicit gap)
-- =============================================================================
CREATE TABLE IF NOT EXISTS daily_wellness (
  date              TEXT PRIMARY KEY,
  has_data          INTEGER DEFAULT 1,     -- 0 = onboarding / not worn
  rhr               INTEGER,               -- resting HR
  steps             INTEGER,
  floors            INTEGER,
  intensity_mod_min INTEGER,
  intensity_vig_min INTEGER,
  -- stress
  stress_avg        INTEGER,
  stress_max        INTEGER,
  rest_min          INTEGER,               -- time in rest stress
  low_stress_min    INTEGER,
  med_stress_min    INTEGER,
  high_stress_min   INTEGER,
  -- respiration / spo2
  resp_avg          REAL,
  resp_min          REAL,
  resp_max          REAL,
  spo2_avg          INTEGER,
  spo2_min          INTEGER,
  -- body battery
  bb_min            INTEGER,
  bb_max            INTEGER,
  bb_charged        INTEGER,
  bb_drained        INTEGER,
  bb_feedback       TEXT                   -- dynamic feedback level/type
);

CREATE TABLE IF NOT EXISTS sleep (
  date            TEXT PRIMARY KEY,
  total_s         INTEGER,
  deep_s          INTEGER,
  rem_s           INTEGER,
  light_s         INTEGER,
  awake_s         INTEGER,
  nap_s           INTEGER,
  deep_pct        INTEGER,                 -- sleepScores.deepPercentage.value
  rem_pct         INTEGER,
  light_pct       INTEGER,
  score           INTEGER,                 -- sleepScores.overall.value
  score_feedback  TEXT,                    -- POSITIVE_HIGHLY_RECOVERING...
  awake_count     INTEGER,
  restless_count  INTEGER,
  avg_sleep_stress INTEGER,
  resting_hr      INTEGER,                 -- top-level restingHeartRate (reliable RHR source!)
  avg_sleep_hr    INTEGER,                 -- dailySleepDTO.avgHeartRate
  avg_overnight_hrv INTEGER,
  avg_resp        REAL,
  low_resp        REAL,
  high_resp       REAL,
  skin_temp_dev_c REAL,                    -- avgSkinTempDeviationC (illness/fatigue signal)
  bb_change       INTEGER,                 -- bodyBatteryChange overnight (recharge)
  sleep_need_min  INTEGER,                 -- sleepNeed.actual (personalised need)
  bedtime_local   TEXT,                    -- sleepStartTimestampLocal
  waketime_local  TEXT
);

CREATE TABLE IF NOT EXISTS hrv_nightly (
  date            TEXT PRIMARY KEY,
  avg_hrv         INTEGER,                 -- hrvSummary.lastNightAvg  (main HRV series)
  night_high_5min INTEGER,                 -- lastNight5MinHigh
  weekly_avg      INTEGER,
  garmin_baseline INTEGER,                 -- Garmin's own baseline (null during onboarding)
  status          TEXT,                    -- BALANCED|LOW|UNBALANCED|NONE(onboarding)
  feedback_phrase TEXT                     -- ONBOARDING_1 etc.
);

-- Training readiness — pick the end-of-day representative row per date
-- (raw keeps all intraday snapshots).
CREATE TABLE IF NOT EXISTS training_readiness (
  date              TEXT PRIMARY KEY,
  score             INTEGER,               -- 0-100
  level             TEXT,                  -- PRIME|HIGH|MODERATE|LOW|POOR
  feedback_short    TEXT,                  -- READY_TO_GO|LET_YOUR_BODY_RECOVER...
  recovery_time_min INTEGER,               -- recoveryTime (minutes to full recovery)
  acwr_factor_pct   INTEGER,
  hrv_factor_pct    INTEGER,
  sleep_factor_pct  INTEGER,
  stress_factor_pct INTEGER,
  sleep_hist_pct    INTEGER,
  valid_sleep       INTEGER
);

-- Training status snapshot (daily) — load balance targets, ACWR, fitness markers.
CREATE TABLE IF NOT EXISTS training_status_daily (
  date                 TEXT PRIMARY KEY,
  status               TEXT,               -- PRODUCTIVE|MAINTAINING|OVERREACHING|DETRAINING|RECOVERY|PEAKING
  status_phrase        TEXT,               -- PRODUCTIVE_3...
  fitness_trend        INTEGER,
  -- Garmin's own acute:chronic (reference cross-check for our own ACWR)
  garmin_acute_load    REAL,
  garmin_chronic_load  REAL,
  garmin_acwr          REAL,               -- dailyAcuteChronicWorkloadRatio
  garmin_acwr_status   TEXT,               -- OPTIMAL|LOW|HIGH
  chronic_min          REAL,               -- loadTunnel min/max (target chronic band)
  chronic_max          REAL,
  -- monthly load balance (source of AEROBIC_LOW_SHORTAGE)
  ml_aero_low          REAL,
  ml_aero_high         REAL,
  ml_anaerobic         REAL,
  ml_aero_low_min      REAL, ml_aero_low_max  REAL,
  ml_aero_high_min     REAL, ml_aero_high_max REAL,
  ml_anaerobic_min     REAL, ml_anaerobic_max REAL,
  balance_phrase       TEXT,               -- AEROBIC_LOW_SHORTAGE...
  -- fitness markers riding along
  vo2max               REAL,
  fitness_age          REAL,
  heat_accl_pct        INTEGER,            -- heatAcclimationPercentage
  heat_trend           TEXT,               -- ACCLIMATIZED...
  altitude_accl        INTEGER
);

-- Optional: body battery discrete events (SLEEP/ACTIVITY/STRESS/RECOVERY/NAP).
-- =============================================================================
-- CORE · PERIODIC / RARE  (pull weekly or on change)
-- =============================================================================
-- Time series of fitness markers (one row when any value changes).
CREATE TABLE IF NOT EXISTS fitness_markers (
  date               TEXT PRIMARY KEY,
  lactate_thr_hr     INTEGER,             -- lactate threshold heart rate
  lactate_thr_pace   REAL               -- lactate threshold pace (seconds per km)
);

-- =============================================================================
-- CONFIG  (data-driven coach rules & plan — no hardcoding in the skill)
-- =============================================================================
CREATE TABLE IF NOT EXISTS coach_thresholds (
  key    TEXT PRIMARY KEY,
  value  REAL,
  note   TEXT
);
-- Algorithm policy defaults, not observations about an athlete. Existing personal
-- overrides remain in installed databases; fresh databases have no borrowed HRV band.
INSERT OR IGNORE INTO coach_thresholds(key,value,note) VALUES
 ('hrv_low_k_sd',           1, 'flag if hrv < baseline - k*SD'),
 ('acwr_risk_high',       1.5, 'injury-risk ceiling'),
 ('acwr_risk_low',        0.8, 'detraining floor'),
 ('acwr_sweet_hi',        1.3, 'top of sweet spot'),
 ('acwr_min_chronic_days', 28, 'ACWR unreliable below this n_chronic'),
 ('hard_te_load',         150, 'session considered "hard" above this training_load'),
 -- Phase 6 personal zones: %LTHR band ceilings (Garmin/Friel) + guards
 ('z1_hi_pct_lthr',      0.80, 'Z1 upper = 80% LTHR'),
 ('z2_hi_pct_lthr',      0.89, 'Z2 upper (easy ceiling) = 89% LTHR'),
 ('z3_hi_pct_lthr',      0.94, 'Z3 upper = 94% LTHR'),
 ('z4_hi_pct_lthr',      0.99, 'Z4 upper = 99% LTHR'),
 ('z2_pace_fallback_mult', 1.30, 'Z2 pace ceiling = LT pace * this, when regression is thin'),
 ('zones_regression_min_runs', 12, 'min heat-clean runs before pace<->HR regression is trusted'),
 ('zones_regression_min_r2', 0.30, 'min regression R^2 before it beats the fallback'),
 ('zones_heat_temp_c',     22, 'runs above this air temp (C) are excluded from the pace fit'),
 ('zones_stale_days',      28, 'LTHR detection older than this marks zones stale'),
 ('monotony_high',        2.0, 'Foster monotony above this flags overtraining (Phase 5)'),
 ('deload_load_rise_weeks', 3, 'consecutive rising-load weeks that arm DELOAD_ADVISED'),
 ('deload_min_history_weeks', 3, 'below this many complete weeks the deload signal is silent'),
 ('deload_drop_pct',     0.40, 'retrospective: a load_total drop this large marks a deload week'),
 -- Phase 6b snapshot: trend lookback windows (days) + minimum measurable span
 ('snapshot_vo2max_lookback_days', 90, 'window for the VO2max trend delta (slow-moving)'),
 ('snapshot_hrv_lookback_days',    28, 'window for the HRV trend delta (Garmin weekly avg)'),
 ('snapshot_trend_min_span_days',   7, 'below this available span a trend delta is NULL'),
 -- Phase 7 strength/Hyrox load model + niggle log
 ('srpe_load_scale',             0.3, 'scales Foster sRPE (rpe*min) into Garmin-load units'),
 ('sila_default_rpe',              7, 'default Borg CR10 RPE for a Sila session with no logged RPE'),
 ('niggle_active_days',            7, 'days a single niggle log stays active for reduced-mode'),
 ('niggle_reduced_mode_severity',  3, 'active-niggle severity (1-5) that arms reduced-mode'),
 -- Phase 8 movement-overlap (Garmin-load units; placeholders, tune as history grows)
 ('pattern_load_floor',           20, 'min per-key session load to count a pattern as loaded'),
 ('pattern_overlap_high',         40, 'adjacent-day overlap at/above this arms PATTERN_STACK/MUSCLE_OVERLAP'),
 -- Phase 9 periodization: block lengths counted back from the race week. `base`
 -- takes whatever is left, so the athlete is always in some block.
 ('taper_weeks',                   2, 'final weeks before the race; TAPER_ACTIVE fires here'),
 ('peak_weeks',                    3, 'weeks of peak work before the taper'),
 ('build_weeks',                   5, 'weeks of build work before the peak'),
 ('deload_every_n_weeks',          4, 'planned deload cadence, counted back from a block end'),
 ('race_proximity_weeks',          3, 'a goal race this close arms RACE_PROXIMITY');

-- Weekly training template for plan-vs-actual (0=Mon..6=Sun).
CREATE TABLE IF NOT EXISTS plan_template (
  dow         INTEGER PRIMARY KEY,         -- 0=Mon
  planned     TEXT,                        -- free text label
  intent      TEXT                         -- rest|quality|easy|hyrox|tempo
);
-- Populated only from the athlete's explicit first-run choice. Bootstrap never
-- recreates an owner's week or changes a template already stored in an installation.

-- Per-week plan override: the authored plan of record for one week, ingested from
-- plans/<monday>_week.md (issue #21). The Markdown file stays the human-authored
-- source of record; these rows are a derived cache. Weeks with no rows fall back
-- to plan_template via the resolver in core/plan.py - the only sanctioned reader.
CREATE TABLE IF NOT EXISTS plan_week (
  week_start  TEXT NOT NULL,                 -- Monday, YYYY-MM-DD
  dow         INTEGER NOT NULL,              -- 0=Mon
  planned     TEXT,                          -- free text label from the plan file
  intent      TEXT,                          -- core/plan.py INTENTS (the vocabulary of
                                             -- record; wider than plan_template's)
  PRIMARY KEY (week_start, dow)
);

-- =============================================================================
-- SYNC  (incremental watermark per stream)
-- =============================================================================
CREATE TABLE IF NOT EXISTS sync_state (
  stream           TEXT PRIMARY KEY,       -- activities|sleep|hrv|wellness|readiness|status
  last_synced_date TEXT NOT NULL,
  updated_at       TEXT
);

-- =============================================================================
-- MART · DAILY  (recomputed by features.py; safe to DROP/rebuild)
-- =============================================================================
CREATE TABLE IF NOT EXISTS daily_metrics (
  date            TEXT PRIMARY KEY,
  -- HRV
  hrv             INTEGER,
  hrv_baseline    REAL,                    -- rolling median
  hrv_sd          REAL,
  hrv_low_flag    INTEGER,                 -- hrv < baseline - k*SD
  -- load / ACWR
  load_day        REAL,
  acute7          REAL,                    -- mean daily load, 7d
  chronic28       REAL,                    -- mean daily load, 28d
  acwr            REAL,                    -- acute7/chronic28 (own)
  n_chronic       INTEGER,                 -- days in chronic window (reliability!)
  -- load balance buckets (by TE, Garmin logic)
  load_low        REAL,
  load_high       REAL,
  load_anaerobic  REAL,
  load_strength   REAL,                    -- Phase 7: blended sRPE load for strength (Siła)
  -- HR-zone minutes (separate language from buckets)
  z1_min REAL, z2_min REAL, z3_min REAL, z4_min REAL, z5_min REAL,
  -- recovery
  sleep_score     INTEGER,
  sleep_debt_h    REAL,                    -- cumulative need - actual
  rhr             INTEGER,
  rhr_delta       REAL,                    -- rhr - baseline (rise = fatigue/illness)
  skin_temp_dev_c REAL,
  bb_min INTEGER, bb_max INTEGER, bb_recharge INTEGER,
  stress_avg      INTEGER,
  readiness_score INTEGER,
  vo2max          REAL
);

-- =============================================================================
-- MART · WEEKLY  (rollups the coach actually reasons on)
-- =============================================================================
CREATE TABLE IF NOT EXISTS weekly_metrics (
  week_start        TEXT PRIMARY KEY,      -- Monday
  load_total        REAL,
  load_low          REAL, load_high REAL, load_anaerobic REAL,
  load_strength     REAL,                  -- Phase 7: weekly sum of blended strength load
  low_share         REAL, high_share REAL, anaero_share REAL,
  strength_share    REAL,                  -- Phase 7: the four shares sum to 1.0
  z2_min            REAL, threshold_min REAL, z5_min REAL,
  -- Foster monotony/strain (classic overtraining flags)
  monotony          REAL,                  -- mean daily load / SD daily load
  strain            REAL,                  -- weekly load * monotony
  acwr_end          REAL,
  hrv_mean          REAL,
  hrv_trend         REAL,                  -- slope vs prior week
  rhr_mean          REAL,
  sleep_score_mean  REAL,
  n_sessions        INTEGER,
  n_quality         INTEGER,
  n_rest_days       INTEGER,
  max_consec_hard   INTEGER,               -- longest run of hard days (fri->sat risk)
  plan_adherence    REAL,                  -- % of planned intents matched
  -- Phase 9: same-run copies of the week's plan_block row (that mart is the
  -- source of truth; these save the weekly report a join). NULL with no anchor.
  block             TEXT,
  weeks_to_event    INTEGER
);

CREATE TABLE IF NOT EXISTS weekly_plan_actual (
  week_start  TEXT NOT NULL,
  dow         INTEGER NOT NULL,            -- 0=Mon
  date        TEXT NOT NULL,
  planned     TEXT,
  actual      TEXT,
  matched     INTEGER NOT NULL,
  PRIMARY KEY (week_start, dow),
  FOREIGN KEY (week_start) REFERENCES weekly_metrics(week_start) ON DELETE CASCADE
);

-- Personal training zones (mart): a single current standing, recomputed from the
-- watch-detected LTHR anchor (fitness_markers) + aerobic runs. Phase 6.
CREATE TABLE IF NOT EXISTS athlete_zones (
  id                       INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  lthr_bpm                 INTEGER,           -- anchor: lactate threshold HR
  z1_hi_bpm                INTEGER,           -- contiguous %LTHR upper bounds;
  z2_hi_bpm                INTEGER,           --   Z1 < z1_hi, Z2 = [z1_hi, z2_hi],
  z3_hi_bpm                INTEGER,           --   ..., Z5 > z4_hi
  z4_hi_bpm                INTEGER,
  threshold_pace_s_per_km  REAL,             -- LT pace (seconds per km)
  z2_pace_ceiling_s_per_km REAL,             -- keep easy runs under this pace
  source                   TEXT,             -- <method>+lthr | no_anchor; method: regression | threshold_pace_fallback
  lthr_detected_on         TEXT,             -- calendar day of the LTHR detection
  computed_at              TEXT,             -- recompute cutoff (through_date)
  stale                    INTEGER           -- 1 when detection older than zones_stale_days
);

-- Athlete snapshot (mart): the single current standing "where do I stand right now".
-- A same-run copy of finished marts + core, recomputed as the tail of `features`
-- after weekly + zones. Serialized to reports/{date}/snapshot.json. Phase 6b.
CREATE TABLE IF NOT EXISTS athlete_status (
  id                       INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
  computed_at              TEXT,              -- as-of date; every latest read scoped to <= this
  -- fitness markers (+ trend delta over an available window; span it was measured over)
  vo2max                   REAL,              -- training_status_daily.vo2max (latest)
  vo2max_delta             REAL,              -- signed change over the lookback (Phase 6b/02)
  vo2max_span_days         INTEGER,           -- actual days the delta spans (may be < lookback)
  -- HRV band (+ trend)
  hrv_baseline             REAL,              -- daily_metrics (latest)
  hrv_sd                   REAL,
  hrv_delta                REAL,
  hrv_span_days            INTEGER,
  -- load / ACWR (reuses digest headline + signals.load_shares)
  acwr                     REAL,              -- daily_metrics (latest)
  n_chronic                INTEGER,
  acwr_reliable            INTEGER,           -- n_chronic >= acwr_min_chronic_days
  load_7d                  REAL,              -- trailing-7-day load total
  low_share                REAL,              -- easy/hard/anaerobic shares of that load
  high_share               REAL,
  anaero_share             REAL,
  -- recovery
  readiness_score          INTEGER,           -- training_readiness (latest)
  readiness_level          TEXT,
  sleep_debt_h             REAL,              -- daily_metrics (latest)
  heat_accl_pct            INTEGER,           -- training_status_daily (latest)
  heat_trend               TEXT,
  altitude_accl            INTEGER,
  -- personal zones (full mirror of athlete_zones for a self-contained snapshot.json)
  lthr_bpm                 INTEGER,
  z1_hi_bpm                INTEGER,
  z2_hi_bpm                INTEGER,
  z3_hi_bpm                INTEGER,
  z4_hi_bpm                INTEGER,
  threshold_pace_s_per_km  REAL,
  z2_pace_ceiling_s_per_km REAL,
  zones_source             TEXT,
  lthr_detected_on         TEXT,
  zones_stale              INTEGER,
  -- active plan; block/weeks_to_event/taper_active mirror this week's plan_block row
  -- (Phase 9). All three are NULL when no confirmed priority-A race is upcoming.
  block                    TEXT,
  weeks_to_event           INTEGER,
  taper_active             INTEGER,
  planned_intent_today     TEXT,              -- resolved plan intent for weekday(computed_at)
  planned_label_today      TEXT,
  plan_source_today        TEXT               -- plan_week | plan_template
);

-- Periodization (mart): one row per week, the training block counted back from the
-- anchor goal event. UNIQUE among the marts in that it spans the FUTURE too - out to
-- the race week - which is why it cannot live as columns on weekly_metrics (that
-- table only holds weeks that already happened). Empty when no confirmed priority-A
-- race is upcoming: the system says it does not know what it is training for rather
-- than counting down to a race already run. Recomputed by periodize.py as a tail of
-- `features`. Phase 9.
CREATE TABLE IF NOT EXISTS plan_block (
  week_start       TEXT PRIMARY KEY,           -- Monday
  block            TEXT NOT NULL CHECK (block IN ('base','build','peak','taper')),
  weeks_to_event   INTEGER NOT NULL,           -- 0 in the race week itself
  is_deload        INTEGER NOT NULL,           -- 1 = the PLAN prescribes a recovery week.
                                               -- Independent of DELOAD_ADVISED, which is
                                               -- what the actual load did; the divergence
                                               -- between them is the finding, not a conflict.
  anchor_event_id  INTEGER NOT NULL,
  FOREIGN KEY (anchor_event_id) REFERENCES goal_event(id) ON DELETE CASCADE
);

-- Movement-pattern overlap: the same pattern or muscle group loaded on adjacent
-- days without a rest day. Long format - one row per (date, dim, key) whose load
-- stacks D-1 -> D above pattern_load_floor. Recomputed by overlap.py as a tail of
-- `features`; only overlap>0 rows are materialized. Phase 8.
CREATE TABLE IF NOT EXISTS pattern_overlap (
  date       TEXT NOT NULL,                  -- the second (stacked-on) day D
  dim        TEXT NOT NULL,                  -- 'pattern' | 'muscle'
  key        TEXT NOT NULL,                  -- e.g. 'hinge' (pattern) | 'grip' (muscle)
  load_d     REAL,                           -- key's blended load on D
  load_prev  REAL,                           -- key's blended load on D-1
  overlap    REAL,                           -- min(load_d, load_prev)
  PRIMARY KEY (date, dim, key)
);

-- =============================================================================
-- VIEWS  (convenience joins; DuckDB-friendly)
-- =============================================================================
-- The single read surface for the movement-overlap mart: an activity's manual rows
-- when it has any, its captured rows otherwise. Per-activity supersede, never a
-- row-level merge - the captured row of a circuit is one pseudo-set standing in for
-- the same work the manual rows describe, so merging would double-count. A read-time
-- decision: activity_sets stays untouched, and a firmware that one day names the
-- stations needs only the manual rows deleted. Issue #60, ADR 0022.
CREATE VIEW IF NOT EXISTS movement_sets AS
SELECT activity_id, set_idx, category, subcategory, reps, sets, duration_s, max_weight,
       'manual' AS source
FROM manual_activity_sets
UNION ALL
SELECT s.activity_id, s.set_idx, s.category, s.subcategory, s.reps, s.sets, s.duration_s,
       s.max_weight, 'captured' AS source
FROM activity_sets s
WHERE NOT EXISTS (SELECT 1 FROM manual_activity_sets m WHERE m.activity_id = s.activity_id);
