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
| **0** | Raw capture + idempotent backfill | ✅ **Done** — see [docs/prd/phase-0.md](docs/prd/phase-0.md) |
| 1 | Incremental sync + resilience (watermark, retry, per-day fallback) | ✅ Implemented offline; live validation pending |
| 2 | Metrics mart (`features.py` → `daily_metrics`) | Planned |
| 3 | Coach skill (report + charts) | Planned |
| 4 | Automation (cron/launchd, alerts) | Planned |
| 5 | Plan-vs-actual, trends, multi-sport | Planned |

## Layout

```
garmin-coach/
├── pyproject.toml            # Poetry: deps, scripts, tool config
├── Taskfile.yml              # task shortcuts for tests, lint, checks, backfill
├── AGENTS.md                 # Codex/agent working rules
├── CLAUDE.md                 # Claude Code working rules
├── .codex/                   # Codex local notes and companion files
├── .env.example             # copy to .env (GARMIN_EMAIL, DATA_START_DATE, DB_PATH, ...)
├── docs/
│   ├── garmin-coach-BUILD.md # the executable brief (phases 0–5, metric specs)
│   ├── schema.sql            # DB schema snapshot (source of truth: the package copy)
│   ├── coach-skill.md        # coach skill spec (phase 3)
│   └── prd/phase-0.md        # Phase 0 PRD: decisions, spec, DoD, test plan
├── src/garmin_coach/
│   ├── config.py             # pydantic-settings: .env + paths
│   ├── client.py             # transport: login (+MFA), garminconnect method map
│   ├── db.py                 # connect, schema bootstrap, idempotent upserts
│   ├── models.py             # pure normalizers payload→row + discipline mapping
│   ├── sync.py               # backfill: raw-first, upsert core, excludes "today"
│   ├── cli.py                # argparse entry point (`garmin-coach backfill`)
│   └── schema.sql            # runtime schema (loaded via importlib.resources)
├── tests/
│   ├── conftest.py           # in-memory DB + FakeGarminClient + fixture loader
│   ├── fixtures/             # anonymized real Garmin payloads
│   └── test_*.py             # models, db, sync, config, schema-sync guard
└── data/garmin.db            # SQLite system-of-record (gitignored)
```

Data layout is medallion: **raw** (`raw_payloads`, append-only) → **core** (normalized,
upserted) → **mart** (`daily_metrics`, recomputed; phase 2+).

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

Health data is sensitive. `.env`, `data/*.db`, `raw/`, `reports/`, and Garmin tokens
are gitignored — only code, `schema.sql`, and docs are committed. Keep the DB and
backups local/private.
