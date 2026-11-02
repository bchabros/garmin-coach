# ADR 0001 - Phase 1 incremental sync semantics

## Status

Accepted

## Context

Repeated daily imports need durable progress and isolation from transient Garmin failures.

## Decision

- Store a watermark per stream in `sync_state`. Bootstrap from that stream's core
  maximum date, or `data_start - 1` if empty; advance only after successful persistence.
- Daily streams use per-day calls with retry and isolation. Activities try a range
  first, falling back to daily ranges after range retries fail.
- Preserve useful partial progress while surfacing each failure. Different streams
  may temporarily hold different watermarks.
- ADR 0026 adds the trailing re-check window and caps final watermarks. ADR 0004
  defines the nightly exit-code contract; enrichment policy is in ADR 0018.
- #106 adds independent weight/forecast source checks, including verified empty
  days. Bootstrap their progress only from contiguous successful source checks,
  never retained legacy observations. Re-read one of seven stable old-date buckets
  per successful nightly cutoff, excluding work already fetched in that run.
  A failed bucket stays due; repeating the same cutoff does not advance it twice.
  This spreads historical work across seven successful runs, not a fixed request
  cap: initial source catch-up is about two daily requests per history day before
  retries, and each source's nightly historical share grows with retained history.
  Existing transport rate-limit/retry handling applies. Missed/failed runs can
  exceed a calendar week; coverage says so without inventing an edit timestamp.
- Initial setup keeps base-day checkpoints and observation-source completion
  separate. Failed observation reads remain visible and independently resumable;
  completed base days cannot hide a missing weight/forecast day or establish full
  import success. A usable local read and a complete import are separate outcomes.

## Consequences

Repeated runs converge without losing another stream's successful work. No new
Garmin range endpoint is added for daily streams without a measured need.
