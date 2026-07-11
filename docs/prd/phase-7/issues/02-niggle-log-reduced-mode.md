# 02 - Niggle log + reduced-mode: `niggle` table, `NIGGLE_REDUCED_MODE` signal, `log-rpe --niggle`

Status: done
Parent: `docs/prd/phase-7/PRD.md`

## What to build

Give the athlete a channel to say "my knee is niggling" and have the coach dial back.
A new core table `niggle` records a body-part severity; the digest surfaces a
`NIGGLE_REDUCED_MODE` signal when an active niggle (logged within the trailing
`niggle_active_days` window) meets `niggle_reduced_mode_severity`. One log stays active
for the window (Runna-style dial-back); re-logging the same body part at a lower
severity clears it early. The `garmin-coach log-rpe --niggle` writer is the vertical
slice.

Phase 7 surfaces the reduced-mode state only; mapping active niggles to an avoid-list
is Phase 10.

## Acceptance criteria

- [ ] `niggle` core table (composite PK `(date, body_part)`, columns `date, body_part,
      severity, note`) added to the packaged `schema.sql` and mirrored to
      `docs/schema.sql`.
- [ ] `db.upsert_niggle(conn, row)` helper following the `_upsert` pattern (composite
      PK).
- [ ] The digest reads `niggle` live (like `weekly` / `zones`), selects the latest
      entry per body part within `[to_date - niggle_active_days + 1, to_date]`, and
      emits `NIGGLE_REDUCED_MODE` (severity `warn`) when any active niggle's severity
      `>= niggle_reduced_mode_severity`. Facts are flat scalars: `body_part` (worst),
      `severity`, `n_active`, `days_active`.
- [ ] The signal is silent when the newest per-body-part entry is below the threshold
      or older than the window; a re-log at lower severity clears it.
- [ ] `garmin-coach log-rpe --niggle <body_part> --severity N [--date YYYY-MM-DD]
      [--note ...]` upserts `niggle` and returns without recomputing `features`
      (reduced-mode is a digest-layer read). `--date` defaults to today, so a niggle
      noticed on a past day can still be logged against it (the active-window math is
      date-based). Range validation: severity 1-5. Transport-free.
- [ ] Two new `coach_thresholds` keys seeded in `schema.sql` and `DEFAULTS`:
      `niggle_active_days=7`, `niggle_reduced_mode_severity=3`.
- [ ] `test_digest.py`: `NIGGLE_REDUCED_MODE` fires for an active niggle at/above the
      threshold with flat facts; silent below threshold and outside the window; cleared
      by a lower-severity re-log.
- [ ] `test_cli.py`: `log-rpe --niggle` writes and does not recompute; severity out of
      range fails loudly; the two modes (`--activity` / `--niggle`) are mutually
      exclusive.
- [ ] `test_thresholds.py` and `test_schema_sync.py` stay green.

## Blocked by

- 01 - Session-RPE load model (shares the `log-rpe` command scaffold, the
  `coach_thresholds`/`DEFAULTS` seeding pattern, and the schema-sync mirror). The
  niggle logic itself is independent of the load blend.
