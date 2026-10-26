# ADR 0032 - A versioned private macOS runtime and opt-in scheduler

## Status

Accepted

## Context

A consumer Mac needs a private reproducible runtime without developer dependencies or personal-data coupling.

## Decision

- Build native binary-wheel releases from the lock plus pinned standalone Python;
  install privately/offline, retain licenses, and use stable absolute launchers.
  Program storage stays outside personal records; no Homebrew/system-Python changes.
- Bootstrap and installed maintenance share canonical stdlib program-receipt operations
  (#102 T3). Build one standalone Python archive from those sources; isolated bootstrap
  must work before application dependencies exist and preserve failure exit codes.
- Bootstrap, maintenance and nested client operations share stdlib command transport
  (#102 T6), bundled from the canonical source into that archive. Keep per-call options
  and recovery ownership unchanged; scoped test injection replaces global subprocess
  patches without adding retry, default deadlines or candidate-side dependencies.
- Prepare a versioned inventory before atomic activation. Receipts contain no tokens,
  profile or athlete metrics. Verify owned files/directories/symlink hashes before
  destructive work; preserve unknown/edited/incomplete resources. Only inventoried
  source-associated bytecode is an allowed changed cache.
- Scheduler is explicit, previewed opt-in for one installation and its saved login;
  no password, immediate import or automatic scheduling. Preserve unrelated/edited jobs,
  resume unchanged owned jobs, and detect the legacy daily job before enabling another.
- No silent auto-update or schema migration. Candidate storage checks are read-only.
- Candidate checks use its isolated interpreter and versioned packaged probe (#102 T4),
  sharing the explicit synthetic builder and stdio conversation. Only absent capability
  permits the older release's own supported check; unknown versions, import errors and
  failed checks refuse. This preserves old-release rollback without hiding regressions.
- Intel macOS keeps cryptography 48.0.1 where newer wheels dropped support, while
  other platforms retain 49. Binary absence refuses rather than building from source;
  review this ceiling before future release promotion.

## Consequences

Native packaged smoke tests do not establish a clean consumer Mac, interactive
own-account login or fresh-client adherence. Keep #85–#87 acceptance pending until
actually observed; immutable v0.1.0 assets do not acquire later source fixes.
