# Operations runbook

For agents and humans **operating the system** (Claude Cowork pointed at the DB,
plus scheduled runs). Changing the code instead? See `docs/DEVELOPMENT.md`. Term
definitions live in `docs/glossary.md`; deep design detail for the nightly path is in
`docs/prd/phase-4.md`.

The golden rule still holds here: this layer reads the finished DB. Only `backfill`,
`sync`, and `daily` talk to Garmin; `features` and `report` never do.

## Directory map (what lives where)

- `reports/{date}/` -- deterministic coach artifacts: `digest.json`, `hrv_band.png`,
  `acwr.png`, and the narrative `report.md`. One folder per report run day.
- `plans/` -- weekly training plans as Markdown (e.g. `2026-07-06_week.md`): the athlete's
  intended week with session detail the DB does not store, plus notes and the plan-vs-actual
  follow-up date. Human record; the pipeline does not read it.
- `memory/` -- long-term athlete context. **Read `memory/athlete-profile.md` at the start of
  a coaching session** (goals, physiology, tendencies, coaching decisions, open threads).
  Qualitative context that does not belong in the DB; numbers there only summarize the DB.
- `docs/` -- system docs (this runbook, `DEVELOPMENT.md`, `PROJECT.md`, `glossary.md`, PRDs,
  ADRs). `skills/coach/` -- the coach skill (narrative layer over the digest).
- `data/garmin.db` -- SQLite system-of-record. `logs/` -- nightly run logs.

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

## Cowork agent notes

For Claude running in Cowork (pointed at this folder, commands via the Linux sandbox):

- **Read context first.** Open `memory/athlete-profile.md`, then the latest `plans/` file,
  before advising. That is the long-term memory that survives between sessions.
- **`poetry` may be missing in the sandbox.** It needs Python 3.13; the sandbox ships 3.10,
  so `poetry run ...` fails. To generate a report without poetry, install the runtime deps
  once and invoke the CLI module directly:

  ```bash
  pip install matplotlib pydantic pydantic-settings python-dotenv garminconnect curl-cffi --break-system-packages
  PYTHONPATH=src python3 -m garmin_coach.cli report      # or: features, if the mart is empty
  ```

  Only run **read-side** commands (`report`, `features`) this way. **Never run
  `sync`/`backfill`/`daily` from the sandbox** -- those call Garmin live, and the operator's
  Mac already runs the nightly job (golden rule).
- **Same-day data is not in the DB yet** (backfill excludes "today"). To read a just-finished
  workout or today's calories/readiness/status, use the `mcp__garmin__*` tools for **ad-hoc
  exploration only** -- never wire them into the pipeline or the coach layer.
- **Athlete-facing output is Polish and number-dense.** Prefer the report artifacts plus the
  `plans/` and `memory/` Markdown; the athlete likes charts. Keep the repo's no-emoji rule.
- **Weekly follow-up.** After a full week closes, the plan-vs-actual for that week is only
  reliable once the Sunday sync lands (Monday morning) -- `weekly` in the digest / the
  `weekly_plan_actual` mart.
