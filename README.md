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
[docs/PROJECT.md](docs/PROJECT.md) for the full brief — the phased build history
that got the system here. New work is tracked as GitHub issues.

## How work is tracked

New work lives as [GitHub issues](https://github.com/bchabros/garmin-coach/issues),
titled by the capability gap they close: the spec sits in the issue body, tickets
are a task-list checklist, and the closing PR squash-merges. Conventions in
[docs/agents/issue-tracker.md](docs/agents/issue-tracker.md); architecture
decisions still land in [docs/adr/](docs/adr/).

The system was originally built as a phased roadmap (phases 0-11: raw capture,
resilient sync, metrics marts, the coach skill, automation, weekly rollups,
personal zones, the athlete snapshot, strength load, movement overlap,
periodization, the recommender, and workout push), capped by the coach MCP server
([ADR 0014](docs/adr/0014-coach-mcp-server.md)). That history is preserved as-is:
the brief and per-phase sections in [docs/PROJECT.md](docs/PROJECT.md), per-phase
PRDs in [docs/prd/](docs/prd/), and decisions in [docs/adr/](docs/adr/). Known
open threads already live as issues — race-day pacing
([#13](https://github.com/bchabros/garmin-coach/issues/13)) and strength/HIIT
authoring + push ([#16](https://github.com/bchabros/garmin-coach/issues/16),
spike [findings](docs/prd/phase-11-workout-push/strength-spike-findings.md)).

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
│   ├── PROJECT.md            # build history: the phased brief (0–11), kept as-is (historical)
│   ├── architecture-roadmap.md # architecture review after the weekly rollups (historical)
│   ├── DEVELOPMENT.md        # coding guide: workflow, module map, conventions, seams
│   ├── OPERATIONS.md         # operator runbook: pipeline, exit codes, logs, reports
│   ├── schema.sql            # DB schema snapshot (source of truth: the package copy)
│   ├── glossary.md           # domain vocabulary (single source of truth)
│   ├── adr/                  # decision records (living — the tracker holds work, the repo holds decisions)
│   └── prd/                  # historical per-phase PRDs (frozen; new specs live in GitHub issues)
├── scripts/
│   ├── daily.sh              # thin cron/launchd entrypoint: execs `garmin-coach daily`
│   └── com.garmincoach.daily.plist.example  # launchd schedule example (macOS)
├── skills/coach/             # narrative layer: SKILL.md router + references/ per flow
├── src/garmin_coach/
│   ├── core/                 # shared foundation
│   │   ├── config.py         # pydantic-settings: .env + paths + logging config
│   │   ├── db.py             # connect, schema bootstrap, idempotent upserts
│   │   ├── models.py         # pure normalizers payload→row + discipline mapping
│   │   ├── weeks.py          # leaf: Monday-anchored week arithmetic
│   │   └── schema.sql        # runtime schema (loaded via importlib.resources)
│   ├── etl/                  # read transport (the only garminconnect importer)
│   │   ├── client.py         # login (+MFA), garminconnect method map
│   │   └── sync.py           # backfill + incremental sync: raw-first, upsert core,
│   │                          #   retry/backoff, per-day fallback, isolated streams
│   ├── marts/                # recomputable mart builders over core tables
│   │   ├── features.py       # daily_metrics (HRV baseline/SD, ACWR, load buckets)
│   │   ├── load.py           # per-activity sRPE load blend (strength/Hyrox) shared by marts
│   │   ├── weekly.py         # weekly_metrics + weekly_plan_actual (run by features)
│   │   ├── zones.py          # athlete_zones (LTHR anchor → %LTHR HR bands + Z2 pace ceiling)
│   │   ├── overlap.py        # pattern_overlap (same movement pattern/muscle on adjacent days)
│   │   ├── periodize.py      # plan_block (training blocks counted back from the goal race)
│   │   └── snapshot.py       # athlete_status (the single current-standing snapshot)
│   ├── coach/                # coach intelligence: reads the finished marts only
│   │   ├── digest.py         # headline + coach signals + weekly/zones/plan sections (build_digest)
│   │   ├── signals.py        # pure signal rules invoked by digest.py (incl. DELOAD_ADVISED, TAPER_ACTIVE)
│   │   ├── thresholds.py     # coach threshold policy: defaults + DB overrides
│   │   ├── recommend.py      # tomorrow's session recommendation composed from the digest
│   │   ├── charts.py         # HRV band + ACWR matplotlib charts
│   │   └── report.py         # orchestrates digest + charts → reports/{date}/
│   ├── workouts/             # structured workout authoring + push (the only Garmin write)
│   │   ├── author.py         # workout request → deterministic workout spec (pure)
│   │   └── publish.py        # idempotent push orchestration behind the confirm interlock
│   ├── mcp/                  # coach MCP server (sanctioned tool surface, ADR 0014)
│   │   ├── server.py         # FastMCP wiring: tools, confirm interlock, live client
│   │   └── tools.py          # one-call read/refresh/push tool implementations
│   ├── daily.py              # nightly orchestrator: sync → features → alerts
│   └── cli.py                # argparse entry point (backfill/sync/features/report/snapshot/event/log-rpe/daily)
├── tests/                    # mirrors the src packages (core/, etl/, marts/, coach/, workouts/, mcp/)
│   ├── conftest.py           # in-memory DB + FakeGarminClient + fixture loader
│   ├── fixtures/             # anonymized real Garmin payloads + golden fixtures
│   └── <pkg>/test_*.py       # one module per seam; cross-cutting guards at the top
│                              #   (cli, daily, refresh, agents-mirror, schema-sync)
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
  easy`). Weekly plan-vs-actual and `plan_adherence` are measured against
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

Run the coach skill (`skills/coach/`) in Cowork against the same DB to turn
the latest `digest.json` + charts into a narrated `report.md`. Coach sessions also
keep qualitative context outside the DB: `memory/` holds the long-term athlete
profile (goals, tendencies, coaching decisions — the DB holds the numbers, this
folder holds the narrative) and `plans/` holds the weekly training plans that
weekly plan-vs-actual is later checked against. Both are personal data and
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
feature workflow described in `docs/DEVELOPMENT.md` (grill the design, spec the issue, then TDD).
The agent skills used along the way are adapted from
[Matt Pocock's skills collection](https://github.com/mattpocock/skills).
