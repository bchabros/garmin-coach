# 01 - Snapshot skeleton: `athlete_status` mart, seam, command, wired into `features`

Status: ready-for-agent
Parent: `docs/prd/phase-6b/PRD.md`

## What to build

`garmin-coach snapshot` produces `reports/{date}/snapshot.json` holding the athlete's
current standing, composed from finished marts + core - the complete vertical path for
every "direct read" field. The `athlete_status` singleton mart is recomputed as the
tail of `features`, after `weekly.rollup` and `zones.rollup`, so it never drifts from
its sources and never calls Garmin.

Populated this ticket: full mirror of the `athlete_zones` bounds; `acwr` / `n_chronic`
/ `acwr_reliable`; `load_7d` + low/high/anaero shares (reusing the digest headline /
`signals.load_shares`); `hrv_baseline` / `hrv_sd`; `sleep_debt_h`; readiness score +
level; heat/altitude acclimation; race predictions; `vo2max` and `weight_kg` current
values; `planned_intent_today` / `planned_label_today` from `plan_template` at the
`computed_at` weekday; `block` / `weeks_to_event` / `taper_active` NULL placeholders.
The `*_delta` / `*_span_days` trend columns exist in the table but are emitted NULL
(filled by ticket 02).

## Acceptance criteria

- [ ] `athlete_status` singleton table (`id = 1 CHECK`) added to the packaged
      `schema.sql` with all column groups from the PRD, mirrored to `docs/schema.sql`.
- [ ] New `snapshot.py` with the pure, total seam `build(conn, through_date) -> dict`
      and `rollup(conn, through_date)` (upsert + commit), structured like `zones.py`.
- [ ] `computed_at` bounds every latest read to `date <= computed_at`;
      `planned_*_today` uses the `computed_at` weekday, not the wall clock.
- [ ] `db.upsert_status(conn, row)` helper, following `upsert_zones`.
- [ ] `features.features(...)` calls `snapshot.rollup` last (after weekly + zones).
- [ ] `garmin-coach snapshot` reads the row and writes `reports/{date}/snapshot.json`
      plus a short stdout summary; it does not recompute.
- [ ] `build` is total: missing zones anchor / readiness / markers degrade to NULL,
      never a crash.
- [ ] `test_snapshot.py` golden over frozen fixtures (all direct-read groups + mirrored
      zone bounds); no-anchor degraded case; as-of reproduces a past standing;
      idempotent recompute leaves one identical row.
- [ ] `test_features.py`: `features` writes exactly one `athlete_status` row,
      idempotent, ordered after weekly + zones.
- [ ] `test_schema_sync.py` stays green (`docs/schema.sql` byte-identical).

## Blocked by

- None - can start immediately.
