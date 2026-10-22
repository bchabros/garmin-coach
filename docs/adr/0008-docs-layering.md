# ADR 0008 - Layered documentation (thin shared core + per-role guides)

## Status

Accepted

## Context

Always-loaded agent instructions had mixed development, operation and human orientation, with duplicated vocabulary and drift.

## Decision

- Keep `CLAUDE.md` a thin core with the golden rule, data cutoff and conditional
  pointers. `AGENTS.md` remains its byte-for-byte mirror, checked by the mirror test.
- Read `DEVELOPMENT.md` for code changes, `OPERATIONS.md` for running the system,
  and `DISTRIBUTION.md`, `CLIENT_SETUP.md`, `LIFECYCLE.md` for installation and clients.
  Coaching follows the canonical skill router and its lazy reference gates.
- `docs/glossary.md` is the sole glossary. Procedures/specs belong in guides/issues;
  ADRs preserve decisions, alternatives and consequences rather than phase diaries.
- Amendment approved in #103 (2026-10-22): retain frozen PRDs, project surveys and old
  validation logs in Git history; remove duplicate working-tree history. Keep new
  wayfinding maps and acceptance evidence in issues/PRs, without private athlete data.
- Keep the packaged schema as the sole schema source. Instruction formatting and
  documentation shortening do not silently revoke accepted behavioral constraints.

## Consequences

Each audience loads the guide it needs. Shared rules and domain definitions have
one authoritative location. Git preserves historical evidence; issues record actual
acceptance and pending native/client work. All ADR filenames/numbers remain stable.
