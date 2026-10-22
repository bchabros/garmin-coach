# ADR 0025 - Preserve coaching history before expiring report artifacts

## Status

Accepted

## Context

Append-only profile prose drifted; derived report folders also held irreplaceable narrative and workout records.

## Decision

- Current profile facts replace superseded facts; valuable replaced context goes in
  dated `Decyzje`. About 150 lines prompts consolidation, not silent truncation.
- Profile amendment or restructuring requires the exact diff and athlete approval;
  refusal changes neither content nor date. The private editing contract is not parsed
  by deterministic code. Preserve the athlete's voice and dated source context.
- Retention is manual, preview-first and explicitly confirmed, under the selected
  installation's reports (ADR 0031), never daily automation.
- Consider only direct ISO-date folders strictly older than the calendar-age threshold
  (default 90 days, nonnegative override). Missing root no-ops; no recursion/symlinks.
- Allow only report.md/hrv_band.png/acwr.png; digest/snapshot require explicit no-keep.
  Preserve specs, receipts, unknown files and nested contents; remove only emptied folders.
- Promote lasting narrative lessons through profile consent before deletion; pending or
  declined promotion retains its source. Individual delete failures are non-atomic and
  reported with nonzero status. Keep first real prune/amendment acceptance separate.

## Consequences

Derived artifacts can be rebuilt, exact narrative cannot. Cleanup never treats all
files in a report directory as disposable or grants automatic profile-writing consent.
