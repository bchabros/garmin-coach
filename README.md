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
[docs/garmin-coach-BUILD.md](docs/garmin-coach-BUILD.md) for the full brief.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 0 | Raw capture + idempotent backfill | ✅ Done — [docs/prd/phase-0.md](docs/prd/phase-0.md) |
| 1 | Incremental sync + resilience (watermark, retry, per-day fallback) | ✅ Done — [docs/prd/phase-1.md](docs/prd/phase-1.md), [ADR 0001](docs/adr/0001-phase-1-incremental-sync.md) |
| 2 | Metrics mart (`features.py` → `daily_metrics`) | ✅ Done — [docs/prd/phase-2.md](docs/prd/phase-2.md), [ADR 0002](docs/adr/0002-phase-2-metrics-semantics.md) |
| 3 | Coach skill (digest + charts + `report.md`) | ✅ Done — [docs/prd/phase-3.md](docs/prd/phase-3.md), [ADR 0003](docs/adr/0003-phase-3-coach-signals.md) |
| 4 | Automation (nightly orchestrator, alerts, launchd/cron) | ✅ Done — [docs/prd/phase-4.md](docs/prd/phase-4.md), [ADR 0004](docs/adr/0004-phase-4-automation.md) |
| **5** | Weekly rollups, plan-vs-actual, deload detection | ✅ **Done** — [docs/prd/phase-5.md](docs/prd/phase-5.md), [ADR 0005](docs/adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md) |

## Layout

```
garmin-coach/
├── pyproject.toml            # Poetry: deps, scripts, tool config
├── Taskfile.yml              # task shortcuts for tests, lint, checks, backfill, daily
├── AGENTS.md                 # Codex/agent working rules
├── CLAUDE.md                 # Claude Code working rules
├── .codex/                   # Codex local notes and companion files
├── .env.example              # copy to .env (GARMIN_EMAIL, DATA_START_DATE, DB_PATH, LOG_PATH, ...)
├── docs/
│   ├── garmin-coach-BUILD.md # the executable brief (phases 0–5, metric specs)
│   ├── schema.sql            # DB schema snapshot (source of truth: the package copy)
│   ├── glossary.md           # domain vocabulary
│   ├── adr/                  # decision records (0001–0005, one per phase)
│   └── prd/                  # per-phase PRDs (phase-0 .. phase-5)
├── scripts/
│   ├── daily.sh              # thin cron/launchd entrypoint: execs `garmin-coach daily`
│   └── com.garmincoach.daily.plist.example  # launchd schedule example (macOS)
├── skills/coach/SKILL.md     # narrative layer: reads digest.json, writes report.md
├── src/garmin_coach/
│   ├── config.py             # pydantic-settings: .env + paths + logging config
│   ├── client.py             # transport: login (+MFA), garminconnect method map
│   ├── db.py                 # connect, schema bootstrap, idempotent upserts
│   ├── models.py             # pure normalizers payload→row + discipline mapping
│   ├── sync.py                 # backfill + incremental sync: raw-first, upsert core,
│   │                           #   retry/backoff, per-day fallback, isolated streams
│   ├── features.py           # mart: daily_metrics (HRV baseline/SD, ACWR, load buckets)
│   ├── weekly.py             # mart: weekly_metrics rollup + plan-vs-actual (run by features)
│   ├── digest.py             # headline + coach signals + weekly section (build_digest)
│   ├── signals.py            # pure signal rules invoked by digest.py (incl. DELOAD_ADVISED)
│   ├── charts.py             # HRV band + ACWR matplotlib charts
│   ├── report.py             # orchestrates digest + charts → reports/{date}/
│   ├── daily.py              # nightly orchestrator: sync → features → alerts
│   ├── cli.py                # argparse entry point (backfill/sync/features/report/daily)
│   └── schema.sql            # runtime schema (loaded via importlib.resources)
├── tests/
│   ├── conftest.py           # in-memory DB + FakeGarminClient + fixture loader
│   ├── fixtures/             # anonymized real Garmin payloads + golden fixtures
│   └── test_*.py             # one module per seam (models, db, sync, features, weekly,
│                              #   signals, digest, daily, config, cli, schema-sync guard)
├── logs/daily.log            # rotating nightly-run log (gitignored)
├── reports/{date}/           # digest.json + charts + report.md (gitignored)
└── data/garmin.db            # SQLite system-of-record (gitignored)
```

Data layout is medallion: **raw** (`raw_payloads`, append-only) → **core** (normalized,
upserted) → **mart** (`daily_metrics`, recomputed by `features`; `weekly_metrics`,
rolled up from it by `weekly` for every *complete* week). `digest.py` reads both marts
and produces the compact digest (headline, signals, weekly plan-vs-actual) the coach
skill narrates; `daily.py` reruns the whole chain unattended and reduces the digest to
log-worthy alerts.

## Setup

Requires Python 3.13 and [Poetry](https://python-poetry.org/). [Task](https://taskfile.dev/) is optional but recommended; every task wraps the underlying Poetry command.

```bash
poetry install
cp .env.example .env      # then fill in GARMIN_EMAIL (password optional)
```

## Usage

```bash
# First run prompts for password + MFA once, then caches OAuth tokens to
# ~/.garminconnect; later runs resume from them (no login endpoint, no rate limits).
task run FROM=2026-06-08

# Optional end date for a bounded local run.
task backfill FROM=2026-06-08 TO=2026-06-30

# Poetry fallback if Task is not installed.
poetry run garmin-coach backfill --from 2026-06-08
```

`backfill` pulls `[--from .. yesterday]` (today is skipped — HRV/sleep land after the
night) across six streams: activities, sleep, HRV, wellness, training readiness,
training status. Raw JSON is stored first, then normalized into core tables.

Once backfilled, the rest of the pipeline runs incrementally:

```bash
poetry run garmin-coach sync                 # pull only what's missing since each stream's watermark
poetry run garmin-coach features             # recompute daily_metrics, then roll up weekly_metrics
poetry run garmin-coach report               # write reports/{date}/digest.json + 2 charts
task daily                                   # sync -> features -> alerts, no charts (nightly path)
```

Run the coach skill (`skills/coach/SKILL.md`) in Cowork against the same DB to turn
the latest `digest.json` + charts into a narrated `report.md`.

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

**Conventions:** code/docstrings in English, public docstrings in Google style,
commit messages in English. Tests run fully offline — the transport is injected,
and fixtures are anonymized real payloads (no PII: user IDs, names, geolocation,
device IDs stripped). Metric definitions and the phasing plan live in
[docs/garmin-coach-BUILD.md](docs/garmin-coach-BUILD.md).

## Privacy

Health data is sensitive. `.env`, `data/*.db`, `raw/`, `reports/`, `logs/`, and Garmin
tokens are gitignored — only code, `schema.sql`, and docs are committed. Keep the DB
and backups local/private.
