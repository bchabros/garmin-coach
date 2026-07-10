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
  fetched_at  TEXT NOT NULL,          -- ISO ts of the pull
  endpoint    TEXT NOT NULL,          -- e.g. 'get_sleep_data', 'get_activities_by_date'
  ref_date    TEXT NOT NULL,          -- date the payload refers to (YYYY-MM-DD)
  payload     TEXT NOT NULL,          -- raw JSON string
  PRIMARY KEY (endpoint, ref_date, fetched_at)
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

  -- strength/hiit rollups
  total_sets      INTEGER,
  total_reps      INTEGER,

  -- fastest splits (running)
  split_1k_s      REAL,
  split_1mi_s     REAL,
  split_5k_s      REAL,
  split_10k_s     REAL,

  -- geo / flags
  location_name   TEXT,
  start_lat REAL, start_lon REAL,
  is_pr           INTEGER DEFAULT 0,       -- personal-record flag on the activity

  -- environment (per-activity weather; Fahrenheit payload converted to Celsius)
  temp_c          REAL                     -- air temperature; NULL when no weather
);
CREATE INDEX IF NOT EXISTS ix_activities_date ON activities(date);
CREATE INDEX IF NOT EXISTS ix_activities_disc ON activities(discipline);

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
CREATE TABLE IF NOT EXISTS body_battery_events (
  date         TEXT NOT NULL,
  event_idx    INTEGER NOT NULL,
  event_type   TEXT,                       -- SLEEP|ACTIVITY|STRESS|RECOVERY|NAP
  start_gmt    TEXT,
  duration_ms  INTEGER,
  bb_impact    INTEGER,                    -- +charge / -drain
  feedback     TEXT,
  PRIMARY KEY (date, event_idx)
);

-- =============================================================================
-- CORE · PERIODIC / RARE  (pull weekly or on change)
-- =============================================================================
CREATE TABLE IF NOT EXISTS weight_log (
  date        TEXT PRIMARY KEY,
  weight_g    INTEGER,
  bmi         REAL,
  body_fat_pct REAL,
  muscle_mass_g INTEGER,
  body_water_pct REAL
);

CREATE TABLE IF NOT EXISTS race_predictions (
  date        TEXT PRIMARY KEY,
  t_5k_s      INTEGER,
  t_10k_s     INTEGER,
  t_half_s    INTEGER,
  t_marathon_s INTEGER
);

-- Time series of fitness markers (one row when any value changes).
CREATE TABLE IF NOT EXISTS fitness_markers (
  date               TEXT PRIMARY KEY,
  vo2max_running     REAL,
  vo2max_cycling     REAL,
  lactate_thr_hr     INTEGER,             -- lactate threshold heart rate
  lactate_thr_pace   REAL,               -- lactate threshold pace (seconds per km)
  endurance_score    INTEGER,
  hill_score         INTEGER,
  fitness_age        REAL
);

CREATE TABLE IF NOT EXISTS personal_records (
  pr_id       INTEGER PRIMARY KEY,
  type_key    TEXT,                        -- longest_run|fastest_5k|...
  value       REAL,
  achieved_at TEXT,
  activity_id INTEGER
);

-- =============================================================================
-- CONFIG  (data-driven coach rules & plan — no hardcoding in the skill)
-- =============================================================================
CREATE TABLE IF NOT EXISTS coach_thresholds (
  key    TEXT PRIMARY KEY,
  value  REAL,
  note   TEXT
);
-- seed defaults derived from this athlete's data (edit as history grows)
INSERT OR IGNORE INTO coach_thresholds(key,value,note) VALUES
 ('hrv_baseline_ms',       68, 'median nightly HRV, 09.06-04.07 window'),
 ('hrv_sd_ms',             11, 'std of nightly HRV'),
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
 ('snapshot_weight_lookback_days', 28, 'window for the body-weight trend delta'),
 ('snapshot_hrv_lookback_days',    28, 'window for the HRV trend delta (Garmin weekly avg)'),
 ('snapshot_trend_min_span_days',   7, 'below this available span a trend delta is NULL');

-- Weekly training template for plan-vs-actual (0=Mon..6=Sun).
CREATE TABLE IF NOT EXISTS plan_template (
  dow         INTEGER PRIMARY KEY,         -- 0=Mon
  planned     TEXT,                        -- free text label
  intent      TEXT                         -- rest|quality|easy|hyrox|tempo
);
INSERT OR IGNORE INTO plan_template(dow,planned,intent) VALUES
 (0,'rest','rest'),
 (1,'FBB + Hyrox','quality'),
 (2,'bieganie easy/long','easy'),
 (3,'rest','rest'),
 (4,'Crossfit + Hyrox','quality'),
 (5,'Hyrox lub tempo bieg','quality'),
 (6,'czasem easy/long run','easy');

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
  hrv_cv          REAL,                    -- night-to-night coefficient of variation
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
  -- HR-zone minutes (separate language from buckets)
  z1_min REAL, z2_min REAL, z3_min REAL, z4_min REAL, z5_min REAL,
  -- recovery
  sleep_score     INTEGER,
  sleep_h         REAL,
  sleep_debt_h    REAL,                    -- cumulative need - actual
  rhr             INTEGER,
  rhr_baseline    REAL,                    -- rolling median RHR
  rhr_delta       REAL,                    -- rhr - baseline (rise = fatigue/illness)
  skin_temp_dev_c REAL,
  bb_min INTEGER, bb_max INTEGER, bb_recharge INTEGER,
  stress_avg      INTEGER,
  readiness_score INTEGER,
  recovery_time_h REAL,
  vo2max          REAL,
  -- composite flags for the coach
  illness_watch   INTEGER                  -- HRV↓ & RHR↑ & (resp↑ or skin_temp↑)
);

-- =============================================================================
-- MART · WEEKLY  (rollups the coach actually reasons on)
-- =============================================================================
CREATE TABLE IF NOT EXISTS weekly_metrics (
  week_start        TEXT PRIMARY KEY,      -- Monday
  load_total        REAL,
  load_low          REAL, load_high REAL, load_anaerobic REAL,
  low_share         REAL, high_share REAL, anaero_share REAL,
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
  plan_adherence    REAL                   -- % of planned intents matched
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
  vo2max                   REAL,              -- fitness_markers.vo2max_running (latest)
  vo2max_delta             REAL,              -- signed change over the lookback (Phase 6b/02)
  vo2max_span_days         INTEGER,           -- actual days the delta spans (may be < lookback)
  weight_kg                REAL,              -- weight_log.weight_g / 1000 (latest)
  weight_delta             REAL,
  weight_span_days         INTEGER,
  t_5k_s                   INTEGER,           -- race_predictions (latest)
  t_10k_s                  INTEGER,
  t_half_s                 INTEGER,
  t_marathon_s             INTEGER,
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
  -- active plan; block/weeks_to_event/taper_active are NULL placeholders until Phase 9
  block                    TEXT,
  weeks_to_event           INTEGER,
  taper_active             INTEGER,
  planned_intent_today     TEXT,              -- plan_template[weekday(computed_at)]
  planned_label_today      TEXT
);

-- =============================================================================
-- VIEWS  (convenience joins; DuckDB-friendly)
-- =============================================================================
CREATE VIEW IF NOT EXISTS v_daily AS
SELECT d.date,
       m.hrv, m.hrv_baseline, m.hrv_low_flag,
       m.acwr, m.n_chronic,
       m.load_day, m.load_low, m.load_high, m.load_anaerobic,
       s.total_s/3600.0 AS sleep_h, s.deep_s/60.0 AS deep_min, s.rem_s/60.0 AS rem_min,
       s.score AS sleep_score, s.resting_hr AS rhr, s.skin_temp_dev_c,
       w.bb_min, w.bb_max, w.stress_avg,
       tr.score AS readiness, tr.level AS readiness_level,
       ts.status AS training_status, ts.balance_phrase, ts.vo2max
FROM daily_wellness d
LEFT JOIN daily_metrics       m  ON m.date  = d.date
LEFT JOIN sleep               s  ON s.date  = d.date
LEFT JOIN hrv_nightly         h  ON h.date  = d.date
LEFT JOIN daily_wellness      w  ON w.date  = d.date
LEFT JOIN training_readiness  tr ON tr.date = d.date
LEFT JOIN training_status_daily ts ON ts.date = d.date;

-- Activities enriched with pace (min/km) and the load bucket they fall into.
CREATE VIEW IF NOT EXISTS v_activity_enriched AS
SELECT a.*,
       CASE WHEN a.distance_m > 0 AND a.avg_speed_mps > 0
            THEN (1000.0/a.avg_speed_mps)/60.0 END AS pace_min_km,
       CASE WHEN a.anaero_te >= 1.0 THEN 'anaerobic'
            WHEN a.aero_te   <  2.5 THEN 'aerobic_low'
            ELSE 'aerobic_high' END AS load_bucket
FROM activities a;

-- Days flagged for the coach (low HRV / ACWR out of band / illness watch).
CREATE VIEW IF NOT EXISTS v_flags AS
SELECT date, hrv, hrv_low_flag, acwr, n_chronic, rhr_delta, illness_watch
FROM daily_metrics
WHERE hrv_low_flag = 1
   OR illness_watch = 1
   OR (n_chronic >= 28 AND (acwr > 1.5 OR acwr < 0.8));
