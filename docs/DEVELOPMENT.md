# Development guide

For coding agents (Claude Code, Codex) and humans **changing the code**. The
always-loaded core is `CLAUDE.md` / `AGENTS.md`; this guide is read on demand when
you touch the implementation. Operating the system instead? See `docs/OPERATIONS.md`.
Term definitions live in `docs/glossary.md`.

## Workflow

Work is tracked as GitHub issues (see `docs/agents/issue-tracker.md`); each issue
carries its Definition of Done in the spec -- don't close it until that's met. The
established loop for a new feature is a chain of skills:

**`/grill-with-docs` -> `/to-spec` -> `/to-tickets` -> `/implement` -> `/code-review`**

- **`/grill-with-docs`** -- stress-test the decisions against the repo's docs before any
  code (reaches `/domain-modeling` for vocabulary; see `docs/agents/domain.md`). For
  very complex tasks use `/wayfinder` instead -- a heavier, map-driven alternative
  (maps and investigation results in the issue; see
  `docs/agents/issue-tracker.md`).
- **`/to-spec`** -- publish the spec as a GitHub issue (title = the capability gap,
  body = problem/solution/decisions; see `docs/agents/issue-tracker.md`).
- **`/to-tickets`** -- break the spec into the issue's task-list checklist
  (see `docs/agents/issue-tracker.md`).
- **`/implement`** -- build a ticket test-first (TDD, red -> green).
- **`/code-review`** -- review the branch before merge.

Before the final PR review, follow the documentation-cleanup checklist in
[the shared documentation rules](../.claude/rules/documentation.md).

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

| Layer | Responsibility |
| --- | --- |
| `core/` | Installation selection/opener, configuration, schema, pure normalizers, persistence, plans/events and week arithmetic. |
| `etl/` | The only `garminconnect` import; login/endpoint adapter and injected raw-first sync. |
| `marts/` | Recomputable features/load, periodization, weekly facts, zones, overlap and standing. |
| `coach/` | Digest/signals/policy/recommendation over finished data; explicit report/chart generation. |
| `workouts/` | Pure author/exercises/hardness, dated-workout lifecycle and the isolated account publisher. |
| `mcp/`, `cli.py` | Conversational/command adapters; `mcp/probe.py` owns bounded protocol diagnosis. |
| `runtime.py` | Captured installation context, fresh database openers and separate lazy Garmin factories. |
| `daily.py`, `plan_changes.py`, `athlete_changes.py` | Complete nightly, plan-import and athlete-change outcomes, including their consequences. |
| `onboarding.py`, `project.py`, `synthetic.py` | Terminal setup/adoption; guarded source launch; explicit synthetic installations. |
| `clients.py`, `coach_resources.py` | Native bindings, public coach-resource selection and generation receipts. |
| `distribution.py`, `lifecycle.py`, `program_receipt.py` | Bootstrap/scheduling and maintenance, sharing program-state and ownership operations. |
| `retention.py` | Explicit report-file retention without DB/transport access. |

`onboarding.py` owns interactive `setup`: atomic creation from packaged empty templates,
existing-installation adoption, confirmed terminal login, resumable initial backfill and
the first local digest. Its checkpoint is separate from nightly sync watermarks; the ETL
calls back only after all daily streams commit. Templates under `templates/` contain no
athlete observations. The schema seeds policy defaults and reference mappings only;
existing personal HRV overrides and weekly templates are preserved in place.

`clients.py` generates installation-bound plugins from the public resource list in
`coach_resources.json`. `coach_resources.py` selects only regular non-symlink files;
private athlete context remains in the installation. Native plugins, release bundles
and legacy archives share that selection. Fresh model acceptance remains separate
from temporary-installation/fake-command and real-stdio package tests.

The wheel and source archive take their skill files from the explicit `include` list
in `pyproject.toml`, built by Poetry's standard backend. A new public reference
therefore needs a manifest entry and a matching `include`. `scripts/build_release.py`
refuses a release when the two differ or a public file is missing or symlinked, before
it runs `poetry build` (`tests/test_release_build.py`). A hand-run local `poetry build`
skips that check; no installation path uses its output.

`distribution.py` and `program_receipt.py` are standard-library-only. Release tooling
bundles their canonical sources as one executable Python archive, `bootstrap.py`, so
isolated Python can run the installer before application dependencies exist. Its entry
point propagates the installer's exit code. Tests execute the generated artifact with
`-I -S`, including receipt refusal, plus the rendered shell's real temporary install.

`program_receipt.ProgramReceipt` owns v1 program-state parsing, atomic private writes,
progress journals, scheduler identity, activation and runtime ownership checks. Its
purpose operations reload state before saving, preserving nested build/update progress;
callers treat read snapshots as evidence. `UpdateJournal` preserves the pre-versioned
v0.1.0 recovery rule and treats unknown v1 phases conservatively. Integration registration
remains owned by `clients`; `recorded_selection` reads saved choices without native probes.
`distribution` prepares/activates releases and schedules explicit jobs; `lifecycle`
orchestrates diagnosis, backup, update/recovery and removal through the shared owner.
Durable database reads and SQLite backup
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

`runtime.InstallationContext` captures selection and configuration at command/server
startup. Adapters and tool freshness use that context; `.env` edits take effect on the
next run/server restart. Context variables carry it through async tool tasks without
retaining SQLite connections, observations or account sessions. Each operation opens
its own connection; import and publisher factories receive the captured settings only
when an authorized operation needs them. The `get_settings()` compatibility facade
returns bound settings inside a run and loads configuration for standalone callers.
Maintenance/client adapters reuse the same captured selection, including equivalent
filesystem aliases; explicit saved-login diagnosis passes only its captured token path
to the isolated child, withholding account credentials.

`core.installation.inspect_database()` reports schema compatibility and independent
mart/snapshot dates; workflow owners interpret that evidence. Setup's import-completion
checkpoint can honestly describe an empty import, while guarded source launch and local
diagnosis require both saved dates. Neither adds an age or date-equality threshold.
`open_existing_database()` validates read-only or guarded writable connections without
bootstrap; the writable mode retains foreign-key enforcement.

`synthetic.create_installation()` owns synthetic settings and observations in an explicit
new or empty destination. Source fixtures add guidance and only manifest-selected public
coach resources. `mcp.probe` owns bounded inventory/digest/snapshot stdio checks and the
packaged `evidence`/`synthetic` commands. Release checks invoke the candidate's isolated
interpreter and versioned probe capability; older releases use their own supported probe
and validated storage opener only when the capability is absent, never after a failure.

`plan_changes.import_plans()` owns the plan import outcome and its consequences:
accepted weeks, the first file error, known conflicts and dated warnings for unreadable
workout evidence, and immediate mart refresh. Unreadable evidence never means no conflict.
Only `daily` defers that refresh until after sync, using its existing single complete
mart pass. The core parser remains independent of marts and transport.

`athlete_changes` owns session RPE, niggles, manual overlays and goal-event outcomes
for both adapters. It commits core facts before a separately transactional mart pass;
`saved` and `recomputed` describe independent results. A failed rebuild preserves facts
and the old mart generation, names the saved record and offers offline `features` in
the same installation. Event failures preserve pre-write block evidence and leave the
post-write block unknown, avoiding an old-block/new-event-date join (ADR 0036).

`workouts.push` alone reads/writes dated workout specs and push receipts. `publish`
receives parsed evidence and owns account rules, without filesystem or database reads.
Status must separate the push event from current reconciliation; failed evidence reads
never rewrite the artifact or establish absence.

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
  Public receipt operations (`tests/test_program_receipt.py`) verify compatibility,
  private writes, malformed-state preservation and scheduler aliases; the generated
  bootstrap is exercised without site packages (`tests/test_release_build.py`).
- Test maintenance through public CLI commands (`tests/test_lifecycle*.py`): verified
  ownership, backups including WAL, interrupted updates, rollback, non-mutating identical
  updates and preview/confirmed removal. Native subprocesses are faked; the doctor stdio
  test reads a temporary database through a real isolated server without login.
- Test persistence through `core/db.py` helpers and observable SQLite state.
- Test shared installation evidence and existing-only openers with temporary missing,
  older, empty, partial and extended schemas (`tests/core/test_installation_evidence.py`);
  test candidate probe capability/refusal through isolated fake packages and real
  subprocesses (`tests/test_runtime_probe.py`). Installed-wheel tests execute the
  packaged synthetic builder and stdio probe without the source checkout.
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
- Test captured configuration, fresh connections and lazy factories through the public
  installation context and CLI (`tests/test_runtime.py`); real stdio verifies unchanged
  running settings, live observations/plan files and adoption after restart. Adapter
  tests bind a public temporary context rather than replacing settings readers.
- Test orchestration through `etl/sync.py` with an injected fake Garmin client.
- Test the complete dated-workout operations through `workouts.push` with real SQLite,
  temporary specs/receipts and a factory returning `FakePublisher` (`tests/workouts/`).
  Author/repeat, push, status/listing, removal and conflict reads share artifact ownership.
  CLI/MCP tests retain adapter contracts for confirmation, output, freshness and parity;
  date, token and unreadable-evidence refusals precede connection.
- Test plan import consequences through CLI/MCP and `daily.run_daily`, with real
  SQLite and temporary plans/receipts. Partial imports retain accepted weeks, and
  nightly conflicts warn without another mart pass or an outbound account call.
- Test athlete changes through `athlete_changes` with temporary SQLite and late-mart
  failure triggers (`tests/test_athlete_changes.py`): committed facts after reopen,
  preserved old marts, honest partial results, overlays, event collisions and offline
  recovery. Keep CLI/MCP adapter tests for flags, exit codes, freshness and block output.
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

- **Poetry** for dependency work; keep the lock reproducible.
- Python 3.13. Code and docstrings in **English**; commit messages in English.
- New and changed public docstrings use **Google-style docstrings**.
- The sole schema source is `src/garmin_coach/core/schema.sql`, loaded via
  `importlib.resources`. Changes must support both fresh and existing installations.
- `AGENTS.md` is a byte-for-byte mirror of `CLAUDE.md`, guarded by
  `tests/test_agents_mirror.py` -- edit `CLAUDE.md`, then copy it over `AGENTS.md`.
- Normalizers must be pure and total: missing fields -> `None`, never raise. All values
  a normalizer emits must be **scalars** (SQLite can't bind dict/list).
- Fixtures are anonymized real payloads. Strip PII: `userProfilePK`/`ownerId`,
  `ownerFullName`, lat/lon, `deviceId`, UUIDs, image URLs. Trim per-minute time series.
- Additional working rules in `.claude/rules/` apply to all changes; follow the
  loading instructions and complete rule list in the shared core.

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
| `snapshot_vo2max_lookback_days`, `snapshot_hrv_lookback_days`, `snapshot_trend_min_span_days` | Observation windows and minimum measurable trend span (ADR 0009). |
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

Not-yet-done work carried forward. Earlier build specifications remain in Git history; accepted decisions are in
`docs/adr/`. New work and acceptance evidence live in GitHub issues and PRs
(`docs/agents/issue-tracker.md`).

- Multi-sport / `discipline` weighting in weekly rollups.
- VO2max / threshold **trend charts**.
- PDF / Notion export.


## Product boundaries

The deterministic engine has no LLM layer, nutrition tracking, social/streak UX or
black-box readiness score. The coach interprets recorded evidence through its skill;
Garmin's readiness remains a named external observation, not an invented local score.

Other deferred work: beat-to-beat/DFA-alpha-1 evidence and forecast-aware pace
adjustment. Neither has an ingestion or coaching contract in the current system.
