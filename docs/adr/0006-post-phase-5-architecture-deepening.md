# ADR 0006 - Post-Phase-5 architecture deepening

## Status

Accepted

## Context

Historical reports exposed shallow interfaces around time, threshold policy and weekly explanatory facts.

## Decision

- One report horizon bounds daily/weekly facts and signals. Expand partial daily
  recomputes to the affected Monday before rebuilding dependent weekly metrics.
- Materialize `weekly_plan_actual` beside weekly adherence; derive missing historical
  rows only as compatibility fallback until recomputation. Do not re-derive stored
  explanations from a later plan or threshold policy.
- `thresholds.py` owns defaults and overrides; null DB rows cannot mask code defaults.
- `SyncResult` owns partial-success/total-outage policy, while stream helpers own
  advancement. Preserve the injected sync orchestration seam.
- The executable narrative guide is `skills/coach/SKILL.md`, reached through its
  routing references rather than a duplicate standalone coach guide.

## Consequences

Past windows exclude future weekly facts, partial recomputation stays coherent,
and stored adherence agrees with its explanatory rows. Existing installations retain
bootstrap and missing-week compatibility behavior.
