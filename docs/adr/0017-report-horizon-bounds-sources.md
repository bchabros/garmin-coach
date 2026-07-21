# ADR 0017 - The report horizon bounds sources; standing reads declare provenance

## Status

Accepted

## Context

ADR 0006 made `to_date` a single **report horizon**: daily rows, weekly rows and
weekly signals are all selected at or before it. ADR 0009 went further for the
snapshot - "`computed_at` bounds every latest read" - so a backfill to a past date
reproduces that day's standing.

Issue #36 found the horizon was a promise the code only half kept. Two different
failures hid behind one symptom:

1. **The zones mart stamped the cutoff without obeying it.** `zones.rollup`
   passed `through_date` into `computed_at`, but `_latest_lthr` and `_aerobic_runs`
   queried `fitness_markers` and `activities` unbounded. Materializing as-of
   `2026-06-15` stored `lthr_detected_on = 2026-07-10`: an anchor detected almost a
   month after the horizon it claimed to describe, with the Z2 pace regression fitted
   over runs that had not happened yet. The same class of leak sat in
   `overlap.coverage`, which counted every captured set regardless of date.

2. **Standing reads have no as-of form at all.** `athlete_zones` and
   `athlete_status` are singletons by ADR 0009's own decision. A digest built for a
   past horizon therefore mixed horizon-bounded daily/weekly sections with whatever
   the singleton happened to hold, and `generate_report` attached the current
   snapshot to a report for any `to_date` - silently, with nothing in the artifact
   saying which horizon the standing belonged to.

These need different answers: (1) is a plain correctness bug, (2) is a semantic gap.

## Decision

- **Every source that can be bounded is bounded by the horizon.** `zones.rollup`
  filters its LTHR anchor and its aerobic runs to `date <= cutoff`, and
  `overlap.coverage` takes an optional `through_date` that the digest passes. The
  bound is inclusive: a detection on the horizon day still anchors the recompute.
  This is what ADR 0009 already required of the snapshot; zones and coverage were
  simply not held to it.

- **The singletons stay singletons; the artifacts declare their provenance.**
  Rejected: historising `athlete_zones` / `athlete_status` into one row per
  `computed_at`. That would make as-of standing reads exact, but it re-litigates
  ADR 0009 for a use case the athlete does not have - reports are generated forward,
  and the one real as-of path (materialize at H, then report at H) already yields a
  standing at H now that (1) is fixed. Instead every standing block carries
  `matches_horizon`: `True` when the row was computed for this report's `to_date`,
  `False` when it was not, `None` when either date is unknown. The digest's `zones`
  section and the report's `snapshot.json` both carry it.

- **The `mcp__coach__*` current-standing tools are unchanged.** `get_zones` and
  `get_snapshot` take no horizon - they are "right now" reads by construction, and
  the freshness envelope already reports mart staleness against the actual today.

## Consequences

- An as-of materialization is genuinely reproducible: re-running `features` for a
  past `to_date` yields the same zones row it would have produced on that day.
- A consumer that ignores `matches_horizon` is no worse off than before; a consumer
  that reads it can tell an as-of report from a current one. The coach skill should
  hedge standing claims when it is `False`.
- Full as-of standing (a real historical query, not a marker) remains available if
  it is ever needed, and would supersede this ADR rather than extend it.
- Amends ADR 0009: `snapshot.rollup` no longer commits - `features` owns one
  transaction for the whole materialization pass (issue #35).
