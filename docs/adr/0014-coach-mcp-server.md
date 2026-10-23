# ADR 0014 - The coach MCP server (reads, same-day refresh, workout push)

## Status

Accepted

## Context

One conversational surface needs finished-DB reads, local logs and guarded refresh/account operations without live metric dependencies.

## Decision

- One local stdio server delegates to existing application seams. CLI/MCP adapters
  share the dated-workout flow, including artifact ownership and import-time conflict
  reads (#102 T1). The original Hyrox success/error mismatch and chat-only reuse showed
  why moving account calls alone did not establish parity; publisher remains account
  policy, without artifact reads. #102 T2–T6 track the remaining shared-flow work.
- Local writes and explicit transport operations coexist, but metric computation
  never reads Garmin live. Refresh reuses ETL, stays partial and never advances
  watermarks; freshness accompanies all reads. Nightly owns alerts.
- MCP push uses the separate confirmation token of ADRs 0019/0021; the original
  spec-hash handshake and initial fixed transport-tool count are superseded.
- Workout status must reconcile the account on each read, without an opt-out.
  No receipt/workout ID means no login. Transport failure yields `unverified`.
- Persist only changed successful reconciliation under `reconciled`, preserving push
  event fields. Unverified reads never write. Older findings stay separate from current
  results. ADRs 0028/0030 add their own guarded repair/removal transport paths.

## Consequences

Conversation can read, refresh, preview and confirm through one surface. Account
status is live evidence; training metrics remain deterministic cached evidence.
Rate-limit exposure is explicit, and a protocol test does not prove model adherence.
