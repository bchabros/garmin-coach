# Domain glossary

Shared vocabulary for the garmin-coach project. Use these terms consistently in
code, docstrings, PRDs, and ADRs.

## Data layers (medallion)

- **raw** - append-only `raw_payloads`; original Garmin JSON, never overwritten.
- **core** - normalized, upserted-by-PK tables (`activities`, `daily_wellness`,
  `sleep`, `hrv_nightly`, `sync_state`). The system of record.
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
  (`aero_te < 2.5`), else `load_high`. A different language from HR zones.
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
  `HRV_SLEEP_CONFOUND`, `DELOAD_ADVISED`.
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

## Process terms

- **data_start** - first date with real (non-onboarding) data: 2026-06-08. Earlier
  dates are explicit gaps, not zero training.
- **watermark** - per-stream `sync_state.last_synced_date`; how incremental sync
  tracks progress.
- **seam** - the agreed boundary a test exercises: pure normalizers (`models.py`),
  the persistence layer (`db.py`), the sync orchestrator (`sync.py`), the features
  materializer (`features.py`), the weekly rollup (`weekly.py`), threshold policy
  (`thresholds.py`), and the digest builder (`build_digest`/`digest.py`) at the DB
  boundary.
- **golden regression** - a test that reproduces the reference hand-analysis
  (2026-06-09..07-04) from frozen real anonymized core data.
