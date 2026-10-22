# ADR 0018 - Raw identity includes the payload hash; enrichment gaps are reported, not degrading

## Status

Accepted

## Context

Second-resolution raw keys silently discarded distinct per-activity enrichment payloads.

## Decision

- Raw identity is `(endpoint, ref_date, fetched_at, payload_sha)`. Different payloads
  in one second survive; an identical same-second call no-ops; the same payload fetched
  later appends. Do not replace this with content-addressing alone.
- File enrichment under the activity's actual day, not the range start.
- Pre-SHA bootstrap transactionally rebuilds the raw table, preserving every existing
  row; migration is idempotent and remains supported after #103.
- Enrichment exceptions log and populate `enrichment_misses`, separate from warnings
  and degraded exit status. `None` means absence, not a failed call.
- Historical repair remains an explicit idempotent re-run; no automatic retry queue.

## Consequences

Raw preserves evidence needed for offline reprocessing. Operators can distinguish
enrichment failures without treating one weather miss as a failed training import.
