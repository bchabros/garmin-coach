# ADR 0018 - Raw identity includes the payload hash; enrichment gaps are reported, not degrading

## Status

Accepted

## Context

`schema.sql` promises the raw layer is append-only: "never overwrite - lets you
reprocess without re-hitting API". Issue #34 found the key could not keep that
promise. `raw_payloads` was keyed by `(endpoint, ref_date, fetched_at)`,
`insert_raw` stamps `fetched_at` at second resolution, and the insert used
`INSERT OR IGNORE`. The activity fan-out issues several distinct payloads for one
endpoint and one `ref_date` inside a single second - weather and exercise sets, once
per activity - and every one after the first was silently discarded. The range path
made it worse by filing all of them under the requested range start rather than the
day each activity happened on, so the collisions were guaranteed rather than
accidental.

Separately, both enrichments swallow every exception (ADR 0007 isolation, ADR 0001
partial success). Nothing recorded that a fetch had failed, and the watermark advanced
regardless, so a missing enrichment was indistinguishable from Garmin having no data
and was never retried.

## Decision

- **The payload hash completes the raw identity**, giving
  `(endpoint, ref_date, fetched_at, payload_sha)`. Rejected: content-addressing alone
  (`(endpoint, ref_date, payload_sha)`, dropping `fetched_at` from the key). It is the
  tidier identity, but it silently changes the existing append-only contract - a
  byte-identical re-pull at a later time currently appends a row, and a test pins that
  as deliberate. Adding the hash is strictly additive: distinct payloads in one second
  now both land, an identical repeat of the same call stays a no-op, and every previous
  semantic is preserved. `bootstrap` rebuilds a pre-`payload_sha` table (SQLite cannot
  ALTER a primary key), hashing existing rows in place; the rebuild is one-shot and
  idempotent.

- **Enrichment payloads are filed under the activity's own day.** `models.date_of`
  became public so the ingest can resolve an activity's day before its row is
  normalized. Reprocessing by date now finds the weather and sets that belong to that
  date.

- **A failed enrichment is reported but does not degrade the run.** Misses are logged
  (`[sync]` tag) and collected in `SyncResult.enrichment_misses`, which `daily` logs and
  counts. Rejected: appending them to `SyncResult.warnings`. That would flip `degraded`
  and the process exit code to 1 for a single missing weather reading, contradicting
  ADR 0001's partial-success stance and training the operator to ignore exit 1. A
  `None` response is not a miss - Garmin genuinely has nothing for that activity.

## Consequences

- The raw layer can finally honour the reprocessing promise: no distinct payload is
  discarded, whatever the fan-out rate.
- Existing databases migrate on the next `bootstrap` with no data loss; the pre-migration
  table is dropped only after the copy succeeds, inside bootstrap's transaction.
- Enrichment holes are greppable in the log and countable per run. Repairing them
  automatically (a retry queue, or a marker table driving re-fetch) stays deliberately
  out of scope - a re-run already repairs them, and nothing yet justifies the machinery.
- `SyncResult` grew a field; anything that reports on a sync run should surface
  `enrichment_misses` alongside `warnings` rather than merging the two.
