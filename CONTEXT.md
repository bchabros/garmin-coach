# Garmin Coach Context

## Glossary

- **System-of-record**: The local SQLite database that stores raw Garmin payloads and normalized core rows. Metrics and coach layers read this database instead of calling Garmin live.
- **Raw payload**: The unmodified JSON response stored in `raw_payloads` before normalization.
- **Core table**: A normalized SQLite table such as `activities`, `sleep`, `hrv_nightly`, `daily_wellness`, `training_readiness`, or `training_status_daily`.
- **Stream**: One independently synchronized Garmin data family: `activities`, `sleep`, `hrv`, `wellness`, `readiness`, or `status`.
- **Watermark**: The last date a stream successfully processed, stored in `sync_state.last_synced_date`.
- **Partial success**: A sync run where at least one stream progresses while another stream fails and leaves its watermark unchanged.
- **Daily stream**: A stream fetched one date at a time: sleep, HRV, wellness, readiness, and training status.
- **Activities range**: The activities stream fetch window, first attempted as one range call and then retried per day if the range call fails.
- **Complete week**: A Monday–Sunday span whose seven days all lie at or before yesterday. Only complete weeks are rolled up into `weekly_metrics`; the in-progress current week is skipped so weekly figures never lie from 1–2 days of data.
- **Weekly rollup**: The derivation of one `weekly_metrics` row per complete week purely from `daily_metrics` (a mart-from-mart step). Never touches Garmin; recomputable and safe to rebuild.
- **Planned intent**: The training category the user's `plan_template` assigns to a day of week (`rest | quality | easy | ...`).
- **Actual intent**: The same category inferred from what actually happened that day, classified by load — `quality` when the day's load reaches `hard_te_load` (or has anaerobic load), `easy` for any lighter activity, `rest` for no activity. A day the athlete trained without wearing the watch is invisible to the system and reads as `rest` (an ETL limitation, by decision, not a bug).
- **Plan adherence**: The fraction of the week's seven days whose actual intent exactly matches the planned intent. The report also shows the *direction* of each mismatch, since the DoD asks to surface divergence, not just a number.
- **Monotony / Strain (Foster)**: `monotony` = mean daily load ÷ SD of daily load across the week (`NULL` when uncomputable, e.g. fewer than two training days); `strain` = weekly load × monotony. Classic overtraining flags.
- **Deload (retrospective)**: A descriptive fact that a completed week's `load_total` dropped by at least `deload_drop_pct` versus the preceding weeks — recorded from the mart, not an alert.
- **Deload advised (prospective)**: The `DELOAD_ADVISED` signal — fires when there is enough history (`deload_min_history_weeks`) and `load_total` rose for `deload_load_rise_weeks` consecutive weeks and either `acwr_end` exceeds `acwr_risk_high` or `monotony` exceeds `monotony_high`. Silent when history is too short (it never guesses).

## Testing Seams

- Test normalizers through pure model functions.
- Test persistence through `db.py` helpers and observable SQLite state.
- Test orchestration through `sync.py` with an injected fake Garmin client.
- Keep real Garmin transport and auth outside unit tests.
