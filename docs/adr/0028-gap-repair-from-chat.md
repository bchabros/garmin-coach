# ADR 0028 - Gap repair from chat: a second transport tool, bounded and previewed

## Status

Accepted

## Context

An empty planned day inside the re-check window was wrongly interpreted as a missed session and deload.

## Decision

- Freshness and digest expose unconfirmed planned days. Ask whether the session
  happened; absence inside the window is not proof of rest, skipping or reduced load.
- Preview is DB-only. Confirmation runs the existing full-range backfill across all
  streams, then rebuilds marts. Token binds the displayed range and per-day state;
  changed state invalidates consent.
- At most fourteen finished days, never today/future or before data_start. Today uses
  explicit same-day refresh. Neither path writes historical sync watermarks.
- Repair is user-requested, never scheduled or looped. Partial failure retains already
  committed days; recover through a fresh preview and idempotent retry.

## Consequences

The coach can repair missing evidence through the sanctioned transport boundary
without inventing missed sessions or silently extending its authority.
