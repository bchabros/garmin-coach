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

## Consequences

Repeated runs converge without losing another stream's successful work. No new
Garmin range endpoint is added for daily streams without a measured need.
