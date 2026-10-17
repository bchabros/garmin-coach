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

The package is layered: `core/` (`config.py` pydantic-settings, `installation.py` for
the selected athlete installation (selection, path resolution, conflict refusals and
the only database opener; ADR 0031), `db.py` connect/
bootstrap/upserts, `models.py` pure `payload dict -> row dict` normalizers +
discipline mapping, `weeks.py`, `schema.sql`) - `etl/` (`client.py` login+MFA,
endpoint->method map, the only garminconnect importer; `sync.py`
`backfill(client, conn, from_date, to_date)`) - `marts/` (mart builders:
`features.py`/`weekly.py`/`zones.py`/`overlap.py`/`periodize.py`/`snapshot.py` +
the `load.py` blend) - `coach/` (`digest.py`/`signals.py` coach digest,
`thresholds.py`, `recommend.py`, `charts.py`, `report.py`) - `workouts/`
(`author.py`/`exercises.py`/`hardness.py`, `push.py` for the complete dated push,
`publish.py` for account policy and the only Garmin write) - `mcp/`
(`server.py`/`tools.py`/`probe.py` for bounded local stdio diagnosis)
- top-level `cli.py` (argparse), `daily.py` (nightly orchestrator), `project.py`
  (guarded local coach launch, configuration, diagnostics and synthetic fixtures), `plan_changes.py`
  (the complete plan import outcome shared by CLI, MCP and the nightly run), and
  `retention.py` (manual report-file retention, with no DB or transport access).

`onboarding.py` owns interactive `setup`: atomic creation from packaged empty templates,
existing-installation adoption, confirmed terminal login, resumable initial backfill and
the first local digest. Its checkpoint is separate from nightly sync watermarks; the ETL
calls back only after all daily streams commit. Templates under `templates/` contain no
athlete observations. The schema seeds policy defaults and reference mappings only;
existing personal HRV overrides and weekly templates are preserved in place.

`clients.py` prepares local Work/Codex and Claude Code plugins from the canonical
coach resources carried by the Python distribution. Generated local marketplaces
and atomic registration receipts belong to the managed runtime, outside athlete
records; their server commands name the installation explicitly. Test its CLI and
generated packages on temporary installations with fake client executables, and
verify stdio reads from a copied cache. Fresh model acceptance is a separate manual
check documented in `docs/CLIENT_SETUP.md`.

`distribution.py` is standard-library-only: release tooling ships it as the standalone
bootstrap before application dependencies exist. It owns verified versioned runtime
preparation, stable launchers and
opt-in scheduling. `lifecycle.py` consumes those ownership receipts for diagnosis,
backup, staged update/rollback and removal. Durable database reads and SQLite backup
remain in `core/installation.py`; maintenance refuses incompatible schemas and
personal storage overlapping the program. See `docs/LIFECYCLE.md` for operator
recovery and the separate native/client acceptance checks.

`scripts/coach_project.py` selects the source checkout's Poetry interpreter before
entering `project.py`, including from GUI clients or an unrelated working directory.
The guarded launch rejects conflicting database/plan environment paths and uses
`core.db.validate_schema()` to compare the existing installation's tables, views,
columns, primary keys, index/view/trigger definitions and required policy keys with
the packaged schema in memory; installed policy values remain authoritative. It never migrates
the installation; recovery uses the explicit offline `garmin-coach features` command.

`plan_changes.import_plans()` owns the plan import outcome and its consequences:
accepted weeks, the first file error, conflicts with pushed workouts, and immediate
mart refresh. Only `daily` defers that refresh until after sync, using its existing
single complete mart pass. The core parser remains independent of marts and transport.

Coach thresholds are read directly through `coach.thresholds.read()`. The report
loads charts only inside `generate_report()`, so CLI, daily, and MCP startup does
not initialize Matplotlib even when the CLI imports the report module.

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
- Test onboarding through `cli.main` on temporary installations
  (`tests/test_onboarding.py`): explicit history/template choices, empty observations,
  adopted data and file preservation, terminal login/MFA, failures and resumption. The
  shared `conn` fixture represents an existing athlete with an explicitly agreed weekly
  template; fresh-install tests bootstrap their own unseeded databases.
- Test client generation through its public CLI on temporary HOME and athlete/runtime
  roots (`tests/test_clients.py`), using fake executables with realistic native inventory
  JSON and interruption points. A copied plugin cache must bind its explicit installation
  through real stdio; client acceptance remains manual.
- Test bootstrap through its standard-library API and rendered installer shell
  (`tests/test_distribution.py`): checksum failures, interrupted sibling builds,
  activation journals, synthetic first setup, native command failures and resumable jobs.
- Test maintenance through public CLI commands (`tests/test_lifecycle*.py`): verified
  ownership, backups including WAL, interrupted updates, rollback, non-mutating identical
  updates and preview/confirmed removal. Native subprocesses are faked; the doctor stdio
  test reads a temporary database through a real isolated server without login.
- Test persistence through `core/db.py` helpers and observable SQLite state.
- Test project setup through `scripts/coach_project.py` and real stdio MCP on
  temporary installations (`tests/test_coach_project.py`): selected configuration,
  environment-path conflicts, schema compatibility and preservation, client config
  round-trips, and actionable prerequisite failures. Inherited operational settings
  are removed; conflict tests inject only explicit temporary installation paths.
- Test installation selection at the entry points on temporary installations: the
  CLI in-process through `cli.main` (`tests/test_installation.py`), the coach server as
  real stdio subprocesses started from an unrelated folder
  (`tests/test_installation_server.py`), and the built package installed into a fresh
  environment with borrowed dependencies (`tests/test_package.py`, skipped without
  Poetry). An autouse guard in `tests/conftest.py` scrubs the selection and path
  variables, isolates HOME/XDG directories and starts every test in an empty folder, so no test can fall back to the
  owner's checkout; a test that needs settings selects `athlete_home`.
- Test orchestration through `etl/sync.py` with an injected fake Garmin client.
- Test the complete workout push through `workouts.push.push_for_date` with real
  SQLite, temporary specs/receipts, and a factory returning `FakePublisher`. CLI and
  MCP only adapt confirmation and output; date and token refusals precede connection.
- Test plan import consequences through CLI/MCP and `daily.run_daily`, with real
  SQLite and temporary plans/receipts. Partial imports retain accepted weeks, and
  nightly conflicts warn without another mart pass or an outbound account call.
- Test the mart builders (`marts/features.py`, `marts/weekly.py`, `marts/zones.py`)
  and the digest builder (`build_digest`/`coach/digest.py`) at the DB boundary.
- Keep real Garmin transport (the endpoint map in `etl/client.py`) outside unit tests --
  validated by a live run, not unit tests.
- Test login (`etl/client.login_api`) offline with the library's `Garmin` class, the
  terminal check and `time.sleep` replaced (`tests/etl/test_client.py`): the prompt gate,
  the saved-login retries and their log lines. The stand-ins must mimic what the real
  library does to errors and logging (it wraps the two-step prompt's exception and logs
  its refresh failures on a child logger), or the test passes against behaviour that
  never happens.
- Test the coach skill as a document contract (`tests/test_coach_skill_routing.py`): the
  router's routing gates against the files in `skills/coach/references/`, and its
  frontmatter description against the trigger phrases that must keep working. Whether the
  model obeys a gate is not testable here -- that is the manual smoke after each upload.
- Test the profile rail at the same seam (`tests/test_coach_skill_profile.py`): the
  router's `## The athlete profile` section against the path and the date line it has to
  name, and -- where the gitignored profile exists -- that file against the same date-line
  contract. It skips wherever the profile is absent, so CI and a fresh clone stay green.
- Test report retention through `cli.main` on temporary dated folders
  (`tests/test_report_retention.py`): preview, confirmed deletion, preserved records,
  age boundaries, symlink exclusion and filesystem failure exit codes. Only filesystem
  failures are faked; fixtures include the required existing temporary database, which
  pruning never opens. Missing-database refusals preserve reports through the installation
  CLI seam. No Garmin transport is used.

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

### First-run seed audit (issue #84)

The packaged schema and `coach.thresholds.DEFAULTS` were checked separately from athlete
observations. Fresh databases no longer receive `hrv_baseline_ms=68`, `hrv_sd_ms=11` or
the original weekly template. Existing rows remain historical overrides. `data_start`
and the optional weekly shape are explicit first-run choices, including a manual `.env`
without a database. Empty profile templates contain no athlete facts.

The retained keys below are configurable model policy, not observations about a new
athlete. Their initial calibration does not establish validation for another athlete or
population; derived loads and signals remain estimates from the documented model.

| Retained keys | Reason and provenance |
|---|---|
| `hrv_low_k_sd`, `hrv_sleep_r_min`, `hrv_sleep_min_pairs` | Signal sensitivity and evidence gates; the baseline and SD themselves must come from the athlete's data (ADR 0003). |
| `acwr_risk_low`, `acwr_sweet_hi`, `acwr_risk_high`, `acwr_min_chronic_days` | Configurable load-ratio policy and minimum history, not measured individual risk limits (ADRs 0002/0003). |
| `hard_te_load`, `hard_rpe`, `monotony_high` | Model classification thresholds. ADR 0010 scales strength load to the existing 150-unit currency; it does not make 150 a personal biometric. |
| `aero_low_target_share` | Fallback when Garmin publishes no lower bound; not an observed aerobic share (ADR 0027). |
| `deload_load_rise_weeks`, `deload_min_history_weeks`, `deload_drop_pct` | Retrospective signal policy and history guards (ADR 0005). |
| `z1_hi_pct_lthr`, `z2_hi_pct_lthr`, `z3_hi_pct_lthr`, `z4_hi_pct_lthr`, `z2_pace_fallback_mult` | Formula coefficients applied to the athlete's own LTHR/pace; without an anchor the bands stay unknown (ADR 0007). |
| `zones_regression_min_runs`, `zones_regression_min_r2`, `zones_heat_temp_c`, `zones_stale_days` | Fit-quality, heat and age gates; no pre-seeded athlete anchor or regression (ADR 0007). |
| `snapshot_vo2max_lookback_days`, `snapshot_weight_lookback_days`, `snapshot_hrv_lookback_days`, `snapshot_trend_min_span_days` | Observation windows and minimum measurable trend span (ADR 0009). |
| `srpe_load_scale`, `sila_default_rpe` | Provisional 0.3 scale and RPE 7 fallback, initially calibrated on the owner's strength sessions; estimates never inserted as reported RPE (ADR 0010). |
| `niggle_active_days`, `niggle_reduced_mode_severity` | Duration and response policy; no injury or pain score is pre-seeded (ADR 0010). |
| `pattern_load_floor`, `pattern_overlap_high` | Provisional 20/40 overlap cutoffs, explicitly placeholders rather than athlete measurements (ADR 0011). |
| `taper_weeks`, `peak_weeks`, `build_weeks`, `deload_every_n_weeks`, `race_proximity_weeks`, `replan_missed_sessions` | Planning defaults, not a seeded race date or an agreed training plan (ADRs 0012/0015). |

The shared exercise-pattern mapping is reference vocabulary. The separate cardio split
weights from ADR 0027 were fitted on the original athlete and remain model coefficients,
not another athlete's observed distribution; issue #84 does not claim general validation
or retune them. This distinguishes provenance from personal-data leakage without silently
changing the established training model.

### Implementation gotchas

- **garminconnect 0.3.6 token API:** persist tokens by calling `api.login(tokenstore)`
  -- it auto-dumps via `api.client.dump(path)`. There is **no** `api.garth` attribute.
- **Shape drift after onboarding:** fields that are `null` during onboarding can become
  objects later (e.g. `hrvSummary.baseline` becomes a band `{balancedLow, ...}`). Test
  normalizers against **both** onboarding and post-onboarding fixtures.
- **Device-keyed maps** in training-status payloads: pick the single device value, never
  hardcode a device ID.
- **Agent notes are not in the repo.** Claude Code's per-project memory lives under
  `~/.claude/projects/<key>/memory/`, and a worktree session gets a different `<key>` from
  the main checkout. `scripts/agent_memory.py` always resolves the main checkout's notes, and
  its tests never touch the real `~/.claude`. Inside a git hook, `GIT_DIR` / `GIT_INDEX_FILE`
  are exported: any test that shells out to `git` in a temp repo must drop `GIT_*` from the
  environment, or it acts on this repo. The monthly pass is in
  `docs/agents/consolidation-pass.md`.

## Deferred / TODO

Not-yet-done work carried forward. The finished build is recorded in
`docs/PROJECT.md` plus each feature's PRD (`docs/prd/`) and ADR (`docs/adr/`); new
work is tracked as GitHub issues (`docs/agents/issue-tracker.md`).

- Multi-sport / `discipline` weighting in weekly rollups (deferred from Phase 5, BUILD
  section 12).
- VO2max / threshold **trend charts** (deferred from Phase 5, BUILD section 12).
- PDF / Notion export (deferred from Phase 5, BUILD section 12).
