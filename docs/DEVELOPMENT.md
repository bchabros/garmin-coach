cho# Development guide

For coding agents (Claude Code, Codex) and humans **changing the code**. The
always-loaded core is `CLAUDE.md`; this guide is read on demand when you touch the
implementation. Operating the system instead? See `docs/OPERATIONS.md`. Term
definitions live in `docs/glossary.md`.

## Workflow

Build phase-by-phase (0 -> 6); each phase has a Definition of Done (the BUILD doc for
phases 0-5, the phase PRD for 6+) -- don't advance until it's met. Forward plan for
everything after Phase 5 is `docs/PROJECT.md` (Part II). The established loop for a new feature is
a chain of skills:

**`/grill-with-docs` -> `/to-spec` -> `/to-tickets` -> `/implement` -> `/code-review`**

- **`/grill-with-docs`** -- stress-test the decisions against the repo's docs before any
  code (reaches `/domain-modeling` for vocabulary; see `docs/agents/domain.md`). For
  very complex tasks use `/wayfinder` instead -- a heavier, map-driven alternative
  (`docs/prd/<feature>/map.md`; see the wayfinding section in
  `docs/agents/issue-tracker.md`).
- **`/to-spec`** -- write the spec to `docs/prd/<feature>/PRD.md`.
- **`/to-tickets`** -- break the spec into `docs/prd/<feature>/issues/NN-<slug>.md`
  (see `docs/agents/issue-tracker.md`).
- **`/implement`** -- build a ticket test-first (TDD, red -> green).
- **`/code-review`** -- review the branch before merge.

- Work test-first. Tests live at agreed **seams** (see below).
- One vertical slice at a time: one test -> minimal impl -> repeat. No bulk test-first.
- Before committing a change: `task check` (or `poetry run pytest && poetry run ruff
  check src tests && poetry run mypy src` if Task is unavailable).

## Architecture

`config.py` (pydantic-settings) - `client.py` (login+MFA, endpoint->method map, the
only garminconnect importer) - `db.py` (connect, bootstrap, upserts) - `models.py`
(pure `payload dict -> row dict` normalizers + discipline mapping) - `sync.py`
(`backfill(client, conn, from_date, to_date)`) - `features.py`/`weekly.py`/`zones.py`
(mart builders) - `digest.py`/`signals.py` (coach digest) - `cli.py` (argparse).

Data is medallion: **raw** `raw_payloads` (append-only, never overwrite -- reprocess
without re-hitting Garmin) -> **core** (normalized, upserted by PK) -> **mart**
`daily_metrics`/`weekly_metrics`/`athlete_zones` (recomputed; phase 2+). Derived values
live only in marts/views, never mixed into core. Full vocabulary in `docs/glossary.md`.

## Testing seams

The agreed boundaries a test exercises -- prefer the highest existing seam, add new
ones sparingly:

- Test normalizers through pure model functions (`models.py`).
- Test persistence through `db.py` helpers and observable SQLite state.
- Test orchestration through `sync.py` with an injected fake Garmin client.
- Test the mart builders (`features.py`, `weekly.py`, `zones.py`) and the digest
  builder (`build_digest`/`digest.py`) at the DB boundary.
- Keep real Garmin transport and auth (`client.py`, `cli.py`) outside unit tests --
  validated by a live run, not unit tests.

## Conventions

- **Poetry**, not `uv`/`pip`, for all dependency work (despite what the BUILD doc says).
- Python 3.13. Code and docstrings in **English**; commit messages in English.
- New and changed public docstrings use **Google-style docstrings**.
- Schema source of truth is the package copy `src/garmin_coach/schema.sql`, loaded via
  `importlib.resources`. `docs/schema.sql` is a snapshot kept identical by
  `tests/test_schema_sync.py` -- edit the package copy, then re-sync docs.
- `AGENTS.md` is a byte-for-byte mirror of `CLAUDE.md`, guarded by
  `tests/test_agents_mirror.py` -- edit `CLAUDE.md`, then copy it over `AGENTS.md`.
- Normalizers must be pure and total: missing fields -> `None`, never raise. All values
  a normalizer emits must be **scalars** (SQLite can't bind dict/list).
- Fixtures are anonymized real payloads. Strip PII: `userProfilePK`/`ownerId`,
  `ownerFullName`, lat/lon, `deviceId`, UUIDs, image URLs. Trim per-minute time series.
- Additional working rules in `.claude/rules/` (no-emoji, code-style) apply to all
  changes.

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

Not-yet-done work carried forward. Completed phases are recorded in the `README.md`
status table plus each phase's PRD (`docs/prd/`) and ADR (`docs/adr/`); the forward plan
(Phases 6+) is in `docs/PROJECT.md` (Part II).

- `activity_sets` (per-set Hyrox/strength via `get_activity_exercise_sets`) -- committed
  in the Phase 0 PRD (D9) but not yet implemented.
- Multi-sport / `discipline` weighting in weekly rollups (deferred from Phase 5, BUILD
  section 12).
- VO2max / threshold **trend charts** (deferred from Phase 5, BUILD section 12).
- PDF / Notion export (deferred from Phase 5, BUILD section 12).
