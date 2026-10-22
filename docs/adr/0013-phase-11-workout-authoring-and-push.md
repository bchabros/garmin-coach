# ADR 0013 - Phase 11: workout authoring and push

## Status

Accepted

## Context

Structured workouts require an explicit outbound boundary while authoring stays deterministic and offline.

## Decision

- Pure `author` turns a source-agnostic request into an inspectable on-disk spec.
  `publish` owns account writes behind an injected publisher and existing login;
  author never imports publish. Neither authoring nor push runs in nightly automation.
- `origin` is recommender or athlete. Hybrid means athlete input validated against
  recommendation evidence, not a third implementation path.
- Signal disagreement produces cited warnings in the spec. The hard plan-of-record
  guard added by ADR 0021 is separate and cannot be bypassed by confirmation/replace.
- Push previews by default; explicit confirmation performs the account write.
  CLI trusts its deliberate flag; MCP uses the token handshake refined by ADR 0019.
- The account, not a local ledger, decides idempotency: create, no-op, schedule,
  refuse or replace. Receipts record events. Lookup by receipt ID, hash and name
  handles later user edits; ADR 0029 refines dated versus reusable replacement.
- Partial upload/schedule failure is repaired by idempotent retry, not an automatic
  compensating deletion whose network failure could make matters worse.
- Run-only delivery was extended by ADR 0016 after the strength/HIIT spike. Original
  probes remain in Git at `6fbd938a4c62b83812315de1a77d00946cc5ef50` under `scratch/`;
  their private endpoint observations are evidence, not executable development tests.

## Consequences

Account mutation is contained and reviewable. Fake-publisher tests cover sequencing
and failure behavior; live acceptance is separate. Renames do not make receipts a
source of truth for current account state; reconciliation reads the account.
