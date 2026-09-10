# ADR 0022 - A hand-logged set overlay makes Hyrox circuits visible to the movement mart

## Status

Accepted

## Context

A Hyrox / group-HIIT session is a station circuit - sled pull, sandbag carry, push
press, overhead lunges, KB swings - but Garmin's watch profile records it as a single
nameless round. `get_activity_exercise_sets` returns one ACTIVE set with
`category=UNKNOWN`, so `activity_sets` holds exactly one unmapped row for the whole
session (activity 23767493130, 2026-07-28, 61:46, blended load ~150 at RPE 6).

`marts/overlap.py` deliberately excludes sets that map to neither a pattern nor a
muscle group (ADR 0011: the `CARDIO` pseudo-set must not dilute the load-split
denominator). The consequence was that every Hyrox session was invisible to the
movement-overlap mart - the one place in the system that knows *what* a session loads.
Hyrox is 2-3 sessions a week and the single biggest source of carry / grip and
posterior-chain work in the plan, so `PATTERN_STACK` and `MUSCLE_OVERLAP` were computed
over strength sessions only, and `pattern_overlap` had 0 rows on 2026-09-10.

The stations are known to the athlete (the box publishes the circuit) but there was no
durable channel for them: `sync._store_exercise_sets` calls `db.replace_activity_sets`,
which is delete-then-insert per activity, so any hand-written row was dropped on the
next sync of that activity. Free text in `session_rpe.notes` is read by the coach skill
but feeds no computation. Issue #60.

A second, smaller gap surfaced while verifying the first: the movement map was keyed on
Garmin's full exercise names (`BARBELL_BENCH_PRESS`), but when the watch has no exercise
name `normalize_exercise_sets` falls back to the bare category (`BENCH_PRESS`,
`BACK_SQUAT`), so the same lift arrived under two spellings and the second was unmapped.
18 of 22 captured sets in the 2026-08-20 digest were unmapped for that reason.

## Decision

- **The overlay is core ground truth, on the same footing as `session_rpe` and
  `niggle`.** A new core table `manual_activity_sets`, byte-identical in shape to
  `activity_sets`, holds hand-logged stations. It is written only by
  `garmin-coach log-sets` / the MCP `log_sets` tool and never by the ETL, so a re-sync
  cannot erase it, and it survives a mart drop/rebuild because it cannot be recomputed
  from anything (the ADR 0010 argument for `session_rpe`).

- **Per-activity supersede, not row-level merge.** When an activity has manual rows,
  its captured rows are ignored entirely for overlap. Merging would double-count: the
  captured row for a Hyrox session is a single pseudo-set standing in for the same work
  the manual rows describe. All-or-nothing per activity keeps the load-split denominator
  honest and is trivial to reason about. Captured rows stay in `activity_sets`
  untouched - the overlay is a read-time decision, so a future firmware that names the
  stations needs only the manual rows deleted.

- **One read surface: the `movement_sets` view, not a UNION inlined in `overlap.py`.**
  It is `manual_activity_sets` for any activity that has manual rows and `activity_sets`
  otherwise, with a `source` column (`manual` / `captured`) naming which answered.
  `_SETS_JOIN_MAP` is shared by `_daily_key_load` and `coverage`; a view keeps both on
  one definition and keeps the supersede rule in the schema next to the tables it
  governs. Nothing else in the mart changes, and `coverage` counts the overlay's rows
  in place of the superseded ones - an unknown station name shows up as unmapped drift
  exactly as an unknown captured exercise does.

- **Set-share still means one row per station** (ADR 0011's set-share-not-tonnage
  decision). An EMOM circuit run for N rounds is logged as one row per station, not
  N x stations: every station gets equal share, which is what a fixed-interval EMOM
  delivers. `reps` / `sets` / `duration_s` / `max_weight` stay nullable and are recorded
  for the record only - and they are the only place in the system that could one day
  hold per-station times for a race plan (issue #13).

- **Names follow Garmin's vocabulary** (`SCREAMING_SNAKE_CASE` subcategories), so a
  future firmware that does name the stations joins the same map without a rename. The
  writer normalizes what the athlete types (`sled pull` -> `SLED_PULL`) and refuses
  anything outside letters, digits and underscores; it does not refuse a name the map
  does not know - that is what the athlete did, and the drift fact is how it asks to be
  mapped.

- **New `exercise_pattern` seeds, all judgment calls, listed here rather than buried in
  the diff:**

  | subcategory | pattern | muscle_group | rationale |
  |---|---|---|---|
  | `SPANISH_SQUAT` | `squat` | `quads` | knee-dominant, band-resisted; quad-biased by design |
  | `BOX_STEP_OVER` | `squat` | `quads` | single-leg knee-dominant step-up |
  | `BOX_JUMP` | `squat` | `quads` | plyometric knee-dominant; same axis as the step-over |
  | `SKI_ERG` | `hinge` | `posterior` | double-pole is a hip hinge; lats/posterior driven |
  | `INDOOR_ROW`, `ROW` | `hinge` | `posterior` | leg drive into a hip hinge; the rowing-erg station |
  | `BATTLE_ROPE` | *(null)* | `shoulders` | no clean pattern; taxes shoulders + grip, so it counts on the muscle axis only |
  | `V_UP`, `SIT_UP` | *(null)* | `core` | trunk flexion; `core` is already in the `muscle_group` vocabulary |
  | `JUMPING_JACKS` | *(null)* | *(null)* | warm-up filler, a known non-movement like `CARDIO` |
  | `BENCH_PRESS`, `BACK_SQUAT`, `SQUAT` | as the barbell rows | as the barbell rows | Garmin's bare category names (aliases) |

  `SLED_PULL`, `SANDBAG_CARRY`, `PUSH_PRESS`, `LUNGE` and `KETTLEBELL_SWING` were
  already seeded and cover the other five stations of the standard circuit.

- **Logging recomputes from the activity's date**, mirroring `log-rpe`: validate ->
  write core -> `features.features(from_date=activity_date)`. The overlap mart is a full
  rebuild inside that pass, so `pattern_overlap` reflects the stations at once. A
  re-log replaces the activity's stations wholesale (`db.replace_manual_activity_sets`,
  delete-then-insert); an empty list is refused rather than treated as a clear -
  removing an overlay is a deliberate `DELETE FROM manual_activity_sets` for now.

## Consequences

- `garmin-coach log-sets --activity <id> STATION [STATION ...]` and the MCP `log_sets`
  tool are the third and fourth manual writers to core, after `log-rpe` and `event`.
  They are transport-free and offline-testable; the golden rule holds.
- `movement.sets_total` in the digest now counts the rows the mart reads: a logged
  circuit contributes its stations, not the watch's single `UNKNOWN` row.
- A circuit logged on one day alone produces no overlap row - `pattern_overlap` is
  adjacent-day by construction (ADR 0011). The stack appears once two neighbouring days
  both carry mapped movement load, which for a 3-a-week Hyrox block means logging each
  circuit as it happens.
- The map remains hand-maintained. Every unmapped name, captured or logged, still
  surfaces in the coverage fact; the aliases above clear the 2026-08-20 drift list.
