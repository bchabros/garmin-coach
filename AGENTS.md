# Agent guide (CLAUDE.md == AGENTS.md)

Guidance for coding agents (Claude Code, Codex) working in this repo. Read this
before making changes. This file is the thin shared core; `AGENTS.md` is its
byte-for-byte mirror (the mirror rule is in `docs/DEVELOPMENT.md`).

## What this is

Local ETL + coaching system for one athlete's Garmin Connect data. Pulls daily,
stores in SQLite as system-of-record, computes training metrics, feeds a coach
skill. Full brief and roadmap: `docs/PROJECT.md`. Per-phase PRDs: `docs/prd/`.

**Golden rule -- separate transport from intelligence.** The deterministic ETL uses
the `garminconnect` library. The metrics/coach layer only ever reads the finished
DB -- it must never call Garmin live. The `mcp__garmin__*` tools are for **ad-hoc
exploration and building test fixtures only**, never the pipeline. The repo's own
`mcp__coach__*` server is the sanctioned tool surface (reads + same-day refresh +
workout push; see ADR 0014).

**Onboarding cutoff.** This account has real data from **2026-06-08** (`data_start`);
earlier dates are onboarding -- explicit gaps, not zero training. Every metric is
interpreted against that cutoff.

## Commands

```bash
poetry install
poetry run garmin-coach backfill --from 2026-06-08   # [--to YYYY-MM-DD]
poetry run pytest        # offline: fake client + fixtures
poetry run ruff check src tests
poetry run ruff check src --select D --ignore D100,D104,D105,D107
poetry run mypy src
task check               # tests + lint + docstrings + mypy
task run FROM=2026-06-08 # local backfill; optional TO=YYYY-MM-DD
```

## Where to go next

- **Changing code** -> `docs/DEVELOPMENT.md` (workflow, module map, conventions,
  developer gotchas, testing seams, deferred backlog).
- **Operating the system** -> `docs/OPERATIONS.md` (running the pipeline, exit-code
  contract, logs, rate limits, generating a coach report).
- **Domain vocabulary** -> `docs/glossary.md` (single source of truth for terms).

## Agent skills

Per-repo config for the engineering skills, written by `/setup-matt-pocock-skills`.

### Issue tracker

New work is tracked as **GitHub issues** titled by the capability gap (spec in the
issue body, tickets as a checklist; the PR closes the issue). The phased history
under `docs/prd/` stays as-is and is never migrated.
See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context -- vocabulary in `docs/glossary.md`, decisions in `docs/adr/`.
See `docs/agents/domain.md`.

## Rules

Additional working rules live in `.claude/rules/` and are imported here so Claude Code
loads them. Codex: these apply too -- read the files directly.

@.claude/rules/code-style.md
