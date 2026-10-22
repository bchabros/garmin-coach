# ADR 0021 - Nothing harder than the plan of record reaches the watch

## Status

Accepted

## Context

An agreed easy day could be overwritten with a harder workout through authoring or push.

## Decision

- Refuse strictly harder-than-plan work at authoring and again at push; allow softer
  work. `--replace` and confirmation never bypass the plan guard.
- Use one hardness scale. ADR 0024 measures resolvable running specs from targets;
  session-type ranking remains the fallback for unmeasurable specs.
- Preview token includes resolved planned intent; account fingerprint excludes it.
- Receipt records pushed session type, judged plan and available hardness; legacy
  receipts fall back to the corresponding local spec rather than being retroactively reranked.
- Divergence is offline-readable and reported when plan import/confirmation creates it.
  Report the dated conflict; never automatically delete, unschedule or re-author.
- ADR 0030 suppresses divergence for a recorded removal until account status shows
  that workout scheduled again.

## Consequences

Earlier authoring cannot bypass a later plan revision. The athlete changes the plan
or softens the request; naming and replacement flags are not permission escalations.
