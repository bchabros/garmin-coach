# garmin-coach

Local system that pulls data from Garmin Connect once a day, keeps it in SQLite as
the **system-of-record**, computes training metrics (HRV baseline, ACWR, load
balance), and lets a coach agent generate a weekly review: is training going well,
and what's missing.

**Core principle — separate transport from intelligence:**

- **Deterministic ETL** (fetch + normalize) goes through the
  [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) library, run
  on a schedule. *Not* through MCP — Garmin's MCP is fine for ad-hoc exploration but
  too flaky for a pipeline (timeouts, >1 MB payloads).
- **Metrics + coach layer** reads the finished DB, never hits Garmin live.

One repo, several work surfaces: Claude Code and Codex build/maintain it, while
Claude Cowork points at the same DB and runs the coach skill. See
[docs/PROJECT.md](docs/PROJECT.md) for the full brief and roadmap — the built
phases 0-10 plus the forward plan for phase 11 + read-MCP.

## Status

| Phase | Scope | State                                                                                                                                   |
|-------|-------|-----------------------------------------------------------------------------------------------------------------------------------------|
| 0 | Raw capture + idempotent backfill | Done — [docs/prd/phase-0.md](docs/prd/phase-0.md)                                                                                       |
| 1 | Incremental sync + resilience (watermark, retry, per-day fallback) | Done — [docs/prd/phase-1.md](docs/prd/phase-1.md), [ADR 0001](docs/adr/0001-phase-1-incremental-sync.md)                                |
| 2 | Metrics mart (`features.py` → `daily_metrics`) | Done — [docs/prd/phase-2.md](docs/prd/phase-2.md), [ADR 0002](docs/adr/0002-phase-2-metrics-semantics.md)                               |
| 3 | Coach skill (digest + charts + `report.md`) | Done — [docs/prd/phase-3.md](docs/prd/phase-3.md), [ADR 0003](docs/adr/0003-phase-3-coach-signals.md)                                   |
| 4 | Automation (nightly orchestrator, alerts, launchd/cron) | Done — [docs/prd/phase-4.md](docs/prd/phase-4.md), [ADR 0004](docs/adr/0004-phase-4-automation.md)                                      |
| 5 | Weekly rollups, plan-vs-actual, deload detection | Done — [docs/prd/phase-5.md](docs/prd/phase-5.md), [ADR 0005](docs/adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md), [ADR 0006](docs/adr/0006-post-phase-5-architecture-deepening.md) |
| 6 | Personal training zones (`athlete_zones` mart, LTHR anchor) | Done — [docs/prd/phase-6.md](docs/prd/phase-6.md), [ADR 0007](docs/adr/0007-phase-6-personal-zones.md)                              |
| 6b | Athlete snapshot (`athlete_status` mart + `snapshot` command) | Done — [docs/prd/phase-6b/PRD.md](docs/prd/phase-6b/PRD.md), [ADR 0009](docs/adr/0009-phase-6b-athlete-snapshot.md)                                      |
| 7 | Session-RPE load model for strength/Hyrox + niggle log | Done — [docs/prd/phase-7/PRD.md](docs/prd/phase-7/PRD.md), [ADR 0010](docs/adr/0010-phase-7-strength-load-and-niggle.md)                                      |
| 8 | Per-set capture + movement-pattern overlap | Done — [docs/prd/phase-8-movement-overlap/PRD.md](docs/prd/phase-8-movement-overlap/PRD.md), [ADR 0011](docs/adr/0011-phase-8-movement-overlap.md)                  |
| 9 | Race-date periodization (`goal_event` + `plan_block` marts) | Done — [docs/prd/phase-9-periodization/PRD.md](docs/prd/phase-9-periodization/PRD.md), [ADR 0012](docs/adr/0012-phase-9-race-date-periodization.md)                  |
| 10 | Prospective session recommender (re-planning-aware) | Done — [docs/prd/phase-10-recommender/PRD.md](docs/prd/phase-10-recommender/PRD.md)                                                                                      |
| 11 | Structured workout authoring + push to Garmin (run first) | Planned — [docs/PROJECT.md](docs/PROJECT.md#phase-11-workout-authoring-and-push)                                                                                      |
| read-MCP | Read-only MCP server over the local marts (tooling, built last) | Planned — [docs/PROJECT.md](docs/PROJECT.md#read-mcp-conversational-read-layer)                                                                                      |

Phases 0-10 are built and everything after is the forward plan; both live in
[docs/PROJECT.md](docs/PROJECT.md), which also records the industry survey and the
dependency ordering between the planned phases. Phase 9b (race-day pacing) has moved
out of the roadmap to GitHub issue [#13](https://github.com/bchabros/garmin-coach/issues/13) —
it is deliberately scoped to run close to race day.

## Layout

```
garmin-coach/
├── pyproject.toml            # Poetry: deps, scripts, tool config
├── Taskfile.yml              # task shortcuts for tests, lint, checks, backfill, daily
├── CLAUDE.md                 # thin shared agent core (what/golden rule/commands/pointers)
├── AGENTS.md                 # byte-for-byte mirror of CLAUDE.md (guarded by a test)
├── .claude/rules/            # extra working rules imported by CLAUDE.md (style, no-emoji)
├── .codex/                   # Codex local notes and companion files
├── .env.example              # optional overrides (credentials, DATA_START_DATE, DB_PATH, LOG_PATH, ...)
├── docs/
│   ├── PROJECT.md            # build brief + roadmap: phases 0–10 (built) and 11 + read-MCP
│   ├── architecture-roadmap.md # post-Phase-5 architecture review (completed)
│   ├── DEVELOPMENT.md        # coding guide: workflow, module map, conventions, seams
│   ├── OPERATIONS.md         # operator runbook: pipeline, exit codes, logs, reports
│   ├── schema.sql            # DB schema snapshot (source of truth: the package copy)
│   ├── glossary.md           # domain vocabulary (single source of truth)
│   ├── adr/                  # decision records (0001–0012; one per phase + architecture/docs)
│   └── prd/                  # per-phase PRDs (phase-0 .. phase-10) + docs-layering
├── scripts/
│   ├── daily.sh              # thin cron/launchd entrypoint: execs `garmin-coach daily`
│   └── com.garmincoach.daily.plist.example  # launchd schedule example (macOS)
├── skills/coach/SKILL.md     # narrative layer: reads digest.json, writes report.md
├── src/garmin_coach/
│   ├── config.py             # pydantic-settings: .env + paths + logging config
│   ├── client.py             # transport: login (+MFA), garminconnect method map
│   ├── db.py                 # connect, schema bootstrap, idempotent upserts
│   ├── models.py             # pure normalizers payload→row + discipline mapping
│   ├── sync.py               # backfill + incremental sync: raw-first, upsert core,
│   │                          #   retry/backoff, per-day fallback, isolated streams
│   ├── features.py           # mart: daily_metrics (HRV baseline/SD, ACWR, load buckets)
│   ├── load.py               # per-activity sRPE load blend (strength/Hyrox) shared by marts
│   ├── weekly.py             # mart: weekly_metrics + weekly_plan_actual (run by features)
│   ├── zones.py              # mart: athlete_zones (LTHR anchor → %LTHR HR bands + Z2 pace ceiling)
│   ├── overlap.py            # mart: pattern_overlap (same movement pattern/muscle on adjacent days)
│   ├── periodize.py          # mart: plan_block (training blocks counted back from the goal race)
│   ├── weeks.py              # leaf: Monday-anchored week arithmetic
│   ├── snapshot.py           # mart: athlete_status (the single current-standing snapshot)
│   ├── thresholds.py         # coach threshold policy: defaults + DB overrides
│   ├── digest.py             # headline + coach signals + weekly/zones/plan sections (build_digest)
│   ├── signals.py            # pure signal rules invoked by digest.py (incl. DELOAD_ADVISED, TAPER_ACTIVE)
│   ├── recommend.py          # tomorrow's session recommendation composed from the digest (Phase 10)
│   ├── charts.py             # HRV band + ACWR matplotlib charts
│   ├── report.py             # orchestrates digest + charts → reports/{date}/
│   ├── daily.py              # nightly orchestrator: sync → features → alerts
│   ├── cli.py                # argparse entry point (backfill/sync/features/report/snapshot/event/log-rpe/daily)
│   └── schema.sql            # runtime schema (loaded via importlib.resources)
├── tests/
│   ├── conftest.py           # in-memory DB + FakeGarminClient + fixture loader
│   ├── fixtures/             # anonymized real Garmin payloads + golden fixtures
│   └── test_*.py             # one module per seam (models, db, sync, features, weekly,
│                              #   zones, signals, digest, daily, thresholds, config, cli,
│                              #   schema-sync guard)
├── memory/                   # coach long-term memory: athlete profile, goals, coaching
│                              #   decisions (Polish, gitignored — numbers stay in the DB)
├── plans/                    # weekly training plans written in coach sessions (gitignored)
├── logs/daily.log            # rotating nightly-run log (gitignored)
├── reports/{date}/           # digest.json + charts + report.md (gitignored)
└── data/garmin.db            # SQLite system-of-record (gitignored)
```

Data layout is medallion: **raw** (`raw_payloads`, append-only) → **core** (normalized,
upserted) → **mart** (`daily_metrics`, recomputed by `features`; `weekly_metrics`
and `weekly_plan_actual`, rolled up from it by `weekly` for every *complete* week;
`athlete_zones`, a singleton recomputed by `zones` from the watch-detected LTHR).
`digest.py` reads the marts and produces the compact digest (headline, signals,
weekly plan-vs-actual) the coach skill narrates; `daily.py` reruns the whole chain
unattended and reduces the digest to log-worthy alerts.

## Setup

Requires Python 3.13 and [Poetry](https://python-poetry.org/). [Task](https://taskfile.dev/) is optional but recommended; every task wraps the underlying Poetry command.

```bash
poetry install                 # or: task install (also installs the pre-commit hook)
```

`task install` additionally runs `pre-commit install`, wiring a git hook that runs
`task check` before every commit (see [Development](#development)). After a bare
`poetry install`, enable it once with `poetry run pre-commit install`.

No `.env` is required: credentials are prompted on the first login and every other
setting has a sensible default. To override anything (DB path, backfill start,
logging), `cp .env.example .env` and uncomment what you need.

### Athlete-specific configuration (lives in the DB)

Two tables are seeded with defaults on first schema bootstrap (`INSERT OR IGNORE`,
so your edits survive re-runs) and are meant to be edited to fit the athlete:

- **`coach_thresholds`** — every coach tunable as a `key/value/note` row: HRV
  baseline and SD, ACWR bands, the "hard session" load cutoff, zone percentages,
  deload rules. The seeds were derived from this athlete's first weeks of data;
  after your own backfill, review them (each row's `note` says what it means).
- **`plan_template`** — your intended weekly pattern, one row per day of week
  (`dow` 0 = Monday) with a free-text label and an intent (`rest | quality |
  easy`). Phase 5's plan-vs-actual and `plan_adherence` are measured against
  this table, so it should reflect *your* plan, not the seeded one.

Edit both with plain SQL (`sqlite3 data/garmin.db`), then re-run
`garmin-coach features` — marts are recomputed, never authoritative.

## Usage

```bash
# First run prompts for email + password + MFA once, then caches OAuth tokens to
# ~/.garminconnect; later runs resume from them (no login endpoint, no rate limits).
task run FROM=2026-06-08

# Optional end date for a bounded local run.
task backfill FROM=2026-06-08 TO=2026-06-30

# Poetry fallback if Task is not installed.
poetry run garmin-coach backfill --from 2026-06-08
```

`backfill` pulls `[--from .. yesterday]` (today is skipped — HRV/sleep land after the
night) across six streams: activities, sleep, HRV, wellness, training readiness,
training status. It also backfills the watch-detected Lactate Threshold (the detection
history) and per-activity temperature. Raw JSON is stored first, then normalized into
core tables.

Once backfilled, the rest of the pipeline runs incrementally:

```bash
poetry run garmin-coach sync                 # pull only what's missing since each stream's watermark
poetry run garmin-coach features             # recompute daily_metrics, then roll up weekly_metrics + athlete_zones
poetry run garmin-coach report               # write reports/{date}/digest.json + 2 charts
task daily                                   # sync -> features -> alerts, no charts (nightly path)
```

A trimmed `digest.json` looks like this (headline facts, cited signals, weekly
plan-vs-actual, personal zones):

```json
{
  "window": {"from": "2026-06-09", "to": "2026-07-07", "days": 29},
  "headline": {
    "acwr": 1.21, "acwr_reliable": false, "n_chronic": 29,
    "hrv_latest": 88, "hrv_baseline": 68.0, "hrv_sd": 11.0,
    "load_7d": 812.0, "load_low_share": 0.34, "load_high_share": 0.55
  },
  "signals": [
    {"code": "AEROBIC_LOW_SHORTAGE", "severity": "warn",
     "facts": {"low_share": 0.34, "target": 0.6}}
  ],
  "weekly": {
    "week_start": "2026-06-29", "load_total": 705.0, "monotony": 1.4,
    "plan_adherence": 0.86,
    "plan_vs_actual": [
      {"dow": 0, "date": "2026-06-29", "planned": "rest",
       "actual": "rest", "match": true}
    ]
  },
  "zones": {
    "lthr_bpm": 168, "z2_hi_bpm": 150,
    "z2_pace_ceiling_s_per_km": 362.0, "source": "regression+lthr", "stale": 0
  }
}
```

Run the coach skill (`skills/coach/SKILL.md`) in Cowork against the same DB to turn
the latest `digest.json` + charts into a narrated `report.md`. Coach sessions also
keep qualitative context outside the DB: `memory/` holds the long-term athlete
profile (goals, tendencies, coaching decisions — the DB holds the numbers, this
folder holds the narrative) and `plans/` holds the weekly training plans that
Phase 5 plan-vs-actual is later checked against. Both are personal data and
gitignored.

## Automation

`garmin-coach daily` (wrapped by `scripts/daily.sh`) runs sync → features → alert
extraction unattended, exits `0`/`1`/`2` for `ok`/`degraded`/`failed`, and logs to a
rotating file (`LOG_PATH`, default `logs/daily.log`). Alerts are any digest signal with
`warn`/`alert` severity (`HRV_LOW_MORNING`, `ACWR_OUT_OF_RANGE`, `AEROBIC_LOW_SHORTAGE`,
`TWO_HARD_DAYS`, and — once a week has closed — `DELOAD_ADVISED`) logged at
`WARNING`/`ERROR`. It never renders charts or calls Garmin outside the sync stage —
those stay in the weekly `report` run.

To schedule it on macOS: copy `scripts/com.garmincoach.daily.plist.example` to
`~/Library/LaunchAgents/com.garmincoach.daily.plist`, fill in the absolute repo path
and a `PATH` that resolves `poetry` (launchd's default `PATH` is minimal), then
`launchctl load` it. Two gotchas worth knowing up front:

- **Keep the repo out of `~/Documents`, `~/Desktop`, `~/Downloads`.** macOS TCC
  blocks launchd-spawned processes from those folders even though an interactive
  Terminal session has access; `~` itself (or e.g. `~/dev/...`) is unaffected.
- **`poetry` needs an explicit `PATH`.** launchd doesn't source your shell profile,
  so the plist's `EnvironmentVariables.PATH` must include wherever `poetry`
  actually lives (check with `which poetry`).

Scheduling is documented, not auto-installed — loading the launchd job is a step you
run yourself.

## Troubleshooting

- **First login asks for email, password, and MFA.** That happens once; OAuth tokens
  are then cached in `GARMINTOKENS` (`~/.garminconnect`) and later runs never touch
  the login endpoint.
- **HTTP 429 on login.** Garmin rate-limits repeated login attempts at the IP level.
  Do not retry in a loop — wait it out. Once tokens are cached this stops being
  possible, because resume skips login entirely.
- **"Yesterday is the last day pulled" is by design.** HRV and sleep for a date only
  land after the night, so backfill/sync always excludes today.
- **Early dates look empty.** Data before `DATA_START_DATE` is watch onboarding, not
  real training; the pipeline records those days as explicit gaps
  (`daily_wellness.has_data = 0`), not zero load.
- **Re-running backfill grows the DB.** Expected: `raw_payloads` is append-only
  (keyed by fetch time) so payloads can be reprocessed without re-hitting Garmin.
  Core tables are upserted by primary key and must not change row counts on a rerun.
- **A training day shows as `rest`.** A session done without the watch is invisible
  to the ETL and classifies as rest in plan-vs-actual — a known limitation, by
  decision.

## Development

Use the Taskfile for the normal local loop:

```bash
task check        # tests + Ruff lint + Google-style docstring check + mypy
task test         # offline tests only (fake client + fixtures)
task lint         # Ruff lint over src and tests
task docstrings   # Ruff pydocstyle check for source docstrings
task typecheck    # mypy over src
task format       # apply Ruff formatting when intentionally reformatting
task schema:check # verify docs/schema.sql matches the packaged schema
```

Poetry equivalents are still available when Task is not installed:

```bash
poetry run pytest
poetry run ruff check src tests
poetry run ruff check src --select D --ignore D100,D104,D105,D107
poetry run mypy src
```

A pre-commit hook (installed by `task install`, or `poetry run pre-commit install`)
runs the full `task check` gate before every commit and blocks it on failure. Bypass
it with `git commit --no-verify` for an intentional WIP commit, or in the poetry-less
Cowork sandbox where `task` is unavailable.

**Conventions:** code/docstrings in English, public docstrings in Google style,
commit messages in English. Tests run fully offline — the transport is injected,
and fixtures are anonymized real payloads (no PII: user IDs, names, geolocation,
device IDs stripped). Metric definitions and the phasing plan live in
[docs/PROJECT.md](docs/PROJECT.md).

## Privacy

Health data is sensitive. `.env`, `data/*.db`, `raw/`, `reports/`, `logs/`,
`memory/`, `plans/`, and Garmin tokens are gitignored — only code, `schema.sql`,
and docs are committed. `memory/` and `plans/` deserve the same care as the DB:
they hold the athlete profile, goals, coaching decisions, and weekly plans in
plain Markdown. Keep the DB, those folders, and any backups local/private.

## Disclaimer

This is a personal tool for one athlete. Its metrics, signals, and reports are a
reading of wearable data, not medical advice or professional coaching — sanity-check
anything it suggests against how you actually feel, and see a professional for
health concerns.

## Credits

Built with [Claude Code](https://claude.com/claude-code) (and Codex) driving the
phase workflow described in `docs/DEVELOPMENT.md` (grill the design, write a PRD, then TDD).
The agent skills used along the way are adapted from
[Matt Pocock's skills collection](https://github.com/mattpocock/skills).
