# ADR 0034 - Exclusive client bindings and resumable owned generations

## Status

Accepted

## Context

Native registrations are user-wide; interrupted commands and old private resource generations cannot be treated as isolated local copies.

## Decision

- Serialize one binding per user/host; Work/Codex share it. Refuse conflicting
  athlete/runtime before mutation. Legacy ownership needs matching native inventory.
- Persist generation and recovery intent before changes, prepare sibling artifacts,
  and roll back only verified owned content. Unknown edits remain preserved.
- Do not eagerly detach all clients or assume unique package names isolate hosts.
  Version update intent; legacy preparing state may already have detached a client.
- After interrupted native commands, reconcile inventory before replay: a lost
  acknowledgement is not evidence that the operation failed.
- Removing obsolete resources requires ownership hashes and a deletion journal.
  Earlier private Markdown ownership requires clean native reinstall even if the source
  file vanished, with possible permission reapproval. ADR 0035 extends the public set.

## Consequences

Refresh and recovery preserve independent client progress and prevent stale native
caches retaining private content. A clean public source folder alone proves neither
safe cache contents nor fresh-chat acceptance.
