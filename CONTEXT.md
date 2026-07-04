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

## Testing Seams

- Test normalizers through pure model functions.
- Test persistence through `db.py` helpers and observable SQLite state.
- Test orchestration through `sync.py` with an injected fake Garmin client.
- Keep real Garmin transport and auth outside unit tests.
