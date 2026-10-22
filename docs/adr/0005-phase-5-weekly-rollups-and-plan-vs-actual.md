# ADR 0005 - Phase 5: weekly rollups, plan-vs-actual, and deload detection

## Status

Accepted

## Context

Daily load alone cannot describe a completed week, plan divergence or a rising-load sequence.

## Decision

- Weekly rollup runs inside `features`, reads daily metrics and materializes only
  complete Monday–Sunday weeks through yesterday. In-progress weeks are excluded.
- Foster monotony is daily mean / SD, strain is weekly load times monotony; invalid
  cases (including fewer than two training days) are null, never infinity.
- Recovery means ignore null observations. Unrecorded training is invisible; recent
  empty planned days require the unconfirmed-day safeguards of ADR 0028.
- Plan adherence compares seven planned days against load-derived actual intent;
  unknown planned days make adherence null. ADRs 0015 and 0024 refine plan sources
  and intent-class matching. Stored per-day facts accompany the adherence ratio.
- Retrospective deload is a configured load drop. Prospective `DELOAD_ADVISED` requires
  sufficient complete history, consecutive rising loads and high ACWR or monotony.
- Extend the existing digest with the latest complete week's facts and divergence
  direction, not another report artifact or new charts.

## Consequences

Facts and prospective policy remain distinct. Multi-sport weighting, long-term
VO2max/threshold trend charts and PDF/Notion export remain deferred. ADR 0006 adds
materialized plan-vs-actual details and horizon coherence.
