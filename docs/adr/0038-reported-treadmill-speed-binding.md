# ADR 0038 - Reported console settings bind to verified work execution

## Status

Accepted (#105)

## Context

The athlete reported 16.4 km/h for six timed treadmill repetitions, but the watch's
pace is separate evidence. #104 showed that each repetition can span two laps; moving
one overlapping RWD split also changes positional work indices, while all six work
groups share one workout-step index. None of those indices is a durable identity.

## Decision

- Use the same stored-execution interpretation for interval metrics, saved reads and
  manual-write validation. This extracts ADR 0037's existing rules without loosening
  source roles, contiguous/disjoint membership, type eligibility or UTC ambiguity gates.
- A versioned work binding describes the activity/type and ordered verified work groups:
  canonical UTC starts, positive durations, source roles and temporal member/step
  evidence. It excludes HR, watch speed/distance, RWD, fetch time and positional/message
  indices. Pure lap renumbering preserves a binding only when explicit membership
  revalidates to the identical temporal execution. Unknown timing cannot authorize a save.
- The athlete chose whole-set staleness when work count/order/boundaries change, while
  metric-only changes preserve the association. An all-work declaration freezes the
  reviewed group set; added work never inherits it. A stale set remains inspectable
  but none of its settings applies to the current layout. Reconfirmation is a fresh
  complete save against the reviewed current binding, not a guessed reassignment.
- Store declared km/h in independent manual core tables owned by explicit local writers,
  with an activity reference, saved work count and binding. Captured-lap replacement
  never writes or deletes these facts; there is no FK to replaceable lap/split rows.
  A re-log replaces the entire reported set; empty input refuses and explicit clear
  removes even stale reports when current work/type no longer qualifies.
- Require the binding from a saved activity read. Validate the complete input and current
  execution before the first data mutation, in the same write transaction as persistence.
  Exactly one whole-work or per-repetition mode is accepted; finite positive speed and
  a representable converted pace, unique integer references and membership are required.
- Reuse ADR 0036's shared athlete-change flow: facts commit before a separately
  transactional features rebuild. A derived failure reports saved but not recomputed.
  Saved activity and complete-week reads reconcile manual core with current execution
  independently of cached HR summaries, so an old generation never substitutes an old
  reported speed after a save or clear. This adds no dirty-state ledger or retry queue.
- Activity and complete-week reads hold one SQLite snapshot across captured execution,
  the declaration and cached summaries. Review reproduced a concurrent import/re-log
  mixing an older binding with newer speeds; snapshot ownership closes that race while
  preserving caller-owned transactions. The declaration header and its values also
  load through one query.
- Read conversion is 3600 / reported km/h in s/km, explicitly athlete-reported evidence.
  Captured speed, distance, duration and HR remain unchanged. Coverage and missing work
  are explicit; retained stale reports are separate from applicable reports. Constant
  reported settings establish neither calibrated belt speed nor measured consistency.
  There is no mixed captured/reported pace spread or new physiological recommendation.

## Consequences

The stable interface is a saved read followed by a binding-checked local save or explicit
clear. CLI and MCP use the same application operations and freshness envelope, without
Garmin transport or a CLI dependency in MCP. A changed layout requires the athlete's new
confirmation; harmless metadata/measurement updates do not. A missing/unverified work
layout can prevent a new declaration but cannot prevent clearing an old one. Existing
installations require the documented offline schema step before guarded clients restart.

This extends ADRs 0022/0036 for reported console evidence and leaves ADR 0037's captured
interval/REST limitations intact. Production athlete writes, native chat acceptance and
operator migration remain separate from temporary-installation/fake-transport tests.
