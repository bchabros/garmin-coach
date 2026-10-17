# ADR 0033 — Program ownership and storage compatibility during lifecycle operations

## Status

Accepted for issue #87, building on installation selection in ADR 0031 and the
versioned macOS runtime in ADR 0032.

## Context

One athlete installation may serve several assistant clients and an unattended daily
job. The runtime, generated assistant plugins and clients' caches have different
lifetimes from the athlete's database, plans, profile and workout receipts. A source
checkout or a similarly named job is not evidence that an installer owns it.

SQLite bootstrap performs migrations, and an ordinary opener therefore cannot be
used as an upgrade health check. Replacing durable storage while a client or daily
writer is active would need a maintenance protocol across every entry point. The
initial distribution does not have that protocol.

## Decision

- Diagnosis and candidate storage checks use read-only SQLite connections and the
  packaged schema validator. Live saved-login checking is explicit and separate.
- Prepare immutable releases alongside the current runtime; preserve the prior
  version and create a private SQLite/configuration/integration recovery snapshot
  before checking candidate compatibility. SQLite snapshots use its backup API.
- Refuse incompatible-schema activation. This lifecycle release does not migrate
  durable schema or automatically restore older data over the current database.
- Refresh only selected assistant integrations using their supported client commands;
  failed updates restore prior runtime/assets where possible. Partial client recovery
  is reported as requiring action, never as successful acceptance.
- Remove only receipt-owned resources with matching identities. Shared Work/Codex
  registration persists while either surface remains selected. Unknown or edited
  resources are preserved, and a scheduler removal failure preserves the runtime.
- Verify the release's complete file inventory against the outer receipt before
  recursive deletion. Refuse missing inventory, unknown files or changed entries.
  Resolved personal storage paths, including symlink aliases, must not overlap the
  program root; refuse maintenance before changing client or scheduler registrations.
- Report each selected assistant's state and recovery actions independently. Isolate
  the explicit authentication subprocess from the starting folder and `PYTHONPATH`.
- Program removal always retains the athlete installation, external personal paths
  and recovery snapshots. It never implies Garmin workout deletion or token revocation.

## Considered options

An in-place environment update is smaller but can destroy the only working runtime.
Automatic migration and file replacement could deliver broader version compatibility,
but without exclusive writer coordination it risks losing concurrent athlete writes.
Deleting all files below a familiar path is simpler than ownership receipts, but would
also remove source checkouts, manual jobs or unrelated files placed there by the user.

## Consequences

Same-schema releases have an automatic staged update path. A schema-changing release
needs explicit backed-up offline recovery or a future tested maintenance mechanism.
Receipt/journal state may remain after removal or interruption. Native assistant and
launchd acceptance remain distinct from offline tests and recorded registration history.
