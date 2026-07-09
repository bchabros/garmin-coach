# Operations runbook

For agents and humans **operating the system** (Claude Cowork pointed at the DB,
plus scheduled runs). Changing the code instead? See `docs/DEVELOPMENT.md`. Term
definitions live in `docs/glossary.md`; deep design detail for the nightly path is in
`docs/prd/phase-4.md`.

The golden rule still holds here: this layer reads the finished DB. Only `backfill`,
`sync`, and `daily` talk to Garmin; `features` and `report` never do.

## Running the pipeline

```bash
poetry run garmin-coach backfill --from 2026-06-08   # [--to YYYY-MM-DD]  one-off history fill
poetry run garmin-coach sync                          # pull missing data since watermarks
poetry run garmin-coach features                      # recompute marts (daily + weekly + zones)
poetry run garmin-coach report [--to YYYY-MM-DD]      # build digest.json + charts for a date
poetry run garmin-coach daily  [--to YYYY-MM-DD]      # nightly: sync -> features -> alerts
scripts/daily.sh [--to YYYY-MM-DD]                    # thin wrapper for cron / launchd
```

- **First time / gaps:** `backfill --from 2026-06-08`. Idempotent (see below), so safe to
  re-run over an already-filled range.
- **Routine catch-up:** `sync` advances each stream from its watermark; then `features`
  rebuilds the marts.
- **Nightly:** `daily` (or `scripts/daily.sh`) chains sync -> features -> alerts. Alerts
  are the digest's `warn`/`alert` signals, logged; **no charts** on the nightly path.
- Scheduling is documented, not auto-installed: see
  `scripts/com.garmincoach.daily.plist.example` for a launchd template.

## Exit-code contract

`daily` (and `scripts/daily.sh`, which passes it through) exits with:

| Status | Exit | Meaning | What to do |
|--------|------|---------|------------|
| `ok` | 0 | All streams synced, features + alerts ran. | Nothing. |
| `degraded` | 1 | An isolated stream failed but the run continued. | Check the log for the failing stream; often self-heals next run. Re-run `sync` if it persists. |
| `failed` | 2 | A stage crashed or the whole sync was down. | Read the log; treat as a real outage (network, auth, Garmin down, or a 429 -- see below). |

## Logs

In-process `RotatingFileHandler`, configured by `daily` itself (not the shell wrapper).
Config keys (`config.py`, overridable via env / `.env`):

- `LOG_PATH` (default `./logs/daily.log`)
- `LOG_MAX_BYTES` (default `1_000_000`)
- `LOG_BACKUP_COUNT` (default `5`)

`degraded` runs log at WARNING, `failed` at ERROR.

## Gotchas (operational)

- **Login rate limits (429).** Garmin returns 429 (IP-level) on repeated login attempts.
  Once tokens are cached in `~/.garminconnect`, resume avoids the login endpoint -- don't
  hammer it, **wait it out**. A 429 typically surfaces as a `failed` run.
- **Backfill / sync exclude "today".** HRV and sleep only land after the night, so the
  pipeline only pulls through **yesterday**. A missing current-day row is expected, not a
  bug.
- **Re-running is safe (idempotency).** Re-running `backfill`/`sync` must not change
  **core** row counts (upsert by PK). Only `raw_payloads` grows (append-only, keyed by
  `fetched_at`). Re-run freely to recover from a degraded run.
- **Onboarding gap.** Real data starts 2026-06-08 (`data_start`, defined in
  `docs/glossary.md`); earlier dates are explicit gaps, not zero training.

## Generating a coach report

`garmin-coach report [--to YYYY-MM-DD]` builds the deterministic artifacts under
`reports/{date}/`: `digest.json` plus two charts (`hrv_band.png`, `acwr.png`). It does
**not** write the narrative. The coach skill reads the digest and writes `report.md` from
it (never the raw mart, never Garmin) -- see `skills/coach/SKILL.md` for how to invoke the
skill and what the narrative should contain.
