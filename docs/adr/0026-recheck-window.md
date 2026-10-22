# ADR 0026 - A day Garmin has answered for is not final: the re-check window

## Status

Accepted

## Context

Garmin may answer before a watch uploads its activity or morning fields are final.

## Decision

- Incremental sync re-pulls the configurable trailing three days through run end
  (normally yesterday), independently per stream, including per-activity enrichment.
- Start is min(watermark + 1, window start), bounded by data_start. Final watermark
  is capped at window start and never moves backward; an older advanced watermark
  waits for that cap to overtake it.
- Preserve range fallback, retry, stream isolation and core-date bootstrapping.
  Null responses do not blank existing rows. Manual overlays remain independently owned.
- Backfill and same-day refresh retain their contracts. Later-than-window uploads need
  explicit repair/backfill. Unconfirmed-day explanation is implemented through #72.

## Consequences

Useful recent data is revised without advancing false finality or losing old imports.
Raw grows with repeated observations; core converges. Empty recent planned days stay
questions rather than proof of skipped training.
