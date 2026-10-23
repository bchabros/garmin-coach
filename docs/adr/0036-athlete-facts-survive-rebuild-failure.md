# ADR 0036 - Athlete facts survive derived rebuild failures

## Status

Accepted (2026-10-23, #102 T2)

## Context

Local writers already committed reported RPE, manual sets and goal events before rebuilding
marts. CLI-owned writers and duplicate chat event flows could then raise a bare rebuild
error, obscuring the successful save. Fault injection during T2 also exposed mixed evidence:
an old plan-block row joined to the newly committed race date after a failed rebuild.
The athlete explicitly chose durable facts and honest partial results over rolling the
new observation back because an unrelated derived computation failed.

## Decision

- One shared athlete-change application flow owns validation, core persistence and the
  result for terminal and conversation; core stays independent of marts and transport.
- Commit accepted facts before recomputation. The existing `features` transaction still
  replaces the entire mart generation atomically; failure preserves the prior generation.
  This retains the durable-observation principles of ADRs 0010/0022 and the mart contract
  of ADR 0006, with their scopes unchanged.
- Report saved identity/date independently from refresh success. Validation/write refusal
  means unsaved; a later failure means saved but unrefreshed, with an explicit error.
  Niggles remain directly readable without a mart pass.
- Save pre-write block evidence before a goal change. On rebuild failure report the newly
  saved event separately, with the post-write block unknown; never label the old-block/new-
  date join a refreshed block. Successful terminal and chat writes show the same transition.
- Recover through offline `features` in the same selected installation, followed by a new
  cached read. Re-adding the event is unnecessary; the existing uniqueness rule remains.

## Consequences

An athlete fact can temporarily be newer than derived reports. Operation results say so;
this flow supplies no persistent retry queue or dirty-state ledger. The athlete's input
survives a derived failure without weakening the all-or-nothing mart transaction.
