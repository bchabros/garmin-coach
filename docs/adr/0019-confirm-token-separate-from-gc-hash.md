# ADR 0019 - The confirm token is not the gc-hash

## Status

Accepted

## Context

A date-free account fingerprint cannot also bind preview approval to the intended calendar day.

## Decision

- `spec_hash` remains name plus steps: the account idempotency marker in `gc-hash:`.
- A separately named `confirm_token` binds name, steps and target date; ADR 0021 adds
  resolved planned intent. A changed preview must fail before account mutation.
- Spec date must match its report-folder date. Date-free workout reuse is governed
  by naming and the dated guards, not by changing account fingerprint semantics.
- CLI retains deliberate `--confirm`. Plan confirmation needs no token because it
  revalidates the full proposal and refuses an existing authored week.

## Consequences

Scheduling consent cannot be replayed across dates while reusable account identity
remains stable. The two hashes have different names and responsibilities.
