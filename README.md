# garmin-coach

Local Garmin Connect ETL and coaching for one athlete. The deterministic pipeline
stores observations in SQLite, recomputes training metrics and exposes compact facts
through the coach MCP server. The coach skill interprets those facts in conversation.

**Separate transport from intelligence.** ETL uses `garminconnect`; metrics and coach
read the finished DB. Ad-hoc Garmin tools are for exploration and fixtures, never the
pipeline. Explicit refresh, repair and workout operations reuse guarded transport paths.

## First run for a new athlete (macOS)

Read [release availability](docs/DISTRIBUTION.md) before using the installer. Published
v0.1.0 has confirmed retry/ownership bugs tracked in [#97](https://github.com/bchabros/garmin-coach/issues/97).
Corrective source is version 0.1.1; wait for its checked versioned package before
new-athlete installer onboarding. Native package tests do not establish clean-Mac or
fresh-client acceptance.

For a source checkout, use Python 3.13 and Poetry:

```sh
poetry install
poetry run garmin-coach --home ~/GarminCoach setup
```

Setup previews the destination, asks for the athlete's own history start and optional
weekly template, then separately confirms Garmin login/MFA and import through yesterday.
Existing installations are adopted without replacing personal state; interrupted
imports resume. `--configure-only` stops before login; `--to YYYY-MM-DD` bounds history.
See [guided setup](docs/OPERATIONS.md#guided-first-run-setup-macos).

An **athlete installation** holds `.env`, database, plans, reports, profile and saved
login. A **managed runtime** holds only the program and public coach resources. Updates
and removal preserve athlete records. The owner's source checkout also serves as their
installation; development tests always use separate temporary installations.

## Guides

| Task | Guide |
| --- | --- |
| Change code, testing seams and conventions | [DEVELOPMENT](docs/DEVELOPMENT.md) |
| Pipeline, data selection, exit codes, reports and recovery | [OPERATIONS](docs/OPERATIONS.md) |
| Native release install and opt-in daily scheduling | [DISTRIBUTION](docs/DISTRIBUTION.md) |
| Work, Codex and Claude Code setup/acceptance | [CLIENT_SETUP](docs/CLIENT_SETUP.md) |
| Diagnose, back up, update or remove the program | [LIFECYCLE](docs/LIFECYCLE.md) |
| Vocabulary and design decisions | [Glossary](docs/glossary.md), [ADRs](docs/adr/README.md) |
| Coaching flows and confirmation gates | [Coach router](skills/coach/SKILL.md) |

## How work is tracked

[GitHub issues](https://github.com/bchabros/garmin-coach/issues) hold the spec and ticket
checklist; closing PRs squash-merge. [Tracker conventions](docs/agents/issue-tracker.md)
cover maps, triage and acceptance evidence. Earlier phased specifications and surveys
remain available in Git history. Race pacing (#13) was closed as not planned; strength/HIIT authoring (#16) is
delivered. Neither is open backlog.

## Layout

```text
src/garmin_coach/
  core/              installation/config, schema, normalizers, persistence, plans/events
  etl/               Garmin login/adapter and injected sync orchestration
  marts/             features, load, weekly, zones, periodize, overlap, snapshot
  coach/             digest, signals, thresholds, recommendation, charts, reports
  workouts/          authoring, exercises, hardness, dated push, account publisher
  mcp/               stdio server, tool adapters and bounded protocol probe
  cli.py             command adapters
  daily.py           nightly plan import -> sync -> features -> alerts
  plan_changes.py    shared plan-import consequences
  onboarding.py      terminal setup/adoption and resumable initial import
  project.py         guarded source launcher and synthetic client fixture
  clients.py         native plugin preparation, registration and receipts
  coach_resources.py canonical public-resource manifest
  distribution.py    standalone bootstrap, runtime activation and opt-in scheduler
  lifecycle.py       diagnosis, backups, staged updates and owned removal
  retention.py       explicit report-file pruning
skills/coach/        canonical router and public flow references
docs/                current guides, vocabulary and decision records
scripts/             source launcher, legacy daily job, packaging and release helpers
tests/               offline tests and anonymized payload/golden fixtures
CLAUDE.md            thin shared agent guide; AGENTS.md is its exact mirror
```

Data is **raw** (append-only captured payloads) -> **core** (normalized observations
and explicit manual logs) -> **mart** (recomputed daily/weekly metrics and standing).
The packaged `src/garmin_coach/core/schema.sql` is the sole schema source. Plans and
profile prose are personal records, not disposable mart output.

## Common commands

Select an installation explicitly with `--home` before the subcommand, or start inside
its `.env` folder. Relative configuration paths resolve against that installation;
conflicting exported path/account settings are refused (ADR 0031).

```sh
poetry run garmin-coach --home ~/GarminCoach sync
poetry run garmin-coach --home ~/GarminCoach features
poetry run garmin-coach --home ~/GarminCoach snapshot
poetry run garmin-coach --home ~/GarminCoach report
poetry run garmin-coach --home ~/GarminCoach refresh-today
poetry run garmin-coach --home ~/GarminCoach daily
```

`sync` and `backfill` import core; run `features` afterwards to rebuild marts.
`daily` performs the complete chain. Today is partial and requires explicit refresh;
nightly sync rechecks recent days. Reports write deterministic artifacts; the coach
writes the narrative. For authoring, push, plan imports, subjective logs and retention,
use the runbook and coach references rather than copying old request examples.

`daily` exits 0/1/2 for clean/degraded/failed and rotates its log. It renders no charts
and never pushes workouts. Managed scheduling is optional and previewed; the legacy
`scripts/daily.sh` and `.plist.example` remain supported. On macOS, use an explicit
runtime PATH and keep unattended source jobs outside TCC-protected Desktop/Documents/
Downloads folders. See the runbook for rate limits, token renewal and missing data.

## Development

```sh
task install      # dependencies plus pre-commit hook
task format       # apply Ruff formatting
task check        # offline tests, lint, Google docstrings, mypy
```

Without Task: `poetry run pytest`, `poetry run ruff check src tests`,
`poetry run ruff check src --select D --ignore D100,D104,D105,D107`, and
`poetry run mypy src`. The hook blocks commits if the full check fails.
Use Poetry's lock, English code/docstrings and the established seams in DEVELOPMENT.
Fixtures are anonymized; test transport is fake. Native acceptance is separately
recorded, never inferred from unit tests.

## Privacy and disclaimer

`.env`, databases, Garmin tokens, logs, reports, plans, athlete memory and backups are
private/gitignored. Keep them local. Only program files, public guidance, shared
reference data and anonymized fixtures belong in source control.

This personal tool interprets wearable data; it does not provide medical advice or
professional coaching. Assess suggestions against how you feel and seek qualified
help for health concerns.

## Credits

Built with Claude Code and Codex. The issue/design/TDD workflow adapts
[Matt Pocock's skills](https://github.com/mattpocock/skills).
