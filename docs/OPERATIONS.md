# Operations runbook

For agents and humans **operating the system** (Claude Cowork pointed at the DB,
plus scheduled runs). Changing the code instead? See `docs/DEVELOPMENT.md`. Term
definitions live in `docs/glossary.md`; deep design detail for the nightly path is in
`docs/prd/phase-4.md`.

The golden rule still holds here: this layer reads the finished DB. Only `backfill`,
`sync`, `refresh-today`, and `daily` talk to Garmin (plus the out-of-seam `push`);
`features` and `report` never do.

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
poetry run garmin-coach refresh-today                 # opt-in same-day pull + features (partial!)
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
  bug. To see *this morning's* HRV/readiness for a same-day call, opt in explicitly with
  `refresh-today`: it pulls today (partial), rebuilds the mart through today, and **never
  advances sync watermarks**, so the nightly run re-pulls the day complete. Morning
  streams (sleep, HRV, readiness) are reliable after wake; intraday fields (today's
  load/TE, steps, RHR) are partial until the nightly run - never read them as final.
  Same exit-code contract as `daily` (0 ok / 1 degraded / 2 failed).
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

## Authoring and pushing a workout (Phase 11)

Turning a recommendation (or your own session) into a structured Garmin workout is a
**two-command, out-of-seam** path -- the only place the system writes to Garmin. It is
never run from the nightly automation, and it bends the golden rule deliberately (see
`docs/adr/0013-phase-11-workout-authoring-and-push.md`).

1. **Author** (pure, no network) -- writes `reports/{date}/workout.json`:

   ```bash
   garmin-coach author --date 2026-07-17 --from-recommendation   # from the Phase 10 read
   garmin-coach author --date 2026-07-17 --request req.json       # from your own workout_request
   ```

   `rest` produces no spec; `hiit`/`strength` requests are understood but deferred to the
   push spike; a `hyrox` recommendation asks you to say run-vs-station. Warnings (target is
   today, no measured pace, an override of the recommender's advice) are printed and written
   into the spec.

2. **Push** -- **dry-run unless `--confirm`.** No `--confirm` prints the payload and exits
   without touching the account; there is no `--dry-run` flag to forget:

   ```bash
   garmin-coach push --date 2026-07-17            # dry-run: shows payload + plan + warnings
   garmin-coach push --date 2026-07-17 --confirm  # writes: upload + schedule, writes push.json
   garmin-coach push --date 2026-07-17 --confirm --replace   # overwrite a changed same-name workout
   ```

   Idempotency uses the **account** as the source of truth (workouts are named `GC {date}
   {type}` and carry a `gc-hash` of the spec): an identical re-push is a no-op, a changed one
   needs `--replace`, and a push that half-fails (uploaded, not scheduled) is completed by
   running `push` again -- it skips the upload. Exit codes: `0` success/dry-run/no-op, `1`
   refused (needs `--replace`) or missing spec, `2` a partial push (see the `error` in
   `push.json`).

**Custom run structure (Phase 11a).** An `athlete`/hybrid request may carry a `structure`
block that shapes the run template (`warmup + reps x (work + recovery) + cooldown`, one
homogeneous interval block) beyond its defaults. Keys:

- `reps` - interval count.
- `<role>_end` for each role (`warmup`/`work`/`recovery`/`cooldown`) - one of `"lap"` (the
  watch lap button, "on-click"), `{"distance_m": N}` (metres), or `{"min": N}` (minutes). A
  `work` step may not be `"lap"`. The pre-11a `<role>_min` / `duration_min` keys still work;
  giving both a `*_end` and its `*_min` for one role is an error.
- `work_pace_band: [fast_s_per_km, slow_s_per_km]` (faster bound first) - a custom pace
  window on the work step. It overrides the recommender's `pace_target_s_per_km` and skips
  the pace -> HR -> none degradation. A band clearly faster than the recommender's
  suggestion adds a (non-blocking) cited warning.

"Tempo Thursday: warm-up on-click, 8x(1km at 3:40-4:00, 2:00 jog), cool-down on-click"
becomes (canonical fixture: `tests/fixtures/tempo_request.json`):

```json
{
  "sport": "run", "origin": "athlete", "date": "2026-07-23",
  "session_type": "quality", "pace_target_s_per_km": null,
  "structure": {
    "reps": 8,
    "warmup_end": "lap",
    "work_end": {"distance_m": 1000},
    "work_pace_band": [220, 240],
    "recovery_end": {"min": 2},
    "cooldown_end": "lap"
  }
}
```

The estimated duration is approximate for distance/lap ends (a distance step with a band is
estimated from its midpoint; lap steps count as 0) - Garmin recomputes it on the device.

**Manual live-push acceptance (run once).** The live transport wrapper is validated by a
single confirmed push, not by CI:

1. `garmin-coach author --date <a near future date> --from-recommendation`
2. `garmin-coach push --date <that date>` -- confirm the dry-run payload looks right.
3. `garmin-coach push --date <that date> --confirm` -- then check Garmin Connect / the watch
   shows exactly one scheduled run workout named `GC ...`.
4. Re-run step 3 -- it must report `noop` and create no duplicate.

If the response-field extraction in `GarminWorkoutPublisher` (workout id, schedule id,
scheduled-list shape) does not match what the live account returns, fix it here -- that
mapping is deliberately settled by this step, not guessed in a unit test.

Passed on 2026-07-17 (a `quality` push): create scheduled exactly one `GC 2026-07-17
quality` (warmup + 4x interval repeat + cooldown, Z4 HR band), the re-push reported
`noop` with no duplicate, and all four response mappings (`get_workouts`,
`upload_workout`, `schedule_workout`, `get_scheduled_workouts`) matched unchanged.

## The coach MCP server (mcp__coach__*)

One local stdio server, registered in the repo's versioned `.mcp.json` — any Claude
Code / Cowork session pointed at this folder gets the tools automatically. It is a
thin layer over the same functions the CLI uses (see ADR 0014); the exploratory
`mcp__garmin__*` server stays separate and ad-hoc-only.

- **Read tools** (local DB, no Garmin): `get_snapshot`, `get_digest`,
  `get_recent_activities(n)`, `get_weekly(week_start)`, `get_zones`,
  `get_recommendation(date)`, `get_events`, `get_workout_status(date)`.
- **Local writes** (transport-free): `log_rpe(activity_id, rpe, ...)`,
  `log_niggle(body_part, severity, ...)` — same validation as `log-rpe` in the CLI.
- **`refresh_today`** — the MCP form of `refresh-today` (see above): pulls today
  partial, rebuilds the mart, never advances watermarks. Call it at most once per
  coach read; it shares the login rate-limit exposure (429) of any transport call.
- **Workout push** — `author_workout(date, request?)` writes `workout.json`;
  `push_preview(date)` returns the resolved action, the Garmin payload, and a
  `spec_hash`; `push_confirm(date, spec_hash, replace?)` writes to the account and
  **refuses any hash other than the previewed one**. Show the preview to the athlete
  before confirming — the handshake exists so an agent cannot push what it has not
  displayed.

**Reading the freshness envelope.** Every response carries
`{data_through, today_included, partial_fields}`. If `today_included` is true, any
field listed in `partial_fields` (load, ACWR, zone minutes, RHR, stress, body
battery) is an intraday running value — quote it as "so far today", never as final.
Sleep, HRV, and readiness are morning-complete and safe to read all day. In
`get_recent_activities`, an activity dated today additionally carries
`partial_today: true` — its training-effect numbers may still settle, so treat them
as provisional too.

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
