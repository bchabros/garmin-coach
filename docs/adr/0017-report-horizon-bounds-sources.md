# ADR 0017 - The report horizon bounds sources; standing reads declare provenance

## Status

Accepted

## Context

An as-of report mixed historical daily facts with future threshold anchors and coverage.

## Decision

- One inclusive report cutoff bounds every boundable source, including LTHR detection,
  qualifying aerobic runs, movement sets and weekly facts/signals.
- Keep zones/status as current singletons rather than introducing historical tables.
  Report standing blocks declare `matches_horizon`: true, false or null when unknown.
  Hedge mismatches instead of presenting a current standing as historical evidence.
- Current MCP zones/snapshot remain current reads without horizon arguments.
- `features` owns materialization order and one transaction. Daily metrics, plan,
  weekly rows, zones, overlap and snapshot advance together or all roll back.
  Individual rollups never commit; standalone callers own their transaction.

## Consequences

As-of recomputation is reproducible without claiming exact historical singleton
queries. This amends ADR 0009's original snapshot commit behavior.
