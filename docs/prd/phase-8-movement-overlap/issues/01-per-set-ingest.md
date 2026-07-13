# 01 - Per-set ingest (transport)

Status: ready-for-agent
Blocked by: -
Sources: `docs/prd/phase-8-movement-overlap/PRD.md` (Transport section).

## Goal

Populate the existing `activity_sets` table by enriching each activity with its
Garmin exercise sets, mirroring the best-effort `_fetch_weather` seam. No new metric
or signal here - just clean, idempotent, non-blocking transport into core.

## Scope

- **Transport seam.** Add `get_activity_exercise_sets(activity_id)` to `GarminClient`
  (real client) and to the fake client used in tests, matching the
  `get_activity_weather` shape.
- **Best-effort fetch.** Add `_fetch_sets(client, activity_id) -> payload | None` in
  `sync.py`, called inside `_store_activities` for every activity. A failure returns
  `None` and never aborts the run (mirror `_fetch_weather`). Append the raw payload to
  `raw_payloads` (endpoint `get_activity_exercise_sets`) before normalizing.
- **Pure normalizer.** `models.normalize_exercise_sets(activity_id, payload) -> list[dict]`
  emitting scalars only: `activity_id`, `set_idx`, `category`, `subcategory`, `reps`,
  `sets`, `duration_s`, `max_weight`. Total over both observed payload shapes; a cardio
  activity with no `exerciseSets` yields `[]`.
- **DB helper.** `db.upsert_activity_sets(conn, rows)` following the existing `_upsert`
  pattern, PK `(activity_id, set_idx)`; idempotent on re-run.

## Fixture (prerequisite)

Capture a real `get_activity_exercise_sets` payload via the `mcp__garmin__*` tools
(allowed for fixtures only - never wire MCP into the pipeline) into `tests/fixtures/`.
Grab a strength/Siła session and, if the API shape varies, a Hyrox/HIIT session too.
These fixtures also seed ticket 02's initial `exercise_pattern` rows (share the real
`subcategory` values found here).

## Tests (`test_sync.py`, `test_models.py`)

- `_fetch_sets` failure on one activity leaves it without sets and does not abort the
  others (stream isolation).
- A successful fetch appends raw and upserts `activity_sets`; a re-run is idempotent
  (no duplicate rows).
- `normalize_exercise_sets` maps both fixture shapes to scalar rows; empty/cardio
  payload -> `[]`.

## Done when

- `garmin-coach backfill --from 2026-06-08` populates `activity_sets` for the existing
  strength/Hyrox activities (idempotent on re-run).
- `task check` green.
