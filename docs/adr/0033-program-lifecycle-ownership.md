# ADR 0033 — Program ownership and storage compatibility during lifecycle operations

## Status

Accepted

## Context

Installation maintenance must preserve personal state and recover from partial runtime/client failures.

## Decision

- Diagnose through read-only schema/storage checks and a bounded stdio read. SQLite
  may create WAL/SHM auxiliaries without changing records/schema. Saved-login testing
  is separately explicit and isolates imports from cwd/PYTHONPATH.
- Before candidate storage checks, back up SQLite consistently (including WAL),
  configuration and integrations. Stage an immutable sibling release, retain previous,
  and refuse incompatible schema. Never migrate or auto-restore an older database.
- Refresh only selected clients; report activation and partial recovery honestly.
  Restore runtime before client rollback. Failed recovery keeps journals/backups.
- Remove only receipt-owned resources after verifying the whole inventory and outer
  receipt digest. Missing inventory, edits or unknowns refuse. Resolve personal paths
  and aliases before native mutations; any runtime overlap blocks removal.
- Preserve the shared OpenAI binding while Work or Codex still uses it. Scheduler
  removal failure retains runtime. Preserve personal/external paths, backups and tokens;
  maintenance never deletes Garmin data or revokes login.

## Consequences

Retry resumes a recorded operation rather than resetting state. Native provisioning,
launchd and fresh-client acceptance remain independent of offline tests/registration.
