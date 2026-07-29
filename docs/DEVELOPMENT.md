# Development guide

For coding agents (Claude Code, Codex) and humans **changing the code**. The
always-loaded core is `CLAUDE.md`; this guide is read on demand when you touch the
implementation. Operating the system instead? See `docs/OPERATIONS.md`. Term
definitions live in `docs/glossary.md`.

## Workflow

Work is tracked as GitHub issues (see `docs/agents/issue-tracker.md`); each issue
carries its Definition of Done in the spec -- don't close it until that's met. The
established loop for a new feature is a chain of skills:

**`/grill-with-docs` -> `/to-spec` -> `/to-tickets` -> `/implement` -> `/code-review`**

- **`/grill-with-docs`** -- stress-test the decisions against the repo's docs before any
  code (reaches `/domain-modeling` for vocabulary; see `docs/agents/domain.md`). For
  very complex tasks use `/wayfinder` instead -- a heavier, map-driven alternative
  (`docs/prd/<feature>/map.md`; see the wayfinding section in
  `docs/agents/issue-tracker.md`).
- **`/to-spec`** -- publish the spec as a GitHub issue (title = the capability gap,
  body = problem/solution/decisions; see `docs/agents/issue-tracker.md`).
- **`/to-tickets`** -- break the spec into the issue's task-list checklist
  (see `docs/agents/issue-tracker.md`).
- **`/implement`** -- build a ticket test-first (TDD, red -> green).
- **`/code-review`** -- review the branch before merge.

- Work test-first. Tests live at agreed **seams** (see below).
- One vertical slice at a time: one test -> minimal impl -> repeat. No bulk test-first.
- Before committing a change: `task check` (or `poetry run pytest && poetry run ruff
  check src tests && poetry run mypy src` if Task is unavailable).

### Pre-commit hook

A pre-commit hook enforces the `task check` gate on every commit. Setup is one-time:
`task install` runs `poetry run pre-commit install`, which writes the hook to
`.git/hooks/`. On each `git commit`, the hook runs the full gate (lint -> docstrings ->
typecheck -> test); a failing check blocks the commit.

To bypass it -- for an intentional WIP commit, or in the poetry-less Cowork/Linux
sandbox where `poetry`/`task` are not on PATH (see `docs/OPERATIONS.md`) -- use
`git commit --no-verify`, or skip a single slow hook with e.g. `SKIP=test git commit`.

## Architecture

The package is layered: `core/` (`config.py` pydantic-settings, `db.py` connect/
bootstrap/upserts, `models.py` pure `payload dict -> row dict` normalizers +
discipline mapping, `weeks.py`, `schema.sql`) - `etl/` (`client.py` login+MFA,
endpoint->method map, the only garminconnect importer; `sync.py`
`backfill(client, conn, from_date, to_date)`) - `marts/` (mart builders:
`features.py`/`weekly.py`/`zones.py`/`overlap.py`/`periodize.py`/`snapshot.py` +
the `load.py` blend) - `coach/` (`digest.py`/`signals.py` coach digest,
`thresholds.py`, `recommend.py`, `charts.py`, `report.py`) - `workouts/`
(`author.py`/`exercises.py`/`publish.py`, the only Garmin write) - `mcp/`
(`server.py`/`tools.py`)
- top-level `cli.py` (argparse) and `daily.py` (nightly orchestrator).

Data is medallion: **raw** `raw_payloads` (append-only, never overwrite -- reprocess
without re-hitting Garmin) -> **core** (normalized, upserted by PK) -> **mart**
`daily_metrics`/`weekly_metrics`/`athlete_zones` (recomputed, never edited). Derived values
live only in marts/views, never mixed into core. Full vocabulary in `docs/glossary.md`.

**Mart transactions.** `marts.features.features()` owns both the materialization order
and the transaction: one pass is one commit, and any failure rolls the whole pass back.
The individual `rollup()` functions therefore do **not** commit -- if you call one
standalone (a test, a new entry point), you own the commit.

## Testing seams

The agreed boundaries a test exercises -- prefer the highest existing seam, add new
ones sparingly:

- Test normalizers through pure model functions (`core/models.py`).
- Test persistence through `core/db.py` helpers and observable SQLite state.
- Test orchestration through `etl/sync.py` with an injected fake Garmin client.
- Test the mart builders (`marts/features.py`, `marts/weekly.py`, `marts/zones.py`)
  and the digest builder (`build_digest`/`coach/digest.py`) at the DB boundary.
- Keep real Garmin transport and auth (`etl/client.py`, `cli.py`) outside unit tests --
  validated by a live run, not unit tests.
- Test the coach skill as a document contract (`tests/test_coach_skill_routing.py`): the
  router's routing gates against the files in `skills/coach/references/`, and its
  frontmatter description against the trigger phrases that must keep working. Whether the
  model obeys a gate is not testable here -- that is the manual smoke after each upload.
- Test the profile rail at the same seam (`tests/test_coach_skill_profile.py`): the
  router's `## The athlete profile` section against the path and the date line it has to
  name, and -- where the gitignored profile exists -- that file against the same date-line
  contract. It skips wherever the profile is absent, so CI and a fresh clone stay green.

## Conventions

- **Poetry**, not `uv`/`pip`, for all dependency work (despite what the BUILD doc says).
- Python 3.13. Code and docstrings in **English**; commit messages in English.
- New and changed public docstrings use **Google-style docstrings**.
- Schema source of truth is the package copy `src/garmin_coach/core/schema.sql`, loaded via
  `importlib.resources`. `docs/schema.sql` is a snapshot kept identical by
  `tests/test_schema_sync.py` -- edit the package copy, then re-sync docs.
- `AGENTS.md` is a byte-for-byte mirror of `CLAUDE.md`, guarded by
  `tests/test_agents_mirror.py` -- edit `CLAUDE.md`, then copy it over `AGENTS.md`.
- Normalizers must be pure and total: missing fields -> `None`, never raise. All values
  a normalizer emits must be **scalars** (SQLite can't bind dict/list).
- Fixtures are anonymized real payloads. Strip PII: `userProfilePK`/`ownerId`,
  `ownerFullName`, lat/lon, `deviceId`, UUIDs, image URLs. Trim per-minute time series.
- Additional working rules in `.claude/rules/` (no-emoji, code-style, plain-language)
  apply to all changes. The whole directory is auto-loaded in Claude Code, so a new rule
  file needs no `@` import in `CLAUDE.md`; Codex reads the files directly.

## Gotchas (developer, learned the hard way)

Operational gotchas (rate limits, backfill window, idempotency) live in
`docs/OPERATIONS.md`. These are implementation gotchas:

- **garminconnect 0.3.6 token API:** persist tokens by calling `api.login(tokenstore)`
  -- it auto-dumps via `api.client.dump(path)`. There is **no** `api.garth` attribute.
- **Shape drift after onboarding:** fields that are `null` during onboarding can become
  objects later (e.g. `hrvSummary.baseline` becomes a band `{balancedLow, ...}`). Test
  normalizers against **both** onboarding and post-onboarding fixtures.
- **Device-keyed maps** in training-status payloads: pick the single device value, never
  hardcode a device ID.

## Deferred / TODO

Not-yet-done work carried forward. The finished build is recorded in
`docs/PROJECT.md` plus each feature's PRD (`docs/prd/`) and ADR (`docs/adr/`); new
work is tracked as GitHub issues (`docs/agents/issue-tracker.md`).

- `activity_sets` (per-set Hyrox/strength via `get_activity_exercise_sets`) -- committed
  in the Phase 0 PRD (D9) but not yet implemented.
- Plan divergence on the **nightly** path: `daily`'s plans stage imports plan files but
  does not check them against already-pushed workouts, so a revision landing overnight
  surfaces only on the next `plan import` or `get_workout_status` (issue #22, ADR 0021).
- Multi-sport / `discipline` weighting in weekly rollups (deferred from Phase 5, BUILD
  section 12).
- VO2max / threshold **trend charts** (deferred from Phase 5, BUILD section 12).
- PDF / Notion export (deferred from Phase 5, BUILD section 12).
