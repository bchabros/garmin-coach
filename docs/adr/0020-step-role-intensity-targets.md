# ADR 0020 - Intensity targets belong to every step role, and a zone name means heart rate

## Status

Accepted

## Context

Per-role targets must share a predictable vocabulary without fabricating unavailable zone bounds.

## Decision

- Offered roles accept none, a named HR zone, or one explicit HR/pace band. Named
  zones mean HR, never pace zones. Explicit bands work without a DB zone ladder.
- Reject named Z1/Z5 because their outer bounds are unavailable; accept explicit bands
  instead. Never invent maximum HR, zone floors or cadence/power/grade targets.
- A valid named target with a missing ladder warns and degrades to untargeted work;
  successful explicit resolution is silent. Faster-than-advised work pace warns.
- Preserve `work_pace_band`; refuse simultaneous old/new work-target spellings.
  All run-role availability is broadened by ADR 0023; session type controls defaults.

## Consequences

Target resolution is consistent across roles and both work aliases. Missing evidence
can soften a target without blocking the requested session or pretending it was measured.
