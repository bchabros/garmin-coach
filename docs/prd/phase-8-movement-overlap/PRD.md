# PRD - Garmin Coach - Phase 8: per-set capture + movement-pattern overlap

> Status: Ready for implementation (TDD) - Date: 2026-07-11
> Triage: ready-for-agent
> Sources: `docs/PROJECT.md` Phase 8, `docs/glossary.md`, grilling decisions 2026-07-11.
> ADR to follow: `docs/adr/0011-phase-8-movement-overlap.md` (write alongside implementation).

## Problem Statement

The load model now sees strength and Hyrox stress as a single per-session number
(Phase 7's sRPE blend), but it is blind to *what* that stress loaded. `activity_sets`
is **empty (0 rows)** - the per-set ingestion committed as Phase 0 D9 was deferred - so
the system cannot tell a session that hammered the posterior chain from one that pressed
overhead. Today's grip / posterior-chain warning (cable row + KB complex, then
row / ski / farmer carry an hour later) was eyeballed, not computed: nothing in the
pipeline notices that the same movement pattern or muscle group is loaded on adjacent
sessions without recovery. No endurance app models this explicitly, so it is a genuine
differentiator, but it needs concrete per-set data and a deterministic overlap metric,
not a hunch.

## Solution

Three additions, extending the existing per-activity ingest seam and the mart/signal
machinery without any new pipeline-owned dependency:

- **Per-set capture (deterministic transport).** ETL enriches each activity with
  `get_activity_exercise_sets`, mirroring the existing best-effort `_fetch_weather`
  seam: raw-first, non-blocking, on every `sync` and `backfill`. A pure
  `normalize_exercise_sets` writes scalars into the existing `activity_sets` table.

- **Movement-pattern map (hand-maintained lookup).** A new seeded core table
  `exercise_pattern` maps Garmin's `subcategory` to a movement `pattern`
  (`push | pull | hinge | squat | carry`) and a `muscle_group` (including `grip`). A
  new mart module computes `pattern_load` per session and a daily `pattern_overlap`:
  the same pattern (or muscle group) loaded on adjacent days without a rest day.

- **Overlap signals.** Two new digest signals - `PATTERN_STACK` (movement axis) and
  `MUSCLE_OVERLAP` (muscle axis) - fire (severity `warn`) when a pattern or muscle
  group stacks across consecutive days above a tunable threshold, naming the offending
  keys. Unmapped exercises are excluded from the metric but surfaced as a coverage fact
  so map drift stays visible.

## User Stories

1. As the athlete, I want each strength/Hyrox session's exercises captured per set, so
   that the system knows what movements I actually loaded, not just a session total.
2. As the athlete, I want a warning when I load the same movement pattern (e.g. hinge)
   on back-to-back days, so that I do not unknowingly overload it without recovery.
3. As the athlete, I want a warning when the same muscle group (e.g. grip + posterior
   chain) stacks across adjacent sessions, so that today's eyeballed risk is computed.
4. As the athlete, I want a single rest day to clear the stack, so that a normal
   train/rest cadence does not fire false alarms.
5. As the athlete, I want a session with no logged sets (a run) to never trigger overlap,
   so that only real strength work is judged.
6. As the athlete, I want an exercise I have never done before to be reported as
   unmapped rather than silently dropped, so that I know to extend the map.
7. As the maintainer, I want per-set fetching to be best-effort, so that a sets failure
   on one activity never aborts the sync of the others.
8. As the maintainer, I want the exercise->pattern map to live in the schema as seed
   data, so that I can extend it without touching Python and it is visible in migrations.
9. As the maintainer, I want `pattern_load` distributed by set-share of the session's
   Phase 7 load, so that the metric is robust to a `NULL` `max_weight` (Hyrox,
   bodyweight).
10. As the maintainer, I want the overlap metric materialized in a long-format mart, so
    that a signal can name exactly which pattern/muscle stacked.
11. As the maintainer, I want the overlap computation pure and golden-tested over frozen
    fixtures, so that it is deterministic and reproducible from core on any recompute.
12. As the maintainer, I want `activity_sets` populated by a re-run backfill, so that no
    separate migration is needed for the existing activities.

## Implementation Decisions

Full rationale to land in `docs/adr/0011-phase-8-movement-overlap.md`.

### Transport (ETL)

- **Per-activity sets fetch mirrors `_fetch_weather`.** A new best-effort
  `_fetch_sets(client, activity_id) -> payload | None` is called inside
  `_store_activities` for every activity; a failure returns `None` and leaves that
  activity without sets, never aborting the run. The raw payload is appended to
  `raw_payloads` (endpoint `get_activity_exercise_sets`) before normalization. Runs on
  every `sync` and `backfill` (the ingest is not gated by discipline; Garmin returns no
  sets for cardio, which normalizes to zero rows).
- **`GarminClient` gains `get_activity_exercise_sets(activity_id)`** on the transport
  seam (real client + fake test client), matching the `get_activity_weather` shape.
- **New pure `models.normalize_exercise_sets(activity_id, payload) -> list[dict]`.**
  Emits one dict per set with scalars only: `activity_id`, `set_idx`, `category`,
  `subcategory`, `reps`, `sets`, `duration_s`, `max_weight`. It reads Garmin's
  `exerciseSets` list, keeps only `setType == 'ACTIVE'` rest/work sets as applicable,
  and is total over both observed payload shapes (see Testing).
- **New `db.upsert_activity_sets(conn, rows)` helper** following the existing `_upsert`
  pattern, PK `(activity_id, set_idx)`. Re-fetch on a re-run upserts cleanly (idempotent).

### Movement-pattern map (core seed)

- **New seeded table `exercise_pattern`** in `schema.sql` (package copy) mirrored to
  `docs/schema.sql` (guarded by `test_schema_sync.py`). Columns: `subcategory` (PK),
  `pattern` (`push|pull|hinge|squat|carry`), `muscle_group`. Seeded with
  `INSERT OR IGNORE` from the fixture's real `subcategory` values, hand-maintained as
  new exercises appear. It is core reference data (a hand-curated lookup), not a
  Garmin-written table.
- **Taxonomy - grip lives on the muscle axis.** `pattern` is exactly the five clean
  movement patterns; `grip` is a `muscle_group` value (alongside `chest`, `back`,
  `posterior`, `quads`, `shoulders`, `core`), because carries and pulls both tax grip -
  it is not a movement pattern. This is a deliberate deviation from the PROJECT.md
  Phase 8 sketch, which listed `grip` as a sixth pattern.

### Metric (mart)

- **New pure module `overlap.py`** materializing the daily `pattern_overlap` table
  (prior art: `weekly.py`, `zones.py` own their marts). Wired into the `features`
  recompute so `garmin-coach features` rebuilds it from core.
- **`pattern_load` per (activity, key).** For each activity with sets, join
  `activity_sets.subcategory -> exercise_pattern`; for each axis
  (`dim in {'pattern', 'muscle'}`) and each `key` on that axis,
  `pattern_load = (n_sets_key / n_sets_total) x sess_load`, where `sess_load` is the
  activity's Phase 7 blended load and `n_sets_total` counts the session's **mapped**
  sets. Distributing the honest session load by set-share is robust to `NULL`
  `max_weight`.
- **Daily aggregation then adjacency.** Same-day sessions sum into `pat_load[D]` per
  `(dim, key)`. Overlap on day `D` for a key:
  `overlap[D] = min(pat_load[D], pat_load[D-1])` when **both** exceed
  `pattern_load_floor`, else `0`. A single rest day (no load for that key on `D-1`)
  clears the stack. Adjacency is strictly consecutive calendar days, consistent with
  Phase 5's `max_consec_hard`.
- **Long-format mart `pattern_overlap`** in `schema.sql` (mirrored). Columns:
  `date`, `dim` (`'pattern'|'muscle'`), `key`, `load_d`, `load_prev`, `overlap`,
  PK `(date, dim, key)`. Only rows with `overlap > 0` are materialized. Daily only;
  a weekly rollup is deferred (deliberate deviation from PROJECT.md's "daily/weekly").
- **Unmapped subcategories.** A set whose `subcategory` has no `exercise_pattern` row is
  excluded from `pattern_load` (never counted). The recompute logs a WARN
  (`[Phase8] N unmapped subcategories: ...`) and the digest carries a flat coverage fact
  (`sets_total`, `sets_unmapped`, and the sorted unmapped names) so drift is visible and
  the metric never silently under-counts.

### Signals

- **Two new digest signal functions** in `signals.py`, both severity `warn`, reading
  the `pattern_overlap` mart live (like `deload_advised` reads `weekly`):
  - `PATTERN_STACK` - `dim = 'pattern'`; fires when any key has
    `overlap >= pattern_overlap_high` on the latest day of the window. Facts (flat
    scalars per the `signals.py` contract): `keys` (offending, sorted), `overlap_max`,
    `date`.
  - `MUSCLE_OVERLAP` - identical on `dim = 'muscle'` (catches `grip` + `posterior`
    stacking in one signal).
- **Report surface.** The two signals join the existing signal list in `report.md`; the
  coverage fact is surfaced as a fact. No new report section and no new chart this phase
  (deferred until `activity_sets` has real history).

### Thresholds

- **Two new `coach_thresholds` keys** (seeded in `schema.sql`, mirrored to `DEFAULTS`),
  placeholders to tune as `activity_sets` history grows:
  `pattern_load_floor = 20` (min per-key session load to count as loaded) and
  `pattern_overlap_high = 40` (overlap at/above which the signals fire). One floor and
  one ceiling on both axes - separate per-axis thresholds are deferred as premature
  without data.

## Testing Decisions

Tests exercise external behavior at the seams - `normalize_exercise_sets`, the composed
`pattern_overlap` mart, and the digest signals - over frozen fixtures, not internal SQL.
Prior art: `test_features.py`, `test_weekly.py`, `test_zones.py`, `test_digest.py`.

- **Fixture (prerequisite):** capture a real `get_activity_exercise_sets` payload via
  the `mcp__garmin__*` tools (allowed for fixtures only, never the pipeline) into
  `tests/fixtures/`, plus a second shape if the API varies, to seed both the normalizer
  test and the initial `exercise_pattern` seed rows.
- **`test_sync.py`:** `_fetch_sets` failure on one activity leaves it without sets and
  does not abort the others; a successful fetch appends raw and upserts `activity_sets`;
  a re-run is idempotent (no duplicate sets).
- **`test_models.py` (or `test_sync.py`):** `normalize_exercise_sets` maps both fixture
  shapes to scalar rows; a cardio activity with no `exerciseSets` yields `[]`.
- **`test_overlap.py` (new seam, primary):** set-share splits a session's load across its
  patterns/muscles; same-day sessions sum; `overlap = min(D, D-1)` when both exceed the
  floor and `0` when a rest day intervenes or one day is below the floor; an unmapped
  subcategory is excluded and counted in coverage; recompute is idempotent.
- **`test_digest.py`:** `PATTERN_STACK` and `MUSCLE_OVERLAP` fire on a constructed
  adjacent-day stack at/above `pattern_overlap_high` with flat facts naming the keys;
  both are silent below the threshold and when a rest day clears the stack; the coverage
  fact reports unmapped sets.
- **`test_thresholds.py`:** the two new keys are present with their defaults.
- **`test_schema_sync.py`:** `docs/schema.sql` stays byte-identical after the
  `exercise_pattern` + `pattern_overlap` tables and the seed rows.

## Out of Scope

- **Weekly overlap rollup** - `pattern_overlap` is daily only; `weekly_metrics` gains no
  overlap column this phase (PROJECT.md's "weekly" half is deferred).
- **Tonnage / per-set intensity** - `pattern_load` is set-share x session load; absolute
  weight, 1RM, or per-set RPE is not modelled.
- **Overlap chart / dedicated report section** - deferred until there is real per-set
  history to visualize.
- **Auto-classifying unmapped exercises** - the `exercise_pattern` map is hand-maintained;
  no heuristic or LLM classification of new `subcategory` values.
- **Grip as a movement pattern or a third axis** - `grip` is a `muscle_group` value only.
- **Phase 10 recommender consumption** - overlap surfaces as a warn signal only; mapping
  a stacked pattern to a session-recommendation avoid-list is deferred.

## Further Notes

- `pattern_load_floor` and `pattern_overlap_high` are stored in `coach_thresholds` so the
  calibration is tunable without code; both are placeholders (20 / 40 Garmin-load units)
  until `activity_sets` accrues history, mirroring the "seed defaults, tune as history
  grows" note on the existing threshold block.
- The ingest deliberately reuses the `_fetch_weather` pattern rather than a new per-day
  stream, because sets are a per-activity enrichment, not a daily record - keeping the
  transport seam uniform.
- Distributing the Phase 7 blended load by set-share (rather than tonnage) keeps the
  metric honest for Hyrox and bodyweight work, which is exactly where grip/carry overlap
  is most likely - and where `max_weight` is `NULL`.
- Glossary additions (write with the ADR): *movement pattern*, *muscle group*,
  *pattern_load*, *pattern overlap*, `PATTERN_STACK`, `MUSCLE_OVERLAP`.
- This phase adds the first hand-curated **core reference** table (`exercise_pattern`):
  not Garmin-written and not a mart, but seed lookup data versioned in the schema.