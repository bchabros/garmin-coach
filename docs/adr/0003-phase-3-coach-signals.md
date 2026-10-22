# ADR 0003 - Phase 3 coach signals + digest semantics

## Status

Accepted

## Context

The narrative coach needs compact, reproducible facts and cited signals rather than ad-hoc arithmetic.

## Decision

- `build_digest(conn, ...)` reads finished SQLite data without transport or writes;
  pure signal helpers produce flat scalar facts and `info`, `warn` or `alert` severity.
- The default window is trailing 28 calendar days through the latest mart day, with
  a seven-day headline. Report horizons also bound weekly/source reads (ADR 0017).
- Threshold policy merges code defaults, non-null DB overrides and explicit test
  overrides in one module. The mart HRV band is authoritative; historical personal
  band overrides are fallback only. Fresh schemas seed no athlete observations.
- ACWR outside the configured range warns; above the high limit alerts only with
  sufficient chronic history. A latest low-HRV flag warns. Consecutive load-hard
  days warn, with trailing stacks distinguished from earlier pairs.
- HRV/sleep correlation is an informational confound only with enough paired nights
  and low-HRV evidence, never a causal diagnosis.
- Our easy-load shortage is computed independently and compared with Garmin's phrase;
  agreement strengthens the narrative, disagreement requires qualification. ADR 0027
  supersedes the original seven-day share test with a 28-day Garmin-target comparison.
- Python writes digest/snapshot and charts; the skill writes narrative and consumes
  these compact artifacts or equivalent sanctioned reads, never raw mart rows.

## Consequences

Signals remain deterministic at the DB boundary. Nightly alerts reuse the same
facts. Reports are derived audit inputs; regeneration can change historical bands.
Threshold defaults are model policy, not validated personal risk limits.
