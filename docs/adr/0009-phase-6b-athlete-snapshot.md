# ADR 0009 - Phase 6b: athlete snapshot (`athlete_status` mart)

## Status

Accepted

## Context

A current-standing answer needs one stable deterministic surface rather than repeated ad-hoc joins.

## Decision

- Persist a recomputed singleton `athlete_status`, composed from finished marts/core
  at the tail of `features`, after weekly/zones/overlap. Rollup never commits itself.
- Bound latest readings to `computed_at`; use that date for planned intent. Mirror
  all zone bounds/provenance so the snapshot is self-contained. ADR 0017 declares
  when a standing singleton does not match a historical report horizon.
- Trend means current value plus signed change over available history and actual
  span. Below the configured minimum span, delta/span are null, not invented.
- VO2max trends use ingested training status; HRV delta uses Garmin weekly-average
  HRV, because our recomputed baseline is constant across the mart's current history.
  Load/ACWR shares reuse existing policy helpers.
- Block/countdown/taper come from ADR 0012; planned intent uses ADR 0015's resolver,
  with null intent when neither authored week nor agreed template exists.
- Amendment approved in #103: remove weight and race-prediction snapshot paths and
  their unused policy/schema requirements because no ETL supplies them. A future
  ingestion feature needs its own issue. Preserve extra legacy storage, while public
  snapshot reads explicitly project only supported fields.

## Consequences

CLI, MCP and reports share the persisted standing without network access. Historical
recomputations remain reproducible, and legacy columns cannot leak retired values
back into the API. No destructive migration is needed for this scope reduction.
