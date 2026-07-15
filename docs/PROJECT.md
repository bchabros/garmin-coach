# garmin-coach — build brief and roadmap

Single source of truth for the whole project: what was built (Phases 0–5, the
original executable brief) and what comes next (Phases 6+, the forward plan and
industry survey). This file merges the former `garmin-coach-BUILD.md` and
`ROADMAP.md`.

**What it is.** A local system that pulls Garmin Connect data once a day, keeps it in
SQLite as the *system-of-record*, computes training metrics (HRV baseline, ACWR, load
balance), and lets a coach agent generate a weekly review: is training going well, and
what is missing.

**Golden rule — separate transport from intelligence.** The deterministic ETL fetches
through the [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
library. The metrics/coach/recommender layers only ever read the finished DB — they
never call Garmin live. The `mcp__garmin__*` tools are for ad-hoc exploration and test
fixtures only, never the pipeline.

**House rules** (every phase): the skill chain in
[DEVELOPMENT.md](DEVELOPMENT.md) (`/grill-with-docs -> /to-spec -> /to-tickets ->
/implement -> /code-review`), medallion data (raw -> core -> mart), tests at agreed
seams, Google-style docstrings, Poetry, Python 3.13.

---

## Status at a glance

Click a phase to jump to its section. Phases 0–5 are the built foundation (Part I);
6+ is the forward plan (Part II).

| Phase | Delivers | Status | Details |
|---|---|---|---|
| 0 | Raw capture + idempotent backfill | Done | [section](#phase-0-raw-capture-and-idempotency) · [PRD](prd/phase-0.md) |
| 1 | Incremental sync + resilience (watermark, retry, per-day fallback) | Done | [section](#phase-1-incremental-sync-and-resilience) · [PRD](prd/phase-1.md) |
| 2 | Metrics mart (`features.py` -> `daily_metrics`) | Done | [section](#phase-2-metrics-mart) · [PRD](prd/phase-2.md) |
| 3 | Coach skill (digest + charts + `report.md`) | Done | [section](#phase-3-coach-skill) · [PRD](prd/phase-3.md) |
| 4 | Automation (nightly orchestrator, alerts, launchd/cron) | Done | [section](#phase-4-automation) · [PRD](prd/phase-4.md) |
| 5 | Weekly rollups, plan-vs-actual, deload detection | Done | [section](#phase-5-closing-the-loop) · [PRD](prd/phase-5.md) |
| 6 | Personal training zones (`athlete_zones` mart) | Done | [section](#phase-6-personal-training-zones) · [PRD](prd/phase-6.md) |
| 6b | Athlete snapshot (`athlete_status` mart + `snapshot`) | Done | [section](#phase-6b-athlete-snapshot) · [PRD](prd/phase-6b/PRD.md) |
| 7 | Session-RPE load model for strength/Hyrox + niggle log | Done | [section](#phase-7-strength-and-hyrox-load-model) · [PRD](prd/phase-7/PRD.md) |
| 8 | Per-set capture + movement-pattern overlap | Done | [section](#phase-8-per-set-capture-and-overlap) · [PRD](prd/phase-8-movement-overlap/PRD.md) |
| 9 | Race-date periodization (`goal_event` + `plan_block` marts) | Done | [section](#phase-9-race-date-periodization) · [PRD](prd/phase-9-periodization/PRD.md) |
| 10 | Prospective session recommender (re-planning-aware) | Done | [section](#phase-10-prospective-recommender) · [PRD](prd/phase-10-recommender/PRD.md) |
| 11 | Structured workout authoring + push to Garmin (run first) | Done | [section](#phase-11-workout-authoring-and-push) · [PRD](prd/phase-11-workout-push/PRD.md) |
| read-MCP | Coach MCP server (reads + same-day refresh + workout push) | Done | [section](#read-mcp-the-coach-mcp-server) · [epic #18](https://github.com/bchabros/garmin-coach/issues/18) |

The roadmap ends here: with the coach MCP server shipped, new work is tracked as
GitHub issues (see `docs/agents/issue-tracker.md`), titled by the capability gap
they close — e.g. [#16](https://github.com/bchabros/garmin-coach/issues/16)
(strength/HIIT authoring + push).

Phase 9b (race-day pacing, `race_plan`) has moved out of this roadmap to GitHub issue
[#13](https://github.com/bchabros/garmin-coach/issues/13) — see the
[stub section](#phase-9b-race-day-pacing) for why.

Reference sections: [database schema](#brief-4-database-schema-raw-core-mart) ·
[metric definitions](#brief-6-metric-definitions-featurespy) · [gotchas](#brief-10-gotchas) ·
[ordering and dependencies](#ordering-and-dependencies) ·
[industry survey](#what-the-industry-survey-established) ·
[non-goals](#explicit-non-goals) · [source index](#source-index).

---

# Part I — Foundation (Phases 0–5, built)

> The original executable brief. Read it whole, then build in phases (0 -> 5); each
> phase had a Definition of Done and you did not advance until it was met. Phases 0–5
> are complete; the per-phase `STATUS` notes below are the historical record. Ongoing
> development lives in Part II.

## Brief §0. Goal and golden rule

Build a local system that pulls Garmin Connect data once a day, keeps it in a database
as the *system-of-record*, computes training metrics (HRV baseline, ACWR, load
distribution), and lets a coach agent generate a weekly report: *is training going well
and what is missing*.

**Golden rule — separate transport from intelligence:**

- **Deterministic ETL** (fetch + normalize) goes through the
  [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library, run on
  a schedule. **Not** through MCP — Garmin's MCP is flaky (RHR timeouts, >1 MB payloads)
  and is only fit for ad-hoc agent exploration, not the pipeline.
- **Metrics + coach layer** reads the finished DB, never hits Garmin live.

This is **one repo** with **two work surfaces**, not two projects:

```
                       +-----------------------------------------+
   Garmin Connect ---> |  garmin-coach (one repo)                |
   (garminconnect)     |                                         |
                       |  sync.py     -> raw/ + SQLite (system-of-record)
                       |  features.py -> daily_metrics (mart)    |
                       |  skills/coach/SKILL.md                   |
                       +---------------+--------------+-----------+
                                       |              |
                        Claude Code <--+              +--> Claude Cowork
                     (builds/maintains repo,      (points at the same repo/DB,
                      migrations, backfills)       runs SKILL.md -> report+charts)
```

Cowork does **not** get its own copy of the logic — it reuses `features.py` and reads
the same DB. Two repos would duplicate and drift the metric logic.

## Brief §1. Technology decisions

| Area | Choice | Rationale / when to change |
|---|---|---|
| Fetching | `garminconnect` + `curl_cffi` | official wrapper, token auto-refresh, MFA |
| Environment manager | `uv` (or `pdm`) | fast, lockfile; `uv run ...` for tasks |
| System-of-record | **SQLite** | transactional, in repo/backup, Claude Code reads it natively |
| Analytical layer | **DuckDB — only when it hurts** | window functions over the same file; do not add up front |
| Raw data | JSON in `raw/{date}/{endpoint}.json` **and/or** a `raw_payloads` table | reprocessing without re-hitting Garmin |
| Charts | `matplotlib` | same as the reference analysis |
| Config/secrets | `.env` (pydantic-settings) + tokens in `~/.garminconnect` | see §8 |

Starting dependencies: `garminconnect`, `curl_cffi`, `pandas`, `numpy`, `matplotlib`,
`pydantic`, `pydantic-settings`, `python-dotenv`; dev: `pytest`, `pytest-recording`
(VCR), `ruff`, `mypy`.

> Deviations that actually shipped (see the per-phase status notes): the repo
> standardized on **Poetry** (not `uv`) and stores raw data in the `raw_payloads` table
> (not `raw/` files). The current, accurate command reference is the top-level
> `README.md` and [OPERATIONS.md](OPERATIONS.md).

## Brief §2. Repository layout

```
garmin-coach/
├── pyproject.toml
├── .env.example                 # EMAIL, PASSWORD (optional), DATA_START_DATE, DB_PATH
├── .gitignore                   # .env, raw/, *.db, ~/.garminconnect NOT committed
├── README.md
├── data/
│   └── garmin.db                # SQLite (gitignored; backed up separately)
├── raw/                         # raw per-day/endpoint JSON (gitignored)
├── src/garmin_coach/
│   ├── __init__.py
│   ├── config.py                # pydantic-settings: env + paths
│   ├── client.py                # login, retry/backoff, garminconnect wrapper
│   ├── db.py                    # connection, migrations, upsert helpers
│   ├── schema.sql               # DDL (§4)
│   ├── sync.py                  # incremental ETL (§5)
│   ├── models.py                # dataclasses/pydantic: Activity, DailyWellness, Sleep, HrvNight
│   ├── features.py              # metrics -> daily_metrics (§6)
│   ├── report.py                # charts + textual weekly report
│   └── cli.py                   # `garmin-coach sync|backfill|features|report`
├── skills/
│   └── coach/
│       └── SKILL.md             # skill for Cowork (§7)
├── tests/
│   ├── cassettes/               # VCR
│   ├── test_sync.py
│   └── test_features.py
└── scripts/
    └── daily.sh                 # cron/launchd entrypoint: sync -> features
```

> The as-built layout has grown (weekly/zones/thresholds/digest/signals/charts/daily
> modules, `skills/coach`, richer tests). The current tree is documented in `README.md`.

## Brief §3. The build phases (0–5)

Each phase works standalone. The `STATUS` note under each phase is the historical
record of what actually shipped.

### Phase 0: raw capture and idempotency
- `client.py`: login with token persistence, `example.py`-style. Handle the MFA callback.
- `sync.py`: for a given date range, fetch and **store raw JSON** to `raw/`, then
  normalize and **upsert** into `activities` / `daily_wellness` / `sleep` / `hrv_nightly`.
- One-off backfill from `DATA_START_DATE` (this user has real data from **2026-06-08**;
  earlier is onboarding/empty — record as an explicit "gap", not a NULL fog).
- **DoD:** `garmin-coach backfill --from 2026-06-08` fills the DB; a re-run creates no
  duplicates (upsert by key); raw JSON sits in `raw/`.

- **STATUS: DONE (2026-07-04).** Delivered test-first (22 tests, `ruff`+`mypy` clean).
  DoD confirmed against the live DB: 26 days (2026-06-08 -> 07-03, "today" excluded), 15
  activities, second run leaves core unchanged (`raw_payloads` grows append-only).
  **Deviations from the brief** (deliberate — see `prd/phase-0.md`): raw data in the
  `raw_payloads` table (not `raw/` files), **Poetry** manager (not `uv`), backfill pulls
  **6 streams** (added `training_readiness` + `training_status_daily`). **Deferred:**
  `activity_sets`.

### Phase 1: incremental sync and resilience
- `sync_state` table (per-stream watermark). Sync pulls only `[watermark+1 .. yesterday]`.
- Retry with exponential backoff; **fall back from range to per-day** for endpoints that
  time out on the whole range (exactly the problem seen with sleep and RHR in MCP).
- An endpoint that fails after retries logs a warning and does **not** block the other
  streams.
- **DoD:** `garmin-coach sync` run twice in a row is idempotent; one stream failing does
  not sink the whole run; the watermark advances.

- **STATUS: DONE.** Per-stream watermark in `sync_state`, retry with backoff, per-day
  fallback, stream isolation. Decisions: `prd/phase-1.md` +
  `adr/0001-phase-1-incremental-sync.md`; seam tests in `tests/test_sync.py`.

### Phase 2: metrics mart
- `features.py` computes and materializes into `daily_metrics` (definitions in §6): HRV
  baseline (rolling median) + SD + a `< baseline − 1·SD` flag; ACWR (acute7/chronic28) +
  `n_chronic` (how many days are really in the window); load-balance buckets by TE;
  minutes in HR zones from `hr_z1..z5`.
- **DoD:** `garmin-coach features` reproduces the reference analysis for 06-09 -> 07-04
  (baseline ≈ 68 ms, SD ≈ 11 ms, threshold ≈ 57 ms; ACWR on 07-03 ≈ 1.0, Garmin ref 1.1).

- **STATUS: DONE.** `features.py` materializes the `daily_metrics` mart; golden
  regression in `tests/test_features.py`. Decisions: `prd/phase-2.md` +
  `adr/0002-phase-2-metrics-semantics.md`.

### Phase 3: coach skill
- `skills/coach/SKILL.md` (skill convention — your area): encapsulates the **rules** (§7),
  reads `daily_metrics`, returns a report + charts. The agent does not "think from scratch".
- **DoD:** in Cowork, "review my last week" produces a textual report + 2 charts (HRV with
  a ±1 SD band, ACWR over time) and a list of concrete signals.

- **STATUS: DONE.** A deterministic engine (`digest.py`/`signals.py` ->
  `garmin-coach report`) builds `reports/{date}/digest.json` + 2 charts; the skill writes
  `report.md` solely from the digest (never from the raw mart, never from Garmin). Signals
  1–5 from §7; rule 6 (plan vs actual) moved to Phase 5. Decisions: `prd/phase-3.md` +
  `adr/0003-phase-3-coach-signals.md`; golden regression in `tests/test_digest.py`.

### Phase 4: automation
- `scripts/daily.sh` (sync -> features) under cron/launchd. Weekly review with Cowork.
- Alerts (rule thresholds): morning HRV < threshold -> "downgrade the quality session";
  ACWR > 1.3 -> "consider a deload"; `AEROBIC_LOW_SHORTAGE` -> "add Z2".
- **DoD:** the nightly run works without interaction; the log is rotated; an error means a
  non-zero exit + a log entry.

- **STATUS: DONE.** Seam `daily.run_daily(client, conn, ...) -> DailyResult` (sync ->
  features -> digest, no charts on the nightly path); alerts = warn/alert signals from the
  digest; exit contract: `ok`/0, `degraded`/1, `failed`/2. `garmin-coach daily` + a thin
  `scripts/daily.sh` + an example launchd plist; logging via `RotatingFileHandler`.
  Decisions: `prd/phase-4.md` + `adr/0004-phase-4-automation.md`; tests in
  `tests/test_daily.py`.

### Phase 5: closing the loop
- Plan-vs-actual (the user's week template against actual logs), deload detection,
  VO2max/threshold trends, multi-sport when the ski-touring season returns (`discipline`
  is already in the schema).
- **DoD:** the report shows the "plan vs actual" divergence and detects "two hard days in
  a row".

- **STATUS: DONE.** `weekly.py` -> `weekly_metrics` mart (complete Mon–Sun weeks only);
  plan-vs-actual (`plan_adherence` vs `plan_template`), Foster `monotony`/`strain`,
  `max_consec_hard`, a new `DELOAD_ADVISED` signal; the digest gained a `weekly` section
  and the report a "Week: plan vs actual". Decisions: `prd/phase-5.md` +
  `adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md`; tests in `tests/test_weekly.py`
  + `tests/test_signals.py`. VO2max/threshold trends, multi-sport weighting and PDF/Notion
  export deferred -> Part II.

## Brief §4. Database schema (raw, core, mart)

`schema.sql` — create idempotently (`CREATE TABLE IF NOT EXISTS`). Keys and upserts are
critical for idempotency.

```sql
-- RAW (append-only, never overwrite) --------------------------------------
CREATE TABLE IF NOT EXISTS raw_payloads (
  fetched_at  TEXT NOT NULL,          -- ISO fetch timestamp
  endpoint    TEXT NOT NULL,          -- e.g. 'get_sleep_data'
  ref_date    TEXT NOT NULL,          -- the date it refers to
  payload     TEXT NOT NULL,          -- raw JSON
  PRIMARY KEY (endpoint, ref_date, fetched_at)
);

-- CORE (normalized, upsert) -----------------------------------------------
CREATE TABLE IF NOT EXISTS activities (
  activity_id   INTEGER PRIMARY KEY,  -- dedup by activityId
  start_local   TEXT NOT NULL,
  gtype         TEXT NOT NULL,        -- running | hiit | strength_training | ...
  discipline    TEXT,                 -- Bieganie | Hyrox/HIIT | Siła | Skitury | Trail
  dur_s         REAL,
  distance_m    REAL,
  aero_te       REAL,
  anaero_te     REAL,
  training_load REAL,                 -- activityTrainingLoad
  te_label      TEXT,                 -- AEROBIC_BASE | TEMPO | LACTATE_THRESHOLD | VO2MAX
  avg_hr        INTEGER,
  max_hr        INTEGER,
  hr_z1_s REAL, hr_z2_s REAL, hr_z3_s REAL, hr_z4_s REAL, hr_z5_s REAL,
  name          TEXT
);

CREATE TABLE IF NOT EXISTS daily_wellness (
  date          TEXT PRIMARY KEY,
  rhr           INTEGER,
  acute_load    REAL,                 -- Garmin acuteLoad (latest ts/day)
  chronic_load  REAL,                 -- from training_status, if available
  garmin_acwr   REAL,                 -- Garmin's reference ACWR
  bb_min INTEGER, bb_max INTEGER,     -- body battery
  steps         INTEGER,
  training_status TEXT,               -- PRODUCTIVE | MAINTAINING | ...
  has_data      INTEGER DEFAULT 1     -- 0 = explicit gap (onboarding/not worn)
);

CREATE TABLE IF NOT EXISTS sleep (
  date        TEXT PRIMARY KEY,
  total_s     INTEGER, deep_s INTEGER, rem_s INTEGER, light_s INTEGER, awake_s INTEGER,
  score       INTEGER,
  resting_hr  INTEGER
);

CREATE TABLE IF NOT EXISTS hrv_nightly (
  date        TEXT PRIMARY KEY,
  avg_hrv     INTEGER,                -- lastNightAvg
  weekly_avg  INTEGER,
  status      TEXT                    -- BALANCED | LOW | NONE(onboarding) ...
);

CREATE TABLE IF NOT EXISTS sync_state (
  stream          TEXT PRIMARY KEY,   -- 'activities' | 'sleep' | 'hrv' | 'wellness'
  last_synced_date TEXT NOT NULL
);

-- MART (computed; overwritable on each `features`) ------------------------
CREATE TABLE IF NOT EXISTS daily_metrics (
  date            TEXT PRIMARY KEY,
  hrv             INTEGER,
  hrv_baseline    REAL,               -- rolling median
  hrv_sd          REAL,
  hrv_low_flag    INTEGER,            -- 1 if hrv < baseline - 1*SD
  load_day        REAL,               -- sum of the day's activity training_load
  acwr            REAL,               -- acute7/chronic28 (own)
  n_chronic       INTEGER,            -- days really in the chronic window (reliability!)
  load_low        REAL,               -- load-balance buckets
  load_high       REAL,
  load_anaerobic  REAL,
  z1_min REAL, z2_min REAL, z3_min REAL, z4_min REAL, z5_min REAL,
  sleep_score     INTEGER,
  rhr             INTEGER
);
```

Upsert in SQLite: `INSERT ... ON CONFLICT(<pk>) DO UPDATE SET ...`. Store days with no
data as `has_data=0`, do not skip them — otherwise gaps are indistinguishable from
"not fetched yet".

> This is the original DDL. The authoritative, current schema is the packaged
> `src/garmin_coach/schema.sql` (mirrored to `docs/schema.sql`, guarded by a test).

## Brief §5. ETL implementation rules (`sync.py`)

- **Incremental:** read `sync_state`, pull `[last_synced_date+1 .. yesterday]`. Skip
  "today" — data is incomplete (HRV/sleep land after the night).
- **Idempotent:** upsert activities by `activity_id`; days by `date`. Two runs = the same
  state.
- **Retry/backoff:** wrap each API call in retry (e.g. 3 attempts, backoff 2^n s). On an
  exception after the last attempt: log WARN, write `has_data=0`/skip the day, **continue**
  the other streams.
- **Per-day fallback:** endpoints with range methods (sleep, HRV) can time out on a wide
  window — if the range fails, drop to a per-day loop. (A real problem on this account:
  sleep >1 MB over a range, RHR timed out.)
- **Raw first:** write raw JSON to `raw_payloads`/`raw/` first, then normalize. When
  normalization changes, reprocess from raw without touching Garmin.
- **Onboarding/NULL:** all-`null` records (like May–early June) -> `has_data=0`.
- **Discipline:** map `gtype`: `running`->`Bieganie` (unless name/elevation suggest trail
  -> `Trail`), `hiit`->`Hyrox/HIIT`, `strength_training`->`Siła`,
  `ski_touring`/`backcountry`->`Skitury`. Keep the mapping in one place (`models.py`),
  easy to extend.

## Brief §6. Metric definitions (`features.py`)

> These definitions reproduce the analysis done by hand in the reference thread. Stick to
> them so results stay comparable.

**HRV baseline / flag**
- `hrv_baseline` = rolling median of `avg_hrv` (configurable window; default the whole
  available window, or 60 nights once history accrues). `hrv_sd` = std dev (ddof=1) over
  the same window.
- `hrv_low_flag = 1` when `avg_hrv < hrv_baseline − 1·hrv_sd`.
- Ref: 06-09 -> 07-04 -> baseline ≈ 68 ms, SD ≈ 11 ms, threshold ≈ 57 ms; flagged 06-11,
  06-18, 06-19, 06-27, 07-04.

**ACWR (own)**
- `load_day` = sum of the day's activity `training_load` (0 if none).
- `acute7` = daily mean of `load_day` over 7 days; `chronic28` = daily mean over 28 days.
- `acwr = acute7 / chronic28`. Risk flag > 1.5, detraining < 0.8, OK band 0.8–1.3.
- **`n_chronic`** = the number of days really in the chronic window. **Critical:** while
  `n_chronic < 28`, ACWR is inflated/indicative — the report MUST mark this (a warning on
  the chart and in the text). When available, add `garmin_acwr` from `daily_wellness` as a
  reference point.

**Load-balance buckets (Garmin's logic — by TE, not by HR zones)**
- `load_anaerobic` += `training_load` when `anaero_te ≥ 1.0`.
- otherwise: `load_low` when `aero_te < 2.5`, else `load_high`.
- Take monthly targets from `get_training_status` (`monthlyLoad*Target*`) and compare —
  that is the source of the `AEROBIC_LOW_SHORTAGE` message.

**HR zones (separate from the buckets!)**
- `z1..z5_min` = sum of `hr_z1..z5_s`/60 for the day's activities. These are minutes in
  heart-rate zones — a different "language" than the load-balance buckets. The report
  shows both side by side, because they answer different questions (time distribution vs
  stimulus distribution).

## Brief §7. Coach skill (`skills/coach/SKILL.md`)

**Input:** `daily_metrics` (+ `activities`, `daily_wellness`) for a given range (default
the last 7/28 days). **Output:** a concise textual report + 2 charts (HRV with a ±1 SD
band, ACWR over time), written to `reports/{date}/`.

Rules to encode (thresholds configurable, defaults from this user):

1. **Intensity distribution** — if `load_low` is below target and `load_high` above ->
   signal "too much grey zone, add Z2" (`AEROBIC_LOW_SHORTAGE`).
2. **ACWR** — flag outside 0.8–1.3; but when `n_chronic < 28`, add "indicative value".
3. **HRV** — nights with `hrv_low_flag=1`; if the morning is < threshold -> recommend
   "downgrade today's quality session to easy".
4. **Two hard days in a row** — detect consecutive days with high `load_day`/`anaero_te`
   without a buffer (for this user the risk is a Friday->Saturday stack).
5. **Sleep** — correlate `hrv` with `sleep_score` (in the reference data r ≈ 0.57); the
   worst HRV is sometimes sleep-driven, not training — do not confuse causes.
6. **Plan vs actual** (Phase 5) — the user's week template: Mon rest · Tue FBB+Hyrox ·
   Wed easy/long run · Thu rest · Fri Crossfit+Hyrox · Sat Hyrox/tempo · Sun sometimes
   easy/long. Show the divergence.

Report tone: concrete, numbers, no fluff; a disclaimer that this is a reading of data,
not medical/coaching advice.

## Brief §8. Secrets, auth, privacy

- **Auth:** `garminconnect` uses mobile SSO. The first login writes tokens to
  `~/.garminconnect/garmin_tokens.json` (mode 0600), then auto-refreshes — a full re-login
  only when the refresh token expires/is revoked. MFA: pass a `prompt_mfa` callback.
- **Password:** best **not** to keep it in `.env` — rely on the stored tokens after the
  first login. If you must, use `.env` (gitignored), never in the repo.
- **.gitignore:** `.env`, `raw/`, `*.db`, `reports/`, any tokens. Only code, `schema.sql`,
  `SKILL.md`, `.env.example` go into the repo.
- **Health data is sensitive** — keep the DB and backups local/private.

## Brief §9. Tests

- `pytest` + VCR (`pytest-recording`): record Garmin response cassettes once, then tests
  replay from `tests/cassettes/` without the network. **Anonymize** cassettes (strip
  tokens, email, `userProfilePK`).
- `test_features.py`: on the 06-09 -> 07-04 fixture assert baseline≈68, SD≈11,
  threshold≈57, ACWR(07-03)≈1.0, correct HRV flags and load buckets. This is the golden
  regression sample.
- `test_sync.py`: upsert idempotency; per-day fallback after a simulated range timeout;
  `has_data=0` for an all-null payload.

## Brief §10. Gotchas

- **Method names:** the library has 130+ methods. **Do not guess** — read the real
  signatures from `demo.py` and the package sources (`python -c "import garminconnect,
  inspect; print([m for m in dir(garminconnect.Garmin) if not m.startswith('_')])"`). Map
  the ones you need: stats/summary, heart rates/RHR, sleep, HRV, training status, training
  readiness, activities-by-date, body battery.
- **RHR is flaky** — keep a per-day fallback; RHR is also in the sleep payload
  (`restingHeartRate`), so you can pull it from there instead of a separate call.
- **Sleep/HRV ranges** can exceed limits — see the per-day fallback.
- **Onboarding:** this account has real data from **2026-06-08**; do not interpret earlier
  zeros/nulls as "zero activity".
- **ACWR without a full chronic (28 days)** is unreliable — always report `n_chronic`.
- **Versions:** `garminconnect` v0.3.4 (May 2026) at the time of writing; if the API/methods
  changed, trust `demo.py` from the installed version, not this document.

## Brief §11. First run (command order)

```bash
# environment
uv venv && source .venv/bin/activate
uv pip install --upgrade garminconnect curl_cffi pandas numpy matplotlib \
    pydantic pydantic-settings python-dotenv
uv pip install --group dev pytest pytest-recording ruff mypy   # or in pyproject

# first login (writes tokens to ~/.garminconnect) — handle MFA if enabled
python -c "from garmin_coach.client import login; login()"

# backfill + metrics + report
garmin-coach backfill --from 2026-06-08
garmin-coach features
garmin-coach report --last 28

# then: the nightly run
scripts/daily.sh   # sync -> features ; hook into cron/launchd
```

> The as-built commands use Poetry — see `README.md` and [OPERATIONS.md](OPERATIONS.md)
> for the current, accurate invocations.

## Brief §12. Backlog (now folded into Part II)

The original post-Phase-5 backlog, superseded by the forward plan below:

- DuckDB as an analytical layer over `garmin.db` when window SQL gets heavy.
- Long-term trends: VO2max, lactate threshold, race predictions (in the API).
- Multi-sport and the ski-touring season (`discipline=Skitury`) — schema already ready.
- Report export to PDF/Notion (the user already lives in Notion).
- Versioning `daily_metrics` when metric definitions change (a `features_version` column).

---

# Part II — Roadmap (Phases 6+)

Forward plan for everything after Phase 5. It merges two inputs:

1. **Live-session evidence** — in a real coaching session the deterministic engine kept
   coming up short and advice had to be improvised; each phase cites the concrete gap
   from the current DB.
2. **Industry survey (2026-07-07)** — a primary-source review (vendor docs, help
   centers, first-party API docs; source index at the bottom) of TrainerRoad,
   TrainAsONE, Athletica, Humango, AI Endurance, Runna, WHOOP, Garmin's native
   features, JOIN, enduco, HRV4Training, Intervals.icu, Xert, Stryd, and Hyrox apps
   (ROXFIT, Ladder). It validated most of the original plan and exposed gaps now
   formalized below (periodization, re-planning, zone staleness, niggles, run/strength
   push split).

The **golden rule** holds throughout — the metrics/coach/recommender layers read the
finished DB only and never call Garmin live. The one new *outbound* transport path
(Phase 11) is deliberately isolated and out-of-seam, like `client.py`.

## What the industry survey established

- **Every serious product is built around a race date.** Periodized plan -> adaptation ->
  taper toward an event is baseline everywhere (TrainerRoad Plan Builder's
  Base/Build/Specialty with automatic tapers around A/B/C events; Stryd, Athletica,
  Runna, Garmin Coach, TrainAsONE). It was the roadmap's single biggest hole — now
  **Phase 9**.
- **The universal adaptation loop is: session done -> subjective feedback -> plan
  adjusts.** TrainerRoad's post-workout survey feeds its Progression Levels; JOIN asks
  RPE + a soreness/readiness question; Athletica and Humango rate every session. Phase
  7's sRPE plan is validated — but industry uses RPE as an **adaptation trigger**, not
  just a load number, so Phase 10 consumes it.
- **Multi-week re-planning after missed sessions is a first-class feature** (Runna
  "Plan Realignment": extend / rebuild-to-race-date / skip; TrainAsONE rebuilds the
  whole plan after every run; Humango rebuilds remaining weeks). Folded into Phase 10.
- **Threshold auto-detection has a cadence everywhere**: TrainerRoad AI FTP is
  deliberately limited to every 28 days; Stryd auto-Critical-Power runs continuously;
  Xert re-estimates on any "breakthrough" effort. Phase 6 gains a re-detection cadence
  and a staleness flag.
- **Environment adjustment is common**: TrainAsONE adjusts paces for forecast
  temperature/terrain and discounts heat-inflated HR from its fitness model; Garmin
  natively computes heat (>22 °C) / altitude (>800 m) acclimation the ETL could ingest.
  Matters *now*: Phase 6's pace<->HR regression would silently absorb summer HR drift.
- **Injury/niggle dial-back modes are standard lightweight features** (Runna "Not
  Feeling 100%" = 3–14-day reduced plan; JOIN/enduco soreness prompts). Phase 7 gains a
  niggle log; Phase 10 maps it to an avoid-list.
- **Phase 10 (recommender) is the most industry-validated design** — readiness +
  load-share deficits -> today's suggested session is essentially Garmin Daily Suggested
  Workouts + Xert XATA. But both anchor to a training phase/goal, which is exactly why
  periodization (Phase 9) must land first.
- **Phase 11 run-push is feasible today**: garminconnect 0.3.6 (this repo's own
  library) documents `upload_running_workout()`, `schedule_workout()`,
  `delete_workout()`, `unschedule_workout()`. **Strength push is the risk**: no
  `StrengthWorkout` class is documented, and even Runna pushes *only run-type* workouts
  to Garmin, keeping strength in-app.
- **Phase 8 goes beyond the industry** — no endurance app models cross-session
  muscle/pattern overlap explicitly. Closest analogues: Garmin Strength Coach's
  push/pull-day organization and Athletica's HYROX strength library organized by
  **push / pull / hinge / squat / carry** patterns (a sane starting vocabulary for the
  Phase 8 taxonomy). A genuine differentiator for a Hyrox athlete.
- **Where this local system beats the industry: explainability.** TrainerRoad RLGL,
  Humango, and TrainAsONE models are unpublished black boxes; the digest's
  cited-signal approach is strictly better and worth protecting (see non-goals).

### Capability matrix (products × capabilities)

Legend: ● = yes (documented), ◐ = partial/limited, — = no/not found. TR = TrainerRoad,
TAO = TrainAsONE, ATH = Athletica, HUM = Humango, AIE = AI Endurance, RUN = Runna,
WHP = WHOOP, GAR = Garmin, JOI = JOIN, END = enduco, HRV4 = HRV4Training,
INT = Intervals.icu, XRT = Xert, STR = Stryd.

| Capability | TR | TAO | ATH | HUM | AIE | RUN | WHP | GAR | JOI | END | HRV4 | INT | XRT | STR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Race-date periodized plan (phases + taper) | ● | ● | ● | ● | ● | ● | — | ● | ● | ● | — | ◐ manual | ◐ | ● |
| Post-workout subjective feedback loop | ● | ◐ | ● | ● | ● | ◐ | — | — | ● | ● | ● tags | ◐ | — | — |
| Auto re-planning after missed sessions | ● | ● | ● | ● | ● | ● | — | ● | ● | ● | — | — | ● daily | — |
| Wearable recovery input (HRV/sleep) | — | ◐ | ● | ● | ● | — | ● | ● | ◐ | ◐ | ● | ● store | — | — |
| Prospective fatigue guardrail / readiness gate | ● RLGL | ● | ● | ● | ● | ◐ | ● | ● | ● | ● | ● | — | ● | — |
| Threshold auto-detection (cadence) | ● 28d | ● | ● CP | ◐ | ● | ◐ | — | ● | ◐ | ◐ | — | ● eFTP | ● event | ● cont. |
| Strength / hybrid programming | — | — | ● Hyrox | ◐ | — | ● | — | ● | — | — | — | — | — | — |
| Per-set strength tracking | — | — | ◐ log | — | — | ◐ | — | ● | — | — | — | — | — | — |
| Structured workout push -> Garmin device | ◐ | ● | ● | ● | ● | ● run-only | — | n/a | — | ● | — | ◐ | ● | ● |
| Environment adjustment (heat/altitude/terrain) | — | ● | — | ◐ | — | ◐ wx | ◐ wx | ● | — | — | — | — | — | ◐ |
| Injury/illness/niggle mode | ◐ RLGL | ◐ | ◐ | ● | ◐ | ● | — | — | ● soreness | ● | ◐ | ◐ custom | — | — |
| Sleep-debt guidance | — | — | ◐ | ◐ | — | — | ● | ● | — | ◐ | ◐ | ◐ | — | — |
| Race-day pacing plan | — | — | — | — | — | ◐ | — | ● PacePro | — | — | — | — | — | ● |
| Fueling/nutrition | — | — | — | — | — | — | ◐ | ● Connect+ | — | — | — | — | — | — |
| Explainable recommendations (cites reasons) | ◐ | ◐ | ◐ | ◐ | ◐ | ◐ | — LLM | ◐ | ◐ | ◐ | ● | ● raw | ● | ● |
| Open API over athlete data | — | — | — | — | — | — | ◐ | ● partner | — | — | ◐ | ● full | ◐ | ◐ |

### Phase validation at a glance

| Phase | Industry analogue | Verdict |
|---|---|---|
| 6 — personal zones | TrainerRoad AI FTP, Stryd auto-CP, Athletica CP, AI Endurance zone detection, Intervals.icu eFTP | Strongly validated; add re-detection cadence + staleness + heat guards |
| 6b — snapshot | Humango Goal Readiness, Intervals.icu athlete page, Garmin dashboard | Validated as the recommender's input surface; add Training Readiness + acclimation + sleep debt |
| 7 — sRPE load | Foster sRPE is the scientific standard; JOIN/Athletica/enduco/Humango all collect RPE; Athletica asks Hyrox athletes to log sets/reps/RPE | Strongly validated; add niggle log; RPE also becomes an adaptation trigger (Phase 10) |
| 8 — per-set + overlap | Garmin Strength Coach push/pull days; Athletica push/pull/hinge/squat/carry | Validated *and differentiating* — nobody models cross-session overlap |
| 9 — periodization | Universal: TrainerRoad, Runna, Athletica, Stryd, Garmin Coach, TrainAsONE | Was the roadmap's biggest gap; **shipped**. Race-day pacing split out as 9b (ADR 0012) |
| 9b — race-day pacing | Garmin PacePro, ROXFIT "PaceMe" | Deferred: HYROX Doubles needs a partner model + station split the DB cannot hold |
| 10 — recommender | Garmin DSW, Xert XATA, TrainerRoad TrainNow, WHOOP strain target | Most validated phase; needs block/goal input from Phase 9 + re-planning rules |
| 11 — authoring & push | Runna/Athletica/Stryd/TrainAsONE push structured workouts; Garmin Training API exists for this | Run-push verified in garminconnect 0.3.6; strength-push must be spiked first |
| read-MCP | Intervals.icu open REST API over the athlete's own data | Validated pattern: deterministic engine + thin read surface |

## Ordering and dependencies

```
6 zones ──┬──► 6b snapshot ──► 9 periodization ──► 10 recommender ──► 11 push-to-Garmin
7 load  ──┤         ▲                ▲                  ▲                  ▲
8 sets/overlap ─────┴────────────────┴──────────────────┘                  │
6 zones (pace targets) ────────────────────────────────────────────────────┘
                       │
6 zones + 6b snapshot ─┴──► 9b race-day pacing   (a leaf: nothing depends on it)

read-MCP (conversational read layer) ── wraps 6/6b/7/8/9 marts + digest, built last
```

Note the shape of **9b**: it hangs off 6 (threshold pace) and 6b (snapshot), and *nothing*
depends on it. Phase 10 gates on Phase 9's `block`, not on `race_plan` — which is what let
the two be split (ADR 0012).

Rationale: **6 (zones)** and **7 (load)** are foundational corrections everything
downstream trusts — 6 is lighter and unblocks pace advice + Phase 11; 7 is the biggest
*correctness* fix; they are independent, reorder freely. **6b (snapshot)** rolls the
current standing into one place. **9 (periodization)** gives the system a notion of
"what block am I in" — without it the recommender advises in a vacuum (both Garmin DSW
and Xert XATA anchor to a phase/goal). **10** composes 6–9 into forward-looking,
re-planning-aware advice. **11** turns advice into a real workout on the watch. The
**read-MCP** is tooling, not a training phase — build it last, once the marts it
exposes have stabilized.

## Phase 6: personal training zones

> **`athlete_zones` mart.** Status: **DONE** — see [PRD](prd/phase-6.md) and
> [ADR 0007](adr/0007-phase-6-personal-zones.md). The design record below is kept for
> context.

**Goal.** Derive the athlete's HR and pace zones from recorded data so intensity advice
is deterministic instead of computed by hand each time — and keep them *fresh*.

**Why (evidence).** Saying "6:00/km = Zone 2" today required an ad-hoc query over
`activities` (10.2 km @ 6:13/km sat at avg_hr **128**, ~61 min in Z2; a 5:19/km run ran
avg_hr **143** with 38 min in Z3 — so the Z2/threshold boundary lives ~5:30–6:00).
Meanwhile `coach_thresholds.hr_z2_upper_bpm = 140` is a hardcoded placeholder literally
annotated *"approx Z2 ceiling; refine from user_settings zones"*.

**Data.** New mart `athlete_zones`: per-zone HR bounds + `z2_pace_ceiling_s_per_km`,
`threshold_pace_s_per_km`, `lthr_bpm`, plus `computed_at`, `source`, `stale`. Method
precedence: (1) Garmin `user_settings` HR zones if present, else (2) data-derived —
LTHR estimate + a pace<->HR regression over aerobic runs. Recomputed mart, never mixed
into core.

**Re-detection cadence (survey).** Recompute on a fixed cadence (~28 days —
TrainerRoad's published rationale: meaningful threshold change needs weeks) **and**
event-driven after a race/PR effort (Xert's "breakthrough" pattern). Digest warns when
zones are `stale`.

**Environment guards (survey).** Exclude hot-weather runs from the pace<->HR fit, or
regress temperature out — per-activity temperature is already in activity weather
payloads; TrainAsONE's rationale: heat-elevated HR is thermoregulatory drift, not
fitness loss. Optionally ingest Garmin heat/altitude acclimation into `daily_wellness`.

**Thresholds.** Retire the hardcoded `hr_z2_upper_bpm`; store zone bounds with a
`source` tag (device vs derived).

**Signals / surface.** `AEROBIC_LOW_SHORTAGE` reclassifies grey-zone vs true Z2 against
the *personal* ceiling. Digest headline exposes the Z2 pace ceiling so the coach can
say "keep easy runs under X:XX" without recomputing.

**Seam & tests.** Pure `zones.compute(activities, user_settings) -> zone rows`; golden
test on onboarding + post-onboarding fixtures (shape drift applies here too).

**DoD.** `features` recompute uses personal zones; digest carries `zones` (+ staleness);
golden green.

**Risk.** Device zones may be stale/auto-set — document the precedence and flag
disagreement.

## Phase 6b: athlete snapshot

> **`athlete_status` mart + `snapshot` command.** Status: **DONE** — see
> [PRD](prd/phase-6b/PRD.md) and
> [ADR 0009](adr/0009-phase-6b-athlete-snapshot.md). The design record below is kept
> for context.

**Goal.** One place that answers "where do I stand right now" — current fitness
markers, zones, load/recovery state, and the active plan — as a compact, deterministic
read.

**Why (evidence).** In conversation the standing picture had to be reassembled by hand
from scattered tables (`fitness_markers`, `race_predictions`, `daily_metrics`,
`plan_template`) plus ad-hoc queries. There is no single "current stats + plan"
surface, and the recommender (Phase 10) needs exactly this as its input.

**Data.** New mart `athlete_status` (latest row, recomputed): VO2max + trend, race
predictions, personal zones (Phase 6), HRV baseline/SD, latest ACWR + reliability,
7-day load + shares, body weight trend, and the active `plan_template` /
periodization block (Phase 9) for the current week. Survey additions: Garmin
**Training Readiness** score, heat/altitude **acclimation**, and a **sleep-debt** fact
(7-day sleep vs personal baseline — WHOOP and Garmin both treat sleep history as a
first-class readiness input; the data is already in the DB). No new Garmin — reads
finished marts + core only.

**Command.** `garmin-coach snapshot` (prints/writes `reports/{date}/snapshot.json`);
optionally a short "Twoje aktualne staty" header in the coach report.

**Seam & tests.** Pure `snapshot.build(conn) -> status dict`; golden test over fixtures.

**DoD.** `snapshot` emits current markers + zones + ACWR/load + active plan in one
object; green. Recommender (Phase 10) consumes it directly.

**Deps.** Best after 6 (zones) and 7 (honest load); harmless without them (fields
degrade to device values / None).

**Risk.** Keep it a *read* — a snapshot, never a recompute; all numbers come from the
marts.

## Phase 7: strength and Hyrox load model

> **Session-RPE load model + niggle log.** Status: **DONE** — see
> [PRD](prd/phase-7/PRD.md) and
> [ADR 0010](adr/0010-phase-7-strength-load-and-niggle.md). The design record below is
> kept for context.

**Goal.** Stop under-counting non-cardio work so ACWR, monotony, and deload reflect
real stress; capture the subjective signals (RPE, soreness, niggles) the industry
treats as core inputs.

**Why (evidence).** `Siła` sessions get `training_load` **21.8** and **33.5** for
**74** and **68** minutes of work — because Garmin's load is HR-driven and lifting
barely moves HR. The model effectively can't see a full strength session. Hyrox/HIIT
sessions also carry no `total_sets`/`total_reps`. Survey: Foster sRPE is the scientific
standard and JOIN/Athletica/enduco/Humango all collect post-session RPE; Athletica
explicitly asks Hyrox athletes to log sets/reps/RPE for strength days.

**Data.** Capture a session-RPE (Borg CR10) + optional soreness/mood in a new core
table `session_rpe(activity_id, rpe, soreness, mood, source, notes)`. Compute Foster
`sRPE_load = rpe × duration_min`. Blend into `daily_metrics.load` with a discipline
rule (e.g. `max(garmin_load, sRPE_load)` for cardio; sRPE for strength). **Fallback:**
a default RPE per discipline so the pipeline stays deterministic when no RPE is logged.

**Niggle log (survey).** Sibling core table `niggle(date, body_part, severity, note)`
written by the same thin CLI writer. This is the local equivalent of Runna's
"Not Feeling 100%" (3–14-day dial-back) and JOIN/enduco soreness prompts: severity ≥
threshold surfaces a *reduced-mode* state in the digest; Phase 10 maps active niggles
to an avoid-list (synergy with Phase 8's exercise->pattern map).

**Thresholds.** Reuse `hard_te_load = 150`; add `rpe_hard`, strength weighting,
`niggle_reduced_mode_severity`. Feeds `monotony`/`strain`, `TWO_HARD_DAYS`,
`ACWR_OUT_OF_RANGE`, `DELOAD_ADVISED` (all currently blind to lifting).

**Command.** `garmin-coach log-rpe --activity <id> --rpe N [--soreness N]` and
`garmin-coach log-rpe --niggle <body_part> --severity N` (thin transport-free writers
to core).

**Seam & tests.** Pure `load.blend(...)`; golden regression proving a strength day now
contributes load and shifts weekly totals; niggle -> reduced-mode state test.

**DoD.** A logged strength session raises daily/weekly load and ACWR; an active niggle
surfaces in the digest; golden green.

**Risk.** Subjective input — keep defaults so nightly automation never blocks on
missing RPE.

## Phase 8: per-set capture and overlap

> **Per-set capture + modality/muscle overlap (finishes D9).**

**Goal.** Capture per-set exercise data and model cross-session overlap (grip,
posterior chain, movement pattern).

**Why (evidence).** `activity_sets` is **empty (0 rows)** — the per-set ingestion
committed as Phase 0 D9 was deferred. Today's grip / posterior-chain warning (cable row
+ KB complex, then row/ski/farmer carry an hour later) was eyeballed, not computed.
Survey: no endurance app models this explicitly — a genuine differentiator; Athletica's
HYROX strength library organizes by **push / pull / hinge / squat / carry**, a sane
starting taxonomy, and Garmin Strength Coach organizes push/pull days with deload
weeks.

**Data.** ETL pull `get_activity_exercise_sets` -> normalize into `activity_sets` (pure
normalizer, scalars only). New lookup mart mapping exercise -> movement pattern / muscle
group (start from push/pull/hinge/squat/carry + grip). Daily/weekly `pattern_overlap`
metric: same pattern loaded on adjacent sessions.

**Thresholds.** `pattern_overlap_high` in `coach_thresholds`.

**Signals.** New `PATTERN_STACK` / `MUSCLE_OVERLAP` (warn) when high-load patterns
repeat without recovery.

**Seam & tests.** Set normalizer unit-tested (both fixture shapes); overlap computation
golden test. ETL write stays in the pull pipeline; mart reads only.

**DoD.** `activity_sets` populated on backfill; overlap metric in mart; signal fires on
a constructed stack; green.

**Risk.** Exercise-name drift across sessions — maintain the exercise->pattern map by
hand.

- **STATUS: DONE.** Per-set ingest reuses the `_fetch_weather` seam
  (`normalize_exercise_sets` -> `activity_sets`, best-effort, idempotent); a seeded
  `exercise_pattern` map drives `overlap.py` -> the long-format `pattern_overlap` mart
  (`pattern_load` = set-share x Phase 7 load; `overlap = min` over consecutive days).
  New `PATTERN_STACK` / `MUSCLE_OVERLAP` warn signals + a `movement` coverage fact.
  Deliberate deviations from this sketch: grip is a `muscle_group` (not a sixth
  pattern), and the overlap mart is daily-only (weekly rollup deferred). Decisions:
  `prd/phase-8-movement-overlap/PRD.md` + `adr/0011-phase-8-movement-overlap.md`; seam
  tests in `tests/test_overlap.py` (+ `test_sync.py`, `test_signals.py`, `test_digest.py`).

## Phase 9: race-date periodization

> **Race-date periodization.** Status: **DONE** — see [PRD](prd/phase-9-periodization/PRD.md)
> and [ADR 0012](adr/0012-phase-9-race-date-periodization.md). Race-day pacing was split
> out into [Phase 9b](#phase-9b-race-day-pacing); the design record below is the shipped
> shape.

**Goal.** Give the system a goal: a race date, training blocks counted back from it, and
taper awareness. Without this, Phase 10 recommends sessions with no notion of "3 weeks out
vs 20 weeks out".

**Why (survey).** The single most universal industry capability: TrainerRoad Plan
Builder (Base -> Build -> Specialty with automatic tapers and openers around A/B/C
events), Stryd (plans timed to finish on race day), Athletica (race date + weekly hours
-> adaptive HYROX plan), Runna, Garmin Coach, TrainAsONE. The repo's `plan_template` was
a static weekly pattern with no concept of race date, block, or taper.

**Data.** Core `goal_event` carries the race with **two orthogonal uncertainty axes** —
`status` (`confirmed`|`tentative`: *will I start*) and `date_precision` (`exact`|`approx`:
*do I know the day*) — plus `target_s` in seconds. Only a `confirmed` priority-A race
**anchors** the plan. The `plan_block` mart holds one row per week (`block`,
`weeks_to_event`, `is_deload`), and is the only mart that **spans future weeks** — out to
race week — which is why it is not two columns on `weekly_metrics`. `plan_template` is
untouched: blocks annotate the athlete's template, they never replace it.

**Blocks.** `base | build | peak | taper` — a pure countdown. `taper`/`peak`/`build` take
fixed lengths from thresholds; `base` absorbs everything earlier (bounded by `data_start`),
so the athlete is always in *some* block. **Deload is not a block**: `is_deload` is what
the *plan* prescribed, `DELOAD_ADVISED` (Phase 5) is what the *actual load* did, and the
divergence between them is the finding — there is deliberately no arbitration rule.
Planned deloads anchor to each block's **end** (never a modulo counter), so the athlete
enters the next block fresh, and a block never opens with a deload.

**Signals.** `TAPER_ACTIVE` and `RACE_PROXIMITY` are **facts** in the digest. Suppressing
intensity on their basis is Phase 10's decision, deliberately not taken here.
`RACE_PROXIMITY` fires for the nearest upcoming race of any priority and status, and asks
for a `tentative` race to be decided and an `approx` date to be pinned.

**Command.** `garmin-coach event add | list | update` — because `status` and
`date_precision` are *designed to change*.

**Seam & tests.** Pure `periodize(event, data_start, thresholds) -> list[WeekPlan]` (no
`history`, no wall clock — blocks are a countdown, so history would only cost determinism);
golden tests over frozen dates. Materialized by `periodize.rollup` **first** in the
`features` tail, ahead of `weekly.rollup`, which copies each week's block from it.

**DoD (met).** Digest carries `block`/`weeks_to_event`; `TAPER_ACTIVE` fires in a
constructed taper; `athlete_status` fills the NULL placeholders Phase 6b left "until
Phase 9"; green.

**Deps.** None beyond a recorded race.

**Risk (respected).** Blocks are week labels, not a generated day-by-day plan — the weekly
template stays the athlete's, the engine annotates it.

## Phase 9b: race-day pacing

> **Moved to GitHub issue
> [#13](https://github.com/bchabros/garmin-coach/issues/13).**

The deterministic race-day pacing plan (`race_plan`) was split out of Phase 9 (see
[ADR 0012](adr/0012-phase-9-race-date-periodization.md)) and now lives outside this
roadmap: the full design record - the Doubles constraint, the athlete-not-the-team
rule, the three blockers, and the open forward-vs-backward question - is in the
issue. It stays a leaf (nothing depends on it) and is deliberately scoped to run
close to race day, when the information it needs actually exists.

## Phase 10: prospective recommender

> **Prospective session recommender (re-planning-aware).** Full spec:
> [prd/phase-10-recommender/PRD.md](prd/phase-10-recommender/PRD.md) (grilling decisions
> 2026-07-15).

**Goal.** Flip the engine from retrospective reading to forward advice: given readiness
+ plan + block + deficits, recommend today's/tomorrow's intensity and what to avoid —
and when the week falls apart, propose how to re-plan instead of pretending it didn't.

**Why (evidence).** Today's verdict — "two quality sessions OK because HRV 88 vs
baseline 68, but ACWR 1.21 is top of range and 0% Z2, so run Z2 tomorrow" — was
assembled by hand from the digest, `plan_template`, and zones. Survey: this shape is
almost exactly Garmin Daily Suggested Workouts (readiness + load-share deficits ->
today's workout) and Xert XATA (surplus/deficit + freshness + phase) — both anchored to
a phase/goal, hence the Phase 9 dependency.

**Data.** Pure `recommend(digest, plan_block, zones) -> {intended_type, intensity_cap,
pace_target, rationale, avoid[]}`. No Garmin. New `recommendation` block in
`digest.json` and a "Rekomendacja na dziś" section in the coach report.

**Re-planning rules (survey).** Industry consensus (Runna Plan Realignment, TrainAsONE,
Humango): missed sessions change the *plan*, not just the day. Deterministic and tiny:
if ≥N planned sessions were missed in the trailing week (already computable from Phase
5 plan-vs-actual), emit one of three **cited options** — *extend*, *rebuild toward the
event date* (drop lowest-priority sessions first), or *continue* — instead of silently
recommending the next template day.

**Adaptation triggers (survey).** Consume Phase 7 subjective inputs: yesterday
hard-RPE + low readiness => cap today's intensity (the TrainerRoad survey loop as one
rule); active niggle => avoid-list gains the mapped movement patterns (Phase 8 map);
`TAPER_ACTIVE` => suppress intensity suggestions.

**Thresholds.** None new beyond `replan_missed_sessions` — composition rules over
existing ACWR / HRV / aerobic-target / deload / taper thresholds. Every recommendation
must cite which signals drove it (explainable — the industry's weakest point is
unpublished black-box models; keep the advantage).

**Command.** Fold into `garmin-coach report`, or a dedicated `garmin-coach recommend`.

**Seam & tests.** Deterministic state->recommendation mapping; golden test over
representative digest states (green day, hot ACWR, HRV low, aerobic deficit, deload
advised, taper week, missed-week re-plan, active niggle).

**DoD.** Report renders a cited recommendation; missed-week fixture produces the three
cited options; golden green.

**Deps.** 6 (pace caps), 7 (honest load + RPE/niggles), 9 (block awareness — `block`,
`weeks_to_event`, `is_deload`, `TAPER_ACTIVE`; **not** 9b, which nothing depends on).

**Risk.** Stays a "reading + suggestion", never a prescription — keep the disclaimer.

## Phase 11: workout authoring and push

> **Structured workout authoring & push to Garmin (run first, strength spiked).**
>
> **Done** — run authoring + push shipped (`author.py` pure, `publish.py` out-of-seam,
> `author`/`push` commands with the `--confirm` interlock and account-of-record
> idempotency). See [PRD](prd/phase-11-workout-push/PRD.md) and
> [ADR 0013](adr/0013-phase-11-workout-authoring-and-push.md). Strength/HIIT push stayed
> a documented spike — no typed strength class in garminconnect, so strength execution
> stays local pending an operator endpoint probe
> ([findings](prd/phase-11-workout-push/strength-spike-findings.md)). The probe ran
> 2026-07-15 and the endpoint **accepted** the payload; strength/HIIT authoring + push is
> now tracked in [issue #16](https://github.com/bchabros/garmin-coach/issues/16).

**Goal.** Turn a recommendation into a concrete Garmin workout — tempo run with pace
targets, or a strength session with named exercises/sets — and schedule it to the
watch.

**Why.** Direct user request; industry-standard delivery (Runna syncs two weeks of run
workouts to Garmin every Monday; Athletica, Stryd, TrainAsONE, enduco all push
structured workouts).

**Architecture (important).** This is a NEW **outbound** transport path and it *bends
the golden rule*, so isolate it exactly like `client.py`:

- `author.py` — **pure**: `recommendation -> workout spec (JSON)` written to
  `reports/{date}/`. Deterministic, unit-tested, no network.
- `publish.py` — **transport, out-of-seam**: reads the spec and calls garminconnect
  workout-create / schedule endpoints. The coach/recommender read path still never
  writes.
- Commands: `garmin-coach author --date` (pure spec) and `garmin-coach push --date`
  (explicit transport; `--dry-run` default; never invoked from the nightly automation).

**Split delivery (survey — de-risk).**

1. **Run-push first (verified surface).** garminconnect 0.3.6 documents typed workout
   upload and scheduling: `upload_running_workout()` (+ cycling/swimming/walking/
   hiking), `schedule_workout(workout_id, date)`, `delete_workout()`,
   `unschedule_workout()`; workout classes include `RunningWorkout` and
   `MultiSportWorkout`. Ship pace-target run workouts on this.
2. **Strength-push as a separate spike.** **No `StrengthWorkout` class is documented**;
   Garmin's partner Training API is cardio-oriented; Runna pushes only run-type
   workouts and keeps strength in-app. Probe the private workout-create endpoint with a
   hand-built strength payload *before* writing any seam code; if it fails, fall back
   to Runna's model — strength stays local (spec + report, watch-free execution) —
   without blocking the run-workout deliverable.

**Seam & tests.** `author` unit-tested (spec <-> Garmin workout JSON); `push` validated
by a live run, idempotent by workout name+date (re-push must not duplicate — this
matches how Runna re-syncs its scheduled fortnight).

**DoD.** `author` produces a valid spec from a recommendation; `push --dry-run` shows
the payload; a confirmed live push creates exactly one scheduled *run* workout; author
tests green. Strength spike outcome documented (endpoint works / fallback chosen).

**Deps.** 6 (pace targets), 10 (what to prescribe); 7/8 sharpen strength authoring.

**Risk.** Writing is near-irreversible (creates account-side workouts). Dry-run by
default, explicit confirm, never auto-schedule from the nightly path.

## Read-MCP: the coach MCP server

> **Done** — shipped as the `coach` MCP server (stdio, versioned `.mcp.json`), scope
> grown beyond the original read-only sketch: reads + same-day refresh (issue #8) +
> the Phase 11 workout push, per [epic #18](https://github.com/bchabros/garmin-coach/issues/18)
> and [ADR 0014](adr/0014-coach-mcp-server.md).

**Goal (as built).** One local MCP server so a chat session can pull "current stats"
in one call, see *today's* morning HRV/readiness on demand, log sRPE/niggles, and
finish an agreed session by authoring + pushing it to the watch — terminal needed
only for operating the pipeline.

**Why (evidence).** Coaching sessions mixed hand-written SQLite queries (repetitive;
now one tool call) with ad-hoc `mcp__garmin__*` calls for same-day data (now
`refresh_today`), and a session that agreed on a workout still had to end in a
terminal (now the preview/confirm pair). Survey precedent: Intervals.icu — a
deterministic data platform with a thin open API surface, not an AI product.

**Not a second Garmin MCP.** The `mcp__garmin__*` server (~150 tools, verbose
payloads) stays scoped to **ad-hoc exploration / fixtures**. `mcp__coach__*` reads
the **local DB** and reuses the system's own transport seams for exactly two
purposes: the same-day refresh (a read that never advances watermarks) and the
Phase 11 push (behind a preview-hash handshake stricter than the CLI's
`--confirm`). No metric ever depends on live Garmin — ADR 0014 records how this
supersedes the earlier "read-only by construction" clause.

**Surface (14 tools, four groups).** Read: `get_snapshot`, `get_digest`,
`get_recent_activities(n)`, `get_weekly(week_start)`, `get_zones`,
`get_recommendation`, `get_events`, `get_workout_status(date)`. Local writes:
`log_rpe`, `log_niggle`. Transport read: `refresh_today`. Workout push:
`author_workout`, `push_preview`, `push_confirm(spec_hash)`. Every response carries
a freshness envelope (`data_through`, `today_included`, `partial_fields`) so partial
same-day numbers are never mistaken for final.

**Build.** `mcp_tools` (pure functions over the same seams the CLI uses; fully
offline-tested) + `mcp_server` (thin FastMCP wiring, smoke-tested in-process).
No new computation anywhere.

## Explicit non-goals

Informed by the survey:

- **No LLM chat layer inside the engine** (WHOOP Coach / Garmin "Active Intelligence"
  territory) — the coach skill + read-MCP already provide the conversational surface
  over deterministic numbers, which is a *better* architecture for auditability.
- **No nutrition tracking.** Only Garmin Connect+ and Ladder do it among the surveyed
  set; it needs a food-logging input surface for little single-athlete value. The
  race-day fueling note in Phase 9 covers most of it.
- **No social / streaks / motivation UX.** SaaS retention features, not signal.
- **No black-box readiness score.** Keep the digest's cited-signal explainability —
  the industry's weakest point (RLGL, Humango, TrainAsONE are unpublished models) is
  exactly where a local deterministic engine can be strictly better.

## Deferred / open questions

- Multi-sport `discipline` weighting in weekly rollups (deferred from Phase 5); ski
  touring season will force it.
- VO2max / threshold **trend charts** and PDF/Notion export (deferred in Part I §12).
- DFA-alpha-1 style in-exercise HRV readiness (AI Endurance) — needs beat-to-beat data
  the current ETL doesn't pull; revisit only if a real need shows up.
- Weather-forecast-aware pace adjustment for *upcoming* runs (TrainAsONE) — needs a
  forecast source, i.e. a new inbound transport; keep out until Phase 6's
  historical-temperature guard proves insufficient.

## Source index

Primary unless noted.

- TrainerRoad: [Adaptive Training Help Guide](https://support.trainerroad.com/hc/en-us/articles/4409099184283-Adaptive-Training-Help-Guide) · [AT recommendations](https://support.trainerroad.com/hc/en-us/articles/4404968208923-How-Does-Adaptive-Training-Recommend-Workouts) · [AI FTP](https://support.trainerroad.com/hc/en-us/articles/4415864080155-How-to-Use-AI-FTP-Detection) · [AI FTP 28-day rationale](https://support.trainerroad.com/hc/en-us/articles/8697549554587-Why-is-AI-FTP-Detection-Only-Available-Every-28-Days) · [Plan Builder](https://support.trainerroad.com/hc/en-us/articles/360037923191-Plan-Builder-Overview) · [TrainNow](https://support.trainerroad.com/hc/en-us/articles/360057075531-TrainNow-Overview) · [RLGL blog](https://www.trainerroad.com/blog/new-sport-types-supported-by-trainerroad-and-red-light-green-light/)
- TrainAsONE: [how it works](https://trainasone.com/how-it-works) · [adaptive plan](https://www.trainasone.com/features/adaptive-training-plan) · [weather FAQ](https://trainasone.com/faqs/2026/how-does-trainasone-handle-temperature-and-weather) · [race elevation FAQ](https://trainasone.com/faqs/2024/does-trainasone-consider-the-elevation-of-my-target-race)
- Athletica: [home](https://athletica.ai/) · [HYROX setup](https://athletica.ai/getting-started-hyrox-athletica/) · [HYROX strength library](https://athletica.ai/hyrox-strength-training-athletica-global-library/) · [Garmin workout sync](https://support.athletica.ai/hc/en-us/articles/23513848128923-How-to-Get-Your-Athletica-Workouts-To-Sync-With-Garmin) · [release notes](https://app.athletica.ai/release-notes)
- Humango: [home](https://humango.ai/) · [for triathletes](https://humango.ai/humango-for-triathletes2/)
- AI Endurance: [product](https://aiendurance.com/en/product) · [readiness & durability (DFA α1)](https://aiendurance.com/blog/readiness-to-train-and-durability-hrv-metrics)
- Runna: [Plan Realignment](https://support.runna.com/en/articles/10026375-how-to-use-the-plan-realignment-feature) · [Not Feeling 100%](https://support.runna.com/en/articles/13531498-how-to-use-not-feeling-100) · [aches & pains](https://support.runna.com/en/articles/13225357-adjusting-your-plan-around-minor-aches-and-pains) · [strength](https://support.runna.com/en/articles/6262149-everything-you-need-to-know-about-strength-training-for-runners) · [Garmin sync (run-only)](https://support.runna.com/en/articles/6169639-using-your-garmin-watch-with-runna)
- WHOOP: [Coach announcement](https://www.whoop.com/us/en/thelocker/whoop-unveils-the-new-whoop-coach-powered-by-openai/) · [Coach support](https://support.whoop.com/s/article/How-to-Use-the-AI-Powered-WHOOP-Coach?language=en_US) · [Strain Target](https://www.whoop.com/us/en/thelocker/strain-coach/)
- Garmin: [Training Readiness](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/training-readiness/) · [Daily Suggested Workouts FAQ](https://support.garmin.com/en-US/?faq=oYknGZ910l1pfBNzkDHX6A) · [Coach overview](https://www.garmin.com/en-US/garmin-coach/overview/) · [Strength Coach](https://www.garmin.com/en-US/garmin-coach/strength/) · [Strength Coach FAQ](https://support.garmin.com/en-US/?faq=pVtmVTZz7C97GZHxtMWgs8) · [Connect+ press release](https://www.garmin.com/en-US/newsroom/press-release/wearables-health/elevate-your-health-and-fitness-goals-with-garmin-connect/) · [Active Intelligence FAQ](https://support.garmin.com/en-US/?faq=kWi5DoaMPZ4VCJBA0lFWP7) · [heat/altitude acclimation](https://www.garmin.com/en-US/garmin-technology/running-science/physiological-measurements/heat-and-altitude-acclimation/) · [PacePro](https://support.garmin.com/en-US/?faq=svpm2I38YB2sU5CiqFXyfA) · [Training API (partner-gated)](https://developer.garmin.com/gc-developer-program/training-api/)
- JOIN: [why JOIN](https://join.cc/why-join) · [integrations](https://help.join.cc/hc/en-150/articles/4404775688081-Integrations) · [workout player & Garmin](https://help.join.cc/hc/en-150/articles/20489720343185-Workout-player-Garmin-connect)
- enduco: [home](https://enduco.app/) · [v7 announcement](https://enduco.app/blog/relaunch-with-strategy-enduco-launches-version-7-featuring-new-technology-and-creator-collaborations)
- HRV4Training: [Pro user guide](https://marcoaltini.substack.com/p/hrv4training-pro-user-guide) · [Altini: HRV-guided training](https://medium.com/@altini_marco/heart-rate-variability-hrv-guided-training-to-improve-performance-24b0ec24e6f8)
- Intervals.icu: [open API](https://www.intervals.icu/features/open-api/) · [API thread](https://forum.intervals.icu/t/api-access-to-intervals-icu/609) · [API cookbook](https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090) · [wellness](https://www.intervals.icu/features/wellness/) · [power curve](https://www.intervals.icu/features/power-curve/)
- Xert: [XATA glossary](https://www.baronbiosys.com/glossary/xert-adaptive-training-advisor/) · [training with XATA](https://www.baronbiosys.com/training-with-the-xert-adaptive-training-advisor/)
- Stryd: [auto-CP](https://blog.stryd.com/2019/07/09/introducing-auto-calculated-critical-power/) · [CP definition](https://help.stryd.com/en/articles/6879345-critical-power-definition) · [plan FAQ](https://help.stryd.com/en/articles/8923322-stryd-training-plan-faq)
- ROXFIT: [how it works](https://roxfit.app/how-it-works/) · Ladder: [home](https://www.joinladder.com/) · [nutrition PR](https://www.businesswire.com/news/home/20251027087979/en/)
- Toolchain: [garminconnect on PyPI (0.3.6)](https://pypi.org/project/garminconnect/) · [python-garminconnect GitHub](https://github.com/cyberjunky/python-garminconnect) · *(secondary)* [the5krunner on Garmin API & strength apps](https://the5krunner.com/2026/03/24/garmin-connect-plus-strength-apps/)