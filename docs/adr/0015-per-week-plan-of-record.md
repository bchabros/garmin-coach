# ADR 0015 - The per-week plan of record (file as source, model proposes, tool validates)

## Status

Accepted

## Context

A repeating template cannot represent the athlete's actual dated week, paces or revisions.

## Decision

- Authored Monday-based seven-day Markdown is the plan of record. `plan_week` is a
  replaceable parsed-intent cache; prose, paces and history remain in the file.
- One resolver prefers the authored week, then an explicitly chosen optional template,
  and names the source. With neither source, planned intent/match/adherence are unknown;
  no missed session is invented (#84 amendment).
- A malformed plan raises and degrades nightly import, never silently falling back.
  Partial import preserves accepted weeks and rebuilds their metrics (shared flow).
- Model proposals pass deterministic validation and preview before explicit confirmation.
  Confirmation uses the same parser, refuses overwriting any authored week and imports
  the accepted file. Rebuilding the cache never destroys source prose/history.
- Actual intent is load-derived; compare collapsed intent classes while keeping original
  planned/actual words. Strength classification accounts for its load and mixed anaerobic
  work; strength recommendations never receive running pace targets (ADR 0024).
- `PLAN_MISSING` means no authored week, naming a template only when one exists.
  Legacy integer caches do not justify destructive migration of personal state.

## Consequences

Dated training remains inspectable and human-owned. Files and deterministic readers
share vocabulary; unknown planning evidence stays unknown. Plan changes report pushed
workout conflicts instead of modifying the account automatically (ADR 0021).
