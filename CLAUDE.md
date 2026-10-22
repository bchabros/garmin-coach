# Agent guide (CLAUDE.md == AGENTS.md)

Guidance for coding agents (Claude Code, Codex) working in this repo. Read this
before making changes. This file is the thin shared core; `AGENTS.md` is its
byte-for-byte mirror (the mirror rule is in `docs/DEVELOPMENT.md`).

## What this is

Local ETL + coaching system for one athlete's Garmin Connect data. Pulls daily,
stores in SQLite as system-of-record, computes training metrics, feeds a coach
skill. Work is tracked as GitHub issues (see below). Architecture decisions live in
`docs/adr/`; earlier phase specifications remain available in Git history.

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

- **Coaching requests** (form, reports, training weeks, workouts) -> **MUST** read
  `skills/coach/SKILL.md` before athlete numbers or advice, then follow its routing
  gates and profile rail. Prefer the sanctioned coach MCP tools. A coaching request
  alone does not authorize source changes.
- **Development requests** -> **MUST** read `docs/DEVELOPMENT.md` before changes.
  Validate with temporary data and fake transport; keep the athlete's real installation
  out of development tests.
- **Connecting Work, Codex, or Claude Code** -> `docs/CLIENT_SETUP.md` (project setup,
  guarded launcher, fixture checks, and the required fresh Work acceptance).

- **Changing code** -> `docs/DEVELOPMENT.md` (workflow, module map, conventions,
  developer gotchas, testing seams, deferred backlog).
- **Operating the system** -> `docs/OPERATIONS.md` (running the pipeline, exit-code
  contract, logs, rate limits, generating a coach report).
- **Managed install or daily job** -> `docs/DISTRIBUTION.md`; **assistant plugins** ->
  `docs/CLIENT_SETUP.md`; **diagnosis, updates, recovery or removal** -> `docs/LIFECYCLE.md`.
- **Domain vocabulary** -> `docs/glossary.md` (single source of truth for terms).

## Agent skills

Per-repo config for the engineering skills, written by `/setup-matt-pocock-skills`.

### Issue tracker

New work is tracked as **GitHub issues** titled by the capability gap (spec in the
issue body, tickets as a checklist; the PR closes the issue). Earlier phase
specifications are retained in Git history; new maps and acceptance
evidence belong to the issue or PR.
See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`,
`ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context -- vocabulary in `docs/glossary.md`, decisions in `docs/adr/`.
See `docs/agents/domain.md`.

## Rules

Before changing repository files or preparing, updating or reviewing a PR, **MUST**
read all rule files listed below. They are shared by Codex and Claude Code despite
the directory name. Codex must open these files directly; Claude Code supports the
`@` imports. Add each new rule to this list; the shared-guidance test checks coverage.

Before the final PR review, complete the documentation cleanup in
`.claude/rules/documentation.md` and report its outcome in the PR description.

@.claude/rules/code-style.md
@.claude/rules/documentation.md
@.claude/rules/git-commits.md
@.claude/rules/plain-language.md
@.claude/rules/testing.md
