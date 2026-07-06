# CLAUDE.md

Guidance for Claude Code working in this repo. Read this before making changes.

## What this is

Local ETL + coaching system for one athlete's Garmin Connect data. Pulls daily,
stores in SQLite as system-of-record, computes training metrics, feeds a coach
skill. Full brief: `docs/garmin-coach-BUILD.md`. Per-phase PRDs: `docs/prd/`.

**Golden rule — separate transport from intelligence.** The deterministic ETL uses
the `garminconnect` library. The metrics/coach layer only ever reads the finished
DB — it must never call Garmin live. The `mcp__garmin__*` tools are for **ad-hoc
exploration and building test fixtures only**, never the pipeline.

## Workflow

Build phase-by-phase (0 → 5); each phase has a Definition of Done in the BUILD doc —
don't advance until it's met. The established loop for a new phase is:
**grill (stress-test decisions) → PRD in `docs/prd/` → TDD (red→green)**.

- Work test-first. Tests live at agreed **seams**: pure normalizers (`models.py`),
  the persistence layer (`db.py`), and the backfill orchestrator (`sync.py`, with an
  injected fake client). `client.py` (real transport) and `cli.py` are out of seam —
  validated by a live run, not unit tests.
- One vertical slice at a time: one test → minimal impl → repeat. No bulk test-first.
- Before committing a change: `task check` (or `poetry run pytest && poetry run ruff check src tests && poetry run mypy src` if Task is unavailable).

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

## Architecture

`config.py` (pydantic-settings) · `client.py` (login+MFA, endpoint→method map, the
only garminconnect importer) · `db.py` (connect, bootstrap, upserts) · `models.py`
(pure `payload dict → row dict` normalizers + discipline mapping) · `sync.py`
(`backfill(client, conn, from_date, to_date)`) · `cli.py` (argparse).

Data is medallion: **raw** `raw_payloads` (append-only, never overwrite — reprocess
without re-hitting Garmin) → **core** (normalized, upserted by PK) → **mart**
`daily_metrics`/`weekly_metrics` (recomputed; phase 2+). Derived values live only in
marts/views, never mixed into core.

## Conventions

- **Poetry**, not `uv`/`pip`, for all dependency work (despite what the BUILD doc says).
- Python 3.13. Code and docstrings in **English**; commit messages in English.
- New and changed public docstrings should use **Google-style docstrings**.
- Schema source of truth is the package copy `src/garmin_coach/schema.sql`, loaded via
  `importlib.resources`. `docs/schema.sql` is a snapshot kept identical by
  `tests/test_schema_sync.py` — edit the package copy, then re-sync docs.
- Normalizers must be pure and total: missing fields → `None`, never raise. All values
  a normalizer emits must be **scalars** (SQLite can't bind dict/list).
- Fixtures are anonymized real payloads. Strip PII: `userProfilePK`/`ownerId`,
  `ownerFullName`, lat/lon, `deviceId`, UUIDs, image URLs. Trim per-minute time series.

## Gotchas (learned the hard way)

- **garminconnect 0.3.6 token API:** persist tokens by calling `api.login(tokenstore)`
  — it auto-dumps via `api.client.dump(path)`. There is **no** `api.garth` attribute.
- **Login rate limits:** Garmin returns 429 (IP-level) on repeated login attempts.
  Once tokens are cached in `~/.garminconnect`, resume avoids the login endpoint —
  don't hammer it, wait it out.
- **Onboarding vs real data:** this account has real data from **2026-06-08**; earlier
  is onboarding — treat as explicit gaps (`daily_wellness.has_data=0`), not zero training.
- **Shape drift after onboarding:** fields that are `null` during onboarding can become
  objects later (e.g. `hrvSummary.baseline` becomes a band `{balancedLow, ...}`). Test
  normalizers against **both** onboarding and post-onboarding fixtures.
- **Backfill excludes "today"** — HRV/sleep only land after the night; only pull through
  yesterday.
- **Idempotency contract:** re-running backfill must not change **core** row counts
  (upsert by PK); `raw_payloads` is expected to grow (append-only, keyed by `fetched_at`).
- Device-keyed maps in training-status payloads: pick the single device value, never
  hardcode a device ID.

## Deferred / TODO

- `activity_sets` (per-set Hyrox/strength via `get_activity_exercise_sets`) — committed
  in the Phase 0 PRD (D9) but not yet implemented.
- Phase 1 (incremental sync, retry/backoff, per-day fallback, stream isolation) — **done**.
- Phase 2 (`features.py` → `daily_metrics` mart) — **done**; decisions in `docs/prd/phase-2.md`
  + `docs/adr/0002-phase-2-metrics-semantics.md`; golden regression in `tests/test_features.py`.
- Phase 3 (`digest.py`/`signals.py` → `garmin-coach report` → `skills/coach/SKILL.md`) — **done**;
  decisions in `docs/prd/phase-3.md` + `docs/adr/0003-phase-3-coach-signals.md`; golden
  regression in `tests/test_digest.py`. Deterministic engine builds `reports/{date}/digest.json`
  + 2 charts (HRV ±1 SD, ACWR); the skill writes `report.md` from the digest (never the raw
  mart, never Garmin). Signals 1–5 from BUILD §7; rule 6 (plan vs actual) deferred to Phase 5.
- Phase 4 (automation) — **done**; decisions in `docs/prd/phase-4.md` + `docs/adr/0004-phase-4-automation.md`;
  seam tests in `tests/test_daily.py`. Seam `daily.run_daily(client, conn, ...) → DailyResult` runs
  sync → features → `build_digest` (alerts only; **no charts** on the nightly path). Alerts = digest
  signals with `warn`/`alert` severity (reused from Phase 3, no new thresholds), logged WARNING/ERROR.
  Status/exit contract: `ok`/0, `degraded`/1 (isolated stream failure), `failed`/2 (stage crash or
  total sync outage). `garmin-coach daily [--to]` + `scripts/daily.sh` (thin) + launchd plist example;
  logging via in-process `RotatingFileHandler` (`configure_logging`, config keys `LOG_PATH`/
  `LOG_MAX_BYTES`/`LOG_BACKUP_COUNT`). Scheduling is documented, not auto-installed.
- Phase 5 (`weekly.py` → `weekly_metrics` mart, plan-vs-actual, deload) — **done**; decisions in
  `docs/prd/phase-5.md` + `docs/adr/0005-phase-5-weekly-rollups-and-plan-vs-actual.md`; golden
  regression + seam tests in `tests/test_weekly.py` (+ `tests/test_signals.py`). Seam
  `weekly.rollup(conn, *, data_start_date, through_date=None)` rolls the daily mart up into
  `weekly_metrics` for **complete weeks only** (Mon–Sun ≤ cutoff), run as the second half of
  `garmin-coach features` (no new command). Actual intent classified by load; `plan_adherence` =
  exact matches / 7 vs `plan_template`; Foster `monotony`/`strain` (NULL when uncomputable);
  `max_consec_hard`; recovery means skip null days. New signal `DELOAD_ADVISED` (prospective, `warn`)
  joins `build_digest`, which gains a `weekly` section (facts + per-day plan-vs-actual); the coach
  skill renders "Tydzień: plan vs realizacja". New `coach_thresholds`: `monotony_high`,
  `deload_load_rise_weeks`, `deload_min_history_weeks`, `deload_drop_pct`. Deferred (BUILD §12):
  multi-sport/`discipline` weighting, VO2max/threshold **trend charts**, PDF/Notion export.
